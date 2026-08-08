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
import datetime
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

ROOT_FOLDER = str(Path(__file__).resolve().parent.parent)  # project root (2.0.19)
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

# Directories that must never be written into by safe_write_file (2.0.19).
WRITE_EXCLUSION_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", "build", "dist", ".mypy_cache",
    # Non-required / external folders the agents must not overwrite:
    ".hermes", "test_results", "github_upload", ".mrbot_backups",
    # Deliverable workspaces are writable but excluded from the planner tree:
    "work",
}

# Backup directory name for safe_write_file() pre-edit copies (2.0.19 safety).
# MUST be defined — safe_write_file/restore_last_backup reference it. Previously
# missing → every backup raised NameError and was silently skipped (no safety).
BACKUP_DIRNAME = ".mrbot_backups"

# App source files that, if truncated/overwritten, prevent the app from launching
# or break the autonomous loop. The Coder worker's full-file-rewrite path
# (analyze_and_fix/refactor) has repeatedly DESTROYED these (e.g. truncated
# action_pipeline.py to 94 lines, or coder.py to 0 bytes) because it shows the LLM
# only the first 3000 chars but asks for the "complete file". safe_write_file
# refuses to overwrite any of these basenames so a bad LLM rewrite can never
# break the running app. For intentional dev edits, use an external editor.
PROTECTED_SOURCE_FILES = {
    "main.py", "manager.py", "ui.py", "theme_config.py", "library.py",
    "database.py", "action_pipeline.py", "earning_pipeline.py",
    "startup_validation.py", "test_earning_pipeline.py",
    "__init__.py", "base_worker.py", "coder.py", "analyst_worker.py",
    "job_search_worker.py", "fiverr_client.py", "upwork_client.py",
    "chat_router.py", "summarizer.py", "shared_context.py",
    "document_scanner.py", "task_workspace.py", "earning_discoverer.py",
    "opportunity_lifecycle.py", "wallet_manager.py", "content_generator.py",
    "workflow_planner.py", "social_earning_platform.py", "microtask_client.py",
    "airdrop_scanner.py", "airdrop_claimer.py", "defi_scanner.py",
}

# Directories excluded from project_file_tree() so the planner only sees real,
# stable source files (2.0.19).
_CODEBASE_INDEX_SKIP_DIRS = WRITE_EXCLUSION_DIRS | {
    ".github", "references", "skills", "scripts", "tests", "docs",
}

# Registry of worker classes by name (populated by manager registration).
WORKER_REGISTRY = {}


