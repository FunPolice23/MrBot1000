"""
ui.py — Enhanced UI v4: separated thought windows, richer sprites,
        summarizer chat, simplified sprite-based agent display.

New in v4:
  • Removed 2D animated office scene (graphics_lib) for performance.
  • Agents displayed as animated sprites in the Agents tab.
  • Quick-action focus bug fixed: buttons have Qt.NoFocus so Enter
    key can't accidentally trigger them while typing.
"""
from __future__ import annotations

import math
import random
from datetime import datetime
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget, QPlainTextEdit, QVBoxLayout, QHBoxLayout,
    QPushButton, QDialog, QLabel, QSplitter, QFrame,
    QScrollArea, QSizePolicy, QLineEdit, QProgressBar,
    QComboBox, QApplication, QGroupBox, QTextEdit, QSpinBox, QTabWidget,
    QListWidget, QListWidgetItem
)
from PySide6.QtGui import (
    QPainter, QBrush, QColor, QPen, QRadialGradient,
    QLinearGradient, QFont, QPainterPath, QPolygonF
)
from PySide6.QtCore import (
    QTimer, QPointF, QSizeF, Property, QObject, Qt, QRectF
)

# ─────────────────────────────────────────────────────────────────────────────
#  Color palette
# ─────────────────────────────────────────────────────────────────────────────
CHANNEL_COLORS = {
    "Manager":  {"header": "#bb86fc", "text": "#d4aaff", "bg": "#1a0033"},
    "Agent":    {"header": "#03dac6", "text": "#80ffe8", "bg": "#001a1a"},
    "Comms":    {"header": "#ffb300", "text": "#ffe082", "bg": "#1a1400"},
    "System":   {"header": "#ff7043", "text": "#ffccbc", "bg": "#1a0a00"},
    "Summary":  {"header": "#9c27b0", "text": "#e1bee7", "bg": "#1a0033"},
    "Chat":     {"header": "#00b0ff", "text": "#b3e5fc", "bg": "#001422"},
}
DEFAULT_CHANNEL = {"header": "#aaaaaa", "text": "#cccccc", "bg": "#111111"}


# ═══════════════════════════════════════════════════════════════════════════
#  Particle  — simple floating particle for sprite effects
# ═══════════════════════════════════════════════════════════════════════════
class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life",
                 "size", "color", "alpha")

    def __init__(self, x, y, color: QColor):
        self.x = x
        self.y = y
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(0.4, 1.6)
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed - 0.5   # slight upward bias
        self.max_life = random.randint(20, 50)
        self.life = self.max_life
        self.size = random.uniform(2, 5)
        self.color = color
        self.alpha = 255

    def tick(self) -> bool:
        """Update position; return False when dead."""
        self.x += self.vx
        self.y += self.vy
        self.vy += 0.04   # gravity
        self.life -= 1
        self.alpha = int(255 * (self.life / self.max_life))
        return self.life > 0


