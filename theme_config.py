import os
from pathlib import Path
from dotenv import set_key


CUSTOM_THEME_KEYS = (
    "bg",
    "panel",
    "fg",
    "accent",
    "highlight",
    "disabled",
)

# Preset theme names exposed in the UI (Theme menu + Settings combobox).
# "Custom" is handled specially by resolve_theme_definition() using MRBOT_THEME_*.
THEME_PRESETS = [
    "Dark", "Light", "Midnight-Blue", "Ocean", "Solar",
    "Forest", "Rose", "Lavender", "Neon-Cyberpunk", "Gradient-Mix",
]
CUSTOM_THEME_NAME = "Custom"


def resolve_theme_definition(theme_name: str, env=None) -> dict:
    """Return a theme definition for a builtin preset or the custom theme."""
    env = os.environ if env is None else env
    if theme_name == "Custom":
        return {
            "bg": env.get("MRBOT_THEME_BG", "#121212"),
            "panel": env.get("MRBOT_THEME_PANEL", "#1a1a1f"),
            "fg": env.get("MRBOT_THEME_FG", "#e0e0e0"),
            "accent": env.get("MRBOT_THEME_ACCENT", "#4fc3f7"),
            "highlight": env.get("MRBOT_THEME_HIGHLIGHT", "#7c4dff"),
            "disabled": env.get("MRBOT_THEME_DISABLED", "#6b7280"),
            "qss_extra": "",
        }

    defaults = {
        "Dark": {
            "bg": "#121212",
            "panel": "#18181c",
            "fg": "#e0e0e0",
            "accent": "#bb86fc",
            "disabled": "#555",
            "highlight": "#03dac6",
            "qss_extra": (
                "QProgressBar::chunk{background:qlineargradient("
                "x1:0,y1:0,x2:1,y2:0,stop:0 #bb86fc,stop:1 #03dac6);}"
            ),
        },
        "Light": {
            "bg": "#f5f5f5",
            "panel": "#ffffff",
            "fg": "#212121",
            "accent": "#6200ee",
            "disabled": "#aaaaaa",
            "highlight": "#3700b3",
            "qss_extra": "",
        },
        "Midnight-Blue": {
            "bg": "#07111f",
            "panel": "#0c1728",
            "fg": "#d7e8ff",
            "accent": "#4fc3f7",
            "disabled": "#5c6b7f",
            "highlight": "#7c4dff",
            "qss_extra": "QProgressBar::chunk{background:#4fc3f7;}",
        },
        "Ocean": {
            "bg": "#06272d",
            "panel": "#0a3b45",
            "fg": "#b8f4ff",
            "accent": "#24d1d1",
            "disabled": "#4f7a84",
            "highlight": "#2bb4ff",
            "qss_extra": "QProgressBar::chunk{background:#2bb4ff;}",
        },
        "Solar": {
            "bg": "#2d1600",
            "panel": "#4a2500",
            "fg": "#ffe3b3",
            "accent": "#ff8c00",
            "disabled": "#8a6d3b",
            "highlight": "#ff5d44",
            "qss_extra": "QProgressBar::chunk{background:#ff8c00;}",
        },
        "Forest": {
            "bg": "#102214",
            "panel": "#18311f",
            "fg": "#dff6dd",
            "accent": "#38b000",
            "disabled": "#5b6f5d",
            "highlight": "#9ef01a",
            "qss_extra": "QProgressBar::chunk{background:#38b000;}",
        },
        "Rose": {
            "bg": "#221018",
            "panel": "#321827",
            "fg": "#ffe0eb",
            "accent": "#f45b7a",
            "disabled": "#7a5c67",
            "highlight": "#ff7f50",
            "qss_extra": "QProgressBar::chunk{background:#f45b7a;}",
        },
        "Lavender": {
            "bg": "#1d1730",
            "panel": "#2b2144",
            "fg": "#efe8ff",
            "accent": "#9b87ff",
            "disabled": "#756d91",
            "highlight": "#c084fc",
            "qss_extra": "QProgressBar::chunk{background:#9b87ff;}",
        },
        "Neon-Cyberpunk": {
            "bg": "#0d001a",
            "panel": "#1d0028",
            "fg": "#00ffea",
            "accent": "#ff00aa",
            "disabled": "#444",
            "highlight": "#ffea00",
            "qss_extra": (
                "*{font-family:'Consolas',monospace;}"
                "QPushButton{border:1px solid #ff00aa;"
                "background:#1a0033;color:#00ffea;}"
                "QPushButton:hover{background:#ff00aa;color:black;}"
                "QProgressBar::chunk{background:#ff00aa;}"
            ),
        },
        "Gradient-Mix": {
            "bg": "#1e0033",
            "panel": "#2b0a4a",
            "fg": "#d4a5ff",
            "accent": "#ff6ec7",
            "disabled": "#663399",
            "highlight": "#00f2ff",
            "qss_extra": (
                "QWidget{background:qlineargradient("
                "x1:0,y1:0,x2:1,y2:1,stop:0 #1e0033,stop:1 #330066);}"
                "QLabel{color:#d4a5ff;}"
                "QProgressBar{background:#330066;border:1px solid #ff6ec7;}"
                "QProgressBar::chunk{background:qlineargradient("
                "x1:0,y1:0,x2:1,y2:0,stop:0 #ff6ec7,stop:1 #00f2ff);}"
            ),
        },
    }
    theme = defaults.get(theme_name, defaults["Dark"])
    return dict(theme)


def save_custom_theme(theme_values: dict, env=None, env_path: str | None = None) -> dict:
    """Persist custom theme colors to the environment and .env file."""
    env = os.environ if env is None else env
    project_root = Path(__file__).resolve().parent
    env_path = env_path or str(project_root / ".env")
    normalized = {}
    for key in CUSTOM_THEME_KEYS:
        fallback = {
            "bg": "#121212",
            "panel": "#1a1a1f",
            "fg": "#e0e0e0",
            "accent": "#4fc3f7",
            "highlight": "#7c4dff",
            "disabled": "#6b7280",
        }[key]
        value = str(theme_values.get(key, fallback)).strip()
        if not value:
            value = fallback
        normalized[key] = value
        env[f"MRBOT_THEME_{key.upper()}"] = value
        set_key(env_path, f"MRBOT_THEME_{key.upper()}", value)
    return normalized