def _normalize_keep_alive(value) -> object:
    """Normalize a keep_alive value into what Ollama 0.6.x accepts.

    Ollama's server rejects bare-number durations ("300" -> status 400
    "missing unit in duration"). Valid forms: int (0, -1) or a unit-suffixed
    string ("300s", "5m", "1h"). We map:
      -1          -> int -1  (keep loaded forever)
      0 / "0"     -> int 0   (unload immediately)
      "300"       -> "300s"  (bare digits get an 's' unit)
      "5m"/"1h"   -> unchanged
      None/""      -> None    (Ollama applies its own default)
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    if s == "":
        return None
    if s in ("-1", "0"):
        return int(s)
    if s.lstrip("-").isdigit():
        return s + "s"
    return s


# Security blocklist – filenames that should never be read/written
FILENAME_BLOCKLIST = {
    "config.yaml", "config.yml", ".env", "credentials.json",
    "id_rsa", "id_dsa", ".gitconfig", ".bashrc", ".zshrc",
    "passwd", "shadow", "sudoers", "hosts", "secure",
}

# PROTECTED SOURCE MODULES — the app's own import-critical Python files.
# The Coder agent MUST NEVER truncate/overwrite these. Writing a broken or
# shortened version of any of these makes the application unlaunchable
# (ImportError at startup) or destroys its own source (e.g. coder.py -> 0 bytes,
# action_pipeline.py -> 94 lines). safe_write_file refuses such writes outright.
# This is the hard backstop: even a pathological LLM output cannot break launch.
# Stored as BASENAMES only — safe_write_file matches by Path(filename).name so
# "./coder.py", "agents/coder.py", and absolute paths are all caught.
PROTECTED_SOURCE_FILES = {
    "main.py", "manager.py", "action_pipeline.py", "database.py", "library.py",
    "ui.py", "theme_config.py", "startup_validation.py", "earning_pipeline.py",
    "earning_memory.py", "test_earning_pipeline.py",
    "coder.py", "base_worker.py", "job_search_worker.py", "analyst_worker.py",
    "__init__.py",
}

# Configuration from environment (with defaults)


# ── Thinking-mode support (v2.0.24) ───────────────────────────────────────────
# Thinking models (e.g. LFM2.5-*Thinking) emit a <think>…</think> reasoning block
# followed by the final answer. We split these apart so the answer is never
# starved by the reasoning tokens and the reasoning can be surfaced separately.
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def split_thinking(content: str):
    """Return (thinking, answer) from a raw model response.

    Handles the common single-<think>…</think> form. If no thinking block is
    present (non-thinking models), returns ("", content). Robust to missing
    close tags (treats the remainder as thinking).
    """
    if not content or _THINK_OPEN not in content:
        return ("", content or "")
    start = content.index(_THINK_OPEN) + len(_THINK_OPEN)
    close_idx = content.find(_THINK_CLOSE, start)
    if close_idx == -1:
        return (content[start:], "")
    thinking = content[start:close_idx].strip()
    answer = content[close_idx + len(_THINK_CLOSE):].strip()
    return (thinking, answer)


def think_budget(level: str, chat: bool) -> int:
    """num_predict headroom for the ANSWER given a thinking level.

    Low  -> model thinks briefly; most of the budget is the answer.
    Med  -> balanced.
    High -> generous answer budget so deeper reasoning never starves the reply.
    """
    lvl = (level or "med").strip().lower()
    if chat:
        table = {"low": 500, "med": 900, "high": 1500}
    else:
        table = {"low": 1500, "med": 2500, "high": 4000}
    return table.get(lvl, table["med"])


def read_think_level(chat: bool) -> str:
    key = "THINK_LEVEL" if chat else "MAIN_THINK_LEVEL"
    return os.getenv(key, os.getenv("THINK_LEVEL", "med")).strip().lower() or "med"


RESEARCH_MAX_CHARS = int(os.getenv("RESEARCH_MAX_CHARS", 15000))  # v2.0.24: x3 (was 5000) so research context is never cut off
DEEP_READ_MAX_CHARS = int(os.getenv("DEEP_READ_MAX_CHARS", 24000))  # v2.0.24: x3 (was 8000)
# Byte-size cap for a single research file: files larger than this are skipped
# during folder scanning (display is still capped at RESEARCH_MAX_CHARS above).
RESEARCH_MAX_BYTES = int(os.getenv("RESEARCH_MAX_BYTES", 2 * 1024 * 1024))  # v2.0.24: defined
# TOTAL research-text SAFETY ceiling (chars) for a single scan. This is ONLY a
# guard against reading a pathological folder into memory (e.g. 50GB of logs);
# it is deliberately large. The REAL mechanism that lets the model ingest ALL
# research regardless of size is the chunked CEO reasoning in manager.py
# (_ceo_decide_chunked): research is split into per-call-sized chunks and fed
# across multiple gather passes. So do NOT set this low — a low value pre-truncates
# the scan and defeats chunking. Default ~1M tokens; raise freely.
RESEARCH_MAX_TOTAL_CHARS = int(os.getenv("RESEARCH_MAX_TOTAL_CHARS", 4000000))  # ~1M tokens safety ceiling
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 2048))  # v2.0.24: x2 (was 1024); thinking budget overrides per-level anyway
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
        self.last_response = {"thinking": "", "answer": "", "raw": "", "provider": None, "model": None, "mode": None}
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

    def llm(self, system: str, user: str, *, chat: bool = False, **kwargs) -> str:
        """Call LLM with retries, multiple providers, and max_tokens."""
        # v2.0.21 P3#6: read MAX_TOKENS live from env (or instance override) so the
        # Settings "Max Tokens" knob applies immediately after Save (no restart).
        # The module-level MAX_TOKENS is only the import-time default.
        if "max_tokens" in kwargs:
            max_tokens = kwargs["max_tokens"]
        elif getattr(self, "_max_tokens", None) is not None:
            max_tokens = self._max_tokens
        else:
            max_tokens = int(os.getenv("MAX_TOKENS", MAX_TOKENS))
        # v2.0.24: thinking-mode budget. If thinking is enabled, size the
        # visible-answer budget by the THINK_LEVEL / MAIN_THINK_LEVEL knob so
        # reasoning tokens never starve the final answer. An explicit
        # max_tokens kwarg (e.g. planner calls) still wins.
        think_on = os.getenv("THINKING_ENABLED", "true").strip().lower()
        think_on = think_on not in ("0", "false", "no", "off")
        if think_on and "max_tokens" not in kwargs:
            level = read_think_level(chat)
            max_tokens = think_budget(level, chat)
            self.log_signal.emit(
                f"[Think] level={level} mode={'chat' if chat else 'main'} num_predict={max_tokens}"
            )
        providers = []

        # OpenAI: use when available and not disabled
        if os.getenv("DISABLE_OPENAI", "false").lower() != "true" and OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            providers.append(("openai", self._call_openai, "OPENAI_MODEL", "gpt-4o-mini"))

        # Anthropic: use when available and not disabled
        if os.getenv("DISABLE_ANTHROPIC", "false").lower() != "true" and ANTHROPIC_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            providers.append(("anthropic", self._call_anthropic, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))

        # Ollama: use when not disabled and available
        if os.getenv("DISABLE_OLLAMA", "false").lower() != "true" and OLLAMA_AVAILABLE:
            # Main model: prefer the live instance override (set by the UI /
            # save_settings), then OLLAMA_MAIN_MODEL, then OLLAMA_MODEL as a
            # legacy fallback. OLLAMA_MAIN_MODEL is the canonical var (see
            # .env + save_settings); OLLAMA_MODEL is only a fallback (2.0.20a).
            env_model = (self._ollama_model_override
                         or os.getenv("OLLAMA_MAIN_MODEL", "").strip()
                         or os.getenv("OLLAMA_MODEL", "llama3.2"))
            chat_model = self._chat_model_effective() if chat else None
            if chat_model:
                model = chat_model
            else:
                model = env_model
            providers.append(("ollama", self._call_ollama, model, model))

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
                    dt_ms = int((time.time() - t0) * 1000)
                    # v2.0.21 P1#2: an empty response (chars=0) is a failure, not
                    # a success. The logs showed the chat model returning empty
                    # bodies, which forced the "heuristic fallback (planner
                    # failed)" path. Treat empty as retryable so a later attempt
                    # (or fallback provider) can produce real output.
                    if not resp or not str(resp).strip():
                        self.log_signal.emit(f"[LLM] {name} returned empty — retrying")
                        try:
                            if self.db is not None:
                                self.db.log_llm_call(
                                    model=model, provider=name,
                                    trigger=getattr(self, "last_trigger", "llm"),
                                    prompt_chars=len(system) + len(user),
                                    response_chars=0, latency_ms=dt_ms,
                                    error="empty response")
                        except Exception:
                            pass
                        # fall through to retry (next provider / attempt)
                        continue
                    # Persist the call so the DB Stats tab can show it (2.0.20e).
                    # Guarded: logging must never break the LLM result.
                    try:
                        if self.db is not None:
                            self.db.log_llm_call(
                                model=model, provider=name,
                                trigger=getattr(self, "last_trigger", "llm"),
                                prompt_chars=len(system) + len(user),
                                response_chars=len(resp or ""),
                                latency_ms=dt_ms, error=None)
                    except Exception as _log_err:
                        self.log_signal.emit(f"[LLM] stats log skipped: {_log_err}")
                    self.last_provider = name
                    return resp
                except Exception as e:
                    self.log_signal.emit(f"[LLM] {name} failed ({e}), trying next...")
                    # Log the failed attempt too (error populated).
                    try:
                        if self.db is not None:
                            self.db.log_llm_call(
                                model=default_model, provider=name,
                                trigger=getattr(self, "last_trigger", "llm"),
                                prompt_chars=len(system) + len(user),
                                response_chars=0, latency_ms=0, error=str(e)[:200])
                    except Exception:
                        pass
                    continue

            if attempt < 2:
                time.sleep(2 ** attempt)
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
                # TTL (not permanent pin): let Ollama evict under VRAM pressure.
                # Fixed in 2.0.16 (Bug B) — keep_alive=-1 pinned the model in
                # VRAM and caused RuntimeError on a 6GB GPU. Ollama 0.6.x requires
                # a unit-suffixed duration ("300s", "5m"); a bare number ("300")
                # is rejected (status 400 "missing unit in duration"). Normalize:
                # -1 => forever (int), bare digits => append "s", else as-given.
                keep_alive=_normalize_keep_alive(os.getenv("OLLAMA_KEEP_ALIVE", "300s")),
            )['message']['content']
            dt = time.time() - t0
            # v2.0.24: Thinking models emit <think>…</think> blocks. Split the
            # reasoning from the final answer so the answer is never starved and
            # the reasoning can be shown separately in the chat UI / logs.
            thinking, answer = split_thinking(content)
            self.log_signal.emit(
                f"[LLM] ollama response model={model} latency={dt:.2f}s "
                f"chars={len(content)} think_chars={len(thinking)} answer_chars={len(answer)}"
            )
            # Keep the structured parts so callers can access reasoning without
            # re-parsing (e.g. summarizer stores thinking in its DB chat turn).
            self.last_response = {
                "thinking": thinking,
                "answer": answer,
                "raw": content,
                "provider": "ollama",
                "model": model,
                "mode": "chat" if chat else "main",
            }
            return content
        except Exception as e:
            raise RuntimeError(f"Ollama model '{model}' failed: {e}")

    # ── File Operations ───────────────────────────────────────────────────────

    def safe_write_file(self, filename: str, content: str) -> bool:
        """Write a file safely inside ROOT_FOLDER only, with a pre-edit backup.

        2.0.19: backs up any existing target to ROOT_FOLDER/.mrbot_backups/
        before overwriting, and refuses protected/write-excluded directories.
        """
        if not is_safe_filename(filename):
            self.log_signal.emit(f"BLOCKED: filename '{filename}' is on blocklist")
            return False

        # Refuse to overwrite the app's own import-critical source files. The
        # Coder's full-file-rewrite path has repeatedly truncated these (e.g.
        # coder.py -> 0 bytes, action_pipeline.py -> 94 lines) via a bad LLM
        # rewrite; blocking here keeps the running app from being destroyed.
        # Matched by basename so absolute paths and nested copies are also blocked.
        _bn = Path(filename).name
        if _bn in PROTECTED_SOURCE_FILES:
            self.log_signal.emit(
                f"BLOCKED: protected app source '{_bn}' — refusing overwrite "
                f"(use an external editor for intentional dev changes)")
            return False

        root_resolved = Path(ROOT_FOLDER).resolve()
        # Accept absolute paths that live under root; otherwise treat as relative.
        candidate = Path(filename)
        full_path = (candidate if candidate.is_absolute()
                     else (root_resolved / filename)).resolve()

        # Write-excluded directories (VCS, venvs, caches, publish mirror, backups).
        rel_parts = full_path.relative_to(root_resolved).parts if str(full_path).startswith(str(root_resolved)) else ()
        if any(part in WRITE_EXCLUSION_DIRS for part in rel_parts):
            self.log_signal.emit(f"BLOCKED: write to protected dir '{'/'.join(rel_parts)}'")
            return False

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

        # Backup-before-edit (2.0.19): keep a recoverable copy of the original.
        try:
            backup_root = root_resolved / BACKUP_DIRNAME
            backup_root.mkdir(parents=True, exist_ok=True)
            rel = full_path.relative_to(root_resolved)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_root / f"{rel}.{stamp}.bak"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if full_path.exists():
                shutil.copy2(full_path, backup_path)
                self.log_signal.emit(f"Backed up -> {backup_path}")
        except Exception as e:
            self.log_signal.emit(f"Backup skipped: {e}")

        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            self.log_signal.emit(f"Created safely: {filename}")
            return True
        except Exception as e:
            self.log_signal.emit(f"Write error: {e}")
            return False

    def restore_last_backup(self, filename: str) -> bool:
        """Restore the most recent .bak backup for `filename` (relative to root)."""
        root_resolved = Path(ROOT_FOLDER).resolve()
        rel = Path(filename)
        backup_root = root_resolved / BACKUP_DIRNAME
        prefix = str(rel).replace("\\", "/") + "."
        backups = []
        if backup_root.exists():
            for p in backup_root.iterdir():
                nm = p.name
                if nm.startswith(prefix) and nm.endswith(".bak"):
                    backups.append(p)
        if not backups:
            self.log_signal.emit(f"No backup found for '{filename}'")
            return False
        latest = max(backups, key=lambda p: p.stat().st_mtime)
        target = (root_resolved / rel).resolve()
        try:
            shutil.copy2(latest, target)
            self.log_signal.emit(f"Restored {target} from {latest}")
            return True
        except Exception as e:
            self.log_signal.emit(f"Restore failed: {e}")
            return False

    # ── Research Methods (used by Manager) ────────────────────────────────────

    def research_all(self) -> dict:
        """Scan ROOT_FOLDER (.py files) and user-selected research_folder."""
        root_parts = []
        try:
            for p in sorted(Path(ROOT_FOLDER).rglob("*.py")):
                try:
                    size = p.stat().st_size
                    if size >= 50000:
                        continue
                    content = p.read_text(encoding="utf-8", errors="ignore")[:1200]
                    root_parts.append(f"[ROOT/{p.name}] ({size} bytes)\n{content}\n---")
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
                total_chars = 0
                dropped_overflow = 0
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
                        piece = f"[{rel.as_posix()}] ({size} bytes)\n{display_content}\n---\n"
                        # v2.0.24d: enforce a TOTAL budget so a huge folder can't
                        # overflow the model context. Keep the first N files that
                        # fit; drop the rest (logged).
                        if RESEARCH_MAX_TOTAL_CHARS and total_chars + len(piece) > RESEARCH_MAX_TOTAL_CHARS:
                            dropped_overflow += 1
                            continue
                        research_parts.append(piece)
                        total_chars += len(piece)
                        research_file_count += 1
                    except Exception as fe:
                        self.log_signal.emit(f"Skipped {p.name}: {fe}")

                if dropped_overflow:
                    self.log_signal.emit(
                        f"Research scan: dropped {dropped_overflow} file(s) over "
                        f"RESEARCH_MAX_TOTAL_CHARS={RESEARCH_MAX_TOTAL_CHARS} budget "
                        f"(increase it to include more)")

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

        return {
            "root": root_text,
            "research": research_text,
            "research_path": rf,
            "research_file_count": research_file_count
        }

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

def project_file_tree(max_files: int = 200) -> str:
    """Return a grounded text tree of real project files for the planner.

    2.0.19: excludes write-excluded / non-required dirs so the Coder/CEO
    planner only references files that actually exist (no hallucinations).
    The deliverable workspace (work/) is excluded so the agent never tries
    to edit its own outputs. Module-level (imported by manager.py).
    """
    try:
        lines = ["Project file tree (rooted at project root):"]
        count = 0
        for pp in sorted(Path(ROOT_FOLDER).rglob("*")):
            try:
                parts = set(pp.parts)
            except Exception:
                continue
            if any(part in _CODEBASE_INDEX_SKIP_DIRS for part in parts):
                continue
            if pp.is_file() and pp.suffix.lower() in {
                ".py", ".md", ".txt", ".json", ".yaml", ".yml"
            }:
                try:
                    rel = pp.relative_to(Path(ROOT_FOLDER))
                except Exception:
                    continue
                lines.append(str(rel).replace("\\", "/"))
                count += 1
                if count >= max_files:
                    break
        return "\n".join(lines)
    except Exception as e:
        return f"[file tree error] {e}"
