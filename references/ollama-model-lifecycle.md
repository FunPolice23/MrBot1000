# Ollama Model Lifecycle — Clean Exit + Live Switching

Reusable recipe for PySide6 desktop apps that run local Ollama models and let the
user pick them from a Settings dropdown. Two recurring bugs:

1. **VRAM leak on exit** — the main model is never unloaded because the shutdown
   path reads a removed config var.
2. **Switch requires restart** — saving a new model in Settings doesn't affect the
   running worker until the app is closed and reopened.

## Detection

```bash
# Did the shutdown path drop the canonical main-model var?
grep -n "OLLAMA_MODEL\b" main.py            # suspect: reading the deprecated var
grep -n "OLLAMA_MAIN_MODEL" main.py        # should appear in the unload loop
# Does save_settings push the new model into the live worker?
grep -n "clicked.connect(self.save_settings)" main.py
grep -n "_chat_ollama_model_override" agents/base_worker.py main.py
```

Red flags:
- `_shutdown_ollama` iterates `{OLLAMA_MODEL, OLLAMA_CHAT_MODEL}` → main model
  never unloaded (the rename moved it to `OLLAMA_MAIN_MODEL`).
- `save_settings` ends after `load_dotenv(override=True)` with no write to
  `self.worker._chat_ollama_model_override`.

## Fix — Clean exit

```python
def _shutdown_ollama(self):
    if not OLLAMA_AVAILABLE or not ollama:
        return
    try:
        # Both canonical vars — NOT the deprecated OLLAMA_MODEL.
        main_model = os.getenv("OLLAMA_MAIN_MODEL", "").strip()
        chat_model = os.getenv("OLLAMA_CHAT_MODEL", "").strip()
        for model in {main_model, chat_model}:
            if not model:
                continue
            try:
                ollama.chat(model=model, messages=[], keep_alive=0)  # release VRAM
                self.log_signal.emit(f"[Ollama] unloaded model={model}")
            except Exception as e:
                self.log_signal.emit(f"[Ollama] failed to unload {model}: {e}")
    except Exception:
        pass
```

## Fix — Live switch on Save

Inside `save_settings()` (after `load_dotenv(override=True)`):

```python
new_chat = self.ollama_chat_model_combo.currentText().strip()
old_chat = getattr(self.worker, "_chat_ollama_model_override", None) or ""
if new_chat and new_chat != old_chat and OLLAMA_AVAILABLE and ollama:
    try:
        ollama.chat(model=old_chat, messages=[], keep_alive=0)  # free old VRAM
    except Exception:
        pass
    self.worker._chat_ollama_model_override = new_chat
    if hasattr(self.manager, "_chat_ollama_model_override"):
        self.manager._chat_ollama_model_override = new_chat
    self.log_signal.emit(f"[Ollama] chat model now live: {new_chat}")
```

Why it works: `WorkerAgent._chat_model_effective()` returns
`self._chat_ollama_model_override or os.getenv("OLLAMA_CHAT_MODEL")`, so updating
the override live-swaps the model for every subsequent chat — no restart.

## Ad-hoc headless verification (no Ollama server)

```python
import os, re
from unittest.mock import MagicMock

src = open("main.py", encoding="utf-8").read__import__("ast").parse(src)  # syntax

# --- exit unload: extract real _shutdown_ollama, exec with fake ollama ---
shut = re.search(r"def _shutdown_ollama\(self\):.*?def [A-Za-z_]+\(", src, re.S).group(0)
shut_core = shut[:shut.rfind("def ")]
class FakeOllama:
    def __init__(s): s.calls = []
    def chat(s, model, messages, keep_alive=None):
        s.calls.append({"model": model, "keep_alive": keep_alive})
fo = FakeOllama()
os.environ["OLLAMA_MAIN_MODEL"] = "hf.co/llmfan46/gemma-4-E2B:Q4_K_M"
os.environ["OLLAMA_CHAT_MODEL"] = "hf.co/LiquidAI/LFM2.5-1.2B:Q5_K_M"
ns = {"ollama": fo, "OLLAMA_AVAILABLE": True, "os": os, "print": print,
      "requests": None, "json": None}
exec(shut_core, ns)
ns["_shutdown_ollama"](type("W", (), {"log_signal": MagicMock()})())
assert {c["model"] for c in fo.calls} == {
    "hf.co/llmfan46/gemma-4-E2B:Q4_K_M", "hf.co/LiquidAI/LFM2.5-1.2B:Q5_K_M"}
assert all(c["keep_alive"] == 0 for c in fo.calls)

# --- live switch: behavioral replica of the save_settings block ---
def switch(new_chat, w, m, ol):
    old = getattr(w, "_chat_ollama_model_override", None) or ""
    if new_chat and new_chat != old and ol:
        try: ol.chat(model=old, messages=[], keep_alive=0)
        except Exception: pass
        w._chat_ollama_model_override = new_chat
        if hasattr(m, "_chat_ollama_model_override"):
            m._chat_ollama_model_override = new_chat

class FW: _chat_ollama_model_override = "old-chat"
class FM: _chat_ollama_model_override = "old-chat"
a = FakeOllama(); w, m = FW(), FM()
switch("new-chat", w, m, a)
assert a.calls[0]["model"] == "old-chat"          # old unloaded
assert w._chat_ollama_model_override == "new-chat"  # worker swapped
assert m._chat_ollama_model_override == "new-chat"  # manager swapped
b = FakeOllama(); w2, m2 = FW(), FM()
switch("old-chat", w2, m2, b)
assert b.calls == []                              # unchanged -> no unload
```

Run from the repo dir; confirm both assertions pass; delete the temp script.
This targets the changed behavior (mocked `ollama`, no network) — not a green suite.

## Pitfall

A startup log like `[INFO] Theme: Dark` / `[INFO] Model loaded` only proves *a*
theme/model was applied, not the configurable one, and says nothing about whether
it will be unloaded on exit. Verify the actual var names and the unload calls.
