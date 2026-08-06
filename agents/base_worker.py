# MrBot1000/agents/base_worker.py — Core LLM integration & worker utilities
"""
WorkerAgent provides base functionality for all MrBot1000 subagents:
- LLM calling with multiple provider fallback (OpenAI -> Anthropic -> Ollama)
- Secure file I/O operations
- Research and file scanning utilities
- Shared context integration via _get_shared_context()
"""

import os
import re
import time
import json
import sqlite3
import mimetypes
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
import shutil

try:
    from groq import Groq as GroqClient
    GROQ_AVAILABLE = True
except (ImportError, ModuleNotFoundError, OSError):
    GROQ_AVAILABLE = False
    GroqClient = None

try:
    import ollama
    OLLAMA_AVAILABLE = True
except (ImportError, ModuleNotFoundError, OSError, Exception):
    OLLAMA_AVAILABLE = False
    ollama = None

try:
    import openai
    OPENAI_AVAILABLE = True
except (ImportError, ModuleNotFoundError, OSError, Exception):
    OPENAI_AVAILABLE = False
    openai = None

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except (ImportError, ModuleNotFoundError, OSError, Exception):
    ANTHROPIC_AVAILABLE = False
    Anthropic = None

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except (ImportError, ModuleNotFoundError, OSError, Exception):
    _REQUESTS_AVAILABLE = False
    _requests = None

try:
    import httpx
    HTTPX_AVAILABLE = True
except (ImportError, ModuleNotFoundError, OSError, Exception):
    HTTPX_AVAILABLE = False
    httpx = None

ROOT_FOLDER = str(Path(__file__).resolve().parent.parent)
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

# Real project file tree (excludes heavy/vendor dirs). Used to ground subagent
# prompts so the model stops hallucinating non-existent files like source.py.
_CODEBASE_INDEX_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", "build", "dist", ".mypy_cache",
}


def project_file_tree(max_files: int = 200) -> str:
    """Return a compact tree (relative paths, sizes) of the real project root.

    This is the single source of truth for subagents. Any file NOT listed here
    does not exist — agents must not invent filenames.
    """
    lines = [f"Project root: {ROOT_FOLDER}", "", "Files (relative path, size bytes):"]
    count = 0
    try:
        for p in sorted(Path(ROOT_FOLDER).rglob("*")):
            if not p.is_file():
                continue
            if any(part in _CODEBASE_INDEX_SKIP_DIRS for part in p.parts):
                continue
            if p.suffix.lower() not in {
                ".py", ".md", ".txt", ".json", ".yaml", ".yml",
                ".toml", ".cfg", ".ini", ".rst", ".csv", ".env.example",
            }:
                continue
            try:
                rel = p.relative_to(ROOT_FOLDER).as_posix()
                lines.append(f"  {rel} ({p.stat().st_size} bytes)")
                count += 1
            except Exception:
                pass
            if count >= max_files:
                lines.append("  ... (truncated)")
                break
    except Exception as e:
        return f"(error indexing project root: {e})"
    lines.append(f"\nTotal indexed: {count} source/config files")
    return "\n".join(lines)

# Security blocklist – filenames that should never be read/written
FILENAME_BLOCKLIST = {
    "config.yaml", "config.yml", ".env", "credentials.json",
    "id_rsa", "id_dsa", ".gitconfig", ".bashrc", ".zshrc",
    "passwd", "shadow", "sudoers", "hosts", "secure",
}

# Configuration from environment (with defaults)
RESEARCH_MAX_CHARS = int(os.getenv("RESEARCH_MAX_CHARS", 5000))
RESEARCH_MAX_BYTES = int(os.getenv("RESEARCH_MAX_BYTES", 200_000))
DEEP_READ_MAX_CHARS = int(os.getenv("DEEP_READ_MAX_CHARS", 8000))
# Increased default MAX_TOKENS for better decisions (Issue F.2)
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2048"))
LLM_TIMEOUT = 15.0  # Default timeout for LLM calls
BLOCKED_MIME_TYPES = {"application/x-executable", "application/x-sharedlib",
                      "application/x-object", "application/x-dosexec"}

