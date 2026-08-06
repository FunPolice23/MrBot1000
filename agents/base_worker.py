# \MrBot1000\agents\.py
import os
import time
import mimetypes
from pathlib import Path
import shutil
import datetime
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except (ImportError, ModuleNotFoundError, OSError):
    GROQ_AVAILABLE = False
    Groq = None

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

ROOT_FOLDER = str(Path(__file__).resolve().parent.parent)
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

# Directories the Coder/agents must NEVER write into (even though they live
# inside ROOT_FOLDER). Prevents clobbering VCS metadata, virtualenvs, caches,
# and the external publish mirror. Backups live under .mrbot_backups instead.
WRITE_EXCLUSION_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", "build", "dist", ".mypy_cache",
    ".hermes", "github_upload", "test_results", ".mrbot_backups",
}

# Directory where pre-edit backups are kept (relative to ROOT_FOLDER) so any
# edit is revertible / recoverable.
BACKUP_DIRNAME = ".mrbot_backups"

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
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 1024))
BLOCKED_MIME_TYPES = {"application/x-executable", "application/x-sharedlib",
                      "application/x-object", "application/x-dosexec"}


def is_safe_path(base: Path, candidate: Path) -> bool:
    """Ensure candidate is inside base and not a symlink pointing outside."""
    try:
        candidate = candidate.resolve()
        base = base.resolve()
        if not str(candidate).startswith(str(base)):
            return False
        # No symlinks that escape the base
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
    """Heuristic mime‑type check – only allow text‑like files."""
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime and mime in BLOCKED_MIME_TYPES:
        return False
    # Even if unknown, allow reading – the content will be checked for binary
    return True