# ═══════════════════════════════════════════════════════════════════════════
#  AgentSprite  — rich animated states (v2: larger, more effects)
# ═══════════════════════════════════════════════════════════════════════════
class AgentSprite(QWidget):
    """
    Animated robot sprite — v2.

    New in v2:
      • Larger: 140×170
      • Floating particle cloud (idle/success/error)
      • Energy ring orbiting the head (communicating)
      • Data-stream lines scrolling down (researching)
      • Typing fingers animation (writing)
      • Heartbeat pulse ring (working)
      • Rainbow starfield (success)
      • Glitch offset lines (error)
      • Named label underneath + gradient name banner

    States
    ------
    idle          green    slow pulse, blink, floating particles
    thinking      blue     multi-dot bounce above head
    researching   cyan     data-stream lines + scan bar
    writing       lime     arm swing + finger dots
    communicating purple   energy ring orbit + radio waves
    working       orange   spinning gear + heartbeat pulse
    success       yellow   rainbow starfield + celebration bounce
    error         red      X eyes + glitch lines + shake
    """

    STATE_CFG: Dict[str, tuple] = {
        "idle":          ("#22c55e", "normal",  "blink"),
        "thinking":      ("#3b82f6", "dots",    "think_cloud"),
        "researching":   ("#06b6d4", "scan",    "data_stream"),
        "writing":       ("#84cc16", "normal",  "write_arm"),
        "communicating": ("#a855f7", "normal",  "energy_ring"),
        "working":       ("#f97316", "busy",    "heartbeat"),
        "success":       ("#facc15", "star",    "rainbow_burst"),
        "error":         ("#ef4444", "cross",   "glitch"),
    }

    W, H = 140, 170

    def __init__(self, parent=None, label: str = "Agent"):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.label      = label
        self.state      = "idle"
        self.frame      = 0

        # Animation counters
        self._scan_x    = 0
        self._gear_ang  = 0
        self._wave_r    = 0
        self._shake     = 0
        self._star_sc   = 1.0
        self._pulse     = 0.0
        self._pulse_dir = 1
        self._ring_ang  = 0.0
        self._hb_r      = 0.0
        self._data_y    = 0
        self._glitch_x  = 0
        self._bounce_y  = 0.0
        self._bounce_dir = 1

        # Particles
        self._particles: List[Particle] = []
        self._particle_timer = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)   # ~30 fps

    def set_state(self, state: str):
        if state != self.state:
            self.state = state if state in self.STATE_CFG else "idle"
            self._scan_x  = 0
            self._wave_r  = 0
            self._star_sc = 1.8
            self._shake   = (8 if state == "error" else 0)
            self._particles.clear()

    def _spawn_particles(self, cx: float, cy: float, color: QColor, n: int = 3):
        for _ in range(n):
            self._particles.append(Particle(cx, cy, color))

    def _tick(self):
        self.frame      = (self.frame + 1) % 3600
        self._scan_x    = (self._scan_x + 2) % 80
        self._gear_ang  = (self._gear_ang + 3) % 360
        self._wave_r    = (self._wave_r + 1.8) % 50
        self._ring_ang  = (self._ring_ang + 2.5) % 360
        self._data_y    = (self._data_y + 3) % 60
        self._glitch_x  = random.randint(-3, 3) if self.state == "error" else 0

        # Pulse
        self._pulse += 0.04 * self._pulse_dir
        if self._pulse >= 1.0:  self._pulse_dir = -1
        elif self._pulse <= 0.0: self._pulse_dir = 1

        # Success star
        if self._star_sc > 1.0:
            self._star_sc = max(1.0, self._star_sc - 0.015)

        # Shake dies
        if self._shake > 0:
            self._shake -= 1

        # Heartbeat ring
        if self.state == "working":
            self._hb_r = (self._hb_r + 1.5) % 55

        # Bounce (success)
        if self.state == "success":
            self._bounce_y += 0.3 * self._bounce_dir
            if self._bounce_y > 6: self._bounce_dir = -1
            elif self._bounce_y < 0: self._bounce_dir = 1

        # Particles
        self._particle_timer += 1
        color_hex = self.STATE_CFG.get(self.state, self.STATE_CFG["idle"])[0]
        c = QColor(color_hex)
        cx, cy = self.W // 2, 75

        if self.state == "idle" and self._particle_timer % 12 == 0:
            self._spawn_particles(cx + random.randint(-20, 20),
                                  cy + random.randint(-10, 10), c, 1)
        elif self.state == "success" and self._particle_timer % 4 == 0:
            self._spawn_particles(cx, cy, c, 4)
        elif self.state == "error" and self._particle_timer % 8 == 0:
            self._spawn_particles(cx, cy, QColor("#ff4444"), 2)
        elif self.state == "communicating" and self._particle_timer % 10 == 0:
            self._spawn_particles(cx, cy - 30, c, 2)

        self._particles = [p for p in self._particles if p.tick()]
        self.update()

    # ─────────────────────────────────────────────────────────────────────
    #  Paint
    # ─────────────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cfg = self.STATE_CFG.get(self.state, self.STATE_CFG["idle"])
        color_hex, eye_style, fx = cfg
        base_color = QColor(color_hex)

        dx = self._glitch_x if self.state == "error" else 0
        cx  = self.W // 2 + dx
        cy  = 75 + (int(self._bounce_y) if self.state == "success" else 0)
        head_w, head_h = 70, 65

        # ── Particles ────────────────────────────────────────────────────
        for part in self._particles:
            pc = QColor(part.color)
            pc.setAlpha(part.alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(pc))
            p.drawEllipse(QRectF(part.x - part.size / 2,
                                 part.y - part.size / 2,
                                 part.size, part.size))

        # ── Background glow ───────────────────────────────────────────────
        glow_r = 55 + self._pulse * 15
        glow = QRadialGradient(cx, cy, glow_r)
        glow.setColorAt(0, QColor(base_color.red(), base_color.green(),
                                  base_color.blue(), 35))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r,
                             glow_r * 2, glow_r * 2))

        # ── Heartbeat ring (working) ──────────────────────────────────────
        if self.state == "working" and self._hb_r < 50:
            alpha = int(220 * (1 - self._hb_r / 50))
            hb_c = QColor(base_color.red(), base_color.green(),
                          base_color.blue(), alpha)
            p.setPen(QPen(hb_c, 2))
            p.setBrush(Qt.NoBrush)
            r = self._hb_r
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # ── Energy ring orbit (communicating) ────────────────────────────
        if self.state == "communicating":
            for i in range(3):
                wave_r = self._wave_r + i * 17
                if wave_r > 55: continue
                alpha = int(200 * (1 - wave_r / 55))
                wc = QColor(base_color.red(), base_color.green(),
                            base_color.blue(), alpha)
                p.setPen(QPen(wc, 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QRectF(cx - wave_r, cy - wave_r,
                                     wave_r * 2, wave_r * 2))
            # Orbiting dot
            ra = math.radians(self._ring_ang)
            ox = cx + math.cos(ra) * 45
            oy = cy + math.sin(ra) * 22
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(base_color.lighter(180)))
            p.drawEllipse(QRectF(ox - 5, oy - 5, 10, 10))

        # ── Data stream lines (researching) ──────────────────────────────
        if self.state == "researching":
            p.setPen(QPen(QColor(0, 230, 210, 80), 1))
            for i in range(5):
                x_pos = cx - 30 + i * 15
                y_start = cy - 40 + (self._data_y + i * 12) % 60
                p.drawLine(x_pos, y_start, x_pos, y_start + 8)

        # ── Rainbow burst (success) ───────────────────────────────────────
        if self.state == "success" and self._star_sc > 1.0:
            rainbow = ["#ff0000","#ff7700","#ffff00",
                       "#00ff00","#0000ff","#8b00ff"]
            for idx, col in enumerate(rainbow):
                angle = math.radians(idx * 60 + self.frame * 2)
                r_burst = 35 * self._star_sc
                p.setPen(QPen(QColor(col), 2))
                p.drawLine(
                    int(cx + math.cos(angle) * 10),
                    int(cy + math.sin(angle) * 10),
                    int(cx + math.cos(angle) * r_burst),
                    int(cy + math.sin(angle) * r_burst),
                )

        # ── Glitch lines (error) ──────────────────────────────────────────
        if self.state == "error" and self._shake > 0:
            for _ in range(3):
                gy = random.randint(cy - 30, cy + 30)
                gx_off = random.randint(-6, 6)
                p.setPen(QPen(QColor(255, 60, 60, 140), 1))
                p.drawLine(cx - 40 + gx_off, gy, cx + 40 + gx_off, gy)

        # ── Head body ─────────────────────────────────────────────────────
        face_grad = QLinearGradient(cx - head_w//2, cy - head_h//2,
                                    cx + head_w//2, cy + head_h//2)
        face_grad.setColorAt(0, base_color.lighter(145))
        face_grad.setColorAt(1, base_color.darker(110))
        p.setBrush(QBrush(face_grad))

        outline_pen_w = 3 if self.state == "error" else 2
        p.setPen(QPen(base_color.lighter(190), outline_pen_w))

        sc = self._star_sc if self.state == "success" else 1.0
        hw2 = head_w * sc / 2
        hh2 = head_h * sc / 2
        p.drawRoundedRect(QRectF(cx - hw2, cy - hh2, hw2*2, hh2*2), 12, 12)

        # ── Neck ──────────────────────────────────────────────────────────
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(base_color.red(), base_color.green(),
                          base_color.blue(), 160))
        p.drawRect(cx - 8, cy + int(hh2), 16, 13)

        # ── Body / torso ──────────────────────────────────────────────────
        body_top = cy + int(hh2) + 13
        body_grad = QLinearGradient(cx - 30, body_top, cx + 30, body_top + 35)
        body_grad.setColorAt(0, base_color.darker(120))
        body_grad.setColorAt(1, base_color.darker(160))
        p.setBrush(QBrush(body_grad))
        p.setPen(QPen(base_color.lighter(140), 1))
        p.drawRoundedRect(QRectF(cx - 28, body_top, 56, 35), 6, 6)

        # Body panel light
        p.setPen(Qt.NoPen)
        panel_c = base_color.lighter(200)
        panel_c.setAlpha(80)
        p.setBrush(QBrush(panel_c))
        p.drawRoundedRect(QRectF(cx - 12, body_top + 7, 24, 8), 3, 3)

        # ── Antenna ───────────────────────────────────────────────────────
        ant_color = base_color.lighter(220) if self.state == "thinking" \
            else QColor("#aaaaaa")
        p.setPen(QPen(ant_color, 2))
        p.drawLine(cx, cy - int(hh2), cx, cy - int(hh2) - 20)
        tip_r = 7 if self.state == "thinking" and (self.frame // 6) % 2 == 0 else 5
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(ant_color))
        p.drawEllipse(QRectF(cx - tip_r, cy - int(hh2) - 20 - tip_r,
                             tip_r * 2, tip_r * 2))

        # ── Gear on head (working) ────────────────────────────────────────
        if self.state == "working":
            p.save()
            p.translate(cx, cy - int(hh2))
            p.rotate(self._gear_ang)
            teeth = 9
            gear_c = QColor("#ffedd5")
            p.setPen(QPen(gear_c, 1))
            p.setBrush(QBrush(gear_c))
            path = QPainterPath()
            for i in range(teeth * 2):
                angle = math.radians(i * 180 / teeth)
                r_tooth = 10 if i % 2 == 0 else 6
                x_t = math.cos(angle) * r_tooth
                y_t = math.sin(angle) * r_tooth
                if i == 0:
                    path.moveTo(x_t, y_t)
                else:
                    path.lineTo(x_t, y_t)
            path.closeSubpath()
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1a1a2e"))
            p.drawEllipse(QRectF(-4, -4, 8, 8))
            p.restore()

        # ── Thinking cloud ────────────────────────────────────────────────
        if self.state == "thinking":
            cloud_color = QColor(200, 220, 255, 100)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(cloud_color))
            for i, (ox, oy, r) in enumerate([
                (-12, -10, 12), (0, -16, 14), (12, -10, 12), (0, -8, 10)
            ]):
                p.drawEllipse(QRectF(cx + ox - r, cy - int(hh2) - 30 + oy - r,
                                     r*2, r*2))

        # ── Eyes ──────────────────────────────────────────────────────────
        p.setPen(Qt.NoPen)
        eye_l_x = cx - 16
        eye_r_x = cx + 6
        eye_y   = cy - 10

        if eye_style == "normal":
            blink = (self.frame // 25) % 12 == 0
            if blink:
                p.setBrush(Qt.white)
                p.drawRect(int(eye_l_x), int(eye_y) + 5, 12, 3)
                p.drawRect(int(eye_r_x), int(eye_y) + 5, 12, 3)
            else:
                p.setBrush(Qt.white)
                p.drawEllipse(QRectF(eye_l_x, eye_y, 12, 16))
                p.drawEllipse(QRectF(eye_r_x, eye_y, 12, 16))
                p.setBrush(QColor("#1a1a2e"))
                p.drawEllipse(QRectF(eye_l_x + 3, eye_y + 4, 6, 8))
                p.drawEllipse(QRectF(eye_r_x + 3, eye_y + 4, 6, 8))
                # Pupil highlight
                p.setBrush(Qt.white)
                p.drawEllipse(QRectF(eye_l_x + 7, eye_y + 4, 3, 3))
                p.drawEllipse(QRectF(eye_r_x + 7, eye_y + 4, 3, 3))

        elif eye_style == "scan":
            p.setBrush(QColor("#001a20"))
            p.drawRect(cx - 28, int(eye_y), 56, 8)
            p.setBrush(QColor("#e0f7fa"))
            p.drawRect(cx - 28, int(eye_y), 56, 2)
            scan_c = QColor(0, 255, 200, 200)
            p.setBrush(QBrush(scan_c))
            p.drawRect(cx - 28 + int(self._scan_x), int(eye_y) + 1, 8, 6)

        elif eye_style == "dots":
            for i, off in enumerate([-18, 0, 18]):
                phase = (self.frame + i * 10) % 30
                dot_y = eye_y - 8 - (6 if phase < 15 else 0)
                alpha = 255 if phase < 15 else 120
                p.setBrush(QColor(200, 220, 255, alpha))
                p.drawEllipse(QRectF(cx + off - 5, dot_y, 10, 10))

        elif eye_style == "busy":
            p.setBrush(Qt.white)
            p.drawEllipse(QRectF(eye_l_x, eye_y, 12, 16))
            p.drawEllipse(QRectF(eye_r_x, eye_y, 12, 16))
            p.setBrush(QColor("#f97316"))
            for ex, ey in [(eye_l_x, eye_y), (eye_r_x, eye_y)]:
                angle = math.radians(self._gear_ang)
                px = ex + 6 + math.cos(angle) * 3.5
                py = ey + 8 + math.sin(angle) * 3.5
                p.drawEllipse(QRectF(px - 3.5, py - 3.5, 7, 7))

        elif eye_style == "cross":
            p.setPen(QPen(QColor("#ff2222"), 3))
            for ex in [eye_l_x + 6, eye_r_x + 6]:
                eyc = eye_y + 8
                p.drawLine(int(ex)-5, int(eyc)-5, int(ex)+5, int(eyc)+5)
                p.drawLine(int(ex)+5, int(eyc)-5, int(ex)-5, int(eyc)+5)
            p.setPen(Qt.NoPen)

        elif eye_style == "star":
            p.setPen(QPen(QColor("#facc15"), 2))
            for angle in range(0, 360, 45):
                r = math.radians(angle + self.frame * 2)
                x1 = cx + math.cos(r) * 8
                y1 = cy + math.sin(r) * 8
                x2 = cx + math.cos(r) * 26
                y2 = cy + math.sin(r) * 26
                p.drawLine(int(x1), int(y1), int(x2), int(y2))
            p.setPen(Qt.NoPen)

        # ── Mouth ─────────────────────────────────────────────────────────
        mouth_y = cy + 18
        p.setPen(QPen(Qt.white, 2))
        p.setBrush(Qt.NoBrush)
        if self.state == "error":
            p.drawArc(QRectF(cx - 14, mouth_y, 28, 12), 0, -180 * 16)
        elif self.state == "success":
            p.drawArc(QRectF(cx - 18, mouth_y - 6, 36, 18), 0, -180 * 16)
        elif self.state == "thinking":
            p.drawArc(QRectF(cx - 8, mouth_y + 2, 16, 8), 0, -180 * 16)
        else:
            p.drawLine(cx - 12, int(mouth_y) + 6,
                       cx + 12, int(mouth_y) + 6)

        # ── Writing arm ───────────────────────────────────────────────────
        if self.state == "writing":
            arm_swing = int(math.sin(math.radians(self.frame * 7)) * 10)
            p.setPen(QPen(base_color.lighter(170), 3))
            p.drawLine(cx + 28, body_top + 10,
                       cx + 48, body_top + 25 + arm_swing)
            p.setBrush(QColor("#fffde7"))
            p.setPen(Qt.NoPen)
            p.drawRect(cx + 46, body_top + 23 + arm_swing, 8, 4)
            # Finger dots
            for fi in range(3):
                fdot_x = cx + 48 + fi * 3
                fdot_y = body_top + 29 + arm_swing + int(
                    math.sin(math.radians(self.frame * 10 + fi * 120)) * 2)
                p.setBrush(base_color.lighter(180))
                p.drawEllipse(QRectF(fdot_x, fdot_y, 3, 3))

        # ── State label banner ────────────────────────────────────────────
        banner_y = self.H - 22
        banner_grad = QLinearGradient(0, banner_y, self.W, banner_y + 18)
        banner_grad.setColorAt(0, QColor(base_color.red(), base_color.green(),
                                         base_color.blue(), 180))
        banner_grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(banner_grad))
        p.setPen(Qt.NoPen)
        p.drawRect(0, banner_y, self.W, 18)

        # Label text
        p.setPen(QPen(base_color.lighter(200), 1))
        font = QFont("Consolas", 7, QFont.Bold)
        p.setFont(font)
        p.drawText(QRectF(0, banner_y + 1, self.W, 16),
                   Qt.AlignCenter,
                   f"{self.label} · {self.state.upper()}")


