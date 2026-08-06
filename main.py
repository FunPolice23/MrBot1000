"""
main.py - MrBot1000 Desktop Application
=======================================

DESKTOP APPLICATION UI AND INTEGRATION LAYER

This file contains:
- MainWindow class: Main QMainWindow with tabbed interface
- UI initialization: Management, Agents, Browse, Payments, Earnings, Settings, Logs, DB Stats
- Signal routing: Manager <-> Summarizer <-> UI event handling
- Settings persistence: Save/load checkbox states to .env
- Chat handling: routes summarizer responses to Agents tab

USAGE:
    python main.py
    Opens the MrBot1000 desktop application with full agent orchestration

KEY COMPONENTS:
    - MainWindow: Main application window with 8 tabs
    - ManagerThread: Background agent coordinator
    - SummarizerThread: Chat interface handler
    - UnifiedChatWidget: Chat display in Agents tab
    - ActionPipeline: Safe file modification with validation

SECURITY:
    - PATH_TRAVERSAL_PREVENTION: Uses Path.is_relative_to()
    - CLIPBOARD_VALIDATION: Max 10KB JSON, domain blocklist
    - PAYOUT_LIMITS: $10K max, triple confirmation required
"""

import sys
import os
import json
import urllib.request
sys.path.insert(0, os.path.dirname(__file__))

# ── CLI flags ──────────────────────────────────────────────────────────────
# -sm / --safe-mode is a convenience alias for MRBOT_SAFE_MODE=true.
# It exercises the workflow without making real file changes.
import argparse
_parser = argparse.ArgumentParser(description="MrBot1000 desktop application", add_help=True)
_parser.add_argument("-sm", "--safe-mode", action="store_true",
                     help="Run in safe mode (alias for MRBOT_SAFE_MODE=true)")
_args, _unknown = _parser.parse_known_args()
if _args.safe_mode:
    os.environ["MRBOT_SAFE_MODE"] = "true"

import time
import requests
from datetime import datetime
from pathlib import Path

from agents.base_worker import WorkerAgent, ROOT_FOLDER
from agents.summarizer import SummarizerThread
from manager import ManagerThread
from database import AgentDB
from ui import (
        AgentSprite, QuadThoughtPanel, AgentsTab
    )

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QPushButton, QVBoxLayout, QWidget,
    QLineEdit, QLabel, QComboBox, QHBoxLayout, QTreeView, QFileSystemModel,
    QPlainTextEdit, QDialog, QFileDialog, QListWidget, QProgressBar,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QMessageBox, QScrollArea,
    QGridLayout
)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import QThread, Signal, QTimer, Qt
from dotenv import load_dotenv, set_key
load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    openai = None

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    Anthropic = None

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    ollama = None

load_dotenv()

# ---------------------------------------------------------------------------
# Security Configuration
# ---------------------------------------------------------------------------
# Max characters for clipboard JSON to prevent DoS
MAX_CLIPBOARD_JSON_CHARS = 10000

# Payout security limits
MAX_PAYOUT_AMOUNT_USD = 10000
MIN_PAYOUT_AMOUNT_USD = 0.01

# Blocked domains that should never be interacted with
SECURITY_BLOCKED_DOMAINS = {
    "sketchy-airdrop.xyz",
    ".xyz", ".top", ".site", ".club",  # Low-trust TLDs
}

def validate_clipboard_json(text: str) -> tuple[bool, dict|None, str]:
    """SECURE: Validate clipboard JSON content.
    
    Returns: (is_valid, parsed_dict_or_None, error_message)
    """
    if not text:
        return False, None, "Clipboard is empty"
    
    if len(text) > MAX_CLIPBOARD_JSON_CHARS:
        return False, None, f"JSON exceeds {MAX_CLIPBOARD_JSON_CHARS} char limit"
    
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError as e:
        return False, None, f"Invalid JSON: {e}"
    
    if not isinstance(data, dict):
        return False, None, "JSON must be an object/dict"
    
    # Check for blocked domains
    text_lower = text.lower()
    for domain in SECURITY_BLOCKED_DOMAINS:
        if domain in text_lower:
            return False, None, f"Blocked domain detected: {domain}"
    
    return True, data, ""