class WorkerAgent:
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
        # Model overrides: instance > env
        self._ollama_model_override = primary_ollama_model or ollama_model
        self._chat_ollama_model_override = chat_ollama_model

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

    def llm(self, system: str, user: str, *, chat: bool = False, **kwargs) -> str:
        """Call LLM with retries, multiple providers, and max_tokens.

        chat=True prefers the chat-specific Ollama model if configured.
        """
        max_tokens = kwargs.get("max_tokens", MAX_TOKENS)
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
            # legacy fallback. NOTE: OLLAMA_MAIN_MODEL is the canonical var
            # (see .env + save_settings); OLLAMA_MODEL is only a fallback.
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
                    resp = func(model, system, user, max_tokens, chat=chat)
                    self.last_provider = name
                    return resp
                except Exception as e:
                    self.log_signal.emit(f"[LLM] {name} failed ({e}), trying next...")
                    continue

            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            break

        self.last_provider = "error"
        return "ERROR: LLM unavailable"

    def _call_openai(self, model: str, system: str, user: str, max_tokens: int) -> str:
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

    def _call_anthropic(self, model: str, system: str, user: str, max_tokens: int) -> str:
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
            # keep_alive: pin the model in VRAM for a bounded idle TTL (seconds)
            # instead of -1 (permanent). Permanent pinning on a single-GPU box
            # keeps BOTH models resident and causes RuntimeError contention
            # when the main model is under load (see live-log analysis 2.0.16).
            # OLLAMA_KEEP_ALIVE=0 would unload immediately (higher latency);
            # a TTL lets Ollama evict under memory pressure while staying warm.
            ka = os.getenv("OLLAMA_KEEP_ALIVE", "300").strip()
            options["keep_alive"] = int(ka) if ka.lstrip("-").isdigit() else 300
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
            )['message']['content']
            dt = time.time() - t0
            self.log_signal.emit(
                f"[LLM] ollama response model={model} latency={dt:.2f}s "
                f"chars={len(content)}"
            )
            return content
        except Exception as e:
            raise RuntimeError(f"Ollama model '{model}' failed: {e}")

    def safe_write_file(self, filename: str, content: str) -> bool:
        """Write a file safely inside ROOT_FOLDER only, with a pre-edit backup.

        Safety layers (defence-in-depth, no hallucinations / no clobbering):
          1. Filename blocklist (.env, credentials, etc.)
          2. Resolve path; reject anything outside ROOT_FOLDER or unsafe symlink
          3. Reject writes into protected dirs (.git, .venv, caches, mirror…)
          4. Back up the EXISTING file before overwriting (revertible)
          5. Size + free-space guards
        """
        if not is_safe_filename(filename):
            self.log_signal.emit(f"BLOCKED: filename '{filename}' is on blocklist")
            return False

        root_resolved = Path(ROOT_FOLDER).resolve()
        # If filename is already absolute (e.g. manager passed a resolved path),
        # Path(root / abs) yields abs; still validated below against root.
        full_path = (root_resolved / filename).resolve()

        if not is_safe_path(root_resolved, full_path):
            self.log_signal.emit("BLOCKED: Write outside root folder or unsafe symlink")
            return False

        # Never write into protected directories.
        if any(part in WRITE_EXCLUSION_DIRS for part in full_path.parts):
            self.log_signal.emit(f"BLOCKED: write into protected dir forbidden: {full_path}")
            return False

        if len(content) > self.max_file_size:
            self.log_signal.emit(
                f"BLOCKED: File size exceeds {self.max_file_size // 1024 // 1024}MB"
            )
            return False

        _, _, free = shutil.disk_usage(ROOT_FOLDER)
        if free < 100 * 1024 * 1024:
            self.log_signal.emit("BLOCKED: Free space < 100MB")
            return False

        # ── Backup BEFORE overwrite (revertible / recoverable) ──
        if full_path.exists() and full_path.is_file():
            try:
                rel = full_path.relative_to(root_resolved)
            except ValueError:
                rel = Path(full_path.name)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = root_resolved / BACKUP_DIRNAME / (str(rel) + f".{ts}.bak")
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full_path, backup_path)
            self.log_signal.emit(f"BACKED UP before edit: {backup_path}")
        elif not full_path.parent.exists():
            full_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            full_path.write_text(content, encoding="utf-8")
            self.log_signal.emit(f"Created safely: {filename}")
            return True
        except Exception as e:
            self.log_signal.emit(f"Write error: {e}")
            return False

    def restore_last_backup(self, filename: str) -> bool:
        """Revert a file to its most recent pre-edit backup (.bak).

        Returns True if a backup was found and restored, False otherwise.
        Used to recover from a bad autonomous edit.
        """
        root_resolved = Path(ROOT_FOLDER).resolve()
        full_path = (root_resolved / filename).resolve()
        if any(part in WRITE_EXCLUSION_DIRS for part in full_path.parts):
            self.log_signal.emit("BLOCKED: cannot restore into protected dir")
            return False
        backup_root = root_resolved / BACKUP_DIRNAME
        try:
            rel = full_path.relative_to(root_resolved)
        except ValueError:
            rel = Path(full_path.name)
        # Backups are stored as "<relative_path>.<timestamp>.bak"
        prefix = str(rel).replace("\\", "/") + "."
        backups = []
        if backup_root.exists():
            for p in backup_root.rglob("*.bak"):
                nm = p.name
                if nm.startswith(prefix) and nm.endswith(".bak"):
                    backups.append(p)
        if not backups:
            self.log_signal.emit(f"No backup found for: {filename}")
            return False
        latest = max(backups, key=lambda p: p.stat().st_mtime)
        try:
            shutil.copy2(latest, full_path)
            self.log_signal.emit(f"RESTORED from backup: {latest} -> {full_path}")
            return True
        except Exception as e:
            self.log_signal.emit(f"Restore error: {e}")
            return False

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
                for p in all_files:
                    if not p.is_file():
                        continue

                    if not is_safe_filename(p.name):
                        skipped_unsafe.append(p.name)
                        continue
                    if not is_safe_mime(p):
                        skipped_ext.append(p.name)
                        continue
                    if p.suffix.lower() not in allowed_ext:
                        skipped_ext.append(p.name)
                        continue

                    size = p.stat().st_size
                    if size >= RESEARCH_MAX_BYTES:
                        skipped_large.append(f"{p.relative_to(rf)} ({size // 1024}KB)")
                        continue

                    rel = p.relative_to(rf)
                    rel_path = rel.as_posix()

                    # Try cache
                    cached = None
                    if self.db:
                        cached = self.db.get_cached_file(str(rf), rel_path,
                                                          RESEARCH_MAX_CHARS,
                                                          current_mtime=p.stat().st_mtime)
                    if cached:
                        research_parts.append(cached)
                        research_file_count += 1
                        continue

                    # Read from disk
                    try:
                        content = p.read_text(encoding="utf-8", errors="ignore")
                        display_content = content[:RESEARCH_MAX_CHARS]
                        research_parts.append(
                            f"[{rel.as_posix()}] ({size} bytes)\n{display_content}\n---"
                        )
                        research_file_count += 1

                        if self.db:
                            mtime = p.stat().st_mtime
                            self.db.save_file_to_cache(str(rf), rel_path,
                                                       content, size, mtime)
                    except Exception as fe:
                        self.log_signal.emit(f"Skipped {p.name}: {fe}")
            except Exception as e:
                self.log_signal.emit(f"Error scanning research folder: {e}")

            if research_parts:
                research_text = "\n".join(research_parts)
                msg = f"Research scan: {research_file_count} file(s) from {rf}"
                if skipped_unsafe:
                    msg += f" | skipped {len(skipped_unsafe)} unsafe file(s)"
                if skipped_large:
                    msg += f" | skipped {len(skipped_large)} large file(s): {', '.join(skipped_large[:5])}"
                if skipped_ext:
                    msg += f" | skipped {len(skipped_ext)} unsupported type(s)"
                self.log_signal.emit(msg)
            else:
                research_text = (
                    f"(no supported files found in {rf} — "
                    f"supported: {', '.join(sorted(allowed_ext))})"
                )
                self.log_signal.emit(f"Research scan: 0 files found in {rf}")

        return {
            "root": root_text,
            "research": research_text,
            "research_path": rf,
            "research_file_count": research_file_count
        }

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

            # Check if file exists before trying to stat it
            if not p.exists():
                self.log_signal.emit(f"File not found: {name}")
                parts.append(f"[{name}] ERROR: file not found\n---")
                continue

            if not is_safe_mime(p):
                self.log_signal.emit(f"BLOCKED read of unsafe mime type: {name}")
                continue

            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                rel = name

            # Get mtime only after confirming existence
            mtime = p.stat().st_mtime

            cached = None
            if self.db:
                cached = self.db.get_cached_file(folder_path, rel,
                                                  DEEP_READ_MAX_CHARS,
                                                  current_mtime=mtime)
            if cached:
                parts.append(cached)
                continue

            try:
                size = p.stat().st_size
                content = p.read_text(encoding="utf-8", errors="ignore")
                display_content = content[:DEEP_READ_MAX_CHARS]
                parts.append(f"[{name}] ({size} bytes)\n{display_content}\n---")

                if self.db:
                    # mtime already obtained
                    self.db.save_file_to_cache(folder_path, rel, content, size, mtime)
            except Exception as e:
                parts.append(f"[{name}] ERROR: {e}\n---")

        return "\n".join(parts) if parts else "(no files read)"

    def file_index(self) -> str:
        """Return a compact index of all files in the research folder."""
        rf = self.research_folder
        if not rf or not Path(rf).exists():
            return "(research folder not set or does not exist)"

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
                    lines.append(f"  {rel_path}  ({size:,} bytes)")
                    count += 1
                except Exception:
                    pass
        except Exception as e:
            return f"(error reading index: {e})"
        lines.append(f"\nTotal: {count} file(s)")
        return "\n".join(lines)

    def _load_prompt_file(self, filename: str) -> str:
        """Load prompt/instruction text from the project folder safely."""
        base = Path(ROOT_FOLDER).resolve().parent
        target = (base / filename).resolve()
        if not is_safe_path(base, target):
            return ""
        if not target.exists() or not target.is_file():
            return ""
        try:
            return target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def build_prompt_with_context(self, skill: str = "") -> str:
        """Build a runtime prompt from Agent.md plus requested skill file."""
        base = Path(ROOT_FOLDER).resolve().parent
        agent_prompt = self._load_prompt_file("Agent.md")
        skill_prompt = ""
        if skill:
            skill_rel = Path("skills") / skill
            skill_prompt = self._load_prompt_file(str(skill_rel))
        parts = [p for p in [agent_prompt, skill_prompt] if p]
        return "\n\n---\n\n".join(parts)