# Python 3.9+ for is_relative_to
try:
    def is_safe_path(base: Path, candidate: Path) -> bool:
        """Ensure candidate is inside base and not a symlink pointing outside."""
        try:
            candidate = candidate.resolve()
            base = base.resolve()
            if not candidate.is_relative_to(base):
                return False
            if candidate.is_symlink():
                target = candidate.resolve()
                if not target.is_relative_to(base):
                    return False
            return True
        except Exception:
            return False
except AttributeError:
    # Fallback for Python < 3.9
    def is_safe_path(base: Path, candidate: Path) -> bool:
        try:
            candidate = candidate.resolve()
            base = base.resolve()
            if not str(candidate).startswith(str(base)):
                return False
            if candidate.is_symlink():
                target = candidate.resolve()
                if not str(target).startswith(str(base)):
                    return False
            return True
        except Exception:
            return False


def is_safe_filename(name: str) -> bool:
    """Reject clearly dangerous filenames."""
    lower = name.lower()
    for bad in FILENAME_BLOCKLIST:
        if bad in lower or lower.endswith(bad):
            return False
    return True


def is_safe_mime(file_path: Path) -> bool:
    """Heuristic mime-type check – only allow text-like files."""
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime and mime in BLOCKED_MIME_TYPES:
        return False
    return True


def fingerprint(s: str) -> str:
    """Generate a short unique ID for a string."""
    import hashlib
    return hashlib.md5(s.encode()).hexdigest()[:16]


def ts_now() -> str:
    """Return ISO timestamp."""
    from datetime import datetime
    return datetime.now().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
