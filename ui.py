from __future__ import annotations

import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from version import APP_NAME, __version__, version_string

import psutil

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QTextCharFormat, QTextCursor, QTextDocument, QTextDocumentFragment,
    QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
from core import config_home as _config_home
API_FILE   = _config_home.config_path()
CONFIG_DIR = API_FILE.parent

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    # Deep space black backdrop
    BG        = "#05050a"
    PANEL     = "#0a0a14"
    PANEL2    = "#0d0d1a"
    BORDER    = "#1c1c30"
    BORDER_B  = "#3a3a5c"
    BORDER_A  = "#26263f"
    # Ethereal cyan/teal primary
    PRI       = "#00f5ff"
    PRI_DIM   = "#00898f"
    PRI_GHO   = "#00232a"
    # Soft purple secondary
    SEC       = "#9d4edd"
    SEC_DIM   = "#5a2d80"
    SEC_GHO   = "#1c0e2b"
    ACC       = "#ff8a3d"
    ACC2      = "#ffcc66"
    GREEN     = "#4dffb0"
    GREEN_D   = "#00aa66"
    RED       = "#ff4d6d"
    MUTED_C   = "#ff4d7a"
    TEXT      = "#cfeeff"
    TEXT_DIM  = "#5c6a8a"
    TEXT_MED  = "#9db3d6"
    WHITE     = "#f0f8ff"
    DARK      = "#07070f"
    BAR_BG    = "#101024"


def detect_system_theme() -> str:
    """Best-effort detection of the OS light/dark preference.
    Returns 'dark' or 'light'. Aether's HUD is dark-themed by design
    regardless of the result — this is a foundation for future theme
    switching (Phase 1 goal), not a full light-mode palette yet."""
    try:
        app = QApplication.instance()
        if app is not None:
            hints = app.styleHints()
            scheme = hints.colorScheme()  # Qt 6.5+
            return "light" if scheme.name.lower() == "light" else "dark"
    except Exception:
        pass
    try:
        if _OS == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if val else "dark"
    except Exception:
        pass
    return "dark"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Bundled fonts (Rajdhani for display, system sans for body) ──────────
FONTS_DIR    = BASE_DIR / "assets" / "fonts"
FONT_DISPLAY = "Rajdhani"
FONT_BODY    = "Segoe UI" if _OS == "Windows" else ("Helvetica Neue" if _OS == "Darwin" else "Inter")

_fonts_loaded = False