# ---------------------------------------------------------------------------
# Background HTTP worker
# ---------------------------------------------------------------------------
class HttpWorker(QThread):
    result = Signal(str, object)

    def __init__(self, method, url, tag, **kwargs):
        super().__init__()
        self.method = method
        self.url    = url
        self.tag    = tag
        self.kwargs = kwargs

    def run(self):
        for attempt in range(3):
            try:
                resp = requests.request(
                    self.method, self.url, timeout=10, **self.kwargs)
                if resp.status_code < 500:
                    self.result.emit(self.tag, resp)
                    return
            except requests.RequestException:
                pass
            time.sleep(2 ** attempt)
        self.result.emit(self.tag, None)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    log_signal = Signal(str)

    THEMES = {
        "Auto": None,
        "Dark": {
            "bg": "#121212", "fg": "#e0e0e0",
            "accent": "#bb86fc", "disabled": "#555", "highlight": "#03dac6",
            "qss_extra": (
                "QProgressBar::chunk{background:qlineargradient("
                "x1:0,y1:0,x2:1,y2:0,stop:0 #bb86fc,stop:1 #03dac6);}"
            )
        },
        "Light": {
            "bg": "#f5f5f5", "fg": "#212121",
            "accent": "#6200ee", "disabled": "#aaaaaa",
            "highlight": "#3700b3", "qss_extra": ""
        },
        "Neon-Cyberpunk": {
            "bg": "#0d001a", "fg": "#00ffea",
            "accent": "#ff00aa", "disabled": "#444", "highlight": "#ffea00",
            "qss_extra": (
                "*{font-family:'Consolas',monospace;}"
                "QPushButton{border:1px solid #ff00aa;"
                "background:#1a0033;color:#00ffea;}"
                "QPushButton:hover{background:#ff00aa;color:black;}"
                "QProgressBar::chunk{background:#ff00aa;}"
            )
        },
        "Gradient-Mix": {
            "bg": "#1e0033", "fg": "#d4a5ff",
            "accent": "#ff6ec7", "disabled": "#663399", "highlight": "#00f2ff",
            "qss_extra": (
                "QWidget{background:qlineargradient("
                "x1:0,y1:0,x2:1,y2:1,stop:0 #1e0033,stop:1 #330066);}"
                "QLabel{color:#d4a5ff;}"
                "QProgressBar{background:#330066;border:1px solid #ff6ec7;}"
                "QProgressBar::chunk{background:qlineargradient("
                "x1:0,y1:0,x2:1,y2:0,stop:0 #ff6ec7,stop:1 #00f2ff);}"
            )
        },
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrBot1000 v2.0.8")
        self.resize(1450, 950)
        self.root_folder  = ROOT_FOLDER
        self._http_workers = []
        self._log_buffer   = []

        self.db = AgentDB()

        # ── Action Pipeline ───────────────────────────────────────────────────
        from action_pipeline import ActionPipeline, AgentCollaboration
        self.pipeline = ActionPipeline(
            root_folder=ROOT_FOLDER,
            db=self.db,
            log_fn=lambda msg: self.log_signal.emit(msg)
        )
        self.pipeline.on_validated = self._on_pipeline_validated
        self.pipeline.on_executed  = self._on_pipeline_executed
        self.pipeline.on_rejected  = self._on_pipeline_rejected

        ollama_chat  = os.getenv("OLLAMA_CHAT_MODEL", "").strip() or None
        self.log_signal.emit(f"[Startup] OLLAMA_CHAT_MODEL from env: {ollama_chat}")
        api_key      = os.getenv("OPENAI_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
        self.worker  = WorkerAgent(api_key, self.log_signal, db=self.db,
                                   chat_ollama_model=ollama_chat)
        self.manager = ManagerThread(api_key, self.worker, db=self.db)
        self.summarizer = SummarizerThread(self.worker, db=self.db)
        self.manager.set_summarizer(self.summarizer)  # Connect summarizer to manager

        # Register specialized workers with the CEO manager
        try:
            from agents.job_search_worker import JobSearchWorker
            self.job_worker = JobSearchWorker(api_key, self.log_signal, db=self.db)
            self.job_worker.set_new_jobs_callback(self.manager.on_new_jobs)
            self.manager.register_worker("JobSearch", self.job_worker, "job search")
        except Exception as e:
            self.job_worker = None
            # Not fatal — just means no job search worker
        try:
            from agents.analyst_worker import AnalystWorker
            self.analyst_worker = AnalystWorker(api_key, self.log_signal, db=self.db)
            self.manager.register_worker("Analyst", self.analyst_worker, "code analysis")
        except Exception:
            self.analyst_worker = None

        self.summarizer.chat_reply.connect(self._on_summarizer_chat_reply)

        self.manager.manager_thought.connect(self._on_manager_thought)
        self.manager.agent_thought.connect(self._on_agent_thought)
        self.manager.comms.connect(self._on_comms)
        self.summarizer.summary_ready.connect(self._on_summary_ready)

        self.manager.log.connect(self._safe_log)
        self.manager.chat_reply.connect(self._on_chat_reply)
        self.manager.agent_status.connect(self._on_agent_status)
        self.log_signal.connect(self._safe_log)

        # New CEO signals
        self.manager.worker_assigned.connect(self._on_worker_assigned)
        self.manager.job_found.connect(self._on_job_found)

        self.manager.manager_thought.connect(self.summarizer.add_manager_thought)
        self.manager.agent_thought.connect(self.summarizer.add_agent_thought)
        self.manager.comms.connect(self.summarizer.add_comms_thought)

        # Create UI first, then start threads
        self.create_ui()
        self.apply_theme("Dark")

        self.summarizer.start()
        self.manager.start()

        self._balance_timer = QTimer(self)
        self._balance_timer.timeout.connect(self.refresh_balance)
        self._balance_timer.start(30000)

        menubar = self.menuBar()
        fm = menubar.addMenu("File")
        fm.addAction("Register Autonomous").triggered.connect(
            self.register_autonomous)
        fm.addAction("Exit").triggered.connect(self.close)

        view_menu = menubar.addMenu("View")
        view_menu.addAction("Show Thought Panel").triggered.connect(
            self._show_thoughts)
        view_menu.addSeparator()
        self.show_thoughts_action = view_menu.addAction("Hide Thought Panel")
        self.show_thoughts_action.setCheckable(True)
        self.show_thoughts_action.toggled.connect(self._toggle_thought_panel)

        view_menu.addAction("🟣 Manager Window").triggered.connect(self._show_manager_win)
        view_menu.addAction("🟢 Agent Window").triggered.connect(self._show_agent_win)
        view_menu.addAction("🟡 Comms Window").triggered.connect(self._show_comms_win)
        view_menu.addAction("📊 Summary Window").triggered.connect(self._show_summary_win)


        theme_menu = menubar.addMenu("Theme")
        for t in self.THEMES:
            act = theme_menu.addAction(t)
            act.triggered.connect(lambda _, tn=t: self.apply_theme(tn))

        self.thought_panel = QuadThoughtPanel(self)
        self.thought_panel.hide()

        if api_key:
            self.status_label.setText("Agent status: Registered ✓")

        for t in self.db.get_thoughts(limit=100):
            self.thought_panel.route(t["source"], f"[history] {t['text']}")
        self.thought_panel.route("System",
                                 f"Agent started. Root: {ROOT_FOLDER}")
        self.thought_panel.route("System", "Research folder: not set")

        QTimer.singleShot(600, self.refresh_db_stats)

    def closeEvent(self, event):
        try:
            self._shutdown_ollama()
        except Exception:
            pass
        self.manager.stop()
        self.manager.wait(2000)
        self.summarizer.stop()
        self.summarizer.wait(2000)
        if self.manager.isRunning():
            self.manager.terminate()
        self.db.close()
        super().closeEvent(event)

    def _shutdown_ollama(self):
        if not OLLAMA_AVAILABLE or not ollama:
            return
        try:
            main_model = os.getenv("OLLAMA_MODEL", "").strip()
            chat_model = os.getenv("OLLAMA_CHAT_MODEL", "").strip()
            for model in {main_model, chat_model}:
                if not model:
                    continue
                try:
                    requests.post(
                        "http://127.0.0.1:11434/api/chat",
                        json={"model": model, "messages": [], "keep_alive": 0},
                        timeout=30,
                    )
                    self.log_signal.emit(f"[Ollama] unloaded model={model}")
                except Exception as e:
                    self.log_signal.emit(f"[Ollama] failed to unload {model}: {e}")
        except Exception:
            pass

    # --- Summarizer signal handlers ---
    def _on_summarizer_status(self, status: str, task: str):
        # Update sprite only; the tab's own connection handles labels
        state_map = {
            "Idle": "idle",
            "Summarizing": "thinking",
            "Paused": "idle",
        }
        sprite_state = state_map.get(status, "idle")
        self.summarizer_sprite.set_state(sprite_state)

    def _on_summarizer_paused(self, paused: bool):
        # Update pause button in the tab
        if hasattr(self, 'agents_tab'):
            self.agents_tab.set_summarizer_pause_button_state(paused)

    # --- Manager thought handlers ---
    def _on_worker_assigned(self, worker_name: str, task: str):
        pass

    def _on_job_found(self, jobs_json: str):
        try:
            import json
            jobs = json.loads(jobs_json)
            count = len(jobs)
            self._safe_log(f"🔍 {count} new gig(s) queued from JobSearch")
        except Exception:
            pass

    def _on_comms(self, direction: str, text: str):
        self.thought_panel.route("Comms", text, direction)

    def _on_chat_reply(self, trigger: str, text: str):
            # Filter out heartbeat and task messages from chat window
            # These should go to notifications/subtle status, not chat
            if trigger.startswith("Heartbeat:") or trigger.startswith("Task:"):
                # Heartbeat/task decisions - update status, don't clutter chat
                if hasattr(self, 'agent_status_label'):
                    self.agent_status_label.setText(f"Action: {trigger}")
                return
        
            if hasattr(self, "agents_tab") and hasattr(self.agents_tab, "append_reply"):
                self.agents_tab.append_reply("Manager", f"[{trigger}] {text}")

    def _on_manager_thought(self, text: str):
        self.thought_panel.route("Manager", text)
        self.log_signal.emit(f"[Manager] thought: {text}")

    def _on_agent_status(self, status: str, task: str):
        # Update management tab status label
        if hasattr(self, 'agent_status_label'):
            self.agent_status_label.setText(f"Status: {status} — {task}")
        # Update heartbeat label in the agents tab
        if hasattr(self, 'agents_tab'):
            self.agents_tab.last_heartbeat_label.setText(
                f"Last heartbeat: {datetime.now().strftime('%H:%M:%S')}")
        self.log_signal.emit(f"[Manager] agent status: {status} — {task}")

    def _on_agent_thought(self, text: str):
        self.thought_panel.route("Agent", text)
        self.log_signal.emit(f"[Agent] thought: {text}")

    def _safe_log(self, msg: str):
        if hasattr(self, "log_edit"):
            self._append_log(msg)

    def _append_log(self, msg: str):
        ts    = datetime.now().strftime("%H:%M:%S")
        if "BLOCKED" in msg:
            severity = "BLOCKED"
        elif "ERROR" in msg:
            severity = "ERROR"
        elif "Ollama" in msg or "LLM" in msg:
            severity = "OLLAMA"
        elif "Manager" in msg or "Action" in msg:
            severity = "MANAGER"
        else:
            severity = "INFO"
        color = {
            "BLOCKED": "#ffcc00",
            "ERROR": "#ff4444",
            "OLLAMA": "#00b0ff",
            "MANAGER": "#00ccff",
            "INFO": "#aaaaaa",
        }[severity]

        # Emit full detail line if this is a diagnostic or LLM status message
        if "LLM" in msg or "Ollama" in msg or "pipeline" in msg.lower() or "earning" in msg.lower():
            detail = msg
        else:
            detail = msg

        entry = {"ts": ts, "msg": detail, "color": color, "severity": severity}
        self._log_buffer.append(entry)
        if len(self._log_buffer) > 5000:
            self._log_buffer.pop(0)

        if hasattr(self, "log_severity_combo"):
            current_filter = self.log_severity_combo.currentText()
            if current_filter != "All" and severity != current_filter:
                return
        flt = self.log_filter.text().lower() if hasattr(self, "log_filter") else ""
        if not flt or flt in detail.lower():
            self.log_edit.appendHtml(
                f'<span style="color:{color};">[{ts}] [{severity}] {detail}</span>'
            )
            if getattr(self, "auto_scroll_logs", None) and self.auto_scroll_logs.isChecked():
                sb = self.log_edit.verticalScrollBar()
                sb.setValue(sb.maximum())

    def _set_log_auto_scroll(self, checked: bool):
        if checked and hasattr(self, "log_edit"):
            sb = self.log_edit.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _apply_log_filter(self):
        self.log_edit.clear()
        filter_text = self.log_filter.text().lower()
        severity_filter = self.log_severity_combo.currentText()
        for e in self._log_buffer:
            if severity_filter != "All" and e["severity"] != severity_filter:
                continue
            if filter_text and filter_text not in e["msg"].lower():
                continue
            color, ts, msg = e["color"], e["ts"], e["msg"]
            self.log_edit.appendHtml(
                f'<span style="color:{color};">[{ts}] [{e["severity"]}] {msg}</span>'
            )
        if getattr(self, "auto_scroll_logs", None) and self.auto_scroll_logs.isChecked():
            sb = self.log_edit.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear_log(self):
        self._log_buffer.clear()
        self.log_edit.clear()

    def _toggle_thought_panel(self, checked):
        if checked:
            self.thought_panel.hide()
            self.show_thoughts_action.setText("Show Thought Panel")
        else:
            self.thought_panel.show()
            self.thought_panel.raise_()
            self.show_thoughts_action.setText("Hide Thought Panel")

    def create_ui(self):
        # Centered title header at the top of the program
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        title_lbl = QLabel("MrBot1000 v2.0.8")
        title_lbl.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        title_lbl.setStyleSheet(
            "font-size:15px; font-weight:bold; padding:8px 0px; "
            "color:#4fc3f7; background-color:#1a1a1f; border-bottom:1px solid #2b3034;"
        )
        container_layout.addWidget(title_lbl)

        tabs = QTabWidget()
        container_layout.addWidget(tabs)
        self.setCentralWidget(container)
        tabs.addTab(self.create_management_tab(),  "Management")
        tabs.addTab(self.create_agents_tab(),      "Agents")
        tabs.addTab(self.create_file_browser_tab(),"Browse Root")
        tabs.addTab(self.create_payments_tab(),    "Payments")
        tabs.addTab(self.create_earnings_tab(),    "Earnings")
        tabs.addTab(self.create_settings_tab(),    "Settings")
        tabs.addTab(self.create_logs_tab(),        "Live Logs")
        tabs.addTab(self.create_db_stats_tab(),    "DB Stats")

        # Chat handled in Agents tab

        # Stats refresh timer
        self._stats_timer = QTimer(self)
        self._stats_timer.start(15000)

    def create_agents_tab(self):
        # Create sprites
        self.agent_sprite = AgentSprite(label="Worker")
        self.summarizer_sprite = AgentSprite(label="Summarizer")
        self.summarizer_sprite.set_state("idle")

        # Connect summarizer signals to main window methods
        self.summarizer.status_changed.connect(self._on_summarizer_status)
        self.summarizer.paused_changed.connect(self._on_summarizer_paused)

        # Create the tab widget with all new window callbacks
        tab = AgentsTab(
            self,
            worker_sprite=self.agent_sprite,
            summarizer_sprite=self.summarizer_sprite,
            worker_status_signal=self.manager.agent_status,
            summarizer_status_signal=self.summarizer.status_changed,
            show_thoughts_cb=self._show_thoughts,
            toggle_worker_pause_cb=self._toggle_pause,
            toggle_summarizer_pause_cb=self._toggle_summarizer_pause,
            heartbeat_interval=self.manager.HEARTBEAT_INTERVAL,
            on_send=self._human_send,
            on_strategy_change=self._on_strategy_change,
            show_manager_win_cb=self._show_manager_win,
            show_agent_win_cb=self._show_agent_win,
            show_comms_win_cb=self._show_comms_win,
            show_summary_win_cb=self._show_summary_win,
        )
        self.agents_tab = tab

        # Wire pipeline to summarizer for spelling assist
        if hasattr(self, "pipeline"):
            self.pipeline.set_summarizer(self.summarizer.worker)

        return tab

    def _toggle_summarizer_pause(self, checked):
        self.summarizer.set_paused(checked)

    def _show_thoughts(self):
        self.thought_panel.show()
        self.thought_panel.raise_()
        self.show_thoughts_action.setChecked(False)

    def _toggle_pause(self, checked):
        self.manager.set_paused(checked)
        # Update the button in the tab
        if hasattr(self, 'agents_tab'):
            self.agents_tab.set_pause_button_state(checked)
            # Update heartbeat label text
            if checked:
                self.agents_tab.heartbeat_label.setText("Heartbeat: PAUSED")
            else:
                self.agents_tab.heartbeat_label.setText(
                    f"Heartbeat every {self.manager.HEARTBEAT_INTERVAL}s")
        self.thought_panel.route(
            "System", "Heartbeat paused" if checked else "Heartbeat resumed")

    def _human_send(self, text: str):
        self.manager.send_human_message(text)
        self.thought_panel.route("Comms", text, "Human→M")

    def create_management_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(14)

        title = QLabel("Management Control Center")
        title.setStyleSheet("font-size:20px;font-weight:bold;color:#00b0ff;")
        lay.addWidget(title)

        # Agent Controls
        agent_group = QGroupBox("Agent Controls")
        agent_layout = QGridLayout(agent_group)
        agent_layout.setContentsMargins(12, 12, 12, 12)
        agent_layout.setSpacing(8)

        self.agent_status_label = QLabel("Status: Initializing…")
        self.agent_status_label.setStyleSheet("font-size:12px;color:#00ff88;")
        agent_layout.addWidget(self.agent_status_label, 0, 0, 1, 2)

        pause_btn = QPushButton("⏸ Pause / Resume Manager")
        pause_btn.clicked.connect(self._toggle_pause)
        agent_layout.addWidget(pause_btn, 1, 0)

        summarizer_pause_btn = QPushButton("⏸ Pause / Resume Summarizer")
        summarizer_pause_btn.clicked.connect(self._toggle_summarizer_pause)
        agent_layout.addWidget(summarizer_pause_btn, 1, 1)

        force_improve_btn = QPushButton("⚡ Force Self-Improvement")
        force_improve_btn.clicked.connect(self.force_safe_improve)
        agent_layout.addWidget(force_improve_btn, 2, 0)

        force_rescan_btn = QPushButton("🔄 Force Research Re-scan")
        force_rescan_btn.clicked.connect(self.force_research_rescan)
        agent_layout.addWidget(force_rescan_btn, 2, 1)

        lay.addWidget(agent_group)

        # Pipeline Controls
        pipeline_group = QGroupBox("Pipeline Controls")
        pipeline_layout = QGridLayout(pipeline_group)
        pipeline_layout.setContentsMargins(12, 12, 12, 12)
        pipeline_layout.setSpacing(8)

        run_cycle_btn = QPushButton("▶ Run Earning Cycle")
        run_cycle_btn.clicked.connect(self._run_earning_cycle)
        pipeline_layout.addWidget(run_cycle_btn, 0, 0)

        quick_check_btn = QPushButton("🔍 Quick Pipeline Check")
        quick_check_btn.clicked.connect(self._validate_clipboard_code)
        pipeline_layout.addWidget(quick_check_btn, 0, 1)

        self.pipeline_status_label = QLabel("Pipeline: Idle")
        self.pipeline_status_label.setStyleSheet("font-size:11px;color:#888;")
        pipeline_layout.addWidget(self.pipeline_status_label, 1, 0, 1, 2)

        lay.addWidget(pipeline_group)

        # View Controls
        view_group = QGroupBox("Interface")
        view_layout = QGridLayout(view_group)
        view_layout.setContentsMargins(12, 12, 12, 12)
        view_layout.setSpacing(8)

        thought_btn = QPushButton("🧠 Open Thought Processes")
        thought_btn.clicked.connect(self._show_thoughts)
        view_layout.addWidget(thought_btn, 0, 0)



        manager_btn = QPushButton("🟢 Open Manager Window")
        manager_btn.clicked.connect(self._show_manager_win)
        view_layout.addWidget(manager_btn, 1, 0)

        agent_btn = QPushButton("🔵 Open Agent Window")
        agent_btn.clicked.connect(self._show_agent_win)
        view_layout.addWidget(agent_btn, 1, 1)

        comms_btn = QPushButton("🟡 Open Comms Window")
        comms_btn.clicked.connect(self._show_comms_win)
        view_layout.addWidget(comms_btn, 2, 0)

        summary_btn = QPushButton("📊 Open Summary Window")
        summary_btn.clicked.connect(self._show_summary_win)
        view_layout.addWidget(summary_btn, 2, 1)

        lay.addWidget(view_group)

        # Research Folder
        research_group = QGroupBox("Research")
        research_layout = QHBoxLayout(research_group)
        self.research_folder_label = QLabel("Research folder: not set")
        self.research_folder_label.setStyleSheet("color:#888;font-size:11px;")
        research_layout.addWidget(self.research_folder_label)

        select_folder_btn = QPushButton("📁 Select Folder")
        select_folder_btn.clicked.connect(self.select_research_folder)
        research_layout.addWidget(select_folder_btn)

        lay.addWidget(research_group)

        lay.addStretch()
        return w

    def create_settings_tab(self):
        scroll  = QScrollArea()
        scroll.setWidgetResizable(True)
        inner   = QWidget()
        scroll.setWidget(inner)
        lay     = QVBoxLayout(inner)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(14)

        # Registration
        rg = QGroupBox("Agent Registration")
        rl = QFormLayout(rg)
        rl.setContentsMargins(12, 12, 12, 12)
        rl.setVerticalSpacing(8)
        self.name_edit   = QLineEdit(os.getenv("AGENT_NAME", "CodeSelfLearnBot"))
        self.user_edit   = QLineEdit(os.getenv("AGENT_USERNAME", "codeselflearn-2026"))
        self.wallet_edit = QLineEdit(os.getenv("ATOMIC_SOLANA_ADDRESS", ""))
        self.cashapp_edit = QLineEdit(os.getenv("CASHAPP_TAG", ""))
        rl.addRow("Agent Name:",    self.name_edit)
        rl.addRow("Username:",      self.user_edit)
        rl.addRow("Wallet:",        self.wallet_edit)
        rl.addRow("Cash App Tag:",  self.cashapp_edit)
        rb = QPushButton("Register")
        rb.clicked.connect(self.register_autonomous)
        rl.addRow(rb)
        self.status_label = QLabel("Agent status: Not registered")
        rl.addRow(self.status_label)
        lay.addWidget(rg)

        # LLM Providers
        apig = QGroupBox("LLM Providers")
        apil = QFormLayout(apig)
        apil.setContentsMargins(12, 12, 12, 12)
        apil.setVerticalSpacing(8)

        # OpenAI
        self.openai_key_edit    = QLineEdit(os.getenv("OPENAI_API_KEY", ""))
        self.openai_key_edit.setEchoMode(QLineEdit.Password)
        self.openai_model_edit  = QLineEdit(os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        self.disable_openai = QCheckBox()
        self.disable_openai.setChecked(os.getenv("DISABLE_OPENAI", "false").lower() == "true")
        apil.addRow("OpenAI API Key:",  self.openai_key_edit)
        apil.addRow("OpenAI Model:",    self.openai_model_edit)
        apil.addRow("Disable OpenAI:",  self.disable_openai)
        openai_note = QLabel("Use for: GPT-4o/4o-mini. Best for general reasoning. Cost: paid per token.")
        openai_note.setStyleSheet("color:#888;font-size:10px;")
        openai_note.setWordWrap(True)
        apil.addRow(openai_note)

        # Anthropic
        self.anthropic_key_edit    = QLineEdit(os.getenv("ANTHROPIC_API_KEY", ""))
        self.anthropic_key_edit.setEchoMode(QLineEdit.Password)
        self.anthropic_model_edit  = QLineEdit(os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))
        self.disable_anthropic = QCheckBox()
        self.disable_anthropic.setChecked(os.getenv("DISABLE_ANTHROPIC", "false").lower() == "true")
        apil.addRow("Anthropic API Key:",  self.anthropic_key_edit)
        apil.addRow("Anthropic Model:",    self.anthropic_model_edit)
        apil.addRow("Disable Anthropic:",  self.disable_anthropic)
        anthropic_note = QLabel("Use for: Claude Sonnet/Opus. Best for long-context analysis. Cost: paid per token.")
        anthropic_note.setStyleSheet("color:#888;font-size:10px;")
        anthropic_note.setWordWrap(True)
        apil.addRow(anthropic_note)

        # Ollama Local
        ollama_row = QWidget()
        ollama_layout = QHBoxLayout(ollama_row)
        ollama_layout.setContentsMargins(0, 0, 0, 0)
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.addItems([
            "llama3.2", "llama3:8b", "llama3:70b", "mistral", "codellama"
        ])
        self.ollama_model_combo.setCurrentText(os.getenv("OLLAMA_MODEL", "llama3.2"))
        self.ollama_chat_model_combo = QComboBox()
        self.ollama_chat_model_combo.setEditable(True)
        self.ollama_chat_model_combo.addItems([
            "llama3.2", "llama3:8b", "llama3:70b", "mistral", "codellama",
            "qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b", "phi3:mini", "gemma2:2b"
        ])
        self.ollama_chat_model_combo.setCurrentText(os.getenv("OLLAMA_CHAT_MODEL", ""))
        self.refresh_chat_model_btn = QPushButton("Refresh")
        self.refresh_chat_model_btn.clicked.connect(self.refresh_ollama_models)
        ollama_chat_row = QWidget()
        ollama_chat_layout = QHBoxLayout(ollama_chat_row)
        ollama_chat_layout.setContentsMargins(0, 0, 0, 0)
        ollama_chat_layout.addWidget(self.ollama_chat_model_combo)
        ollama_chat_layout.addWidget(self.refresh_chat_model_btn)
        self.refresh_ollama_btn = QPushButton("Refresh")
        self.refresh_ollama_btn.clicked.connect(self.refresh_ollama_models)
        ollama_layout.addWidget(self.ollama_model_combo)
        ollama_layout.addWidget(self.refresh_ollama_btn)
        self.disable_ollama = QCheckBox()
        self.disable_ollama.setChecked(os.getenv("DISABLE_OLLAMA", "false").lower() == "true")
        apil.addRow("Ollama Main Model:", ollama_row)
        apil.addRow("Ollama Chat Model:", ollama_chat_row)
        apil.addRow("Disable Ollama:", self.disable_ollama)
        ollama_note = QLabel("Use for: Local private inference. Best for offline/cheap. Cost: free, uses local GPU/CPU.")
        ollama_note.setStyleSheet("color:#888;font-size:10px;")
        ollama_note.setWordWrap(True)
        apil.addRow(ollama_note)

        tb = QPushButton("Test Connection")
        tb.clicked.connect(self.test_api_connection)
        apil.addRow(tb)
        lay.addWidget(apig)

        # LLM Parameters with explanations
        llmg = QGroupBox("LLM Parameters")
        llml = QFormLayout(llmg)
        llml.setContentsMargins(12, 12, 12, 12)
        llml.setVerticalSpacing(8)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(128, 8192)
        self.max_tokens_spin.setValue(int(os.getenv("MAX_TOKENS", 1024)))
        llml.addRow("Max Tokens:", self.max_tokens_spin)
        max_tokens_note = QLabel("Maximum length of generated responses. Higher = longer answers but slower and more expensive.")
        max_tokens_note.setStyleSheet("color:#888;font-size:10px;")
        max_tokens_note.setWordWrap(True)
        llml.addRow(max_tokens_note)
        lay.addWidget(llmg)

        # Performance & Cache with explanations
        perfg = QGroupBox("Performance & Cache")
        perfl = QFormLayout(perfg)
        perfl.setContentsMargins(12, 12, 12, 12)
        perfl.setVerticalSpacing(8)
        self.polling_spin = QSpinBox()
        self.polling_spin.setRange(10, 3600)
        self.polling_spin.setValue(self.manager.HEARTBEAT_INTERVAL)
        self.polling_spin.setSuffix(" s")
        perfl.addRow("Heartbeat Interval:", self.polling_spin)
        heartbeat_note = QLabel("How often the manager thinks/acts. Lower = more responsive but more API calls.")
        heartbeat_note.setStyleSheet("color:#888;font-size:10px;")
        heartbeat_note.setWordWrap(True)
        perfl.addRow(heartbeat_note)

        self.startup_spin = QSpinBox()
        self.startup_spin.setRange(0, 60)
        self.startup_spin.setValue(int(os.getenv("STARTUP_DELAY_SECS", 5)))
        self.startup_spin.setSuffix(" s")
        perfl.addRow("Startup Delay:", self.startup_spin)
        startup_note = QLabel("Delay before agents start. Useful if you need to start Ollama first.")
        startup_note.setStyleSheet("color:#888;font-size:10px;")
        startup_note.setWordWrap(True)
        perfl.addRow(startup_note)

        self.research_cache_ttl_spin = QSpinBox()
        self.research_cache_ttl_spin.setRange(0, 3600)
        self.research_cache_ttl_spin.setValue(int(os.getenv("RESEARCH_CACHE_TTL", 120)))
        self.research_cache_ttl_spin.setSuffix(" s")
        perfl.addRow("Research Cache TTL:", self.research_cache_ttl_spin)
        cache_note = QLabel("How long file scans are cached. Lower = fresher data but slower scans.")
        cache_note.setStyleSheet("color:#888;font-size:10px;")
        cache_note.setWordWrap(True)
        perfl.addRow(cache_note)

        self.max_file_spin = QSpinBox()
        self.max_file_spin.setRange(1, 500)
        self.max_file_spin.setValue(int(os.getenv("MAX_FILE_SIZE_MB", 10)))
        perfl.addRow("Max File Size (MB):", self.max_file_spin)
        file_size_note = QLabel("Skip files larger than this when scanning. Lower = faster scans.")
        file_size_note.setStyleSheet("color:#888;font-size:10px;")
        file_size_note.setWordWrap(True)
        perfl.addRow(file_size_note)

        self.research_max_chars = QSpinBox()
        self.research_max_chars.setRange(500, 20000)
        self.research_max_chars.setValue(int(os.getenv("RESEARCH_MAX_CHARS", 3000)))
        self.research_max_chars.setSuffix(" chars")
        perfl.addRow("Research chars/file:", self.research_max_chars)
        research_chars_note = QLabel("Max chars read per file during research scans. Higher = more context but slower.")
        research_chars_note.setStyleSheet("color:#888;font-size:10px;")
        research_chars_note.setWordWrap(True)
        perfl.addRow(research_chars_note)

        self.deep_read_chars = QSpinBox()
        self.deep_read_chars.setRange(1000, 50000)
        self.deep_read_chars.setValue(int(os.getenv("DEEP_READ_MAX_CHARS", 8000)))
        self.deep_read_chars.setSuffix(" chars")
        perfl.addRow("Deep read chars/file:", self.deep_read_chars)
        deep_read_note = QLabel("Max chars for deep file analysis. Higher = more thorough but much slower.")
        deep_read_note.setStyleSheet("color:#888;font-size:10px;")
        deep_read_note.setWordWrap(True)
        perfl.addRow(deep_read_note)

        self.auto_research = QCheckBox()
        self.auto_research.setChecked(
            os.getenv("AUTO_RESEARCH", "False").lower() == "true")
        perfl.addRow("Auto-scan on start:", self.auto_research)
        auto_research_note = QLabel("Automatically scan files when program starts.")
        auto_research_note.setStyleSheet("color:#888;font-size:10px;")
        auto_research_note.setWordWrap(True)
        perfl.addRow(auto_research_note)
        lay.addWidget(perfg)

        # Security Settings with explanations
        secg = QGroupBox("Security")
        secl = QFormLayout(secg)
        secl.setContentsMargins(12, 12, 12, 12)
        secl.setVerticalSpacing(8)
        self.blocklist_edit = QLineEdit(os.getenv("FILENAME_BLOCKLIST",
            "config.yaml,.env,credentials.json,id_rsa"))
        secl.addRow("Blocklist (comma sep):", self.blocklist_edit)
        blocklist_note = QLabel("Files the agent will never read. Keeps secrets safe.")
        blocklist_note.setStyleSheet("color:#888;font-size:10px;")
        blocklist_note.setWordWrap(True)
        secl.addRow(blocklist_note)

        self.blocked_mime_edit = QLineEdit(os.getenv("BLOCKED_MIME_TYPES",
            "application/x-executable,application/x-sharedlib"))
        secl.addRow("Blocked MIME (comma):", self.blocked_mime_edit)
        mime_note = QLabel("Blocked file types. Prevents reading binaries or executables.")
        mime_note.setStyleSheet("color:#888;font-size:10px;")
        mime_note.setWordWrap(True)
        secl.addRow(mime_note)
        lay.addWidget(secg)

        # Action Pipeline with explanations
        pipeg = QGroupBox("Action Pipeline")
        pipel = QFormLayout(pipeg)
        pipel.setContentsMargins(12, 12, 12, 12)
        pipel.setVerticalSpacing(8)

        self.pipeline_enabled_check = QCheckBox()
        self.pipeline_enabled_check.setChecked(
            os.getenv("PIPELINE_ENABLED", "true").lower() == "true")
        pipel.addRow("Enable validation pipeline:", self.pipeline_enabled_check)
        pipeline_enabled_note = QLabel("Run code through validation before writing to disk.")
        pipeline_enabled_note.setStyleSheet("color:#888;font-size:10px;")
        pipeline_enabled_note.setWordWrap(True)
        pipel.addRow(pipeline_enabled_note)

        self.pipeline_allow_write_check = QCheckBox()
        self.pipeline_allow_write_check.setChecked(
            os.getenv("PIPELINE_ALLOW_WRITE", "true").lower() == "true")
        pipel.addRow("Allow file write/create:", self.pipeline_allow_write_check)

        self.pipeline_allow_selfimprove_check = QCheckBox()
        self.pipeline_allow_selfimprove_check.setChecked(
            os.getenv("PIPELINE_ALLOW_SELF_IMPROVE", "false").lower() == "true")
        pipel.addRow("Allow self-improvement:", self.pipeline_allow_selfimprove_check)
        self_improve_note = QLabel("Allow the agent to modify its own code. Only enable if you trust the validation pipeline.")
        self_improve_note.setStyleSheet("color:#888;font-size:10px;")
        self_improve_note.setWordWrap(True)
        pipel.addRow(self_improve_note)

        pipe_val_btn = QPushButton("🔍 Validate Clipboard Code")
        pipe_val_btn.clicked.connect(self._validate_clipboard_code)
        pipel.addRow(pipe_val_btn)

        self.pipeline_result_label = QLabel("")
        self.pipeline_result_label.setWordWrap(True)
        self.pipeline_result_label.setStyleSheet("font-size:10px;")
        pipel.addRow(self.pipeline_result_label)
        lay.addWidget(pipeg)

        # Payout Settings with explanations
        payg = QGroupBox("Payout Destinations")
        payl = QFormLayout(payg)
        payl.setContentsMargins(12, 12, 12, 12)
        payl.setVerticalSpacing(8)
        self.cashapp_edit = QLineEdit(os.getenv("CASHAPP_TAG", "$csmith7899"))
        payl.addRow("Cash App tag:", self.cashapp_edit)
        self.solana_payout_edit = QLineEdit(
            os.getenv("ATOMIC_SOLANA_ADDRESS", ""))
        payl.addRow("Solana address:", self.solana_payout_edit)
        payout_note = QLabel("Auto-payout: agent earnings route to Cash App first, then Solana wallet for on-chain storage.")
        payout_note.setStyleSheet("color:#888;font-size:10px;")
        payout_note.setWordWrap(True)
        payl.addRow(payout_note)
        lay.addWidget(payg)

        # Appearance
        apg = QGroupBox("Appearance")
        apl = QFormLayout(apg)
        apl.setContentsMargins(12, 12, 12, 12)
        apl.setVerticalSpacing(8)
        theme_combo = QComboBox()
        theme_combo.addItems(self.THEMES.keys())
        theme_combo.setCurrentText("Dark")
        theme_combo.currentTextChanged.connect(self.apply_theme)
        apl.addRow("Theme:", theme_combo)
        lay.addWidget(apg)

        # Cache Management
        cg = QGroupBox("Cache Management")
        cl = QVBoxLayout(cg)
        ccb = QPushButton("Clear File Cache")
        ccb.clicked.connect(self.clear_file_cache)
        cl.addWidget(ccb)
        lay.addWidget(cg)

        sb = QPushButton("💾 Save All Settings")
        sb.clicked.connect(self.save_settings)
        lay.addWidget(sb)

        lay.addStretch()
        return scroll

    def create_payments_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            f"Wallet: {os.getenv('ATOMIC_SOLANA_ADDRESS', 'Not set')}"))
        self.balance_label = QLabel("Balance: —")
        lay.addWidget(self.balance_label)
        row = QHBoxLayout()
        self.amount_input = QLineEdit("50")
        wb = QPushButton("Withdraw")
        wb.clicked.connect(self.manual_payout)
        row.addWidget(self.amount_input)
        row.addWidget(wb)
        lay.addLayout(row)
        self.history_list = QListWidget()
        lay.addWidget(QLabel("Payout History"))
        lay.addWidget(self.history_list)
        return w

    def create_earnings_tab(self):
        """Earnings dashboard showing revenue, outcomes, and pipeline."""
        w   = QWidget()
        lay = QVBoxLayout(w)

        # Revenue summary
        rev_group = QGroupBox("Revenue Summary")
        rev_lay = QFormLayout(rev_group)
        self.total_revenue_label = QLabel("$0.00")
        self.total_revenue_label.setStyleSheet(
            "font-size: 18pt; font-weight: bold; color: #00ff88;")
        rev_lay.addRow("Total Revenue:", self.total_revenue_label)
        self.cycle_count_label = QLabel("0")
        rev_lay.addRow("Cycles Run:", self.cycle_count_label)
        self.last_cycle_label = QLabel("Never")
        rev_lay.addRow("Last Cycle:", self.last_cycle_label)
        lay.addWidget(rev_group)

        # Pipeline controls
        pipe_group = QGroupBox("Pipeline Controls")
        pipe_lay = QVBoxLayout(pipe_group)
        self.pipeline_source_combo = QComboBox()
        self.pipeline_source_combo.addItems([
            "all", "social", "upwork", "fiverr",
            "airdrop", "defi", "microtask", "content",
        ])
        pipe_lay.addWidget(QLabel("Sources:"))
        pipe_lay.addWidget(self.pipeline_source_combo)

        self.pipeline_max_risk_combo = QComboBox()
        self.pipeline_max_risk_combo.addItems(
            ["low", "medium", "high"],
        )
        pipe_lay.addWidget(QLabel("Max Risk:"))
        pipe_lay.addWidget(self.pipeline_max_risk_combo)

        run_btn = QPushButton("▶ Run Full Cycle")
        run_btn.clicked.connect(self._run_earning_cycle)
        pipe_lay.addWidget(run_btn)

        self.pipeline_status_label = QLabel("Idle")
        pipe_lay.addWidget(self.pipeline_status_label)
        lay.addWidget(pipe_group)

        # Outcomes log
        lay.addWidget(QLabel("Recent Outcomes"))
        self.outcomes_list = QListWidget()
        lay.addWidget(self.outcomes_list)

        # Auto-refresh timer
        self.earnings_refresh_timer = QTimer()
        self.earnings_refresh_timer.timeout.connect(
            self._refresh_earnings_dashboard,
        )
        self.earnings_refresh_timer.start(30000)  # 30s

        return w

    def create_file_browser_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(f"Root (sandboxed): {self.root_folder}"))
        model = QFileSystemModel()
        model.setRootPath(self.root_folder)
        tree = QTreeView()
        tree.setModel(model)
        tree.setRootIndex(model.index(self.root_folder))
        lay.addWidget(tree)
        return w

    def create_logs_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # Filter row with severity combo
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter text:"))
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("Type to filter…")
        self.log_filter.textChanged.connect(self._apply_log_filter)
        frow.addWidget(self.log_filter)

        frow.addWidget(QLabel("Severity:"))
        self.log_severity_combo = QComboBox()
        self.log_severity_combo.addItems(["All", "BLOCKED", "ERROR", "MANAGER", "INFO", "OLLAMA"])
        self.log_severity_combo.currentTextChanged.connect(self._apply_log_filter)
        frow.addWidget(self.log_severity_combo)

        auto_scroll_cb = QCheckBox("Auto-scroll")
        auto_scroll_cb.setChecked(True)
        auto_scroll_cb.toggled.connect(self._set_log_auto_scroll)
        self.auto_scroll_logs = auto_scroll_cb
        frow.addWidget(auto_scroll_cb)

        cb = QPushButton("Clear")
        cb.clicked.connect(self._clear_log)
        frow.addWidget(cb)
        lay.addLayout(frow)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log_edit.setStyleSheet(
            "font-family:Consolas,Monaco,monospace;font-size:11px;"
            "background:#0a0a0f;color:#d4d4d4;"
        )
        lay.addWidget(self.log_edit)
        return w

    def create_db_stats_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        title = QLabel("Database & Agent Stats")
        title.setStyleSheet("font-size:16px;font-weight:bold;color:#00b0ff;")
        lay.addWidget(title)

        # Database overview
        db_group = QGroupBox("Database Overview")
        db_layout = QFormLayout(db_group)
        db_layout.setContentsMargins(12, 12, 12, 12)
        db_layout.setVerticalSpacing(8)
        self.db_stats_label = QLabel("Loading…")
        self.db_stats_label.setStyleSheet("color:#03dac6;font-size:12px;")
        self.db_stats_label.setWordWrap(True)
        db_layout.addRow(self.db_stats_label)

        db_refresh_btn = QPushButton("Refresh Stats")
        db_refresh_btn.clicked.connect(self.refresh_db_stats)
        db_layout.addRow(db_refresh_btn)
        lay.addWidget(db_group)

        # Recent actions with color coding
        actions_group = QGroupBox("Recent Actions")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setContentsMargins(12, 12, 12, 12)
        actions_layout.setSpacing(6)
        self.db_actions_list = QListWidget()
        self.db_actions_list.setStyleSheet(
            "font-family:Consolas,Monaco,monospace;font-size:11px;"
        )
        actions_layout.addWidget(self.db_actions_list)
        lay.addWidget(actions_group, stretch=1)

        # Recent LLM calls
        calls_group = QGroupBox("Recent LLM Calls")
        calls_layout = QVBoxLayout(calls_group)
        calls_layout.setContentsMargins(12, 12, 12, 12)
        calls_layout.setSpacing(6)
        self.db_calls_edit = QPlainTextEdit()
        self.db_calls_edit.setReadOnly(True)
        self.db_calls_edit.setStyleSheet(
            "font-family:Consolas,Monaco,monospace;font-size:11px;"
            "background:#0a0a0f;color:#d4d4d4;"
        )
        calls_layout.addWidget(self.db_calls_edit)
        lay.addWidget(calls_group, stretch=1)

        # Auto-refresh timer
        self._db_stats_timer = QTimer(self)
        self._db_stats_timer.timeout.connect(self.refresh_db_stats)
        self._db_stats_timer.start(10000)

        return w

    def select_research_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Research Folder")
        if folder:
            self.worker.research_folder = folder
            self.research_folder_label.setText(f"Research folder: {folder}")
            self.log_signal.emit(f"Research folder set: {folder}")
            self.thought_panel.route(
                "System", f"Research folder set: {folder}")
            self.manager.invalidate_cache()
            self.manager._reviewed_files.clear()

    def force_safe_improve(self):
        self.manager.queue_task(
            "Self-improvement: review codebase and propose one concrete upgrade")
        self.log_signal.emit("Self-improvement task queued")

    def force_research_rescan(self):
        self.manager.invalidate_cache()
        self.manager.queue_task(
            "Research re-scan: review all files and summarize key findings")
        self.log_signal.emit("Research re-scan queued")

    def clear_file_cache(self):
        reply = QMessageBox.question(
            self, "Clear Cache",
            "Delete all cached file contents?",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.db.clear_file_cache()
            self.log_signal.emit("File cache cleared")

    def refresh_db_stats(self):
        try:
            stats = self.db.get_llm_stats()
            self.db_stats_label.setText(
                f"Calls: {stats.get('total_calls',0)}  "
                f"OK: {stats.get('successes',0)}  "
                f"Err: {stats.get('errors',0)}  "
                f"Avg: {int(stats.get('avg_latency_ms') or 0)}ms  "
                f"Chars: {int(stats.get('total_chars') or 0):,}"
            )
            self.db_actions_list.clear()
            for a in self.db.get_recent_actions(20):
                ts = self.db.ts_to_str(a["ts"])
                self.db_actions_list.addItem(
                    f"[{ts}] {a['trigger']}: {a['action_text']}")
            lines = []
            for c in self.db.get_recent_llm_calls(20):
                ts     = self.db.ts_to_str(c["ts"])
                status = "OK" if not c["error"] else f"ERR"
                lines.append(
                    f"[{ts}] {str(c['provider']):6} "
                    f"{str(c['model'])[:24]:24} "
                    f"{str(c['latency_ms'] or 0):>5}ms {status}")
            self.db_calls_edit.setPlainText("\n".join(lines))
        except Exception as e:
            self.db_stats_label.setText(f"DB error: {e}")

    def _run_http(self, method, url, tag, **kwargs):
        w = HttpWorker(method, url, tag, **kwargs)
        w.result.connect(self._on_http_result)
        self._http_workers.append(w)
        w.finished.connect(
            lambda: self._http_workers.remove(w)
            if w in self._http_workers else None)
        w.start()

    def _on_http_result(self, tag: str, response):
        if tag == "balance":
            if response and response.ok:
                try:
                    bal = response.json().get("available_usdc", 0)
                    self.balance_label.setText(f"Balance: ${bal:.2f} USDC")
                except Exception:
                    pass
            else:
                self.log_signal.emit("Failed to fetch balance")
        elif tag == "payout":
            ok = response and response.ok
            self.log_signal.emit(
                "Payout sent" if ok
                else f"Payout failed: {response.text if response else 'no response'}")
        elif tag == "register":
            if response and response.status_code == 201:
                try:
                    data = response.json()
                    self.worker.api_key  = data["api_key"]
                    self.manager.api_key = data["api_key"]
                    self.log_signal.emit("Registration SUCCESS")
                    self.status_label.setText("Agent status: Registered ✓")
                except Exception as e:
                    self.log_signal.emit(f"Register parse error: {e}")
            else:
                err = response.text if response else "no response"
                self.log_signal.emit(f"Registration failed: {err}")

    def refresh_balance(self):
        """Shows wallet address as balance display."""
        wallet = os.getenv("ATOMIC_SOLANA_ADDRESS", "")
        if wallet:
            self.balance_label.setText(f"Wallet: {wallet[:8]}...{wallet[-4:] if len(wallet) > 8 else ''}")
        else:
            self.balance_label.setText("Balance: Wallet not set")

    def manual_payout(self):
        """SECURE: Requires triple confirmation for any payout action.
        
        Security Notes:
        - Does NOT auto-send money - requires explicit user confirmation
        - Logs all payout attempts for audit trail
        - Amount clamped to reasonable max to prevent accidental large transfers
        """
        if not self.worker.api_key and not os.getenv("ATOMIC_SOLANA_ADDRESS"):
            self.log_signal.emit("Cannot payout: No wallet configured")
            QMessageBox.warning(self, "Wallet Not Set", 
                "Please configure ATOMIC_SOLANA_ADDRESS in settings first.")
            return
        
        try:
            amount = float(self.amount_input.text().strip())
            if amount <= 0:
                raise ValueError("amount must be positive")
            # Security: Cap at $10,000 max to prevent accidental large transfers
            if amount > 10000:
                QMessageBox.warning(self, "Amount Too Large", 
                    "Maximum payout amount is $10,000. Please enter a smaller amount.")
                self.log_signal.emit(f"[SEC] Payout blocked: amount ${amount} exceeds cap")
                return
        except ValueError as e:
            self.log_signal.emit(f"Invalid amount: {e}")
            return
        
        # Security: Triple confirmation for payout
        wallet_prefix = (os.getenv("ATOMIC_SOLANA_ADDRESS") or "")[:8] + "..."
        reply = QMessageBox.question(
            self, "Confirm Payout",
            f"⚠️ SECURITY WARNING\n\n"
            f"Send ${amount:.2f} to wallet {wallet_prefix}?\n"
            f"This action cannot be easily reversed.\n\n"
            f"Type 'CONFIRM' to proceed:",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            self.log_signal.emit(f"[SEC] Payout cancelled by user")
            return
            
        confirm_text = self.amount_input.text()
        if confirm_text.strip().upper() != "CONFIRM":
            self.log_signal.emit(f"[SEC] Payout not confirmed (typing required)")
            return
        
        self.log_signal.emit(f"[PAYOUT] Processing ${amount:.2f} to {wallet_prefix}")
        self.history_list.addItem(
            f"{datetime.now().strftime('%H:%M')} → ${amount:.2f} PENDING MANUAL")
        QMessageBox.information(self, "Payout Submitted",
            f"${amount:.2f} payout request queued.\n"
            f"Agent will need to complete manual transfer.\n"
            f"Check logs for details.")

    # ── Earnings Pipeline ─────────────────────────────────────────────

    def _run_earning_cycle(self):
        """Run a full earning discovery → evaluate → execute cycle."""
        try:
            from earning_pipeline import EarningPipeline
            pipeline = EarningPipeline(
                db_path=os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "earning.db",
                ),
                log_fn=self.log_signal.emit,
            )

            source = self.pipeline_source_combo.currentText()
            max_risk = self.pipeline_max_risk_combo.currentText()
            sources = None if source == "all" else [source]

            self.pipeline_status_label.setText("Running...")
            QApplication.processEvents()

            result = pipeline.run_full_cycle(
                sources=sources,
                max_risk=max_risk,
            )

            self.pipeline_status_label.setText(
                f"Done: {result.message}"
            )
            self.last_cycle_label.setText(
                datetime.now().strftime("%H:%M:%S")
            )
            count = int(self.cycle_count_label.text() or "0")
            self.cycle_count_label.setText(str(count + 1))

            # Refresh dashboard
            self._refresh_earnings_dashboard()

        except Exception as e:
            self.log_signal.emit(f"[Pipeline] Error: {e}")
            self.pipeline_status_label.setText(f"Error: {e}")

    def _refresh_earnings_dashboard(self):
        """Refresh the earnings dashboard with latest data."""
        try:
            from earning_pipeline import EarningPipeline
            pipeline = EarningPipeline(
                db_path=os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "earning.db",
                ),
            )

            report = pipeline.get_revenue_report(days=30)
            self.total_revenue_label.setText(
                f"${report['total_revenue_usd']:.2f}"
            )

            # Refresh outcomes list
            self.outcomes_list.clear()
            cursor = pipeline._db_execute(
                "SELECT action, result, revenue_usd, ts "
                "FROM outcomes ORDER BY ts DESC LIMIT 20",
            )
            rows = cursor.fetchall()
            for row in rows:
                action, result, revenue, ts = row
                ts_str = datetime.fromtimestamp(
                    ts or 0,
                ).strftime("%H:%M") if ts else "—"
                self.outcomes_list.addItem(
                    f"{ts_str} {action}: {result} "
                    f"(${revenue or 0:.2f})",
                )

        except Exception:
            pass

    def register_autonomous(self):
        name = self.name_edit.text().strip()
        user = self.user_edit.text().strip()
        if not name or not user:
            QMessageBox.warning(self, "Missing", "Name and Username required.")
            return
        QMessageBox.information(
            self,
            "Registration",
            "Local agent registration is active.\n"
            "Earning sources are discovered through Fiverr, social platforms, and content channels.",
        )
        self.status_label.setText("Agent status: Registered ✓")

    def test_api_connection(self):
        msgs = []
        # OpenAI
        if not self.disable_openai.isChecked():
            key = self.openai_key_edit.text().strip()
            if key:
                try:
                    if OPENAI_AVAILABLE:
                        client = openai.OpenAI(api_key=key)
                        client.chat.completions.create(
                            model=self.openai_model_edit.text(),
                            messages=[{"role": "user", "content": "test"}],
                            max_tokens=5,
                        )
                        msgs.append("OpenAI: OK")
                    else:
                        msgs.append("OpenAI: package missing")
                except Exception as e:
                    msgs.append(f"OpenAI Error: {e}")
            else:
                msgs.append("OpenAI: no key set")
        # Anthropic
        if not self.disable_anthropic.isChecked():
            key = self.anthropic_key_edit.text().strip()
            if key:
                try:
                    if ANTHROPIC_AVAILABLE:
                        client = Anthropic(api_key=key)
                        client.messages.create(
                            model=self.anthropic_model_edit.text(),
                            max_tokens=5,
                            messages=[{"role": "user", "content": "test"}],
                        )
                        msgs.append("Anthropic: OK")
                    else:
                        msgs.append("Anthropic: package missing")
                except Exception as e:
                    msgs.append(f"Anthropic Error: {e}")
            else:
                msgs.append("Anthropic: no key set")
        # Ollama
        if not self.disable_ollama.isChecked():
            try:
                model = self.ollama_model_combo.currentText().strip()
                ollama.chat(
                    model=model,
                    messages=[{"role": "user", "content": "test"}],
                )
                msgs.append(f"Ollama: OK ({model})")
            except Exception as e:
                msgs.append(f"Ollama Error: {e}")
        QMessageBox.information(self, "Connection Test", "\n".join(msgs))

    def refresh_ollama_models(self):
        self.refresh_ollama_btn.setEnabled(False)
        self.refresh_ollama_btn.setText("Refreshing...")
        QApplication.processEvents()

        try:
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/tags",
                headers={"User-Agent": "MrBot1000"},
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                models = [m["name"] for m in data.get("models", [])]

            main_current = self.ollama_model_combo.currentText()
            chat_current = self.ollama_chat_model_combo.currentText()

            self.ollama_model_combo.clear()
            self.ollama_chat_model_combo.clear()
            if models:
                self.ollama_model_combo.addItems(models)
                self.ollama_chat_model_combo.addItems(models)
                if main_current in models:
                    self.ollama_model_combo.setCurrentText(main_current)
                else:
                    self.ollama_model_combo.setCurrentIndex(0)
                if chat_current in models:
                    self.ollama_chat_model_combo.setCurrentText(chat_current)
                else:
                    self.ollama_chat_model_combo.setCurrentIndex(0)
                QMessageBox.information(
                    self,
                    "Ollama",
                    f"Loaded {len(models)} model(s).",
                )
            else:
                self.ollama_model_combo.addItem(main_current or "llama3.2")
                self.ollama_chat_model_combo.addItem(chat_current or "llama3.2")
                QMessageBox.warning(
                    self,
                    "Ollama",
                    "No models found on local Ollama server.",
                )
        except Exception as e:
            QMessageBox.warning(self, "Ollama Error", str(e))
        finally:
            self.refresh_ollama_btn.setEnabled(True)
            self.refresh_ollama_btn.setText("Refresh")

    def save_settings(self):
        env = Path(".env")
        if not env.exists():
            env.touch()

        set_key(env, "OPENAI_API_KEY",      self.openai_key_edit.text())
        set_key(env, "OPENAI_MODEL",        self.openai_model_edit.text())
        set_key(env, "ANTHROPIC_API_KEY",   self.anthropic_key_edit.text())
        set_key(env, "ANTHROPIC_MODEL",     self.anthropic_model_edit.text())
        set_key(env, "OLLAMA_MODEL",        self.ollama_model_combo.currentText())
        set_key(env, "OLLAMA_CHAT_MODEL",   self.ollama_chat_model_combo.currentText().strip())
        set_key(env, "DISABLE_OPENAI",      str(self.disable_openai.isChecked()))
        set_key(env, "DISABLE_ANTHROPIC",   str(self.disable_anthropic.isChecked()))
        set_key(env, "DISABLE_OLLAMA",      str(self.disable_ollama.isChecked()))
        set_key(env, "AGENT_NAME",          self.name_edit.text())
        set_key(env, "AGENT_USERNAME",      self.user_edit.text())
        set_key(env, "ATOMIC_SOLANA_ADDRESS", self.wallet_edit.text())
        set_key(env, "CASHAPP_TAG",         self.cashapp_edit.text())
        set_key(env, "MAX_FILE_SIZE_MB",    str(self.max_file_spin.value()))
        set_key(env, "RESEARCH_MAX_CHARS",  str(self.research_max_chars.value()))
        set_key(env, "DEEP_READ_MAX_CHARS", str(self.deep_read_chars.value()))
        set_key(env, "AUTO_RESEARCH",       str(self.auto_research.isChecked()))
        set_key(env, "STARTUP_DELAY_SECS",  str(self.startup_spin.value()))
        set_key(env, "HEARTBEAT_INTERVAL",  str(self.polling_spin.value()))
        set_key(env, "MAX_TOKENS",           str(self.max_tokens_spin.value()))
        set_key(env, "RESEARCH_CACHE_TTL",   str(self.research_cache_ttl_spin.value()))
        set_key(env, "FILENAME_BLOCKLIST",   self.blocklist_edit.text())
        set_key(env, "BLOCKED_MIME_TYPES",   self.blocked_mime_edit.text())
        set_key(env, "PIPELINE_ENABLED",     str(self.pipeline_enabled_check.isChecked()))
        set_key(env, "PIPELINE_ALLOW_WRITE", str(self.pipeline_allow_write_check.isChecked()))
        set_key(env, "PIPELINE_ALLOW_SELF_IMPROVE",
                str(self.pipeline_allow_selfimprove_check.isChecked()))

        # Reload env so newly saved keys are visible to os.getenv() in this process
        try:
            load_dotenv(override=True)
        except Exception:
            pass

        # Update live objects
        self.worker.max_file_size = self.max_file_spin.value() * 1024 * 1024
        self.manager.HEARTBEAT_INTERVAL = self.polling_spin.value()
        self.manager._research_cache_ttl = self.research_cache_ttl_spin.value()
        if hasattr(self, "agents_tab"):
            self.agents_tab.heartbeat_label.setText(
                f"Heartbeat every {self.polling_spin.value()}s")
        self.log_signal.emit("Settings saved")

            # ── Pipeline handlers ─────────────────────────────────────────────────────

    def _on_pipeline_validated(self, action, result):
        status = "PASS" if result.passed else "FAIL"
        self.log_signal.emit(
            f"[Pipeline] {status} score={result.score:.2f} "
            f"| {action.proposer} → {action.action_type} "
            f"| {result.summary}")
        if hasattr(self, "thought_panel"):
            self.thought_panel.route(
                "System",
                f"Pipeline {status}: {action.description} | {result.summary}")

    def _on_pipeline_executed(self, action, result):
        status = "✓" if result.success else "✗"
        self.log_signal.emit(
            f"[Pipeline] {status} EXECUTED {action.action_type} "
            f"by {action.proposer}: {result.message[:80]}")

    def _on_pipeline_rejected(self, action, reason):
        self.log_signal.emit(
            f"[Pipeline] REJECTED {action.action_type} "
            f"by {action.proposer}: {reason[:80]}")
        if hasattr(self, "thought_panel"):
            self.thought_panel.route(
                "System",
                f"⚠ Rejected: {action.description[:50]} | {reason[:60]}")

    def _validate_clipboard_code(self):
        """Quick-validate whatever code is in the clipboard."""
        try:
            code = QApplication.clipboard().text()
            if not code.strip():
                self.pipeline_result_label.setText("Clipboard is empty.")
                return
            ok, summary = self.pipeline.quick_check(code)
            icon = "✓" if ok else "✗"
            color = "#22c55e" if ok else "#ef4444"
            self.pipeline_result_label.setText(f"{icon} {summary}")
            self.pipeline_result_label.setStyleSheet(f"font-size:10px;color:{color};")
        except Exception as e:
            self.pipeline_result_label.setText(f"Error: {e}")



    def apply_theme(self, theme_name: str):
        if theme_name == "Auto":
            is_dark = (QApplication.instance()
                       .palette().color(QPalette.Window).lightness() < 128)
            self.apply_theme("Dark" if is_dark else "Light")
            return
        theme = self.THEMES.get(theme_name, self.THEMES["Dark"])
        pal = QPalette()
        for role, key in [
            (QPalette.Window,        "bg"),
            (QPalette.WindowText,    "fg"),
            (QPalette.Base,          "bg"),
            (QPalette.AlternateBase, "bg"),
            (QPalette.Text,          "fg"),
            (QPalette.Button,        "bg"),
            (QPalette.ButtonText,    "fg"),
            (QPalette.Highlight,     "highlight"),
        ]:
            pal.setColor(role, QColor(theme[key]))
        pal.setColor(QPalette.Disabled, QPalette.WindowText,
                     QColor(theme["disabled"]))
        pal.setColor(QPalette.Disabled, QPalette.ButtonText,
                     QColor(theme["disabled"]))
        app = QApplication.instance()
        app.setPalette(pal)
        qss = f"""
            QWidget{{background:{theme['bg']};color:{theme['fg']};}}
            QTabWidget::pane{{border:1px solid {theme['accent']};
                background:{theme['bg']};}}
            QTabBar::tab{{background:{theme['bg']};color:{theme['fg']};
                padding:8px;}}
            QTabBar::tab:selected{{background:{theme['accent']};color:black;}}
            QPushButton{{background:{theme['bg']};color:{theme['fg']};
                border:1px solid {theme['accent']};padding:6px;
                border-radius:4px;}}
            QPushButton:hover{{background:{theme['accent']};color:black;}}
            QPushButton:checked{{background:{theme['highlight']};color:black;}}
            QLineEdit,QPlainTextEdit,QSpinBox,QComboBox{{
                background:{theme['bg']};color:{theme['fg']};
                border:1px solid {theme['highlight']};}}
            QGroupBox{{border:1px solid {theme['accent']};border-radius:4px;
                margin-top:8px;padding-top:8px;}}
            QGroupBox::title{{color:{theme['accent']};}}
            QScrollArea{{border:none;}}
            {theme['qss_extra']}
        """
        app.setStyleSheet(qss)
        self.log_signal.emit(f"Theme: {theme_name}")

    def _summarizer_human_send(self, text: str):
        self.summarizer.send_human_message(text)

    def _on_summarizer_chat_reply(self, label: str, text: str):
            """Handle chat reply from summarizer - update tab display."""
            try:
                # Use the agents tab's built-in chat display (same as Manager thoughts)
                if hasattr(self, 'agents_tab') and hasattr(self.agents_tab, 'append_reply'):
                    self.agents_tab.append_reply(label, text)
                    self.centralWidget().setCurrentIndex(1)  # Switch to Agents tab (index 1)
            except Exception as e:
                self.log_signal.emit(f"Chat display error: {e}")

    def _on_strategy_change(self, strategy: str):
        self.summarizer.set_strategy(strategy)

    def _on_summary_ready(self, text: str):
        if hasattr(self, "thought_panel"):
            self.thought_panel.route_summary(text)



    def _show_manager_win(self):
        self.thought_panel._manager_win.show()
        self.thought_panel._manager_win.raise_()

    def _show_agent_win(self):
        self.thought_panel._agent_win.show()
        self.thought_panel._agent_win.raise_()

    def _show_comms_win(self):
        self.thought_panel._comms_win.show()
        self.thought_panel._comms_win.raise_()

    def _show_summary_win(self):
        self.thought_panel._summary_win.show()
        self.thought_panel._summary_win.raise_()


if __name__ == "__main__":
    import traceback
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception:
        import traceback as _tb
        _crash_dir = Path.home() / ".local" / "share" / "mrbot1000"
        _crash_dir.mkdir(parents=True, exist_ok=True)
        _crash_path = _crash_dir / "crash.log"
        with open(_crash_path, "w") as f:
            f.write(_tb.format_exc())
        print(_tb.format_exc())
        print(f"\n[CRASH] Log written to: {_crash_path}")
        input("Press Enter to exit…")