# ═══════════════════════════════════════════════════════════════════════════
#  ThoughtChannel  — single scrolling thought stream
# ═══════════════════════════════════════════════════════════════════════════
class ThoughtChannel(QWidget):
    """One scrollable column of thoughts."""

    def __init__(self, channel_name: str, parent=None):
        super().__init__(parent)
        self.channel_name = channel_name
        self._paused = False
        cfg = CHANNEL_COLORS.get(channel_name, DEFAULT_CHANNEL)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        header = QLabel(f"◈ {channel_name.upper()}")
        header.setStyleSheet(
            f"color:{cfg['header']};font-weight:bold;font-size:13px;"
            f"background:{cfg['bg']};padding:5px;border-radius:4px;"
        )
        header.setAlignment(Qt.AlignCenter)
        lay.addWidget(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self.pause_btn = QPushButton("⏸")
        self.pause_btn.setFixedWidth(34)
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause)
        toolbar.addWidget(self.pause_btn)

        self.clear_btn = QPushButton("🗑")
        self.clear_btn.setFixedWidth(34)
        toolbar.addWidget(self.clear_btn)

        self.count_label = QLabel("0")
        self.count_label.setStyleSheet(
            f"color:{cfg['header']};font-size:10px;")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch()
        lay.addLayout(toolbar)

        self._display = QPlainTextEdit()
        self._display.setReadOnly(True)
        self._display.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self._display.setStyleSheet(
            f"background:{cfg['bg']};color:{cfg['text']};"
            f"border:1px solid {cfg['header']}44;font-size:11px;"
        )
        lay.addWidget(self._display)

        self.clear_btn.clicked.connect(self._clear)
        self._entry_count = 0

    def _on_pause(self, checked: bool):
        self._paused = checked
        self.pause_btn.setText("▶" if checked else "⏸")

    def _clear(self):
        self._display.clear()
        self._entry_count = 0
        self.count_label.setText("0")

    def append(self, text: str, prefix: str = ""):
        if self._paused:
            return
        cfg = CHANNEL_COLORS.get(self.channel_name, DEFAULT_CHANNEL)
        ts = datetime.now().strftime("%H:%M:%S")
        safe = (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
        label = f"[{prefix}] " if prefix else ""
        html = (
            f'<span style="color:{cfg["header"]};font-size:10px;">[{ts}]</span> '
            f'<span style="color:{cfg["header"]}88;">{label}</span>'
            f'<span style="color:{cfg["text"]};">{safe}</span>'
        )
        self._display.appendHtml(html)
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._entry_count += 1
        self.count_label.setText(str(self._entry_count))


# ═══════════════════════════════════════════════════════════════════════════
#  Separated thought windows  — one per channel
# ═══════════════════════════════════════════════════════════════════════════
def _make_channel_window(channel_name: str, parent=None) -> QDialog:
    """
    Create a standalone floating window for a single thought channel.
    Returns (dialog, channel_widget).
    """
    cfg = CHANNEL_COLORS.get(channel_name, DEFAULT_CHANNEL)
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"◈ {channel_name} Thoughts")
    dlg.resize(680, 520)
    dlg.setStyleSheet(
        f"QDialog{{background:{cfg['bg']};color:{cfg['text']};}}"
        f"QPushButton{{background:{cfg['bg']};color:{cfg['header']};"
        f"border:1px solid {cfg['header']};padding:4px;border-radius:3px;}}"
        f"QPushButton:hover{{background:{cfg['header']};color:#000;}}"
    )
    lay = QVBoxLayout(dlg)

    # Global controls
    ctrl = QHBoxLayout()
    pause_all  = QPushButton("⏸ Pause")
    resume_all = QPushButton("▶ Resume")
    clear_all  = QPushButton("🗑 Clear")
    for btn in (pause_all, resume_all, clear_all):
        ctrl.addWidget(btn)
    ctrl.addStretch()
    lay.addLayout(ctrl)

    ch = ThoughtChannel(channel_name)
    lay.addWidget(ch)

    pause_all.clicked.connect(lambda: ch.pause_btn.setChecked(True))
    resume_all.clicked.connect(lambda: ch.pause_btn.setChecked(False))
    clear_all.clicked.connect(ch._clear)

    dlg._channel = ch  # attach for external access
    return dlg


class ManagerWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        dlg = _make_channel_window("Manager", parent)
        # Copy the built window's layout — we ARE the window
        self.setWindowTitle(dlg.windowTitle())
        self.resize(680, 520)
        self._channel = ThoughtChannel("Manager")
        lay = QVBoxLayout(self)
        ctrl = QHBoxLayout()
        for label, slot in [("⏸ Pause",  lambda: self._channel.pause_btn.setChecked(True)),
                             ("▶ Resume", lambda: self._channel.pause_btn.setChecked(False)),
                             ("🗑 Clear",  self._channel._clear)]:
            b = QPushButton(label); b.clicked.connect(slot); ctrl.addWidget(b)
        ctrl.addStretch()
        lay.addLayout(ctrl)
        lay.addWidget(self._channel)

    def append(self, text: str, prefix: str = ""):
        self._channel.append(text, prefix)


class AgentWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("◈ Agent Thoughts")
        self.resize(680, 520)
        self._channel = ThoughtChannel("Agent")
        lay = QVBoxLayout(self)
        ctrl = QHBoxLayout()
        for label, slot in [("⏸ Pause",  lambda: self._channel.pause_btn.setChecked(True)),
                             ("▶ Resume", lambda: self._channel.pause_btn.setChecked(False)),
                             ("🗑 Clear",  self._channel._clear)]:
            b = QPushButton(label); b.clicked.connect(slot); ctrl.addWidget(b)
        ctrl.addStretch()
        lay.addLayout(ctrl)
        lay.addWidget(self._channel)

    def append(self, text: str, prefix: str = ""):
        self._channel.append(text, prefix)


class CommsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("◈ Comms Channel")
        self.resize(680, 520)
        self._channel = ThoughtChannel("Comms")
        lay = QVBoxLayout(self)
        ctrl = QHBoxLayout()
        for label, slot in [("⏸ Pause",  lambda: self._channel.pause_btn.setChecked(True)),
                             ("▶ Resume", lambda: self._channel.pause_btn.setChecked(False)),
                             ("🗑 Clear",  self._channel._clear)]:
            b = QPushButton(label); b.clicked.connect(slot); ctrl.addWidget(b)
        ctrl.addStretch()
        lay.addLayout(ctrl)
        lay.addWidget(self._channel)

    def append(self, text: str, prefix: str = ""):
        self._channel.append(text, prefix)


# ═══════════════════════════════════════════════════════════════════════════
#  SummaryWindow  — dedicated summary channel window
# ═══════════════════════════════════════════════════════════════════════════
class SummaryWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("◈ Summaries")
        self.resize(720, 560)
        cfg = CHANNEL_COLORS["Summary"]
        self.setStyleSheet(
            f"QDialog{{background:{cfg['bg']};color:{cfg['text']};}}"
        )
        self._channel = ThoughtChannel("Summary")
        lay = QVBoxLayout(self)
        ctrl = QHBoxLayout()
        for label, slot in [("⏸ Pause",  lambda: self._channel.pause_btn.setChecked(True)),
                             ("▶ Resume", lambda: self._channel.pause_btn.setChecked(False)),
                             ("🗑 Clear",  self._channel._clear)]:
            b = QPushButton(label); b.clicked.connect(slot); ctrl.addWidget(b)
        ctrl.addStretch()
        lay.addLayout(ctrl)
        lay.addWidget(self._channel)

    def append_summary(self, text: str):
        self._channel.append(text)