#  WorkerAgent
# ─────────────────────────────────────────────────────────────────────────────
class WorkerAgent:
    """Base class for MrBot1000 subagents with LLM integration and secure I/O."""
    
    def __init__(self, api_key: str, log_signal, db=None,
                 ollama_model: str | None = None,
                 chat_ollama_model: str | None = None,
                 primary_ollama_model: str | None = None):
        self.api_key = api_key
        self.log_signal = log_signal
        self.db = db
        self.groq = None
        self.research_folder = None
        self.max_file_size = MAX_FILE_SIZE
        self.last_provider = None
        self.last_model = None
        self.chat_model = None
        self._shared_context = None
        self._last_action = ""
        self._running = True
        # Model overrides: instance > env
        self._ollama_model_override = primary_ollama_model or ollama_model
        self._chat_ollama_model_override = chat_ollama_model

    def _get_shared_context(self):
        """Lazy-load shared context for cross-model communication"""
        if self._shared_context is None:
            from agents.shared_context import get_shared_context
            self._shared_context = get_shared_context()
        return self._shared_context

    def _chat_model_effective(self) -> str | None:
        chat_model = self._chat_ollama_model_override or os.getenv("OLLAMA_CHAT_MODEL", "").strip() or None
        if not chat_model or not OLLAMA_AVAILABLE:
            return chat_model
        try:
            available = self._ollama_model_names()
        except Exception:
            available = []
        if available and chat_model not in available:
            self.log_signal.emit(f"[LLM] chat model not found: {chat_model}; available={available[:5]}")
            return None
        return chat_model

    def _main_model_effective(self) -> str | None:
        """Resolve the main/coding model with validation and fallback."""
        candidates = [
            os.getenv("OLLAMA_MAIN_MODEL", "").strip(),
            self._ollama_model_override or "",
            os.getenv("OLLAMA_MODEL", "").strip(),
        ]
        candidates = [c for c in candidates if c]
        if not candidates:
            return None

        if not OLLAMA_AVAILABLE:
            return candidates[0]

        try:
            available = self._ollama_model_names()
        except Exception:
            available = []

        if available:
            for cand in candidates:
                if cand in available:
                    self.log_signal.emit(f"[LLM] using main model: {cand}")
                    return cand
            self.log_signal.emit(
                f"[LLM] main model not found: {candidates[0]}; available={available[:5]}"
            )
            return None

        # If tags query failed, still try the highest-priority candidate.
        self.log_signal.emit(f"[LLM] using main model: {candidates[0]}")
        return candidates[0]

    def _ollama_model_names(self) -> list[str]:
        if not _REQUESTS_AVAILABLE or not _requests:
            return []
        try:
            r = _requests.get("http://127.0.0.1:11434/api/tags", timeout=3)
            r.raise_for_status()
            data = r.json()
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception:
            return []

    # ── LLM Methods ────────────────────────────────────────────────────────────

    async def llm_async(self, system: str, user: str, *, chat: bool = False, 
                        timeout: float = LLM_TIMEOUT, **kwargs) -> str:
        """Non-blocking async LLM call with proper timeout handling (Issue C.3, C.4)."""
        max_tokens = kwargs.get("max_tokens", MAX_TOKENS)
        self.log_signal.emit(f"[LLM] async call starting (timeout={timeout}s) max_tokens={max_tokens}")
        
        # Prefer Ollama first so local chat/main models remain active in parallel.
        if os.getenv("DISABLE_OLLAMA", "false").lower() != "true" and OLLAMA_AVAILABLE:
            chat_model = self._chat_model_effective() if chat else None
            if chat_model:
                model = chat_model
            elif not chat:
                model = self._main_model_effective()
            else:
                model = os.getenv("OLLAMA_MODEL", "llama3.2")
            
            if model:
                try:
                    result = await asyncio.wait_for(
                        self._call_ollama_async(model, system, user, max_tokens, chat=chat),
                        timeout=timeout
                    )
                    self.last_provider = "ollama"
                    return result
                except asyncio.TimeoutError:
                    self.log_signal.emit(f"[LLM] timeout for model {model}")
                    raise RuntimeError(f"Ollama model '{model}' timeout after {timeout}s")
                except Exception as e:
                    self.log_signal.emit(f"[LLM] async ollama failed: {e}")

        if os.getenv("DISABLE_OPENAI", "false").lower() != "true" and OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            try:
                result = await asyncio.wait_for(
                    self._call_openai_async("gpt-4o-mini", system, user, max_tokens, chat=chat),
                    timeout=timeout
                )
                self.last_provider = "openai"
                return result
            except asyncio.TimeoutError:
                self.log_signal.emit("[LLM] OpenAI timeout")
                raise RuntimeError(f"OpenAI timeout after {timeout}s")
            except Exception as e:
                self.log_signal.emit(f"[LLM] async openai failed: {e}")

        if os.getenv("DISABLE_ANTHROPIC", "false").lower() != "true" and ANTHROPIC_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            try:
                result = await asyncio.wait_for(
                    self._call_anthropic_async("claude-3-5-sonnet-20241022", system, user, max_tokens),
                    timeout=timeout
                )
                self.last_provider = "anthropic"
                return result
            except asyncio.TimeoutError:
                self.log_signal.emit("[LLM] Anthropic timeout")
                raise RuntimeError(f"Anthropic timeout after {timeout}s")
            except Exception as e:
                self.log_signal.emit(f"[LLM] async anthropic failed: {e}")

        self.last_provider = "error"
        return "ERROR: Any LLM provider failed"

    def llm(self, system: str, user: str, *, chat: bool = False, **kwargs) -> str:
        """Call LLM with retries, multiple providers, and max_tokens."""
        max_tokens = kwargs.get("max_tokens", MAX_TOKENS)
        trigger = kwargs.get("trigger", "unspecified")
        providers = []

        # Prefer Ollama first so local chat/main models remain active in parallel.
        if os.getenv("DISABLE_OLLAMA", "false").lower() != "true" and OLLAMA_AVAILABLE:
            env_model = os.getenv("OLLAMA_MODEL", "llama3.2")
            chat_model = self._chat_model_effective() if chat else None
            if chat_model:
                model = chat_model
            elif not chat:
                model = self._main_model_effective()
            else:
                model = env_model
            if model:
                providers.append(("ollama", self._call_ollama, model, model))

        # OpenAI: use when available and not disabled
        if os.getenv("DISABLE_OPENAI", "false").lower() != "true" and OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            providers.append(("openai", self._call_openai, "OPENAI_MODEL", "gpt-4o-mini"))

        # Anthropic: use when available and not disabled
        if os.getenv("DISABLE_ANTHROPIC", "false").lower() != "true" and ANTHROPIC_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            providers.append(("anthropic", self._call_anthropic, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))

        self.log_signal.emit(f"[LLM] providers={[p[0] for p in providers]}")

        for attempt in range(3):
            for name, func, model_key, default_model in providers:
                try:
                    model = default_model
                    self.last_model = model
                    mode_label = "chat" if chat else "main"
                    self.log_signal.emit(f"[LLM] trying {name} mode={mode_label} model={model}")
                    t0 = time.time()
                    resp = func(model, system, user, max_tokens, chat=chat)
                    latency = int((time.time() - t0) * 1000)
                    self.last_provider = name
                    if self.db:
                        try:
                            self.db.log_llm_call(
                                model=str(model),
                                provider=name,
                                trigger=trigger,
                                prompt_chars=len(system) + len(user),
                                response_chars=len(resp),
                                latency_ms=latency,
                                error=None,
                            )
                        except Exception:
                            pass
                    return resp
                except Exception as e:
                    latency = int((time.time() - t0) * 1000) if 't0' in locals() else 0
                    self.log_signal.emit(f"[LLM] {name} failed ({type(e).__name__}), trying next...")
                    if self.db:
                        try:
                            self.db.log_llm_call(
                                model=str(default_model),
                                provider=name,
                                trigger=trigger,
                                prompt_chars=len(system) + len(user),
                                response_chars=0,
                                latency_ms=latency,
                                error=str(e),
                            )
                        except Exception:
                            pass
                    continue

            if attempt < 2:
                time.sleep(1.5 ** attempt)  # Shorter backoff (Issue C.4)
                continue
            break

        self.last_provider = "error"
        return "ERROR: LLM unavailable"

    def _call_openai(self, model: str, system: str, user: str, max_tokens: int, chat: bool = False) -> str:
        if not OPENAI_AVAILABLE or not openai or not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OpenAI not available")
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
        ).choices[0].message.content

    async def _call_openai_async(self, model: str, system: str, user: str, 
                                  max_tokens: int, chat: bool = False) -> str:
        """Async OpenAI call using httpx (Issue F.4)."""
        if not HTTPX_AVAILABLE or not httpx:
            raise RuntimeError("httpx not available for async OpenAI")
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OpenAI not available")
        
        client = httpx.AsyncClient(timeout=LLM_TIMEOUT)
        try:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                }
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        finally:
            await client.aclose()

    def _call_anthropic(self, model: str, system: str, user: str, max_tokens: int, chat: bool = False) -> str:
        if not ANTHROPIC_AVAILABLE or not Anthropic or not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Anthropic not available")
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "user", "content": user}
            ],
            system=system,
        ).content[0].text

    async def _call_anthropic_async(self, model: str, system: str, user: str, 
                                     max_tokens: int) -> str:
        """Async Anthropic call using httpx (Issue F.4)."""
        if not HTTPX_AVAILABLE or not httpx:
            raise RuntimeError("httpx not available for async Anthropic")
        
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("Anthropic not available")
        
        client = httpx.AsyncClient(timeout=LLM_TIMEOUT)
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01"
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}]
                }
            )
            response.raise_for_status()
            return response.json()["content"][0]["text"]
        finally:
            await client.aclose()

    async def _call_ollama_async(self, model: str, system: str, user: str, 
                                  max_tokens: int, chat: bool = False) -> str:
        """Async Ollama call with timeout (Issue C.3, F.3)."""
        if not OLLAMA_AVAILABLE or not ollama:
            raise RuntimeError("Ollama not available")
        
        options = {"num_predict": max_tokens}
        if chat:
            chat_gpu = os.getenv("OLLAMA_CHAT_GPU", "").strip()
            if chat_gpu.isdigit() or (chat_gpu.startswith("-") and chat_gpu[1:].isdigit()):
                options["num_gpu"] = int(chat_gpu)
            else:
                # Allow GPU for chat model if available (Issue F.3)
                ollama_gpu = os.getenv("OLLAMA_GPU", "0").strip()
                if ollama_gpu.isdigit():
                    options["num_gpu"] = int(ollama_gpu)
                else:
                    options["num_gpu"] = 0

        content = await asyncio.wait_for(
            asyncio.to_thread(
                self._sync_ollama_call,
                model, system, user, options
            ),
            timeout=LLM_TIMEOUT
        )
        return content

    def _sync_ollama_call(self, model: str, system: str, user: str, options: dict) -> str:
        """Synchronous Ollama call for use with asyncio.to_thread."""
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            options=options,
            keep_alive=-1,
        )
        return response['message']['content']

    def _call_ollama(self, model: str, system: str, user: str, max_tokens: int, chat: bool = False) -> str:
        if not OLLAMA_AVAILABLE or not ollama:
            raise RuntimeError("Ollama not available or pydantic_core issue")
        try:
            options = {"num_predict": max_tokens}
            if chat:
                chat_gpu = os.getenv("OLLAMA_CHAT_GPU", "").strip()
                if chat_gpu.isdigit() or (chat_gpu.startswith("-") and chat_gpu[1:].isdigit()):
                    options["num_gpu"] = int(chat_gpu)
                else:
                    options["num_gpu"] = 0
            self.log_signal.emit(
                f"[LLM] ollama request model={model} mode={'chat' if chat else 'main'} "
                f"options={options}"
            )
            t0 = time.time()
            content = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                options=options,
                keep_alive=-1,
            )['message']['content']
            dt = time.time() - t0
            self.log_signal.emit(
                f"[LLM] ollama response model={model} latency={dt:.2f}s "
                f"chars={len(content)}"
            )
            return content
        except Exception as e:
            raise RuntimeError(f"Ollama model '{model}' failed: {e}")

    # ── File Operations ───────────────────────────────────────────────────────

    def safe_write_file(self, filename: str, content: str) -> bool:
        """Write a file safely inside ROOT_FOLDER only."""
        if not is_safe_filename(filename):
            self.log_signal.emit(f"BLOCKED: filename '{filename}' is on blocklist")
            return False

        root_resolved = Path(ROOT_FOLDER).resolve()
        full_path = (root_resolved / filename).resolve()

        if not is_safe_path(root_resolved, full_path):
            self.log_signal.emit("BLOCKED: Write outside root folder or unsafe symlink")
            return False

        if len(content) > self.max_file_size:
            self.log_signal.emit(f"BLOCKED: File size exceeds {self.max_file_size // 1024 // 1024}MB")
            return False

        _, _, free = shutil.disk_usage(ROOT_FOLDER)
        if free < 100 * 1024 * 1024:
            self.log_signal.emit("BLOCKED: Free space < 100MB")
            return False

        try:
            full_path.write_text(content, encoding="utf-8")
            self.log_signal.emit(f"Created safely: {filename}")
            return True
        except Exception as e:
            self.log_signal.emit(f"Write error: {e}")
            return False

    # ── Research Methods (used by Manager) ────────────────────────────────────

    def research_all(self) -> dict:
        """Scan ROOT_FOLDER (.py files) and user-selected research_folder."""
        # Explicit file-tree header so subagents know the COMPLETE set of real
        # files. Anything not listed below does not exist (no source.py etc).
        root_tree = project_file_tree()

        root_parts = [root_tree, "", "=== File contents (root) ==="]
        try:
            for p in sorted(Path(ROOT_FOLDER).rglob("*.py")):
                if any(part in _CODEBASE_INDEX_SKIP_DIRS for part in p.parts):
                    continue
                try:
                    size = p.stat().st_size
                    if size >= 50000:
                        continue
                    content = p.read_text(encoding="utf-8", errors="ignore")[:1200]
                    rel = p.relative_to(ROOT_FOLDER).as_posix()
                    root_parts.append(f"[ROOT/{rel}] ({size} bytes)\n{content}\n---")
                except Exception:
                    pass
        except Exception as e:
            self.log_signal.emit(f"Error scanning root files: {e}")

        root_text = "\n".join(root_parts) if root_parts else "(no .py files found in root)"

        rf = self.research_folder
        research_text = ""
        research_file_count = 0

        if not rf:
            research_text = "(research folder not set — select one via Management tab)"
        elif not Path(rf).exists():
            research_text = f"(path does not exist: {rf})"
        else:
            allowed_ext = {".py", ".txt", ".md", ".json", ".yaml", ".yml",
                           ".toml", ".cfg", ".ini", ".rst", ".csv"}
            research_parts = []
            skipped_large = []
            skipped_ext = []
            skipped_unsafe = []

            try:
                all_files = sorted(Path(rf).rglob("*"))
                for p in all_files:
                    if not p.is_file():
                        continue

                    if not is_safe_filename(p.name):
                        skipped_unsafe.append(str(p.relative_to(rf)))
                        continue
                    if not is_safe_mime(p):
                        skipped_ext.append(str(p.relative_to(rf)))
                        continue
                    if p.suffix.lower() not in allowed_ext:
                        skipped_ext.append(str(p.relative_to(rf)))
                        continue

                    size = p.stat().st_size
                    if size >= RESEARCH_MAX_BYTES:
                        skipped_large.append(f"{p.relative_to(rf)} ({size // 1024}KB)")
                        continue

                    rel = p.relative_to(rf)
                    rel_path = rel.as_posix()

                    try:
                        content = p.read_text(encoding="utf-8", errors="ignore")
                        display_content = content[:RESEARCH_MAX_CHARS]
                        research_parts.append(f"[{rel.as_posix()}] ({size} bytes)\n{display_content}\n---")
                        research_file_count += 1
                    except Exception as fe:
                        self.log_signal.emit(f"Skipped {p.name}: {fe}")

                if research_parts:
                    research_text = "\n".join(research_parts)
                    msg = f"Research scan: {research_file_count} file(s) from {rf}"
                    if skipped_unsafe:
                        msg += f" | skipped {len(skipped_unsafe)} unsafe file(s)"
                    if skipped_large:
                        msg += f" | skipped {len(skipped_large)} large file(s)"
                    if skipped_ext:
                        msg += f" | skipped {len(skipped_ext)} unsupported type(s)"
                    self.log_signal.emit(msg)
                else:
                    research_text = (
                        f"(no supported files found in {rf} — "
                        f"supported: {', '.join(sorted(allowed_ext))})"
                    )
                    self.log_signal.emit(f"Research scan: 0 files found in {rf}")
            except Exception as e:
                self.log_signal.emit(f"Error scanning research folder: {e}")

        payload = {
            "root": root_text,
            "research": research_text,
            "research_path": rf,
            "research_file_count": research_file_count
        }
        try:
            self._get_shared_context().update_research_snapshot(payload)
        except Exception:
            pass
        return payload

    def file_index(self) -> str:
        """Return a compact index of all files in the research folder."""
        rf = self.research_folder
        if not rf or not Path(rf).exists():
            return f"(research folder not set or does not exist: {rf})"

        allowed_ext = {".py", ".txt", ".md", ".json", ".yaml", ".yml",
                       ".toml", ".cfg", ".ini", ".rst", ".csv"}
        lines = [f"Research folder: {rf}", "Files (relative path, size):"]
        count = 0
        try:
            for p in sorted(Path(rf).rglob("*")):
                if not p.is_file():
                    continue
                if not is_safe_filename(p.name):
                    continue
                if p.suffix.lower() not in allowed_ext:
                    continue
                try:
                    size = p.stat().st_size
                    rel_path = p.relative_to(rf).as_posix()
                    lines.append(f"{rel_path} ({size} bytes)")
                    count += 1
                except Exception:
                    pass
        except Exception as e:
            self.log_signal.emit(f"Error indexing research folder: {e}")

        return "\n".join(lines) + f"\n\nTotal: {count} files"

    def read_specific_files(self, filenames: list, base_path: str = None) -> str:
        """Read full content of specific files by relative path."""
        base = Path(base_path or self.research_folder or ROOT_FOLDER).resolve()
        parts = []
        folder_path = str(base)

        for name in filenames:
            if not is_safe_filename(name):
                self.log_signal.emit(f"BLOCKED read of unsafe name: {name}")
                continue

            p = (base / name).resolve()
            if not is_safe_path(base, p):
                self.log_signal.emit(f"BLOCKED read outside base: {name}")
                continue

            if not p.exists():
                self.log_signal.emit(f"File not found: {name}")
                parts.append(f"[{name}] ERROR: file not found\n---")
                continue

            if not is_safe_mime(p):
                self.log_signal.emit(f"BLOCKED read of unsafe mime type: {name}")
                continue

            try:
                size = p.stat().st_size
                content = p.read_text(encoding="utf-8", errors="ignore")
                display_content = content[:DEEP_READ_MAX_CHARS]
                parts.append(f"[{name}] ({size} bytes)\n{display_content}\n---")
            except Exception as e:
                parts.append(f"[{name}] ERROR: {e}\n---")

        return "\n".join(parts) if parts else "(no files read)"

    # ── State Management ─────────────────────────────────────────────────────

    def set_research_folder(self, path: str):
        """Set the research folder path."""
        self.research_folder = path

    def stop(self):
        """Signal the worker to stop running."""
        self._running = False

    def is_running(self) -> bool:
        """Check if the worker is still running."""
        return self._running