def load_bundled_fonts() -> None:
    """Load bundled TTFs into the app font database. Falls back silently
    to system fonts if the files aren't present."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    if not FONTS_DIR.exists():
        return
    for fname in ("Rajdhani-Regular.ttf", "Rajdhani-Medium.ttf", "Rajdhani-Bold.ttf"):
        fp = FONTS_DIR / fname
        if fp.exists():
            try:
                QFontDatabase.addApplicationFont(str(fp))
            except Exception:
                pass

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class AetherOrb(QWidget):
    """The Aether Core — a soft glowing ethereal sphere that breathes,
    listens, thinks, speaks, and flashes on error. Keeps the same public
    attribute surface as the old HudCanvas (muted / speaking / state) so
    MainWindow doesn't need to change how it drives this widget."""

    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick        = 0
        self._breathe      = 0.0       # slow idle breathing phase
        self._scale        = 1.0
        self._tgt_scale    = 1.0
        self._glow         = 60.0
        self._tgt_glow     = 60.0
        self._nebula_rot   = 0.0        # inner nebula swirl
        self._ring_rot     = 0.0        # outer energy ring
        self._orbit_rot    = 0.0        # thinking orbit dots
        self._wave_phase   = 0.0
        self._error_flash  = 0.0        # 0..1, decays after an error ping
        self._pulses: list[float] = []
        self._bars = [0.0] * 24         # speaking audio bars (smoothed)
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def flash_error(self):
        self._error_flash = 1.0

    def _step(self):
        self._tick += 1
        speaking = self.speaking
        thinking = self.state in ("THINKING", "PROCESSING")

        # breathing / target glow + scale depending on state
        self._breathe = (self._breathe + 0.014) % (2 * math.pi)
        breath = (math.sin(self._breathe) + 1) / 2  # 0..1

        if self.muted:
            self._tgt_glow, self._tgt_scale = 24 + breath * 6, 1.0
        elif speaking:
            self._tgt_glow  = 140 + breath * 40
            self._tgt_scale = 1.05 + breath * 0.05
        elif thinking:
            self._tgt_glow  = 95 + breath * 20
            self._tgt_scale = 1.0 + breath * 0.02
        else:  # idle / listening
            self._tgt_glow  = 55 + breath * 18
            self._tgt_scale = 1.0 + breath * 0.015

        sp = 0.3 if speaking else 0.12
        self._glow  += (self._tgt_glow  - self._glow)  * sp
        self._scale += (self._tgt_scale - self._scale) * sp

        self._nebula_rot = (self._nebula_rot + (1.8 if speaking else (1.1 if thinking else 0.35))) % 360
        self._ring_rot    = (self._ring_rot    - (1.3 if speaking else (0.9 if thinking else 0.25))) % 360
        self._orbit_rot   = (self._orbit_rot   + (5.5 if thinking else 2.0)) % 360
        self._wave_phase  += 0.22

        # listening concentric pulses
        fw  = min(self.width(), self.height())
        lim = fw * 0.62
        pspd = 2.1
        self._pulses = [r + pspd for r in self._pulses if r + pspd < lim]
        if not self.muted and not speaking and len(self._pulses) < 2 and random.random() < 0.02:
            self._pulses.append(0.0)

        # speaking bars — smooth toward random targets
        for i in range(len(self._bars)):
            tgt = random.uniform(0.15, 1.0) if speaking else 0.0
            self._bars[i] += (tgt - self._bars[i]) * (0.5 if speaking else 0.2)

        if self._error_flash > 0:
            self._error_flash = max(0.0, self._error_flash - 0.05)

        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # deep space background with subtle radial vignette
        bg = QRadialGradient(cx, cy, fw * 0.9)
        bg.setColorAt(0.0, qcol("#0d0d1c"))
        bg.setColorAt(1.0, qcol(C.BG))
        p.fillRect(self.rect(), QBrush(bg))

        # faint starfield dots
        rnd = random.Random(42)
        p.setPen(QPen(qcol(C.TEXT_DIM, 40), 1))
        for _ in range(60):
            x = rnd.randint(0, max(1, W)); y = rnd.randint(0, max(1, H))
            p.drawPoint(x, y)

        core_r = fw * 0.30 * self._scale

        # error flash overrides colour momentarily
        if self._error_flash > 0.55:
            base_col, sec_col = C.RED, C.RED
        elif self._error_flash > 0:
            base_col, sec_col = C.ACC, C.ACC2
        elif self.muted:
            base_col, sec_col = C.MUTED_C, C.MUTED_C
        else:
            base_col, sec_col = C.PRI, C.SEC

        # ── outer halo glow (soft layered rings) ──
        for i in range(9):
            r   = core_r * (2.0 - i * 0.11)
            frc = 1.0 - i / 9
            a   = max(0, min(255, int(self._glow * 0.09 * frc)))
            p.setPen(QPen(qcol(base_col, a), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # listening pulse rings
        for pr in self._pulses:
            a = max(0, int(200 * (1.0 - pr / (fw * 0.62))))
            p.setPen(QPen(qcol(base_col, a), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # slow outer energy ring (dashed arc segments, purple accent)
        ring_r = core_r * 1.55
        a_ring = max(0, min(255, int(self._glow * 0.9)))
        p.setPen(QPen(qcol(sec_col, a_ring), 2))
        rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
        seg, gap = 46, 30
        ang = self._ring_rot
        while ang < self._ring_rot + 360:
            p.drawArc(rect, int(ang * 16), int(seg * 16))
            ang += seg + gap

        # thinking: orbiting dots
        if self.state in ("THINKING", "PROCESSING") and not self.speaking:
            n_dots = 5
            for i in range(n_dots):
                a_deg = self._orbit_rot + i * (360 / n_dots)
                rad   = math.radians(a_deg)
                orx   = core_r * 1.3
                dx, dy = cx + orx * math.cos(rad), cy + orx * math.sin(rad)
                a = max(0, min(255, int(self._glow * 1.4)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(qcol(sec_col, a)))
                p.drawEllipse(QPointF(dx, dy), 3.2, 3.2)

        # ── inner nebula swirl (layered translucent arcs inside the core) ──
        grad = QRadialGradient(cx, cy, core_r)
        cA = QColor(base_col); cA.setAlpha(min(255, int(self._glow * 2.2)))
        cB = QColor(sec_col);  cB.setAlpha(min(255, int(self._glow * 0.9)))
        cC = QColor(base_col); cC.setAlpha(0)
        grad.setColorAt(0.0, cA)
        grad.setColorAt(0.55, cB)
        grad.setColorAt(1.0, cC)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

        # nebula flow lines swirling inside the core
        p.save()
        p.setClipPath(self._circle_path(cx, cy, core_r))
        for i, (rf, wgt) in enumerate([(0.9, 1.3), (0.65, 1.0), (0.4, 0.8)]):
            r = core_r * rf
            a = max(0, min(255, int(self._glow * (0.5 - i * 0.1))))
            col = sec_col if i % 2 else base_col
            p.setPen(QPen(qcol(col, a), wgt))
            p.setBrush(Qt.BrushStyle.NoBrush)
            start = (self._nebula_rot * (1 + i * 0.4)) % 360
            p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), int(start * 16), int(260 * 16))
        p.restore()

        # face image if the user supplied one, drawn softly inside the core
        if self._face_px:
            fsz    = int(core_r * 1.5)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.setOpacity(0.85)
            p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
            p.setOpacity(1.0)

        # speaking: vertical audio bars across the core + outward light waves
        if self.speaking:
            n = len(self._bars)
            bw = (core_r * 1.5) / n
            x0 = cx - (core_r * 1.5) / 2
            for i, v in enumerate(self._bars):
                h = 6 + v * core_r * 0.9
                col = qcol(C.WHITE, 210)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(col))
                p.drawRoundedRect(QRectF(x0 + i * bw, cy - h / 2, bw * 0.6, h), 2, 2)

            for k in range(3):
                wr = core_r * (1.15 + 0.35 * ((self._wave_phase + k * 2.2) % 3) / 3)
                a  = max(0, int(150 * (1 - ((self._wave_phase + k * 2.2) % 3) / 3)))
                p.setPen(QPen(qcol(C.PRI, a), 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(cx - wr, cy - wr, wr * 2, wr * 2))

        # status caption beneath the orb
        label = {
            "LISTENING":  "Listening",
            "THINKING":   "Thinking",
            "PROCESSING": "Processing",
            "SPEAKING":   "Speaking",
            "MUTED":      "Muted",
        }.get("MUTED" if self.muted else ("SPEAKING" if self.speaking else self.state), self.state.title())
        p.setPen(QPen(qcol(base_col, 235), 1))
        p.setFont(QFont(FONT_DISPLAY, 15, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, cy + core_r * 1.85, W, 30),
                   Qt.AlignmentFlag.AlignCenter, label)

    @staticmethod
    def _circle_path(cx: float, cy: float, r: float) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        return path

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont(FONT_BODY, 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont(FONT_BODY, 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont(FONT_BODY, 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._sig.connect(self._append_entry)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _append_entry(self, text: str):
        tl = text.lower()
        if   tl.startswith("you:"):    tag = "you"
        elif tl.startswith("aether:"): tag = "ai"
        elif tl.startswith("file:"):   tag = "file"
        elif "err" in tl:              tag = "err"
        else:                           tag = "sys"
        color = {
            "you":  qcol(C.WHITE),
            "ai":   qcol(C.PRI),
            "err":  qcol(C.RED),
            "file": qcol(C.GREEN),
            "sys":  qcol(C.ACC2),
        }.get(tag, qcol(C.TEXT))
        safe_text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
        document = QTextDocument()
        document.setDefaultFont(self.font())
        document.setMarkdown(
            safe_text,
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
            QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        document_cursor = QTextCursor(document)
        document_cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setForeground(QBrush(color))
        document_cursor.mergeCharFormat(fmt)
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        cursor.insertFragment(QTextDocumentFragment(document))
        self.setTextCursor(cursor)
        self.ensureCursorVisible()


class StatsBar(QWidget):
    """
    Compact "Message Stats" panel — shows the metrics for the most recent
    LLM response: model, input/output/total tokens, generation speed,
    response time, and how full the model's context window is.

    All fields default to "—" until the first response arrives. Any field
    the backend doesn't report (e.g. token counts for some OpenAI-compatible
    servers) is shown as "—" rather than a misleading 0.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        frame = QFrame()
        outer.addWidget(frame)

        grid = QGridLayout(frame)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self._value_labels: dict[str, QLabel] = {}

        fields = [
            ("model",  "MODEL"),
            ("input",  "INPUT"),
            ("output", "OUTPUT"),
            ("total",  "TOTAL"),
            ("speed",  "SPEED"),
            ("time",   "TIME"),
        ]
        for i, (key, caption) in enumerate(fields):
            row, col = divmod(i, 3)
            cell = QVBoxLayout()
            cell.setSpacing(0)

            cap = QLabel(caption)
            cap.setFont(QFont(FONT_BODY, 6, QFont.Weight.Bold))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            cell.addWidget(cap)

            val = QLabel("—")
            val.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
            val.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            val.setWordWrap(True)
            cell.addWidget(val)

            self._value_labels[key] = val
            grid.addLayout(cell, row, col)

        # ── Context-window usage bar ───────────────────────────────────
        ctx_row = QVBoxLayout()
        ctx_row.setSpacing(2)

        ctx_cap = QLabel("CONTEXT WINDOW")
        ctx_cap.setFont(QFont(FONT_BODY, 6, QFont.Weight.Bold))
        ctx_cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        ctx_row.addWidget(ctx_cap)

        bar_row = QHBoxLayout()
        bar_row.setSpacing(6)

        self._ctx_bar = QProgressBar()
        self._ctx_bar.setRange(0, 100)
        self._ctx_bar.setValue(0)
        self._ctx_bar.setTextVisible(False)
        self._ctx_bar.setFixedHeight(8)
        self._ctx_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {C.BAR_BG};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {C.PRI};
                border-radius: 3px;
            }}
        """)
        bar_row.addWidget(self._ctx_bar, stretch=1)

        self._ctx_label = QLabel("0%")
        self._ctx_label.setFont(QFont(FONT_BODY, 7, QFont.Weight.Bold))
        self._ctx_label.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._ctx_label.setFixedWidth(60)
        bar_row.addWidget(self._ctx_label)

        ctx_row.addLayout(bar_row)
        grid.addLayout(ctx_row, 2, 0, 1, 3)

    # ------------------------------------------------------------------
    def update_stats(self, data: dict) -> None:
        """
        data keys (all optional):
            model          : str
            prompt_tokens  : int | None
            output_tokens  : int | None
            elapsed        : float seconds
            tokens_per_sec : float | None
            context_window : int | None  — model's max context size
        """
        model = data.get("model")
        if model:
            self._value_labels["model"].setText(str(model))

        prompt_tokens = data.get("prompt_tokens")
        output_tokens = data.get("output_tokens")
        elapsed       = data.get("elapsed")
        tps           = data.get("tokens_per_sec")
        context_window = data.get("context_window")

        self._value_labels["input"].setText(
            str(prompt_tokens) if prompt_tokens is not None else "—")
        self._value_labels["output"].setText(
            str(output_tokens) if output_tokens is not None else "—")

        if prompt_tokens is not None and output_tokens is not None:
            total = prompt_tokens + output_tokens
            self._value_labels["total"].setText(str(total))
        else:
            total = None
            self._value_labels["total"].setText("—")

        self._value_labels["speed"].setText(
            f"{tps:.1f} tok/s" if tps is not None else "—")
        self._value_labels["time"].setText(
            f"{elapsed:.2f}s" if elapsed is not None else "—")

        if total is not None and context_window:
            pct = max(0, min(100, round(total / context_window * 100)))
            self._ctx_bar.setValue(pct)
            self._ctx_label.setText(f"{pct}% of {context_window}")
            if pct >= 90:
                chunk_color = C.RED
            elif pct >= 70:
                chunk_color = C.ACC2
            else:
                chunk_color = C.PRI
            self._ctx_bar.setStyleSheet(f"""
                QProgressBar {{
                    background: {C.BAR_BG};
                    border: 1px solid {C.BORDER};
                    border-radius: 3px;
                }}
                QProgressBar::chunk {{
                    background: {chunk_color};
                    border-radius: 3px;
                }}
            """)
        else:
            self._ctx_bar.setValue(0)
            self._ctx_label.setText("—")


_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for Aether", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont(FONT_BODY, 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont(FONT_BODY, 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont(FONT_BODY, 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont(FONT_BODY, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont(FONT_BODY, 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont(FONT_BODY, 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    # Emits a JSON string containing the full config dict
    done = pyqtSignal(str)

    # ------------------------------------------------------------------ #
    _INPUT_STYLE = ""  # filled in __init__ after C is available

    def __init__(self, parent=None, initial: dict | None = None, mode: str = "init"):
        super().__init__(parent)
        self._mode = mode
        _init = initial or {}
        self._initial_config = _init
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(5, 5, 10, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
            }}
        """)

        self._INPUT_STYLE = f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
                font-family: '{FONT_BODY}'; font-size: 9pt;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """

        self._sel_stt          = _init.get("stt_engine",    "whisper")
        self._sel_tts          = _init.get("tts_engine",    "edgetts")

        _prov_init = _init.get("llm_provider", "ollama")
        self._sel_llm_provider = _prov_init if _prov_init in ("ollama", "openai", "gemini", "openai_api") else "ollama"
        # Which local backend to fall back to if the user switches from Gemini back to Local
        self._sel_local_sub = self._sel_llm_provider if self._sel_llm_provider in ("ollama", "openai") else "ollama"
        self._sel_engine = self._sel_llm_provider if self._sel_llm_provider in ("gemini", "openai_api") else "local"

        # Best-effort local hardware detection, used to recommend Ollama models.
        # Never allowed to break setup if it fails (missing psutil, sandboxed host, etc).
        try:
            from core.device_specs import get_device_specs, recommend_models
            self._device_specs = get_device_specs()
            self._model_recs   = recommend_models(self._device_specs)
        except Exception:
            self._device_specs = None
            self._model_recs   = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(6)

        # ── Header ──────────────────────────────────────────────────── #
        if mode == "config":
            layout.addWidget(self._lbl("◈  CONFIGURATION", 12, True))
            layout.addWidget(self._lbl("Update Aether\u2019s settings and click Apply.", 8, col=C.PRI_DIM))
        else:
            layout.addWidget(self._lbl("◈  INITIALISATION REQUIRED", 12, True))
            layout.addWidget(self._lbl("Configure Aether before first boot.", 8, col=C.PRI_DIM))
        layout.addWidget(self._sep())

        self._build_stt_section(layout, _init)
        self._build_llm_section(layout, _init)
        self._build_tts_section(layout, _init)

        # Set correct initial state for TTS UI
        self._update_tts_ui(self._sel_tts)

        self._build_action_buttons(layout, mode)

    # ------------------------------------------------------------------ #
    # Shared widget-builder helpers (formerly local closures in __init__)
    # ------------------------------------------------------------------ #

    def _lbl(self, txt, sz=9, bold=False, col=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
        w = QLabel(txt); w.setAlignment(align)
        w.setFont(QFont(FONT_BODY, sz,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {col}; background: transparent;")
        return w

    def _sep(self):
        s = QFrame(); s.setFrameShape(QFrame.Shape.HLine)
        s.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        return s

    def _input(self, placeholder="", pw=False, fixed_h=28):
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        w.setFixedHeight(fixed_h)
        if pw:
            w.setEchoMode(QLineEdit.EchoMode.Password)
        w.setStyleSheet(self._INPUT_STYLE)
        return w

    def _toggle_row(self, keys_labels: list, getter, setter):
        row = QHBoxLayout(); row.setSpacing(5)
        btns: dict[str, QPushButton] = {}
        def _click(k):
            setter(k)
            for bk, b in btns.items():
                self._style_btn(b, bk == k)
        for k, lbl in keys_labels:
            b = QPushButton(lbl)
            b.setFixedHeight(26)
            b.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, kk=k: _click(kk))
            row.addWidget(b)
            btns[k] = b
        _click(getter())
        return row, btns

    def _style_btn(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI}; color: #001a22;
                    border: none; border-radius: 3px; font-weight: bold;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #000d12; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ------------------------------------------------------------------ #
    # Section builders (formerly inline in __init__)
    # ------------------------------------------------------------------ #

    def _build_stt_section(self, layout, _init) -> None:
        layout.addWidget(self._lbl("SPEECH-TO-TEXT ENGINE", 7, col=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        stt_row, self._stt_btns = self._toggle_row(
            [("whisper","🎙 Whisper"), ("vosk","🔊 Vosk")],
            lambda: self._sel_stt,
            self._set_stt,
        )
        layout.addLayout(stt_row)

        self._COMBO_STYLE = f"""
            QComboBox {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
                font-family: '{FONT_BODY}'; font-size: 9pt;
            }}
            QComboBox:focus {{ border: 1px solid {C.PRI}; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER};
                selection-background-color: {C.PRI_GHO};
                font-family: '{FONT_BODY}'; font-size: 9pt;
            }}
        """

        stt_detail = QHBoxLayout(); stt_detail.setSpacing(5)
        stt_detail.addWidget(self._lbl("Model:", 7, col=C.TEXT_MED,
                                   align=Qt.AlignmentFlag.AlignRight))

        # Whisper: dropdown with predefined sizes
        self._whisper_combo = QComboBox()
        self._whisper_combo.setFixedHeight(28)
        self._whisper_combo.setStyleSheet(self._COMBO_STYLE)
        for m in ["tiny", "base", "small", "medium", "large-v3"]:
            self._whisper_combo.addItem(m)
        _cur_model = _init.get("stt_model", "small")
        _idx = self._whisper_combo.findText(_cur_model)
        self._whisper_combo.setCurrentIndex(_idx if _idx >= 0 else 1)
        stt_detail.addWidget(self._whisper_combo)

        # Vosk: free-text path input
        self._vosk_model_input = self._input("model dir path  (leave empty for auto-download)")
        self._vosk_model_input.setText(_init.get("vosk_model_path", ""))
        stt_detail.addWidget(self._vosk_model_input)

        layout.addLayout(stt_detail)

        # Initial visibility
        self._whisper_combo.setVisible(self._sel_stt == "whisper")
        self._vosk_model_input.setVisible(self._sel_stt == "vosk")

        stt_lang_row = QHBoxLayout(); stt_lang_row.setSpacing(5)
        stt_lang_row.addWidget(self._lbl("Language:", 7, col=C.TEXT_MED,
                                    align=Qt.AlignmentFlag.AlignRight))
        self._stt_lang_input = self._input("auto  (or: tr / en / de / fr / es / zh …)")
        self._stt_lang_input.setText(_init.get("stt_language", "auto"))
        stt_lang_row.addWidget(self._stt_lang_input)
        layout.addLayout(stt_lang_row)
        layout.addWidget(self._sep())

    _GEMINI_MODELS = [
        ("gemini-3.8-flash",      "Gemini 3.8 Flash — fast & cheap (recommended)"),
        ("gemini-2.5-flash",      "Gemini 2.5 Flash — retired for new keys"),
        ("gemini-2.5-pro",        "Gemini 2.5 Pro — strongest reasoning"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite — fastest / cheapest"),
        ("gemini-2.0-flash",      "Gemini 2.0 Flash — previous gen"),
    ]
    GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

    def _build_llm_section(self, layout, _init) -> None:
        layout.addWidget(self._lbl("AI ENGINE", 7, col=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))

        # Top-level choice: local models or hosted model APIs.
        engine_row, self._engine_btns = self._toggle_row(
            [
                ("local",  "🖥  Local (Ollama / LM Studio)"),
                ("gemini", "✨  Gemini (cloud API key)"),
                ("openai_api", "✦  OpenAI API (usage billed separately)"),
            ],
            lambda: self._sel_engine,
            self._set_engine,
        )
        layout.addLayout(engine_row)

        # ── Local panel (Ollama / LM Studio + device-based recommendations) ──
        self._llm_local_widget = QWidget()
        self._llm_local_widget.setStyleSheet("background: transparent;")
        local_lay = QVBoxLayout(self._llm_local_widget)
        local_lay.setContentsMargins(0, 6, 0, 0)
        local_lay.setSpacing(5)

        llm_prov_row, self._llm_prov_btns = self._toggle_row(
            [
                ("ollama", "🦙 Ollama"),
                ("openai", "🔌 LM Studio / OpenAI"),
            ],
            lambda: self._sel_local_sub,
            self._set_llm_provider,
        )
        local_lay.addLayout(llm_prov_row)

        _ollama_hint = "ollama.com  ·  run: ollama pull qwen2.5:3b"
        _openai_hint = "lmstudio.ai  ·  start Local Server first, then pick model"
        self._llm_hint_lbl = self._lbl(
            _openai_hint if self._sel_local_sub == "openai" else _ollama_hint,
            7, col=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
        )
        local_lay.addWidget(self._llm_hint_lbl)

        # Best-effort device spec line + clickable Ollama model recommendations
        specs = self._device_specs
        if specs and specs.detected_ok:
            _gpu = f"  ·  GPU: {specs.gpu_name} ({specs.gpu_vram_gb:.0f}GB)" if specs.gpu_name and specs.gpu_vram_gb else ""
            _spec_txt = f"Detected: {specs.ram_gb:.0f}GB RAM  ·  {specs.cpu_cores} cores{_gpu}"
        else:
            _spec_txt = "Couldn't read hardware specs — showing safe defaults"
        local_lay.addWidget(self._lbl(_spec_txt, 7, col=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))

        reco_row = QHBoxLayout(); reco_row.setSpacing(4)
        self._reco_btns: list[QPushButton] = []
        for rec in self._model_recs:
            label = f"★ {rec.tag}" if rec.recommended else rec.tag
            b = QPushButton(label)
            b.setFixedHeight(24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(QFont(FONT_BODY, 7, QFont.Weight.Bold if rec.recommended else QFont.Weight.Normal))
            b.setToolTip(f"{rec.note}   (~{rec.approx_gb:.1f}GB, needs {rec.min_ram_gb:.0f}GB+ RAM)")
            if rec.recommended:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI}; color: #001a22; border: none;
                                   border-radius: 3px; padding: 0 6px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: #000d12; color: {C.TEXT_DIM};
                                   border: 1px solid {C.BORDER}; border-radius: 3px; padding: 0 6px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)
            b.clicked.connect(lambda _, t=rec.tag: self._apply_model_reco(t))
            reco_row.addWidget(b)
            self._reco_btns.append(b)
        reco_row.addStretch(1)
        local_lay.addLayout(reco_row)

        llm_row = QHBoxLayout(); llm_row.setSpacing(5)
        llm_row.addWidget(self._lbl("URL:", 7, col=C.TEXT_MED,
                                align=Qt.AlignmentFlag.AlignRight))
        _local_default_url = "http://localhost:1234" if self._sel_local_sub == "openai" else "http://localhost:11434"
        # Don't prefill the local URL box with the Gemini endpoint if the saved config was Gemini.
        _default_url = _local_default_url if self._sel_llm_provider in ("gemini", "openai_api") else _init.get("llm_url", _local_default_url)
        self._llm_url_input = self._input(
            "http://localhost:1234" if self._sel_local_sub == "openai"
            else "http://localhost:11434"
        )
        self._llm_url_input.setText(_default_url)
        llm_row.addWidget(self._llm_url_input, stretch=2)
        llm_row.addWidget(self._lbl("Model:", 7, col=C.TEXT_MED,
                                align=Qt.AlignmentFlag.AlignRight))
        self._llm_model_input = self._input("e.g.  qwen2.5:3b  /  llama3.2  /  mistral")
        self._llm_model_input.setText(_init.get("llm_model", "") if self._sel_llm_provider not in ("gemini", "openai_api") else "")
        llm_row.addWidget(self._llm_model_input, stretch=2)
        local_lay.addLayout(llm_row)

        layout.addWidget(self._llm_local_widget)

        # ── Gemini panel (cloud, just needs an API key + model) ────────────
        self._llm_gemini_widget = QWidget()
        self._llm_gemini_widget.setStyleSheet("background: transparent;")
        gem_lay = QVBoxLayout(self._llm_gemini_widget)
        gem_lay.setContentsMargins(0, 6, 0, 0)
        gem_lay.setSpacing(5)

        gem_lay.addWidget(self._lbl(
            "aistudio.google.com/app/apikey  ·  free tier available",
            7, col=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
        ))

        gem_key_row = QHBoxLayout(); gem_key_row.setSpacing(5)
        gem_key_row.addWidget(self._lbl("API Key:", 7, col=C.TEXT_MED,
                                    align=Qt.AlignmentFlag.AlignRight))
        self._gemini_key_input = self._input("AIza…", pw=True)
        self._gemini_key_input.setText(_init.get("gemini_api_key", ""))
        gem_key_row.addWidget(self._gemini_key_input)
        gem_lay.addLayout(gem_key_row)

        gem_model_row = QHBoxLayout(); gem_model_row.setSpacing(5)
        gem_model_row.addWidget(self._lbl("Model:", 7, col=C.TEXT_MED,
                                    align=Qt.AlignmentFlag.AlignRight))
        self._gemini_model_combo = QComboBox()
        self._gemini_model_combo.setFixedHeight(28)
        self._gemini_model_combo.setStyleSheet(self._COMBO_STYLE)
        for val, disp in self._GEMINI_MODELS:
            self._gemini_model_combo.addItem(disp, userData=val)
        _cur_gem_model = _init.get("llm_model", "gemini-3.8-flash") if self._sel_llm_provider == "gemini" else "gemini-3.8-flash"
        for i in range(self._gemini_model_combo.count()):
            if self._gemini_model_combo.itemData(i) == _cur_gem_model:
                self._gemini_model_combo.setCurrentIndex(i)
                break
        gem_model_row.addWidget(self._gemini_model_combo)
        gem_lay.addLayout(gem_model_row)

        layout.addWidget(self._llm_gemini_widget)

        self._llm_openai_widget = QWidget()
        self._llm_openai_widget.setStyleSheet("background: transparent;")
        openai_lay = QVBoxLayout(self._llm_openai_widget)
        openai_lay.setContentsMargins(0, 6, 0, 0)
        openai_lay.setSpacing(5)
        openai_lay.addWidget(self._lbl(
            "platform.openai.com/api-keys  ·  API usage is billed separately from ChatGPT",
            7, col=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
        ))
        openai_key_row = QHBoxLayout(); openai_key_row.setSpacing(5)
        openai_key_row.addWidget(self._lbl("API Key:", 7, col=C.TEXT_MED,
                                          align=Qt.AlignmentFlag.AlignRight))
        self._openai_key_input = self._input("sk-…", pw=True)
        self._openai_key_input.setText(_init.get("openai_api_key", ""))
        openai_key_row.addWidget(self._openai_key_input)
        openai_lay.addLayout(openai_key_row)
        openai_model_row = QHBoxLayout(); openai_model_row.setSpacing(5)
        openai_model_row.addWidget(self._lbl("Model ID:", 7, col=C.TEXT_MED,
                                            align=Qt.AlignmentFlag.AlignRight))
        self._openai_model_input = self._input("model ID from your OpenAI API account")
        self._openai_model_input.setText(
            _init.get("llm_model", "") if self._sel_llm_provider == "openai_api" else ""
        )
        openai_model_row.addWidget(self._openai_model_input)
        openai_lay.addLayout(openai_model_row)
        layout.addWidget(self._llm_openai_widget)
        layout.addWidget(self._sep())

        # Apply initial visibility now that both panels exist
        self._llm_local_widget.setVisible(self._sel_engine == "local")
        self._llm_gemini_widget.setVisible(self._sel_engine == "gemini")
        self._llm_openai_widget.setVisible(self._sel_engine == "openai_api")
        for k, btn in self._engine_btns.items():
            self._style_btn(btn, k == self._sel_engine)

    def _build_tts_section(self, layout, _init) -> None:
        layout.addWidget(self._lbl("TEXT-TO-SPEECH ENGINE", 7, col=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        tts_row, self._tts_btns = self._toggle_row(
            [("edgetts","🔈 EdgeTTS"), ("kokoro","🤖 Kokoro"), ("elevenlabs","⚡ ElevenLabs")],
            lambda: self._sel_tts,
            self._set_tts,
        )
        layout.addLayout(tts_row)

        voice_row = QHBoxLayout(); voice_row.setSpacing(5)
        self._voice_lbl = QLabel("Voice:")
        self._voice_lbl.setFont(QFont(FONT_BODY, 7))
        self._voice_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._voice_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        voice_row.addWidget(self._voice_lbl)

        self._tts_voice_input = self._input("en-US-GuyNeural")
        self._tts_voice_input.setText(_init.get("tts_voice", "en-US-GuyNeural"))
        voice_row.addWidget(self._tts_voice_input)

        # Kokoro: dropdown with predefined voices (hidden unless Kokoro selected)
        self._kokoro_combo = QComboBox()
        self._kokoro_combo.setFixedHeight(28)
        self._kokoro_combo.setStyleSheet(self._COMBO_STYLE)
        _KOKORO_VOICES = [
            ("af_heart",    "af_heart  — EN-F warm (recommended)"),
            ("af_sky",      "af_sky  — EN-F clear"),
            ("af_bella",    "af_bella  — EN-F bella"),
            ("af_sarah",    "af_sarah  — EN-F sarah"),
            ("am_adam",     "am_adam  — EN-M adam"),
            ("am_michael",  "am_michael  — EN-M michael"),
            ("bf_emma",     "bf_emma  — UK-F emma"),
            ("bf_isabella", "bf_isabella  — UK-F isabella"),
            ("bm_george",   "bm_george  — UK-M george"),
            ("bm_lewis",    "bm_lewis  — UK-M lewis"),
            ("jf_alpha",    "jf_alpha  — Japanese Female"),
            ("jm_kumo",     "jm_kumo  — Japanese Male"),
        ]
        for val, display in _KOKORO_VOICES:
            self._kokoro_combo.addItem(display, userData=val)
        _cur_voice = _init.get("tts_voice", "af_heart")
        for i in range(self._kokoro_combo.count()):
            if self._kokoro_combo.itemData(i) == _cur_voice:
                self._kokoro_combo.setCurrentIndex(i)
                break
        self._kokoro_combo.setVisible(False)  # shown only when Kokoro active
        voice_row.addWidget(self._kokoro_combo)

        layout.addLayout(voice_row)

        # Kokoro speed — only visible when Kokoro is selected
        self._kokoro_speed_widget = QWidget()
        self._kokoro_speed_widget.setStyleSheet("background: transparent;")
        ks_row = QHBoxLayout(self._kokoro_speed_widget)
        ks_row.setContentsMargins(0, 0, 0, 0)
        ks_row.setSpacing(5)
        ks_row.addWidget(self._lbl("Speed:", 7, col=C.TEXT_MED,
                               align=Qt.AlignmentFlag.AlignRight))
        self._kokoro_speed_combo = QComboBox()
        self._kokoro_speed_combo.setFixedHeight(28)
        self._kokoro_speed_combo.setStyleSheet(self._COMBO_STYLE)
        for val, label in [
            ("0.8",  "0.8×  — Slow"),
            ("1.0",  "1.0×  — Normal"),
            ("1.1",  "1.1×  — Slightly fast"),
            ("1.2",  "1.2×  — Fast (recommended)"),
            ("1.3",  "1.3×  — Faster"),
            ("1.5",  "1.5×  — Very fast"),
        ]:
            self._kokoro_speed_combo.addItem(label, userData=val)
        _cur_speed = str(_init.get("tts_speed", "1.2"))
        for i in range(self._kokoro_speed_combo.count()):
            if self._kokoro_speed_combo.itemData(i) == _cur_speed:
                self._kokoro_speed_combo.setCurrentIndex(i)
                break
        ks_row.addWidget(self._kokoro_speed_combo)
        layout.addWidget(self._kokoro_speed_widget)

        # ElevenLabs key — only visible when ElevenLabs is selected
        self._el_key_widget = QWidget()
        self._el_key_widget.setStyleSheet("background: transparent;")
        el_row = QHBoxLayout(self._el_key_widget)
        el_row.setContentsMargins(0, 0, 0, 0)
        el_row.setSpacing(5)
        el_row.addWidget(self._lbl("API Key:", 7, col=C.TEXT_MED,
                               align=Qt.AlignmentFlag.AlignRight))
        self._el_key_input = self._input("ElevenLabs API key", pw=True)
        self._el_key_input.setText(_init.get("elevenlabs_api_key", ""))
        el_row.addWidget(self._el_key_input)
        layout.addWidget(self._el_key_widget)

        layout.addWidget(self._sep())

    def _build_action_buttons(self, layout, mode) -> None:
        # ── Action buttons ─────────────────────────────────────────────── #
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        if mode == "config":
            cancel_btn = QPushButton("✕  CANCEL")
            cancel_btn.setFont(QFont(FONT_BODY, 9, QFont.Weight.Bold))
            cancel_btn.setFixedHeight(34)
            cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{
                    color: {C.RED}; border: 1px solid {C.RED};
                }}
            """)
            cancel_btn.clicked.connect(self.hide)
            btn_row.addWidget(cancel_btn)

        btn_label = "▸  APPLY CHANGES" if mode == "config" else "▸  INITIALISE SYSTEMS"
        init_btn = QPushButton(btn_label)
        init_btn.setFont(QFont(FONT_BODY, 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(34)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        btn_row.addWidget(init_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    def _update_tts_ui(self, key: str) -> None:
        """Show/hide and relabel TTS fields based on selected engine."""
        if not hasattr(self, "_voice_lbl"):
            return

        is_kokoro = (key == "kokoro")

        # Kokoro uses a dropdown; other engines use a text input
        if hasattr(self, "_tts_voice_input"):
            self._tts_voice_input.setVisible(not is_kokoro)
        if hasattr(self, "_kokoro_combo"):
            self._kokoro_combo.setVisible(is_kokoro)

        if key == "elevenlabs":
            self._voice_lbl.setText("Voice ID:")
            if hasattr(self, "_tts_voice_input"):
                self._tts_voice_input.setPlaceholderText("ElevenLabs voice ID")
        elif key == "kokoro":
            self._voice_lbl.setText("Voice:")
        else:  # edgetts
            self._voice_lbl.setText("Voice:")
            if hasattr(self, "_tts_voice_input"):
                self._tts_voice_input.setPlaceholderText(
                    "en-US-GuyNeural  /  en-GB-RyanNeural  /  tr-TR-AhmetNeural  …"
                )

        if hasattr(self, "_kokoro_speed_widget"):
            self._kokoro_speed_widget.setVisible(is_kokoro)
        if hasattr(self, "_el_key_widget"):
            self._el_key_widget.setVisible(key == "elevenlabs")

    def _set_engine(self, key: str):
        """Top-level toggle between local models and hosted model APIs."""
        self._sel_engine = key
        self._sel_llm_provider = self._sel_local_sub if key == "local" else key
        if hasattr(self, "_engine_btns"):
            for k, btn in self._engine_btns.items():
                self._style_btn(btn, k == key)
        if hasattr(self, "_llm_local_widget"):
            self._llm_local_widget.setVisible(key == "local")
        if hasattr(self, "_llm_gemini_widget"):
            self._llm_gemini_widget.setVisible(key == "gemini")
        if hasattr(self, "_llm_openai_widget"):
            self._llm_openai_widget.setVisible(key == "openai_api")

    def _apply_model_reco(self, tag: str):
        """Clicking a recommended model fills the model field and switches to Ollama."""
        if hasattr(self, "_llm_model_input"):
            self._llm_model_input.setText(tag)
            self._llm_model_input.setStyleSheet(self._INPUT_STYLE)
        self._set_llm_provider("ollama")

    def _set_llm_provider(self, key: str):
        """Sub-toggle within the Local panel: Ollama vs LM Studio / OpenAI-compatible."""
        self._sel_local_sub = key
        if self._sel_engine == "local":
            self._sel_llm_provider = key
        if not hasattr(self, "_llm_prov_btns"):
            return
        for k, btn in self._llm_prov_btns.items():
            active = (k == key)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {'#00d4ff' if active else '#000d12'};
                    color: {'#001a22' if active else C.TEXT_DIM};
                    border: {'none' if active else f'1px solid {C.BORDER}'};
                    border-radius: 3px; font-weight: {'bold' if active else 'normal'};
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)
        # Update URL placeholder and hint to match selected provider
        if hasattr(self, "_llm_url_input"):
            if key == "openai":
                self._llm_url_input.setPlaceholderText("http://localhost:1234")
                # Only override if it still looks like an Ollama default URL
                cur = self._llm_url_input.text().strip()
                if not cur or cur == "http://localhost:11434":
                    self._llm_url_input.setText("http://localhost:1234")
            else:
                self._llm_url_input.setPlaceholderText("http://localhost:11434")
                cur = self._llm_url_input.text().strip()
                if not cur or cur == "http://localhost:1234":
                    self._llm_url_input.setText("http://localhost:11434")
        if hasattr(self, "_llm_hint_lbl"):
            if key == "openai":
                self._llm_hint_lbl.setText("lmstudio.ai  ·  start Local Server first, then pick model")
            else:
                self._llm_hint_lbl.setText("ollama.com  ·  run: ollama pull qwen2.5:3b")

    def _set_stt(self, key: str):
        self._sel_stt = key
        if not hasattr(self, "_stt_btns"):
            return
        for k, btn in self._stt_btns.items():
            active = (k == key)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {'#00d4ff' if active else '#000d12'};
                    color: {'#001a22' if active else C.TEXT_DIM};
                    border: {'none' if active else f'1px solid {C.BORDER}'};
                    border-radius: 3px; font-weight: {'bold' if active else 'normal'};
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)
        # Toggle model selector widgets
        if hasattr(self, "_whisper_combo"):
            self._whisper_combo.setVisible(key == "whisper")
        if hasattr(self, "_vosk_model_input"):
            self._vosk_model_input.setVisible(key == "vosk")

    def _set_tts(self, key: str):
        self._sel_tts = key
        if not hasattr(self, "_tts_btns"):
            return
        for k, btn in self._tts_btns.items():
            active = (k == key)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {'#00d4ff' if active else '#000d12'};
                    color: {'#001a22' if active else C.TEXT_DIM};
                    border: {'none' if active else f'1px solid {C.BORDER}'};
                    border-radius: 3px; font-weight: {'bold' if active else 'normal'};
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)
        self._update_tts_ui(key)

    def _submit(self):
        _provider = getattr(self, "_sel_llm_provider", "ollama")

        if _provider == "gemini":
            llm_model = self._gemini_model_combo.currentData() or "gemini-3.8-flash"
            from core.credentials import get_secret
            if not (self._gemini_key_input.text().strip() or
                    get_secret("gemini_api_key", self._initial_config)):
                self._gemini_key_input.setStyleSheet(
                    self._INPUT_STYLE + f" QLineEdit {{ border: 1px solid {C.RED}; }}"
                )
                return
        elif _provider == "openai_api":
            llm_model = self._openai_model_input.text().strip()
            from core.credentials import get_secret
            if not (self._openai_key_input.text().strip() or
                    get_secret("openai_api_key", self._initial_config)):
                self._openai_key_input.setStyleSheet(
                    self._INPUT_STYLE + f" QLineEdit {{ border: 1px solid {C.RED}; }}"
                )
                return
            if not llm_model:
                self._openai_model_input.setStyleSheet(
                    self._openai_model_input.styleSheet() +
                    f" QLineEdit {{ border: 1px solid {C.RED}; }}"
                )
                return
        else:
            llm_model = self._llm_model_input.text().strip()
            if not llm_model:
                self._llm_model_input.setStyleSheet(
                    self._llm_model_input.styleSheet() +
                    f" QLineEdit {{ border: 1px solid {C.RED}; }}"
                )
                return

        # STT model: combo for Whisper, text input for Vosk
        if self._sel_stt == "whisper":
            stt_model = self._whisper_combo.currentText()
        else:
            stt_model = self._vosk_model_input.text().strip()

        # Voice: Kokoro uses dropdown, others use text input
        if self._sel_tts == "kokoro":
            tts_voice = self._kokoro_combo.currentData() or "af_heart"
            tts_speed = self._kokoro_speed_combo.currentData() or "1.2"
        else:
            tts_voice = self._tts_voice_input.text().strip() or "en-US-GuyNeural"
            tts_speed = "1.0"

        if _provider == "gemini":
            llm_url = self.GEMINI_BASE_URL
        elif _provider == "openai_api":
            llm_url = "https://api.openai.com"
        else:
            _default_url = "http://localhost:1234" if _provider == "openai" else "http://localhost:11434"
            llm_url = self._llm_url_input.text().strip() or _default_url

        cfg = {
            "stt_engine":         self._sel_stt,
            "stt_model":          stt_model,
            "stt_language":       self._stt_lang_input.text().strip() or "auto",
            "llm_provider":       _provider,
            "llm_url":            llm_url,
            "llm_model":          llm_model,
            "tts_engine":         self._sel_tts,
            "tts_voice":          tts_voice,
            "tts_speed":          tts_speed,
            "elevenlabs_api_key": self._el_key_input.text().strip(),
            "gemini_api_key":     self._gemini_key_input.text().strip(),
            "openai_api_key":    self._openai_key_input.text().strip(),
        }
        if self._sel_stt == "vosk" and stt_model:
            cfg["vosk_model_path"] = stt_model
        self.done.emit(json.dumps(cfg))


class StartupPanel(QWidget):
    """Animated startup progress overlay — shown while components initialize."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            StartupPanel {{
                background: rgba(0, 6, 10, 235);
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(10)

        # ── Title ──────────────────────────────────────────────────────
        title = QLabel("◈  SYSTEMS INITIALISING")
        title.setFont(QFont(FONT_BODY, 11, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(title)

        lay.addSpacing(2)

        # ── Component rows ──────────────────────────────────────────────
        self._rows: dict[str, dict] = {}
        _COMPS = [
            ("stt", "SPEECH RECOGNITION  (STT)", C.GREEN),
            ("llm", "LANGUAGE MODEL  (LLM)",      C.ACC2),
            ("tts", "VOICE SYNTHESIS  (TTS)",      C.PRI),
        ]
        for key, label, color in _COMPS:
            box = QWidget()
            box.setStyleSheet(
                f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
            )
            box_lay = QVBoxLayout(box)
            box_lay.setContentsMargins(10, 6, 10, 6)
            box_lay.setSpacing(4)

            top = QHBoxLayout()
            nm = QLabel(label)
            nm.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
            nm.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            top.addWidget(nm)
            top.addStretch()

            st = QLabel("LOADING…")
            st.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
            st.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            top.addWidget(st)
            box_lay.addLayout(top)

            bar = QProgressBar()
            bar.setFixedHeight(4)
            bar.setRange(0, 0)     # indeterminate marquee
            bar.setTextVisible(False)
            bar.setStyleSheet(f"""
                QProgressBar {{
                    background: {C.BAR_BG}; border: none; border-radius: 2px;
                }}
                QProgressBar::chunk {{
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 {C.BORDER}, stop:1 {color});
                    border-radius: 2px; width: 60px; margin: 0px;
                }}
            """)
            box_lay.addWidget(bar)
            lay.addWidget(box)
            self._rows[key] = {"bar": bar, "status": st, "color": color}

        lay.addSpacing(4)

        # ── Bottom status ───────────────────────────────────────────────
        self._status_lbl = QLabel("Initialising components…")
        self._status_lbl.setFont(QFont(FONT_BODY, 8))
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._status_lbl.setWordWrap(True)
        lay.addWidget(self._status_lbl)

        tip = QLabel("All AI models run 100% locally · No data leaves your device")
        tip.setFont(QFont(FONT_BODY, 7))
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setStyleSheet(f"color: {C.BORDER}; background: transparent;")
        lay.addWidget(tip)

    # Called only from the main thread (via MainWindow._startup_sig)
    def update_component(self, key: str, status: str) -> None:
        if key not in self._rows:
            return
        row = self._rows[key]
        ok     = status == "ready"
        color  = row["color"] if ok else C.RED
        label  = "READY  ✓" if ok else "ERROR  ✗"

        bar = row["bar"]
        bar.setRange(0, 100)
        bar.setValue(100)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: {C.BAR_BG}; border: none; border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {color}; border-radius: 2px;
            }}
        """)
        st = row["status"]
        st.setText(label)
        st.setStyleSheet(f"color: {color}; background: transparent; border: none;")

    def set_status(self, text: str) -> None:
        self._status_lbl.setText(text)
        col = C.GREEN if "online" in text.lower() else C.TEXT_DIM
        self._status_lbl.setStyleSheet(f"color: {col}; background: transparent;")


class MainWindow(QMainWindow):
    _log_sig     = pyqtSignal(str)
    _clear_log_sig = pyqtSignal()
    _chat_sessions_sig = pyqtSignal(object, str)
    _state_sig   = pyqtSignal(str)
    _startup_sig = pyqtSignal(str, str)  # action, data — thread-safe startup panel control
    _stats_sig   = pyqtSignal(dict)      # message-stats updates from worker threads
    _confirm_sig = pyqtSignal(str, object)  # message, result-holder dict — see AetherUI.confirm_blocking

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Your Ethereal Local Intelligence  ·  v{__version__}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_stop_command  = None
        self._muted           = False
        self._current_file: str | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        self.orb = AetherOrb(face_path)
        self.orb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body.addWidget(self.orb, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._clear_log_sig.connect(self._log.clear)
        self._chat_sessions_sig.connect(self._set_chat_sessions)
        self._state_sig.connect(self._apply_state)
        self._startup_sig.connect(self._on_startup_sig)
        self._stats_sig.connect(self._stats_bar.update_stats)
        self._confirm_sig.connect(self._on_confirm_requested)

        _theme = detect_system_theme()
        self._log.append_log(
            f"SYS: OS theme detected: {_theme} (Aether HUD stays dark by design)."
        )

        self._overlay: SetupOverlay | None = None
        self._startup_panel: StartupPanel | None = None
        self._on_reconfigure_cb = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_show = QShortcut(QKeySequence("Ctrl+Alt+A"), self)
        sc_show.activated.connect(self._toggle_visibility)
        sc_collapse = QShortcut(QKeySequence("Escape"), self)
        sc_collapse.activated.connect(self._toggle_collapse)
        sc_newchat = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        sc_newchat.activated.connect(self._request_new_chat)

        self._collapsed = False
        self._saved_size = None

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _toggle_collapse(self):
        """Esc: collapse the whole HUD down to just the orb, or restore it."""
        if self.isFullScreen():
            self.showNormal()
            return
        if not self._collapsed:
            self._saved_size = self.size()
            self._left_panel.hide()
            self._right_panel.hide()
            self.resize(320, 380)
            self._collapsed = True
        else:
            self._left_panel.show()
            self._right_panel.show()
            if self._saved_size:
                self.resize(self._saved_size)
            self._collapsed = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 560, 660
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._startup_panel and self._startup_panel.isVisible():
            pw, ph = 400, 310
            self._startup_panel.setGeometry(
                (cw.width()  - pw) // 2,
                (cw.height() - ph) // 2,
                pw, ph,
            )

    # ── Startup panel (thread-safe via _startup_sig) ────────────────────
    def _on_confirm_requested(self, message: str, result_holder: dict) -> None:
        """Runs on the Qt UI thread — shows a blocking Yes/No dialog for a
        high-risk operation (shell exec, file delete, port scan, etc.) and
        reports the answer back to the worker thread that's blocked waiting
        on result_holder['event']. See AetherUI.confirm_blocking for the
        thread-safe entry point background code should actually call."""
        reply = QMessageBox.question(
            self, "Aether — Confirmation Required", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        result_holder["answer"] = (reply == QMessageBox.StandardButton.Yes)
        result_holder["event"].set()

    def _on_startup_sig(self, action: str, data: str) -> None:
        """Runs on main thread — handles all startup panel state changes."""
        if action == "show":
            self._create_startup_panel()
        elif action in ("ready", "error"):
            if self._startup_panel:
                self._startup_panel.update_component(data, action)
        elif action == "status":
            if self._startup_panel:
                self._startup_panel.set_status(data)
        elif action == "hide":
            if self._startup_panel:
                # Fade out after a short pause so "READY ✓" is visible
                QTimer.singleShot(1200, self._destroy_startup_panel)

    def _create_startup_panel(self) -> None:
        if self._startup_panel and self._startup_panel.isVisible():
            return
        cw = self.centralWidget()
        pw, ph = 400, 310
        panel = StartupPanel(cw)
        panel.setGeometry(
            (cw.width()  - pw) // 2,
            (cw.height() - ph) // 2,
            pw, ph,
        )
        panel.show()
        panel.raise_()
        self._startup_panel = panel

    def _destroy_startup_panel(self) -> None:
        if self._startup_panel:
            self._startup_panel.hide()
            self._startup_panel.deleteLater()
            self._startup_panel = None

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setStyleSheet(f"background: {C.DARK}; border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont(FONT_BODY, 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(f"{APP_NAME} v{__version__}", C.PRI_DIM))
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        title = QLabel("AETHER")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont(FONT_BODY, 17, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(title)
        sub = QLabel("Your Ethereal Local Intelligence")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont(FONT_BODY, 7))
        sub.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(sub)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont(FONT_BODY, 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont(FONT_BODY, 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("◈ SYSTEM")
        hdr.setFont(QFont(FONT_BODY, 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont(FONT_BODY, 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont(FONT_BODY, 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addStretch()

        for txt, col in [
            ("CORE\nACTIVE",       C.GREEN),
            ("MEMORY\nSYNCED",      C.PRI),
            ("AETHER\nLOCAL",        C.SEC),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont(FONT_BODY, 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont(FONT_BODY, 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        chat_row = QHBoxLayout()
        self._chat_combo = QComboBox()
        self._chat_combo.setToolTip("Select a saved conversation")
        self._chat_combo.currentTextChanged.connect(self._on_chat_selected)
        chat_row.addWidget(self._chat_combo, stretch=1)
        new_chat_btn = QPushButton("＋ New")
        new_chat_btn.setToolTip("Start a new conversation (Ctrl+Shift+N)")
        new_chat_btn.clicked.connect(self._request_new_chat)
        chat_row.addWidget(new_chat_btn)
        lay.addLayout(chat_row)
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        lay.addWidget(_sec("MESSAGE STATS"))
        self._stats_bar = StatsBar()
        lay.addWidget(self._stats_bar)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont(FONT_BODY, 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont(FONT_BODY, 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont(FONT_BODY, 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{
                color: {C.PRI}; border: 1px solid {C.BORDER_B};
            }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        cfg_btn = QPushButton("⚙  CONFIGURE")
        cfg_btn.setFixedHeight(26)
        cfg_btn.setFont(QFont(FONT_BODY, 7))
        cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cfg_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{
                color: {C.ACC2}; border: 1px solid {C.ACC2};
            }}
        """)
        cfg_btn.clicked.connect(self._show_config)
        lay.addWidget(cfg_btn)

        return w

    def _set_chat_sessions(self, titles: list[str], active_title: str) -> None:
        self._chat_combo.blockSignals(True)
        self._chat_combo.clear()
        self._chat_combo.addItems(titles)
        index = self._chat_combo.findText(active_title)
        if index >= 0:
            self._chat_combo.setCurrentIndex(index)
        self._chat_combo.blockSignals(False)

    def _on_chat_selected(self, title: str) -> None:
        if title and self.on_text_command:
            self.on_text_command(f"/switchchat {title}")

    def _request_new_chat(self) -> None:
        if self.on_text_command:
            self.on_text_command("/newchat")

    def _request_stop(self) -> None:
        if self.on_stop_command:
            self.on_stop_command()

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont(FONT_BODY, 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        self._send_btn = QPushButton("▸")
        self._send_btn.setFixedSize(30, 30)
        self._send_btn.setFont(QFont(FONT_BODY, 11, QFont.Weight.Bold))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        self._send_btn.clicked.connect(self._send)
        row.addWidget(self._send_btn)

        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedSize(30, 30)
        self._stop_btn.setToolTip("Stop the current response")
        self._stop_btn.setFont(QFont(FONT_BODY, 9, QFont.Weight.Bold))
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background: #190808; color: {C.RED};
                border: 1px solid {C.RED}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: #2a0b0b; }}
        """)
        self._stop_btn.clicked.connect(self._request_stop)
        self._stop_btn.hide()
        row.addWidget(self._stop_btn)
        return row

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont(FONT_BODY, 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen  ·  [Esc] Collapse"))
        lay.addStretch()
        lay.addWidget(_fl("Aether  ·  Local Intelligence"))
        lay.addStretch()
        lay.addWidget(_fl("Ctrl+Alt+A Toggle · Esc Collapse", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell Aether what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.orb.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.orb.state    = state
        self.orb.speaking = (state == "SPEAKING")
        show_stop = state in ("THINKING", "PROCESSING", "SPEAKING") and bool(self.on_stop_command)
        self._send_btn.setVisible(not show_stop)
        self._stop_btn.setVisible(show_stop)
        if state == "ERROR":
            self.orb.flash_error()

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return (
                bool(d.get("llm_model")) and
                bool(d.get("stt_engine")) and
                bool(d.get("tts_engine"))
            )
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 560, 660
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, config_json: str):
        try:
            cfg = json.loads(config_json)
        except Exception:
            cfg = {}
        os.makedirs(CONFIG_DIR, exist_ok=True)
        from core.config_io import save_config  # merges: keeps settings the dialog does not show
        save_config(API_FILE, cfg)
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        llm = cfg.get("llm_model", "")
        stt = cfg.get("stt_engine", "")
        tts = cfg.get("tts_engine", "")
        self._log.append_log(
            f"SYS: Initialised. LLM={llm} | STT={stt} | TTS={tts}"
        )

    def _show_config(self):
        if self._overlay and self._overlay.isVisible():
            return
        current: dict = {}
        try:
            current = json.loads(API_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        ov = SetupOverlay(self.centralWidget(), initial=current, mode="config")
        cw = self.centralWidget()
        ow, oh = 560, 680
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_config_done)
        ov.show()
        self._overlay = ov

    def _on_config_done(self, config_json: str):
        try:
            cfg = json.loads(config_json)
        except Exception:
            cfg = {}
        os.makedirs(CONFIG_DIR, exist_ok=True)
        from core.config_io import save_config  # merges: keeps settings the dialog does not show
        save_config(API_FILE, cfg)
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        llm = cfg.get("llm_model", "")
        stt = cfg.get("stt_engine", "")
        tts = cfg.get("tts_engine", "")
        self._log.append_log(f"SYS: Config updated. LLM={llm} | STT={stt} | TTS={tts}")
        if self._on_reconfigure_cb:
            self._on_reconfigure_cb(cfg)


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class AetherUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        load_bundled_fonts()
        self._system_theme = detect_system_theme()
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_stop_command(self):
        return self._win.on_stop_command

    @on_stop_command.setter
    def on_stop_command(self, cb):
        self._win.on_stop_command = cb

    @property
    def on_reconfigure(self):
        return self._win._on_reconfigure_cb

    @on_reconfigure.setter
    def on_reconfigure(self, cb):
        self._win._on_reconfigure_cb = cb

    def set_state(self, state: str):
        # Background threads (TTS worker, tool dispatch, STT, etc.) call
        # this well after the window may have been destroyed during
        # shutdown -- PySide raises RuntimeError("wrapped C/C++ object of
        # type MainWindow has been deleted") in that case, and previously
        # that propagated out of a thread's `finally` block uncaught,
        # crashing the whole process (seen as exit code -1073741819).
        # There's nothing meaningful to update once the window is gone,
        # so swallow it here -- this is the single choke point every
        # set_state() caller goes through, so this covers all of them.
        try:
            self._win._state_sig.emit(state)
        except RuntimeError:
            pass

    def write_log(self, text: str):
        try:
            self._win._log_sig.emit(text)
        except RuntimeError:
            pass

    def clear_log(self):
        try:
            self._win._clear_log_sig.emit()
        except RuntimeError:
            pass

    def set_chat_sessions(self, titles: list[str], active_title: str):
        try:
            self._win._chat_sessions_sig.emit(titles, active_title)
        except RuntimeError:
            pass

    def confirm_blocking(self, message: str, timeout: float = 120.0) -> bool:
        """Thread-safe blocking Yes/No confirmation dialog for high-risk
        operations (shell exec, file delete, port scan, breach lookups).
        Safe to call from any background worker thread -- blocks the
        CALLING thread (not the Qt UI thread) until the user answers or
        `timeout` seconds elapse, at which point it fails closed (returns
        False) rather than hanging forever. Must NOT be called from the Qt
        UI thread itself: the UI thread is the one that shows the dialog
        and sets the result, so calling this from that same thread would
        deadlock waiting on an event nothing can ever set.

        This is the intended callback for core.safety.SafetyLayer's
        set_confirm_callback() -- see main.py's AetherLocal init for the
        wiring, and core/safety.py's requires_confirmation()/
        request_confirmation() for the gating logic that decides when to
        call this at all."""
        if threading.current_thread() is threading.main_thread():
            raise RuntimeError(
                "AetherUI.confirm_blocking() must be called from a background "
                "worker thread, not the main/UI thread (it would deadlock)."
            )
        result = {"answer": False, "event": threading.Event()}
        self._win._confirm_sig.emit(message, result)
        result["event"].wait(timeout=timeout)  # fails closed on timeout: answer stays False
        return result["answer"]

    def update_message_stats(self, data: dict) -> None:
        """Thread-safe update of the Message Stats panel. See StatsBar.update_stats
        for the expected keys (model, prompt_tokens, output_tokens, elapsed,
        tokens_per_sec, context_window)."""
        self._win._stats_sig.emit(data)


    # ── Startup panel (all thread-safe) ──────────────────────────────────
    def show_startup_panel(self) -> None:
        self._win._startup_sig.emit("show", "")

    def mark_startup_ready(self, key: str, error: bool = False) -> None:
        self._win._startup_sig.emit("error" if error else "ready", key)

    def set_startup_status(self, text: str) -> None:
        self._win._startup_sig.emit("status", text)

    def hide_startup_panel(self) -> None:
        self._win._startup_sig.emit("hide", "")

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