# ═══════════════════════════════════════════════════════════════════════════
#  QuadThoughtPanel — backward-compat wrapper + pop-out buttons
# ═══════════════════════════════════════════════════════════════════════════
class QuadThoughtPanel(QDialog):
    """
    Kept for backward compatibility.
    Now shows compact channel strips with a 'Pop Out' button for each.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Thought Overview — All Channels")
        self.resize(1400, 640)

        # Create the four separate windows
        self._manager_win  = ManagerWindow(parent)
        self._agent_win    = AgentWindow(parent)
        self._comms_win    = CommsWindow(parent)
        self._summary_win  = SummaryWindow(parent)

        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("◈ All channels — click 'Pop Out' to open each in its own window"))
        top.addStretch()
        for label, win in [
            ("Manager",  self._manager_win),
            ("Agent",    self._agent_win),
            ("Comms",    self._comms_win),
            ("Summary",  self._summary_win),
        ]:
            btn = QPushButton(f"🗗 {label}")
            btn.setFixedWidth(110)
            btn.clicked.connect(win.show)
            btn.clicked.connect(win.raise_)
            top.addWidget(btn)
        lay.addLayout(top)

        # Compact inline preview strips (splitter)
        splitter = QSplitter(Qt.Horizontal)
        for win, name in [
            (self._manager_win, "Manager"),
            (self._agent_win,   "Agent"),
            (self._comms_win,   "Comms"),
            (self._summary_win, "Summary"),
        ]:
            preview = ThoughtChannel(name)
            # Wire: when the win's channel gets a message, mirror to preview
            # (done via route() below)
            setattr(self, f"_preview_{name.lower()}", preview)
            splitter.addWidget(preview)
        splitter.setSizes([350, 350, 350, 350])
        lay.addWidget(splitter)

    # ── Routing ──────────────────────────────────────────────────────────────
    def route(self, channel: str, text: str, prefix: str = ""):
        """Route a message to the correct channel(s)."""
        channels_map = {
            "Manager":  [self._manager_win, self._preview_manager],
            "Agent":    [self._agent_win,   self._preview_agent],
            "Comms":    [self._comms_win,   self._preview_comms],
            "Summary":  [self._summary_win, self._preview_summary],
        }
        if channel == "System":
            for target in [self._manager_win, self._preview_manager,
                           self._agent_win,   self._preview_agent]:
                target.append(text, "SYS")
        elif channel in channels_map:
            for target in channels_map[channel]:
                target.append(text, prefix)
        else:
            self._manager_win.append(text, channel)
            self._preview_manager.append(text, channel)

    def route_summary(self, text: str):
        self._summary_win.append_summary(text)
        self._preview_summary.append(text)

    # Legacy alias helpers for main.py
    @property
    def manager_channel(self):  return self._preview_manager
    @property
    def agent_channel(self):    return self._preview_agent
    @property
    def comms_channel(self):    return self._preview_comms
    @property
    def summary_channel(self):  return self._preview_summary

    def _pause_all(self):
        for ch in (self._preview_manager, self._preview_agent,
                   self._preview_comms, self._preview_summary):
            ch.pause_btn.setChecked(True)

    def _resume_all(self):
        for ch in (self._preview_manager, self._preview_agent,
                   self._preview_comms, self._preview_summary):
            ch.pause_btn.setChecked(False)

    def _clear_all(self):
        for ch in (self._preview_manager, self._preview_agent,
                   self._preview_comms, self._preview_summary):
            ch._clear()


# ═══════════════════════════════════════════════════════════════════════════
#  UnifiedChatWidget — replaces split ManagerChatWidget + SummarizerChatWindow
# ═══════════════════════════════════════════════════════════════════════════
class UnifiedChatWidget(QDialog):
    """
    Single conversational surface for Manager + Summarizer.
    Uses clear visual layers:
      - user bubble
      - assistant bubble
      - system/heartbeat block
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assistant Chat")
        self.resize(900, 620)
        self.setStyleSheet(
            "QDialog{background:#0a0a1a;color:#e0e0e0;}"
            "QPlainTextEdit{background:#050510;color:#e0e0e0;"
            " border:1px solid #333;font-size:12px;}"
            "QLineEdit{background:#050510;color:#e0e0e0;"
            " border:1px solid #00b0ff;padding:4px;}"
            "QPushButton{background:#050510;color:#00b0ff;"
            " border:1px solid #00b0ff;padding:5px;border-radius:3px;}"
            "QPushButton:hover{background:#00b0ff;color:#000;}"
            "QLabel{color:#b3e5fc;}"
            "QComboBox{background:#050510;color:#b3e5fc;"
            " border:1px solid #00b0ff;}"
            "QGroupBox{border:1px solid #00b0ff44;border-radius:4px;"
            " margin-top:8px;padding-top:8px;color:#00b0ff;}"
        )

        self.on_send = None
        self.on_strategy_change = None

        self._build_ui()

    def _build_ui(self):
        main_lay = QHBoxLayout(self)

        left = QVBoxLayout()

        # Header row
        header_row = QHBoxLayout()
        self._sprite = AgentSprite(label="Assistant")
        self._sprite.set_state("idle")
        header_row.addWidget(self._sprite)

        info_col = QVBoxLayout()
        self._title_lbl = QLabel("Assistant Chat")
        self._title_lbl.setStyleSheet(
            "font-size:16px;font-weight:bold;color:#00b0ff;")
        info_col.addWidget(self._title_lbl)

        self._status_lbl = QLabel("● Idle")
        self._status_lbl.setStyleSheet("font-size:11px;color:#22c55e;")
        info_col.addWidget(self._status_lbl)

        strategy_row = QHBoxLayout()
        strategy_row.addWidget(QLabel("Depth:"))
        self._strategy_combo = QComboBox()
        self._strategy_combo.addItems(["brief", "standard", "detailed"])
        self._strategy_combo.setCurrentText("standard")
        self._strategy_combo.currentTextChanged.connect(self._on_strategy_change)
        strategy_row.addWidget(self._strategy_combo)
        strategy_row.addStretch()
        info_col.addLayout(strategy_row)
        info_col.addStretch()

        header_row.addLayout(info_col)
        header_row.addStretch()
        left.addLayout(header_row)

        # Chat display
        self._display = QPlainTextEdit()
        self._display.setReadOnly(True)
        left.addWidget(self._display, stretch=1)

        # Quick actions
        quick_grp = QGroupBox("Quick actions")
        quick_lay = QHBoxLayout(quick_grp)
        for label, msg in [
            ("📊 Status", "What is the current status and last action taken?"),
            ("🔍 Scan", "Scan the research folder and summarise key findings."),
            ("⚡ Improve", "What is the single highest-impact improvement right now?"),
            ("🐛 Debug", "Check all files for bugs or error handling gaps."),
            ("💡 Suggest", "Based on what you've seen, what should I focus on?"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _, m=msg: self._send_quick(m))
            quick_lay.addWidget(b)
        quick_lay.addStretch()
        left.addWidget(quick_grp)

        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Message the assistant… (Enter to send)")
        self._input.returnPressed.connect(self.send)
        self._char_lbl = QLabel("0")
        self._char_lbl.setFixedWidth(36)
        self._char_lbl.setStyleSheet("color:#888;font-size:10px;")
        self._input.textChanged.connect(
            lambda t: self._char_lbl.setText(str(len(t))))
        send_btn = QPushButton("Send ➤")
        send_btn.clicked.connect(self.send)
        input_row.addWidget(self._input)
        input_row.addWidget(self._char_lbl)
        input_row.addWidget(send_btn)
        left.addLayout(input_row)

        main_lay.addLayout(left, stretch=3)

        # Right sidebar: topics + stats
        right = QVBoxLayout()
        topics_grp = QGroupBox("🔥 Top Topics")
        topics_grp.setFixedWidth(180)
        self._topics_lay = QVBoxLayout(topics_grp)
        self._topics_labels = []
        for _ in range(10):
            lbl = QLabel("—")
            lbl.setStyleSheet("font-size:10px;color:#80cbc4;")
            self._topics_lay.addWidget(lbl)
            self._topics_labels.append(lbl)
        self._topics_lay.addStretch()
        right.addWidget(topics_grp)

        stats_grp = QGroupBox("📊 Stats")
        stats_grp.setFixedWidth(180)
        self._stats_lbl = QLabel("—")
        self._stats_lbl.setWordWrap(True)
        self._stats_lbl.setStyleSheet("font-size:9px;color:#80cbc4;")
        right.addWidget(stats_grp)
        right.addStretch()

        main_lay.addLayout(right, stretch=0)

    def set_status(self, status: str, _task: str = ""):
        colours = {
            "Idle": "#22c55e",
            "Summarizing": "#f97316",
            "Chatting": "#00b0ff",
            "Paused": "#888888",
        }
        col = colours.get(status, "#aaaaaa")
        self._status_lbl.setText(f"● {status}")
        self._status_lbl.setStyleSheet(f"font-size:11px;color:{col};")
        state_map = {
            "Idle": "idle", "Summarizing": "thinking",
            "Chatting": "communicating", "Paused": "idle"
        }
        self._sprite.set_state(state_map.get(status, "idle"))

    def append_reply(self, label: str, text: str):
        """Append a message - notifications go to sidebar, chat stays clean."""
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        # Route notifications to sidebar, chat to main display
        # Check both label and text (text contains [Heartbeat] for Manager messages)
        is_notification = any(x in label or f"[{x}]" in text for x in ["Heartbeat", "Worker", "Coordinator",
                          "Result", "JobSearch", "Analyst", "Coder", "Action", "Evaluator"])
        if "lifecycle" in text.lower() or ("opportunity" in text.lower() and "status" in text.lower()):
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#00b0ff;font-weight:bold;">📊 Opportunity Update:</span><br>'
                f'<span style="color:#b3e5fc;">{safe}</span><br>'
            )
            self._append_notification(label, ts, safe)
            sb = self._display.verticalScrollBar()
            sb.setValue(sb.maximum())
            return
        if is_notification:
            self._append_notification(label, ts, safe)
        elif "Manager" in label:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#bb86fc;font-weight:bold;">{label}:</span><br>'
                f'<span style="color:#d4aaff;">{safe}</span><br>'
            )
        elif "Summarizer" in label or "Answer" in label:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#00b0ff;font-weight:bold;">🤖 Assistant:</span><br>'
                f'<span style="color:#b3e5fc;">{safe}</span><br>'
            )
        else:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#03dac6;font-weight:bold;">{label}:</span> '
                f'<span style="color:#e0e0e0;">{safe}</span>'
            )
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _append_notification(self, label: str, ts: str, text: str):
        """Append agent notifications to the collapsible sidebar panel."""
        if hasattr(self, '_notifications_list') and self._notifications_list.isVisible():
            self._notifications_list.addItem(f"[{ts}] {label}: {text}")
            sb = self._notifications_list.verticalScrollBar()
            sb.setValue(sb.maximum())

    def append_system(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        self._display.appendHtml(
            f'<span style="color:#444;">[{ts}] ⚙ System:</span> '
            f'<span style="color:#666;font-size:11px;">{safe}</span>'
        )
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def append_manager(self, text: str, trigger: str = ""):
        label = f"[{trigger}] " if trigger else ""
        self.append_reply("Manager" + label, text)

    def append_you(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        self._display.appendHtml(
            f'<span style="color:#888;">[{ts}]</span> '
            f'<span style="color:#03dac6;font-weight:bold;">You:</span> '
            f'<span style="color:#e0f7fa;">{safe}</span>'
        )
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def load_history(self, history: List[dict]):
        for turn in history[-30:]:
            ts_str = datetime.fromtimestamp(turn["ts"]).strftime("%H:%M:%S")
            role = turn.get("role", "user")
            safe = (turn["text"]
                    .replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
            if role == "user":
                self._display.appendHtml(
                    f'<span style="color:#555;">[{ts_str}]</span> '
                    f'<span style="color:#03dac680;font-weight:bold;">You:</span> '
                    f'<span style="color:#b0bec5;">{safe}</span>'
                )
            else:
                self._display.appendHtml(
                    f'<span style="color:#555;">[{ts_str}]</span> '
                    f'<span style="color:#00b0ff80;font-weight:bold;">🤖 Summarizer:</span><br>'
                    f'<span style="color:#78909c;">{safe}</span><br>'
                )

    def update_topics(self, topics: List[dict]):
        for i, lbl in enumerate(self._topics_labels):
            if i < len(topics):
                t = topics[i]
                lbl.setText(f"#{t['topic']} ({t['count']})")
            else:
                lbl.setText("—")

    def update_stats(self, stats: dict):
        lines = [
            f"Summaries: {stats.get('total_summaries', 0)}",
            f"Samples: {stats.get('speech_samples', 0)}",
            f"Style: {stats.get('formality', '—')}",
            f"Avg len: {stats.get('avg_sentence_len', 0)} wds",
            f"Strategy: {stats.get('strategy', '—')}",
        ]
        for lbl in getattr(self, "_stats_labels", []):
            lbl.deleteLater()
        self._stats_labels = []
        for line in lines:
            lbl = QLabel(line)
            lbl.setStyleSheet("font-size:9px;color:#80cbc4;")
            self._stats_lay = getattr(self, "_stats_lay", None)
            if self._stats_lay is None:
                return
            self._stats_lay.addWidget(lbl)
            self._stats_labels.append(lbl)
        if self._stats_lay is not None:
            self._stats_lay.addStretch()

    # ─────────────────────────────────────────────────────────────────────
    #  Internal slots
    # ─────────────────────────────────────────────────────────────────────
    def _send_quick(self, task: str):
        self._input.setText(task)
        self.send()

    def send(self):
        text = self._input.text().strip()
        if not text:
            return
        self.append_you(text)
        if self.on_send:
            self.on_send(text)
        self._input.clear()

    def _on_strategy_change(self, strategy: str):
        if self.on_strategy_change:
            self.on_strategy_change(strategy)


# ═══════════════════════════════════════════════════════════════════════════
#  SummarizerWindow  — dedicated summarizer output (legacy compat)
# ═══════════════════════════════════════════════════════════════════════════
class SummarizerWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Summarizer Output")
        self.resize(620, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.text_display = QPlainTextEdit()
        self.text_display.setReadOnly(True)
        self.text_display.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text_display.setStyleSheet("font-size:12px;")
        lay.addWidget(self.text_display)

        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.text_display.clear)
        btn_row.addWidget(self.clear_btn)
        self.copy_btn = QPushButton("Copy Last")
        self.copy_btn.clicked.connect(self._copy_last)
        btn_row.addWidget(self.copy_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        self.last_summary = ""

    def append_summary(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.text_display.appendPlainText(f"[{ts}] {text}")
        self.last_summary = text
        sb = self.text_display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _copy_last(self):
        if self.last_summary:
            QApplication.clipboard().setText(self.last_summary)


# ═══════════════════════════════════════════════════════════════════════════
#  SummarizerChatWindow — rich chat UI for human ↔ summarizer
# ═══════════════════════════════════════════════════════════════════════════
class SummarizerChatWindow(QDialog):
    """
    Full-featured chat window for the Summarizer agent.

    Features:
      - Chat display with colour-coded messages
      - Strategy selector (brief / standard / detailed)
      - Speech style indicator badge
      - Top-topics sidebar
      - Quick-action shortcuts
      - Connected to SummarizerThread via signals
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🤖 Summarizer Chat")
        self.resize(900, 620)
        self.setStyleSheet(
            "QDialog{background:#0a0a1a;color:#b3e5fc;}"
            "QPlainTextEdit{background:#050510;color:#b3e5fc;"
            " border:1px solid #00b0ff44;font-size:12px;}"
            "QLineEdit{background:#050510;color:#b3e5fc;"
            " border:1px solid #00b0ff;padding:4px;}"
            "QPushButton{background:#050510;color:#00b0ff;"
            " border:1px solid #00b0ff;padding:5px;border-radius:3px;}"
            "QPushButton:hover{background:#00b0ff;color:#000;}"
            "QLabel{color:#b3e5fc;}"
            "QComboBox{background:#050510;color:#b3e5fc;"
            " border:1px solid #00b0ff;}"
            "QGroupBox{border:1px solid #00b0ff44;border-radius:4px;"
            " margin-top:8px;padding-top:8px;color:#00b0ff;}"
        )

        self.on_send = None          # callback: fn(text: str)
        self.on_strategy_change = None  # callback: fn(strategy: str)

        self._build_ui()

    def _build_ui(self):
        main_lay = QHBoxLayout(self)

        # ── Left: chat area ───────────────────────────────────────────────
        left = QVBoxLayout()

        # Header with sprite + style badge
        header_row = QHBoxLayout()
        self._sprite = AgentSprite(label="Summarizer")
        self._sprite.set_state("idle")
        header_row.addWidget(self._sprite)

        info_col = QVBoxLayout()
        self._title_lbl = QLabel("Summarizer Chat")
        self._title_lbl.setStyleSheet(
            "font-size:16px;font-weight:bold;color:#00b0ff;")
        info_col.addWidget(self._title_lbl)

        self._style_badge = QLabel("Style: learning…")
        self._style_badge.setStyleSheet(
            "font-size:10px;color:#80cbc4;background:#001a2a;"
            "padding:3px 6px;border-radius:8px;border:1px solid #00b0ff44;")
        info_col.addWidget(self._style_badge)

        self._status_lbl = QLabel("● Idle")
        self._status_lbl.setStyleSheet("font-size:11px;color:#22c55e;")
        info_col.addWidget(self._status_lbl)

        strategy_row = QHBoxLayout()
        strategy_row.addWidget(QLabel("Summary depth:"))
        self._strategy_combo = QComboBox()
        self._strategy_combo.addItems(["brief", "standard", "detailed"])
        self._strategy_combo.setCurrentText("standard")
        self._strategy_combo.currentTextChanged.connect(self._on_strategy_change)
        strategy_row.addWidget(self._strategy_combo)
        strategy_row.addStretch()
        info_col.addLayout(strategy_row)
        info_col.addStretch()

        header_row.addLayout(info_col)
        header_row.addStretch()
        left.addLayout(header_row)

        # Chat display
        self._display = QPlainTextEdit()
        self._display.setReadOnly(True)
        left.addWidget(self._display, stretch=1)

        # Quick actions — NoFocus prevents Enter key from triggering these
        quick_grp = QGroupBox("Quick actions")
        quick_lay = QHBoxLayout(quick_grp)
        for label, msg in [
            ("📊 What's happening?", "Can you explain what the agents are doing right now?"),
            ("📋 Last summary",      "What was the last thing the agents did?"),
            ("🔥 Top topics",        "What topics have come up most frequently?"),
            ("💡 Suggest",           "Based on what you've seen, what should I focus on?"),
            ("📈 Stats",             "Show me your stats and speech pattern analysis."),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.setFocusPolicy(Qt.NoFocus)   # ← prevents Enter key triggering button
            b.clicked.connect(lambda _, m=msg: self._send_quick(m))
            quick_lay.addWidget(b)
        quick_lay.addStretch()
        left.addWidget(quick_grp)

        # Input
        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Chat with the Summarizer… (Enter to send)")
        self._input.returnPressed.connect(self.send)
        self._char_lbl = QLabel("0")
        self._char_lbl.setFixedWidth(36)
        self._char_lbl.setStyleSheet("color:#888;font-size:10px;")
        self._input.textChanged.connect(
            lambda t: self._char_lbl.setText(str(len(t))))
        send_btn = QPushButton("Send ➤")
        send_btn.clicked.connect(self.send)
        input_row.addWidget(self._input)
        input_row.addWidget(self._char_lbl)
        input_row.addWidget(send_btn)
        left.addLayout(input_row)

        main_lay.addLayout(left, stretch=3)

        # ── Right: topics sidebar ─────────────────────────────────────────
        right = QVBoxLayout()
        topics_grp = QGroupBox("🔥 Top Topics")
        topics_grp.setFixedWidth(180)
        self._topics_lay = QVBoxLayout(topics_grp)
        self._topics_labels: List[QLabel] = []
        for _ in range(10):
            lbl = QLabel("—")
            lbl.setStyleSheet("font-size:10px;color:#80cbc4;")
            self._topics_lay.addWidget(lbl)
            self._topics_labels.append(lbl)
        self._topics_lay.addStretch()
        right.addWidget(topics_grp)

        stats_grp = QGroupBox("📊 Stats")
        stats_grp.setFixedWidth(180)
        self._stats_lay = QVBoxLayout(stats_grp)
        self._stats_lbl = QLabel("—")
        self._stats_lbl.setWordWrap(True)
        self._stats_lbl.setStyleSheet("font-size:9px;color:#80cbc4;")
        self._stats_lay.addWidget(self._stats_lbl)
        right.addWidget(stats_grp)
        right.addStretch()

        main_lay.addLayout(right, stretch=0)

    # ─────────────────────────────────────────────────────────────────────
    #  Public API (called from MainWindow)
    # ─────────────────────────────────────────────────────────────────────
    def set_status(self, status: str, _task: str = ""):
        colours = {
            "Idle":        "#22c55e",
            "Summarizing": "#f97316",
            "Chatting":    "#00b0ff",
            "Paused":      "#888888",
        }
        col = colours.get(status, "#aaaaaa")
        self._status_lbl.setText(f"● {status}")
        self._status_lbl.setStyleSheet(f"font-size:11px;color:{col};")
        state_map = {
            "Idle": "idle", "Summarizing": "thinking",
            "Chatting": "communicating", "Paused": "idle"
        }
        self._sprite.set_state(state_map.get(status, "idle"))

    def set_style_badge(self, description: str):
        self._style_badge.setText(f"Style: {description[:60]}")

    def update_topics(self, topics: List[dict]):
        for i, lbl in enumerate(self._topics_labels):
            if i < len(topics):
                t = topics[i]
                lbl.setText(f"#{t['topic']} ({t['count']})")
            else:
                lbl.setText("—")

    def update_stats(self, stats: dict):
        lines = [
            f"Summaries: {stats.get('total_summaries', 0)}",
            f"Samples: {stats.get('speech_samples', 0)}",
            f"Style: {stats.get('formality', '—')}",
            f"Avg len: {stats.get('avg_sentence_len', 0)} wds",
            f"Strategy: {stats.get('strategy', '—')}",
        ]
        self._stats_lbl.setText("\n".join(lines))

    def load_history(self, history: List[dict]):
        """Pre-populate display with saved chat history."""
        for turn in history[-30:]:
            ts_str = datetime.fromtimestamp(turn["ts"]).strftime("%H:%M:%S")
            role = turn.get("role", "user")
            safe = (turn["text"]
                    .replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
            if role == "user":
                self._display.appendHtml(
                    f'<span style="color:#555;">[{ts_str}]</span> '
                    f'<span style="color:#03dac680;font-weight:bold;">You:</span> '
                    f'<span style="color:#b0bec5;">{safe}</span>'
                )
            else:
                self._display.appendHtml(
                    f'<span style="color:#555;">[{ts_str}]</span> '
                    f'<span style="color:#00b0ff80;font-weight:bold;">🤖 Summarizer:</span><br>'
                    f'<span style="color:#78909c;">{safe}</span><br>'
                )

    # ─────────────────────────────────────────────────────────────────────
    #  Internal slots
    # ─────────────────────────────────────────────────────────────────────
    def _send_quick(self, task: str):
        self._input.setText(task)
        self.send()

    def send(self):
        text = self._input.text().strip()
        if not text:
            return
        self.append_you(text)
        if self._on_send:
            self._on_send(text)
        self._input.clear()

    def append_you(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        self._display.appendHtml(
            f'<span style="color:#888;">[{ts}]</span> '
            f'<span style="color:#03dac6;font-weight:bold;">You:</span> '
            f'<span style="color:#e0f7fa;">{safe}</span>'
        )
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def append_reply(self, label: str, text: str):
        """Append a message - notifications go to sidebar, chat stays clean."""
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        # Route notifications to sidebar, chat to main display
        # Check both label and text (text contains [Heartbeat] for Manager messages)
        is_notification = any(x in label or f"[{x}]" in text for x in ["Heartbeat", "Worker", "Coordinator",
                          "Result", "JobSearch", "Analyst", "Coder", "Action", "Evaluator"])
        if "lifecycle" in text.lower() or ("opportunity" in text.lower() and "status" in text.lower()):
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#00b0ff;font-weight:bold;">📊 Opportunity Update:</span><br>'
                f'<span style="color:#b3e5fc;">{safe}</span><br>'
            )
            self._append_notification(label, ts, safe)
            sb = self._display.verticalScrollBar()
            sb.setValue(sb.maximum())
            return
        if is_notification:
            self._append_notification(label, ts, safe)
        elif "Manager" in label:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#bb86fc;font-weight:bold;">{label}:</span><br>'
                f'<span style="color:#d4aaff;">{safe}</span><br>'
            )
        elif "Summarizer" in label or "Answer" in label:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#00b0ff;font-weight:bold;">🤖 Assistant:</span><br>'
                f'<span style="color:#b3e5fc;">{safe}</span><br>'
            )
        else:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#03dac6;font-weight:bold;">{label}:</span> '
                f'<span style="color:#e0e0e0;">{safe}</span>'
            )
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _append_notification(self, label: str, ts: str, text: str):
        """Append agent notifications to the collapsible sidebar panel."""
        if hasattr(self, '_notifications_list') and self._notifications_list.isVisible():
            self._notifications_list.addItem(f"[{ts}] {label}: {text}")
            sb = self._notifications_list.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_strategy_change(self, strategy: str):
        if self._on_strategy_change:
            self._on_strategy_change(strategy)


# ═══════════════════════════════════════════════════════════════════════════
#  AgentsTab — v4: chat + live info panel, no office scene
# ═══════════════════════════════════════════════════════════════════════════
class AgentsTab(QWidget):
    """
    Agents tab with animated sprites and roster controls.
    No 2D office scene — lightweight for earning-focused workflows.
    """

    def __init__(self, parent,
                 worker_sprite: "AgentSprite",
                 summarizer_sprite: "AgentSprite",
                 worker_status_signal,
                 summarizer_status_signal,
                 show_thoughts_cb,
                 show_chat_cb=None,
                 toggle_worker_pause_cb=None,
                 toggle_summarizer_pause_cb=None,
                 show_summarizer_window_cb=None,
                 heartbeat_interval=None,
                 on_send=None,
                 on_strategy_change=None,
                 show_manager_win_cb=None,
                 show_agent_win_cb=None,
                 show_comms_win_cb=None,
                 show_summary_win_cb=None,
                 show_summarizer_chat_cb=None):
        super().__init__(parent)

        self.worker_sprite    = worker_sprite
        self.summarizer_sprite = summarizer_sprite
        self.heartbeat_interval = heartbeat_interval

        self.show_thoughts_cb           = show_thoughts_cb
        self.show_chat_cb               = show_chat_cb or (lambda: None)
        self.toggle_worker_pause_cb     = toggle_worker_pause_cb or (lambda: None)
        self.toggle_summarizer_pause_cb = toggle_summarizer_pause_cb or (lambda: None)
        self.show_summarizer_window_cb  = show_summarizer_window_cb or (lambda: None)

        self._on_send = on_send
        self._on_strategy_change = on_strategy_change

        self._show_manager_win    = show_manager_win_cb    or (lambda: None)
        self._show_agent_win      = show_agent_win_cb      or (lambda: None)
        self._show_comms_win      = show_comms_win_cb      or (lambda: None)
        self._show_summary_win    = show_summary_win_cb    or (lambda: None)
        self._show_summarizer_chat = show_summarizer_chat_cb or (lambda: None)

        # Roster status labels  {role: QLabel}
        self._roster_labels: Dict[str, QLabel] = {}

        worker_status_signal.connect(self._on_worker_status)
        summarizer_status_signal.connect(self._on_summarizer_status)

        self._init_ui()

    # ── Build UI ──────────────────────────────────────────────────────

    def _init_ui(self):
        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(8, 8, 8, 8)
        vlay.setSpacing(8)

        # ── Top: embedded chat surface ───────────────────────────────
        chat_group = QGroupBox("Assistant Chat")
        chat_group.setStyleSheet(
            "QGroupBox{border:1px solid #00b0ff44;border-radius:4px;"
            " margin-top:8px;padding-top:8px;color:#00b0ff;}"
        )
        chat_lay = QVBoxLayout(chat_group)
        chat_lay.setContentsMargins(8, 8, 8, 8)
        chat_lay.setSpacing(6)

        self._display = QPlainTextEdit()
        self._display.setReadOnly(True)
        self._display.setStyleSheet(
            "background:#050510;color:#e0e0e0;font-size:12px;"
            "border:1px solid #333;"
        )
        self._display.setPlaceholderText("Assistant output will appear here…")
        self._display.setCenterOnScroll(True)
        chat_lay.addWidget(self._display, stretch=1)

        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Message the assistant… (Enter to send)")
        self._input.returnPressed.connect(self.send)
        self._char_lbl = QLabel("0")
        self._char_lbl.setFixedWidth(36)
        self._char_lbl.setStyleSheet("color:#888;font-size:10px;")
        self._input.textChanged.connect(
            lambda t: self._char_lbl.setText(str(len(t))))
        send_btn = QPushButton("Send ➤")
        send_btn.clicked.connect(self.send)
        input_row.addWidget(self._input)
        input_row.addWidget(self._char_lbl)
        input_row.addWidget(send_btn)
        chat_lay.addLayout(input_row)

        # ── Side panel: Collapsible notifications ─────────────────────
        self._notifications_toggle = QPushButton(" Notifications")
        self._notifications_toggle.setCheckable(True)
        self._notifications_toggle.setChecked(True)
        self._notifications_toggle.setStyleSheet(
            "QPushButton{border:1px solid #00b0ff44;border-radius:4px;"
            "padding:4px;font-size:10px;background:#1a1a2e;color:#00b0ff;}"
        )
        self._notifications_toggle.clicked.connect(self._toggle_notifications)

        notifications_group = QGroupBox("Agent Notifications")
        notifications_group.setStyleSheet(
            "QGroupBox{border:1px solid #00b0ff44;border-radius:4px;"
            " margin-top:8px;padding-top:8px;color:#00b0ff;}"
        )
        notifications_lay = QVBoxLayout(notifications_group)
        notifications_lay.setContentsMargins(4, 4, 4, 4)
        notifications_lay.setSpacing(4)
        notifications_lay.addWidget(self._notifications_toggle)

        self._notifications_list = QListWidget()
        self._notifications_list.setStyleSheet(
            "font-family:Consolas,Monaco,monospace;font-size:10px;"
            "background:#050510;color:#e0e0e0;border:1px solid #333;"
        )
        self._notifications_list.setAlternatingRowColors(True)
        self._notifications_list.setSelectionMode(QListWidget.NoSelection)
        notifications_lay.addWidget(self._notifications_list)

        # Use splitter to separate chat from notifications (resizable)
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(chat_group)
        main_splitter.addWidget(notifications_group)
        main_splitter.setStretchFactor(0, 3)  # Chat takes 3x space
        main_splitter.setStretchFactor(1, 1)  # Notifications smaller
        vlay.addWidget(main_splitter, stretch=2)

        # ── Bottom: live info + controls ─────────────────────────────
        bottom = QWidget()
        bottom_lay = QHBoxLayout(bottom)
        bottom_lay.setContentsMargins(0, 0, 0, 0)
        bottom_lay.setSpacing(8)

        # Agent roster with live status
        roster_grp = QGroupBox("🏢 Agent Roster")
        roster_grp.setMinimumWidth(220)
        roster_grp.setMaximumWidth(280)
        roster_inner = QVBoxLayout(roster_grp)
        roster_inner.setSpacing(4)
        for role in ("Manager", "Coordinator", "JobSearch", "Analyst", "Coder", "Summarizer"):
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setFixedWidth(14)
            dot.setStyleSheet("color:#22c55e;font-size:11px;")
            lbl = QLabel(f"{role}: idle")
            lbl.setStyleSheet("font-size:11px;color:#aaaaaa;")
            row.addWidget(dot)
            row.addWidget(lbl)
            row.addStretch()
            roster_inner.addLayout(row)
            self._roster_labels[role] = (dot, lbl)
        bottom_lay.addWidget(roster_grp, stretch=1)

        # Current actions panel
        actions_grp = QGroupBox("⚡ Current Actions")
        actions_grp.setMinimumWidth(260)
        actions_lay = QVBoxLayout(actions_grp)
        actions_lay.setSpacing(4)
        self._actions_list = QListWidget()
        self._actions_list.setStyleSheet(
            "font-family:Consolas,Monaco,monospace;font-size:11px;"
        )
        actions_lay.addWidget(self._actions_list)
        bottom_lay.addWidget(actions_grp, stretch=2)

        # Controls
        ctrl_grp = QGroupBox("⚙ Controls")
        ctrl_grp.setMinimumWidth(180)
        ctrl_lay = QVBoxLayout(ctrl_grp)
        ctrl_lay.setSpacing(6)

        self.last_heartbeat_label = QLabel("Last heartbeat: never")
        self.last_heartbeat_label.setStyleSheet("color:#888;font-size:10px;")
        self.last_heartbeat_label.setWordWrap(True)
        ctrl_lay.addWidget(self.last_heartbeat_label)

        self.heartbeat_label = QLabel(f"Heartbeat every {self.heartbeat_interval}s")
        self.heartbeat_label.setStyleSheet("color:#888;font-size:10px;")
        ctrl_lay.addWidget(self.heartbeat_label)

        self.worker_progress = QProgressBar()
        self.worker_progress.setRange(0, 0)
        self.worker_progress.setVisible(False)
        self.worker_progress.setMaximumHeight(8)
        ctrl_lay.addWidget(self.worker_progress)

        pause_row = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Pause Manager")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setFocusPolicy(Qt.NoFocus)
        self.pause_btn.toggled.connect(self.toggle_worker_pause_cb)
        pause_row.addWidget(self.pause_btn)

        self.summarizer_pause_btn = QPushButton("⏸ Pause Summarizer")
        self.summarizer_pause_btn.setCheckable(True)
        self.summarizer_pause_btn.setFocusPolicy(Qt.NoFocus)
        self.summarizer_pause_btn.toggled.connect(self.toggle_summarizer_pause_cb)
        pause_row.addWidget(self.summarizer_pause_btn)
        ctrl_lay.addLayout(pause_row)

        bottom_lay.addWidget(ctrl_grp, stretch=1)
        vlay.addWidget(bottom, stretch=1)

    # ── Signal handlers ───────────────────────────────────────────────

    def _on_worker_status(self, status: str, task: str):
        role = "Coder"
        state = "idle"
        if ":" in status:
            parts = status.split(":", 1)
            role  = parts[0].strip()
            state_str = parts[1].strip()
        else:
            state_str = status

        for key, mapped in self._STATUS_STATE_MAP.items():
            if key.lower() in state_str.lower():
                state = mapped
                break

        # Update roster/actions panels
        self._update_roster_label(role, state, task)
        self._append_action(f"{role}: {task or state}")

        # Progress bar visible when any worker is busy
        self.worker_progress.setVisible(
            state in ("working", "researching", "writing", "thinking"))

        # Update heartbeat label
        self.last_heartbeat_label.setText(
            f"Last action: {__import__('datetime').datetime.now().strftime('%H:%M:%S')}")

    def _on_summarizer_status(self, status: str, task: str):
        state_str = status.lower()
        state = "idle"
        for key, mapped in self._STATUS_STATE_MAP.items():
            if key.lower() in state_str:
                state = mapped
                break
        self._update_roster_label("Summarizer", state, task)
        self._append_action(f"Summarizer: {task or state}")

    # Keep the old status map for backward compat
    _STATUS_STATE_MAP = {
        "Working":     "working",
        "Thinking":    "thinking",
        "Researching": "researching",
        "Writing":     "writing",
        "Idle":        "idle",
        "Ready":       "idle",
        "Error":       "error",
        "Chatting":    "communicating",
    }

    def _update_roster_label(self, role: str, state: str, task: str = ""):
        if role not in self._roster_labels:
            return
        dot_lbl, text_lbl = self._roster_labels[role]
        color_map = {
            "working": "#f97316", "thinking": "#3b82f6",
            "researching": "#06b6d4", "writing": "#84cc16",
            "communicating": "#a855f7", "success": "#facc15",
            "error": "#ef4444", "idle": "#22c55e",
        }
        col = color_map.get(state, "#22c55e")
        dot_lbl.setStyleSheet(f"color:{col};font-size:10px;")
        task_short = f" — {task[:30]}" if task else ""
        text_lbl.setText(f"{role}: {state}{task_short}")

    def _append_action(self, text: str):
        ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{ts}] {text}")
        color_map = {
            "Manager": "#00ccff",
            "Summarizer": "#00b0ff",
            "JobSearch": "#f97316",
            "Analyst": "#06b6d4",
            "Coder": "#84cc16",
        }
        for role, color in color_map.items():
            if role in text:
                item.setForeground(QColor(color))
                break
        self._actions_list.addItem(item)
        if self._actions_list.count() > 200:
            self._actions_list.takeItem(0)

    # ── Embedded chat surface methods ───────────────────────────────

    def send(self):
        text = self._input.text().strip()
        if not text:
            return
        self.append_you(text)
        if self._on_send:
            self._on_send(text)
        self._input.clear()

    def append_you(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        self._display.appendHtml(
            f'<span style="color:#888;">[{ts}]</span> '
            f'<span style="color:#03dac6;font-weight:bold;">You:</span> '
            f'<span style="color:#e0f7fa;">{safe}</span>'
        )
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def append_reply(self, label: str, text: str):
        """Append a message - notifications go to sidebar, chat stays clean."""
        ts = datetime.now().strftime("%H:%M:%S")
        safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        # Route notifications to sidebar, chat to main display
        # Check both label and text (text contains [Heartbeat] for Manager messages)
        is_notification = any(x in label or f"[{x}]" in text for x in ["Heartbeat", "Worker", "Coordinator",
                          "Result", "JobSearch", "Analyst", "Coder", "Action", "Evaluator"])
        if "lifecycle" in text.lower() or ("opportunity" in text.lower() and "status" in text.lower()):
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#00b0ff;font-weight:bold;">📊 Opportunity Update:</span><br>'
                f'<span style="color:#b3e5fc;">{safe}</span><br>'
            )
            self._append_notification(label, ts, safe)
            sb = self._display.verticalScrollBar()
            sb.setValue(sb.maximum())
            return
        if is_notification:
            self._append_notification(label, ts, safe)
        elif "Manager" in label:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#bb86fc;font-weight:bold;">{label}:</span><br>'
                f'<span style="color:#d4aaff;">{safe}</span><br>'
            )
        elif "Summarizer" in label or "Answer" in label:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#00b0ff;font-weight:bold;">🤖 Assistant:</span><br>'
                f'<span style="color:#b3e5fc;">{safe}</span><br>'
            )
        else:
            self._display.appendHtml(
                f'<span style="color:#888;">[{ts}]</span> '
                f'<span style="color:#03dac6;font-weight:bold;">{label}:</span> '
                f'<span style="color:#e0e0e0;">{safe}</span>'
            )
        sb = self._display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _append_notification(self, label: str, ts: str, text: str):
        """Append agent notifications to the collapsible sidebar panel."""
        if hasattr(self, '_notifications_list') and self._notifications_list.isVisible():
            self._notifications_list.addItem(f"[{ts}] {label}: {text}")
            sb = self._notifications_list.verticalScrollBar()
            sb.setValue(sb.maximum())

    
    def _toggle_notifications(self):
        """Toggle the visibility of the notifications panel."""
        is_visible = self._notifications_list.isVisible()
        self._notifications_list.setVisible(not is_visible)
        self._notifications_toggle.setText(" Notifications" if is_visible else "Hide Notifications")
        self._notifications_toggle.setChecked(not is_visible)
# ── Layout editor callbacks (kept for compat but no-op without office) ──

    def set_layout_edit_allowed(self, allowed: bool):
        pass

    def set_layout_callbacks(self, save_cb=None, load_cb=None):
        pass

    # ── Compat methods ────────────────────────────────────────────────

    def set_heartbeat_interval(self, interval):
        self.heartbeat_interval = interval
        self.heartbeat_label.setText(f"Heartbeat every {interval}s")

    def set_pause_button_state(self, paused: bool):
        self.pause_btn.setChecked(paused)

    def set_summarizer_pause_button_state(self, paused: bool):
        self.summarizer_pause_btn.setChecked(paused)

    # Backward compat label attrs that main.py might reference
    @property
    def worker_status_label(self):
        _, lbl = self._roster_labels.get("Coder", (None, QLabel()))
        return lbl

    @property
    def worker_task_label(self):
        _, lbl = self._roster_labels.get("Manager", (None, QLabel()))
        return lbl

    @property
    def summarizer_status_label(self):
        _, lbl = self._roster_labels.get("Summarizer", (None, QLabel()))
        return lbl

    @property
    def summarizer_task_label(self):
        return self.heartbeat_label