# ── Worker registry (populated by each agent module's register_worker) ────────
WORKER_REGISTRY: Dict[str, type] = {}


# Real project file tree (excludes heavy/vendor dirs). Used to ground subagent
# prompts so the model stops hallucinating non-existent files like source.py.
# Also excludes publish/mirror/working dirs the program never needs to see.
_CODEBASE_INDEX_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", "build", "dist", ".mypy_cache",
    # Non-required / external folders the agents must not see or edit:
    "github_upload",   # external publish mirror (lives at D:/github_upload)
    ".hermes",         # Hermes agent tooling state
    "test_results",    # local test-run artifacts
    "work",            # per-task deliverable workspaces (see agents/task_workspace.py)
}


def project_file_tree(max_files: int = 200) -> str:
    """Return a compact tree (relative paths, sizes) of the real project root.

    This is the single source of truth for subagents. Any file NOT listed here
    does not exist -- agents must not invent filenames.
    """
    lines = [f"Project root: {ROOT_FOLDER}", "", "Files (relative path, size bytes):"]
    count = 0
    try:
        for pp in sorted(Path(ROOT_FOLDER).rglob("*")):
            if not pp.is_file():
                continue
            if any(part in _CODEBASE_INDEX_SKIP_DIRS for part in pp.parts):
                continue
            if pp.suffix.lower() not in {
                ".py", ".md", ".txt", ".json", ".yaml", ".yml"
            }:
                continue
            relative_path = str(pp.relative_to(Path(ROOT_FOLDER)))
            size = pp.stat().st_size
            lines.append(f"{relative_path}: {size}")
            count += 1
        if count == 0:
            lines.append("No relevant files found.")
    except Exception as e:
        error_message = f"Error generating project file tree: {e}"
        print(error_message)
        lines.append(f"ERROR: {error_message}")
    return chr(10).join(lines)
