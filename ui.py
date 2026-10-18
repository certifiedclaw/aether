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
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QTabWidget, QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
from core import config_home as _config_home
API_FILE   = _config_home.config_path()
CONFIG_DIR = API_FILE.parent

_DEFAULT_W, _DEFAULT_H = 1180, 780
_MIN_W,     _MIN_H     = 900, 620
_LEFT_W  = 180

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    # Mineral tones give Aether a calm, tactile feel instead of a neon HUD.
    BG        = "#101518"
    PANEL     = "#182125"
    PANEL2    = "#202c31"
    BORDER    = "#2a393f"
    BORDER_B  = "#42545b"
    BORDER_A  = "#34444a"
    PRI       = "#8bc9b2"
    PRI_DIM   = "#5d9e86"
    PRI_GHO   = "#203c32"
    SEC       = "#a4b5ec"
    SEC_DIM   = "#6e7fae"
    SEC_GHO   = "#293248"
    ACC       = "#e2ad6e"
    ACC2      = "#e6c796"
    GREEN     = "#94cdaa"
    GREEN_D   = "#5d9c73"
    RED       = "#e88983"
    MUTED_C   = "#d98b98"
    TEXT      = "#dce4e4"
    TEXT_DIM  = "#8b9b9f"
    TEXT_MED  = "#b2c0c0"
    WHITE     = "#f3f6f4"
    DARK      = "#141b1e"
    BAR_BG    = "#283438"


ROUTE_COLORS = {"local": C.PRI, "remote": C.SEC, "cloud": C.ACC}


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
FONT_DISPLAY = "Segoe UI"
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

class PresenceLine(QWidget):
    """A quiet, animated status line that keeps chat at the center of attention."""

    _ROUTES = {"local": "This PC", "remote": "GPU PC", "cloud": "Cloud"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.setMinimumWidth(220)
        self.setAccessibleName("Aether response status")
        self.muted = False
        self.speaking = False
        self.state = "INITIALISING"
        self.route = "local"
        self._phase = 0.0
        self._error_ticks = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(40)

    def _animate(self):
        self._phase = (self._phase + 0.12) % (math.pi * 2)
        self._error_ticks = max(0, self._error_ticks - 1)
        self.update()

    def set_route(self, route: str) -> None:
        route = route if route in ROUTE_COLORS else "local"
        self.route = route
        label = self._ROUTES[route]
        self.setToolTip(f"Response route: {label}")
        self.setAccessibleDescription(f"Response route: {label}")
        self.update()

    def flash_error(self):
        self._error_ticks = 30
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setBrush(QBrush(qcol(C.PANEL2)))
        painter.setPen(QPen(qcol(C.BORDER), 1))
        painter.drawRoundedRect(bounds, 10, 10)

        state_labels = {
            "LISTENING": "Ready when you are",
            "THINKING": "Thinking it through",
            "PROCESSING": "Working on it",
            "SPEAKING": "Aether is speaking",
            "MUTED": "Microphone muted",
            "ERROR": "Something went wrong",
        }
        color = (
            qcol(C.RED) if self._error_ticks or self.state == "ERROR"
            else qcol(C.ACC) if self.state in {"THINKING", "PROCESSING"}
            else qcol(C.TEXT_DIM) if self.muted or self.state == "MUTED"
            else qcol(ROUTE_COLORS.get(self.route, C.PRI))
        )
        painter.setPen(QPen(qcol(C.TEXT), 1))
        painter.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(16, 7, bounds.width() - 150, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            state_labels.get(self.state, "Aether is ready"),
        )

        route_text = self._ROUTES.get(self.route, "This PC")
        painter.setPen(QPen(color, 1))
        painter.setFont(QFont(FONT_BODY, 8, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(bounds.width() - 126, 7, 108, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            route_text,
        )

        line_rect = QRectF(16, 34, max(1, bounds.width() - 32), 13)
        base_y = line_rect.center().y()
        painter.setPen(QPen(qcol(C.BORDER_B), 1))
        painter.drawLine(QPointF(line_rect.left(), base_y), QPointF(line_rect.right(), base_y))
        amplitude = (
            4.2 if self.state == "SPEAKING"
            else 3.2 if self.state in {"THINKING", "PROCESSING"}
            else 1.8 if not self.muted and self.state != "MUTED"
            else 0.4
        )
        path = QPainterPath()
        steps = max(24, int(line_rect.width() / 5))
        for index in range(steps + 1):
            x = line_rect.left() + line_rect.width() * index / steps
            envelope = math.sin(math.pi * index / steps) ** 0.45
            phase = self._phase - index * 0.13
            y = base_y + math.sin(phase) * amplitude * envelope
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

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
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 8, 8)

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

        p.setFont(QFont(FONT_BODY, 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(10, 5, 52, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(60, 4, W - 70, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

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


class MessageInput(QPlainTextEdit):
    sendRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Ask Aether anything…  (/help for commands)")
        self.setToolTip(
            "Enter adds a new line. Press Ctrl+Enter or use Send to send."
        )
        self.setAccessibleName("Message Aether")
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.textChanged.connect(self._fit_height)
        self._fit_height()

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.sendRequested.emit()
            return
        super().keyPressEvent(event)

    def _fit_height(self):
        line_height = self.fontMetrics().lineSpacing()
        lines = min(max(self.document().blockCount(), 1), 6)
        self.setFixedHeight(max(54, min(132, lines * line_height + 24)))


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
        self.on_route = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        frame = QFrame()
        frame.setStyleSheet(
            f"background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 8px;"
        )
        outer.addWidget(frame)

        grid = QGridLayout(frame)
        grid.setContentsMargins(10, 7, 10, 7)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)

        self._value_labels: dict[str, QLabel] = {}

        fields = [
            ("model",  "Model"),
            ("input",  "Input"),
            ("output", "Output"),
            ("total",  "Total"),
            ("speed",  "Speed"),
            ("time",   "Time"),
        ]
        for i, (key, caption) in enumerate(fields):
            cell = QVBoxLayout()
            cell.setSpacing(0)

            cap = QLabel(caption)
            cap.setFont(QFont(FONT_BODY, 8, QFont.Weight.DemiBold))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            cell.addWidget(cap)

            val = QLabel("—")
            val.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
            val.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            val.setMinimumWidth(48)
            cell.addWidget(val)

            self._value_labels[key] = val
            grid.addLayout(cell, 0, i)

        # ── Context-window usage bar ───────────────────────────────────
        ctx_row = QHBoxLayout()
        ctx_row.setSpacing(8)
        ctx_cap = QLabel("Context window")
        ctx_cap.setFont(QFont(FONT_BODY, 8, QFont.Weight.DemiBold))
        ctx_cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        ctx_row.addWidget(ctx_cap)

        self._ctx_bar = QProgressBar()
        self._ctx_bar.setRange(0, 100)
        self._ctx_bar.setValue(0)
        self._ctx_bar.setTextVisible(False)
        self._ctx_bar.setFixedHeight(7)
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
        ctx_row.addWidget(self._ctx_bar, stretch=1)

        self._ctx_label = QLabel("0%")
        self._ctx_label.setFont(QFont(FONT_BODY, 7, QFont.Weight.Bold))
        self._ctx_label.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._ctx_label.setFixedWidth(80)
        ctx_row.addWidget(self._ctx_label)
        grid.addLayout(ctx_row, 1, 0, 1, 6)

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
        route = data.get("route")
        if route and self.on_route:
            self.on_route(route)

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
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "X")

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
            layout.addWidget(self._lbl("CONFIGURATION", 12, True))
            layout.addWidget(self._lbl("Update Aether\u2019s settings and click Apply.", 8, col=C.PRI_DIM))
        else:
            layout.addWidget(self._lbl("INITIALISATION REQUIRED", 12, True))
            layout.addWidget(self._lbl("Configure Aether before first boot.", 8, col=C.PRI_DIM))
        layout.addWidget(self._sep())

        self._build_stt_section(layout, _init)
        self._build_llm_section(layout, _init)
        self._build_tts_section(layout, _init)

        # Set correct initial state for TTS UI
        self._set_tts(self._sel_tts)

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
            [("whisper","Whisper"), ("vosk","Vosk")],
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
                ("local",  "Local (Ollama / LM Studio)"),
                ("gemini", "Gemini (cloud API key)"),
                ("openai_api", "OpenAI API (usage billed separately)"),
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
                ("ollama", "Ollama"),
                ("openai", "LM Studio / OpenAI"),
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
            b = QPushButton(rec.tag)
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
            [("edgetts","EdgeTTS"), ("kokoro","Kokoro"), ("elevenlabs","ElevenLabs")],
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
            cancel_btn = QPushButton("CANCEL")
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

        btn_label = "APPLY CHANGES" if mode == "config" else "INITIALISE SYSTEMS"
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
        if key == "elevenlabs" and hasattr(self, "_tts_voice_input"):
            voice = self._tts_voice_input.text().strip()
            if (not voice or voice.endswith("Neural") or
                    voice.startswith(("af_", "am_", "bf_", "bm_", "jf_", "jm_"))):
                self._tts_voice_input.setText("pNInz6obpgDQGcFmaJgB")
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
        title = QLabel("SYSTEMS INITIALISING")
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
        label  = "READY" if ok else "ERROR"

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
        self.setWindowTitle(f"{APP_NAME} — Local-first assistant · v{__version__}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_stop_command  = None
        self.on_action_catalog = None
        self.on_action_command = None
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

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=1)

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

        self._log.append_log("SYS: Aether is ready. Your conversations stay on this device by default.")

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
        sc_focus_input = QShortcut(QKeySequence("Ctrl+L"), self)
        sc_focus_input.activated.connect(self._focus_input)

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
        """Esc: hide the system rail for a more focused chat view."""
        if self.isFullScreen():
            self.showNormal()
            return
        if not self._collapsed:
            self._saved_size = self.size()
            self._left_panel.hide()
            self.resize(980, 700)
            self._collapsed = True
        else:
            self._left_panel.show()
            if self._saved_size:
                self.resize(self._saved_size)
            self._collapsed = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.height() < 700
        if hasattr(self, "_chat_layout"):
            self._chat_layout.setSpacing(6 if compact else 10)
            self._log.setMinimumHeight(100 if compact else 200)
            self.orb.setFixedHeight(44 if compact else 58)
            self._chat_subtitle.setVisible(not compact)
            self._stats_heading.setVisible(not compact)
            self._input_hint.setVisible(not compact)
            self._update_prompt_suggestions()
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
        self._bar_gpu.setVisible(gpu >= 0)
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")

        # TMP
        tmp = snap["tmp"]
        self._bar_tmp.setVisible(tmp >= 0)
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"Uptime  {h:02d}h {m:02d}m")
        except Exception:
            self._uptime_lbl.setText("Uptime  unavailable")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"Processes  {proc_count}")
        except Exception:
            self._proc_lbl.setText("Processes  unavailable")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(66)
        w.setStyleSheet(f"background: {C.PANEL}; border-bottom: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 0, 24, 0)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        title = QLabel("Aether")
        title.setFont(QFont(FONT_BODY, 20, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        brand.addWidget(title)
        sub = QLabel("Your private, local-first assistant")
        sub.setFont(QFont(FONT_BODY, 9))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        brand.addWidget(sub)
        lay.addLayout(brand)
        lay.addStretch()

        version = QLabel(f"{APP_NAME}  ·  v{__version__}")
        version.setFont(QFont(FONT_BODY, 9))
        version.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(version)
        lay.addSpacing(24)

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont(FONT_BODY, 12, QFont.Weight.DemiBold))
        self._clock_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont(FONT_BODY, 8))
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
        w.setMinimumWidth(_LEFT_W)
        w.setMaximumWidth(220)
        w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(8)

        hdr = QLabel("This machine")
        hdr.setFont(QFont(FONT_BODY, 11, QFont.Weight.DemiBold))
        hdr.setStyleSheet(f"color: {C.TEXT}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 8px;")
        lay.addWidget(hdr)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("RAM", C.SEC)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TEMP", C.RED)

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 8px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(10, 9, 10, 9)
        ip_lay.setSpacing(5)

        self._uptime_lbl = QLabel("Uptime  —")
        self._uptime_lbl.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("Processes  —")
        self._proc_lbl.setFont(QFont(FONT_BODY, 9))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"Operating system  {os_name}")
        os_lbl.setFont(QFont(FONT_BODY, 8))
        os_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addStretch()

        privacy = QLabel("Private by default\nLocal models keep prompts on this device.")
        privacy.setWordWrap(True)
        privacy.setFont(QFont(FONT_BODY, 8))
        privacy.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: {C.PANEL};"
            f"border: 1px solid {C.BORDER}; border-radius: 8px; padding: 10px;"
        )
        lay.addWidget(privacy)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setMinimumWidth(580)
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        w.setStyleSheet(f"background: {C.BG};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        def _section_label(txt):
            l = QLabel(txt)
            l.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
            l.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            return l

        self._right_tabs = QTabWidget()
        self._right_tabs.setDocumentMode(True)
        self._right_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                background: transparent; border: none; top: 1px;
            }}
            QTabBar::tab {{
                background: transparent; color: {C.TEXT_DIM};
                border: none; border-bottom: 2px solid transparent;
                padding: 9px 18px; min-width: 72px;
            }}
            QTabBar::tab:selected {{
                color: {C.TEXT}; border-bottom: 2px solid {C.PRI};
            }}
            QTabBar::tab:hover:!selected {{ color: {C.TEXT_MED}; }}
        """)

        chat_tab = QWidget()
        chat_tab.setStyleSheet("background: transparent;")
        chat_lay = QVBoxLayout(chat_tab)
        self._chat_layout = chat_lay
        chat_lay.setContentsMargins(0, 10, 0, 0)
        chat_lay.setSpacing(10)

        chat_heading = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(1)
        chat_title = QLabel("Conversation")
        chat_title.setFont(QFont(FONT_BODY, 17, QFont.Weight.DemiBold))
        chat_title.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        title_block.addWidget(chat_title)
        chat_subtitle = QLabel("Ask a question, or choose a starting point below.")
        self._chat_subtitle = chat_subtitle
        chat_subtitle.setFont(QFont(FONT_BODY, 9))
        chat_subtitle.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        title_block.addWidget(chat_subtitle)
        chat_heading.addLayout(title_block)
        chat_heading.addStretch()
        self._chat_status_lbl = QLabel("Ready to help")
        self._chat_status_lbl.setAccessibleName("Aether status")
        self._chat_status_lbl.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
        self._chat_status_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        chat_heading.addWidget(self._chat_status_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)
        chat_lay.addLayout(chat_heading)

        chat_row = QHBoxLayout()
        self._chat_combo = QComboBox()
        self._chat_combo.setToolTip("Select a saved conversation")
        self._chat_combo.currentTextChanged.connect(self._on_chat_selected)
        self._chat_combo.setMinimumHeight(36)
        self._chat_combo.setStyleSheet(f"""
            QComboBox {{
                color: {C.TEXT}; background: {C.PANEL};
                border: 1px solid {C.BORDER}; border-radius: 7px; padding: 6px 10px;
            }}
            QComboBox:hover {{ border-color: {C.BORDER_B}; }}
            QComboBox QAbstractItemView {{
                color: {C.TEXT}; background: {C.PANEL}; selection-background-color: {C.PANEL2};
            }}
        """)
        chat_row.addWidget(self._chat_combo, stretch=1)
        new_chat_btn = QPushButton("New chat")
        new_chat_btn.setToolTip("Start a new conversation (Ctrl+Shift+N)")
        new_chat_btn.setMinimumHeight(36)
        new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_chat_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT}; background: {C.PANEL};
                border: 1px solid {C.BORDER}; border-radius: 7px; padding: 6px 14px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        new_chat_btn.clicked.connect(self._request_new_chat)
        chat_row.addWidget(new_chat_btn)
        chat_lay.addLayout(chat_row)

        self.orb = PresenceLine()
        chat_lay.addWidget(self.orb)

        self._log = LogWidget()
        self._log.setMinimumHeight(200)
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 10px;
                padding: 14px; selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent; width: 8px; border: none; margin: 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 4px; min-height: 28px;
            }}
        """)
        chat_lay.addWidget(self._log, stretch=1)

        stats_heading = QHBoxLayout()
        stats_heading.addStretch()
        self._stats_bar = StatsBar()
        self._stats_bar.on_route = self.orb.set_route
        self._stats_bar.setMaximumHeight(90)
        self._stats_heading = _section_label("Response details")
        stats_heading.addWidget(self._stats_heading)
        chat_lay.addLayout(stats_heading)
        chat_lay.addWidget(self._stats_bar)

        input_heading = QHBoxLayout()
        input_heading.addWidget(_section_label("Message"))
        input_heading.addStretch()
        input_hint = QLabel("Enter for a new line  ·  Ctrl+Enter to send  ·  Ctrl+L to focus")
        self._input_hint = input_hint
        input_hint.setFont(QFont(FONT_BODY, 8))
        input_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        input_heading.addWidget(input_hint)
        chat_lay.addLayout(input_heading)
        chat_lay.addLayout(self._build_input_row())
        self._build_prompt_suggestions(chat_lay)

        chat_controls = QHBoxLayout()
        chat_controls.setSpacing(8)
        self._mute_btn = QPushButton("Microphone active")
        self._mute_btn.setFixedHeight(34)
        self._mute_btn.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        chat_controls.addWidget(self._mute_btn)
        chat_controls.addStretch()

        fullscreen_btn = QPushButton("Full screen")
        fullscreen_btn.setFixedHeight(34)
        fullscreen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fullscreen_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_MED}; background: transparent;
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 5px 12px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        chat_controls.addWidget(fullscreen_btn)

        cfg_btn = QPushButton("Settings")
        cfg_btn.setFixedHeight(34)
        cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cfg_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_MED}; background: transparent;
                border: 1px solid {C.BORDER}; border-radius: 7px; padding: 5px 12px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cfg_btn.clicked.connect(self._show_config)
        chat_controls.addWidget(cfg_btn)
        chat_lay.addLayout(chat_controls)
        self._right_tabs.addTab(chat_tab, "Chat")

        actions_tab = QWidget()
        actions_tab.setStyleSheet("background: transparent;")
        actions_lay = QVBoxLayout(actions_tab)
        actions_lay.setContentsMargins(0, 0, 0, 0)
        actions_scroll = QScrollArea()
        actions_scroll.setWidgetResizable(True)
        actions_scroll.setFrameShape(QFrame.Shape.NoFrame)
        actions_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        actions_content = QWidget()
        actions_content.setStyleSheet("background: transparent;")
        actions_content_lay = QVBoxLayout(actions_content)
        actions_content_lay.setContentsMargins(0, 10, 0, 0)
        actions_content_lay.setSpacing(10)
        actions_content_lay.addWidget(_section_label("Add a file"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        actions_content_lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file selected. Drop a file here or choose one to get started.")
        self._file_hint.setFont(QFont(FONT_BODY, 9))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        actions_content_lay.addWidget(self._file_hint)

        actions_content_lay.addSpacing(8)
        actions_content_lay.addWidget(_section_label("Quick actions"))
        quick_actions = QGridLayout()
        quick_actions.setContentsMargins(0, 0, 0, 0)
        quick_actions.setHorizontalSpacing(5)
        quick_actions.setVerticalSpacing(5)
        self._quick_action_buttons = {}
        for index, (label, name) in enumerate([
            ("Research", "research"),
            ("Write code", "code_helper"),
            ("Files", "file_controller"),
            ("PC control", "computer_settings"),
            ("Weather", "weather_report"),
            ("Reminder", "reminder"),
            ("Analyze screen", "screen_process"),
            ("YouTube", "youtube_video"),
        ]):
            button = QPushButton(label)
            button.setToolTip(f"Open the {label.split(maxsplit=1)[-1]} form")
            button.setMinimumHeight(42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT};
                    border: 1px solid {C.BORDER}; border-radius: 8px;
                    padding: 8px; text-align: left;
                }}
                QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI_DIM}; }}
            """)
            button.clicked.connect(
                lambda _checked=False, selected=name: self._open_named_action(selected)
            )
            quick_actions.addWidget(button, index // 2, index % 2)
            self._quick_action_buttons[name] = button
        actions_content_lay.addLayout(quick_actions)

        actions_btn = QPushButton("Browse all actions")
        actions_btn.setFixedHeight(40)
        actions_btn.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
        actions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        actions_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px;
            }}
            QPushButton:hover {{ color: {C.SEC}; border-color: {C.SEC_DIM}; }}
        """)
        actions_btn.clicked.connect(self._open_action_catalog)
        actions_content_lay.addWidget(actions_btn)
        actions_content_lay.addStretch()
        actions_scroll.setWidget(actions_content)
        actions_lay.addWidget(actions_scroll)
        self._right_tabs.addTab(actions_tab, "Actions")
        lay.addWidget(self._right_tabs, stretch=1)
        return w

    def _open_named_action(self, name: str) -> None:
        if not self.on_action_catalog:
            QMessageBox.information(self, "Actions", "Aether's action catalog is not ready yet.")
            return
        try:
            tools = self.on_action_catalog()
        except Exception as exc:
            QMessageBox.critical(self, "Actions unavailable", str(exc))
            return
        if name == "research":
            research = next(
                (tool.get("function", {}) for tool in tools or []
                 if tool.get("function", {}).get("name") == "langchain"),
                None,
            )
            if research:
                research = dict(research)
                research["description"] = (
                    "Multi-step research using web search and Wikipedia. "
                    "Describe the question or topic to investigate."
                )
                research["preset_args"] = {"action": "research"}
                research["parameters"] = {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "What should the research agent investigate?",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional background or constraints",
                        },
                    },
                    "required": ["task"],
                }
                self._open_action_form(research)
                return
            web_search = next(
                (tool.get("function", {}) for tool in tools or []
                 if tool.get("function", {}).get("name") == "web_search"),
                None,
            )
            if web_search:
                web_search = dict(web_search)
                web_search["description"] = (
                    "The multi-step research integration is unavailable. "
                    "This fallback performs a single web search."
                )
                web_search["parameters"] = {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Topic or question to search for",
                        },
                    },
                    "required": ["query"],
                }
                self._open_action_form(web_search)
                return
        for tool in tools or []:
            function = tool.get("function", {})
            if function.get("name") == name:
                self._open_action_form(function)
                return
        QMessageBox.information(
            self, "Action unavailable",
            f"{name.replace('_', ' ').capitalize()} is not enabled in the current setup.",
        )

    def _open_action_catalog(self) -> None:
        if not self.on_action_catalog:
            QMessageBox.information(self, "Actions", "Aether's action catalog is not ready yet.")
            return
        try:
            tools = self.on_action_catalog()
        except Exception as exc:
            QMessageBox.critical(self, "Actions unavailable", str(exc))
            return

        entries = []
        for tool in tools or []:
            function = tool.get("function", {})
            name = function.get("name")
            if name:
                entries.append(function)
        friendly_names = {
            "open_app": "Open an app or website",
            "web_search": "Research the web",
            "langchain": "Multi-step research (web and Wikipedia)",
            "weather_report": "Check weather",
            "send_message": "Send a message",
            "reminder": "Set a reminder",
            "youtube_video": "YouTube videos",
            "screen_process": "Analyze screen or camera",
            "computer_settings": "Computer settings and controls",
            "browser_control": "Control a web browser",
            "file_controller": "Manage files and folders",
            "desktop_control": "Manage the desktop",
            "code_helper": "Write or work with code",
            "dev_agent": "Build a software project",
            "agent_task": "Run a multi-step task",
            "computer_control": "Control mouse and keyboard",
            "game_updater": "Manage game updates",
            "flight_finder": "Find flights",
            "file_processor": "Analyze an uploaded file",
            "osint_lookup": "Authorized OSINT lookup",
            "pdf_tool": "Work with a PDF",
            "rag_query": "Search indexed documents",
            "undo": "Undo a file operation",
            "shutdown_aether": "Shut down Aether",
            "save_memory": "Save a memory",
        }
        entries.sort(
            key=lambda item: friendly_names.get(
                item["name"], item["name"].replace("_", " ").capitalize()
            ).casefold()
        )
        quick_commands = [
            ("/help", "List chat commands"),
            ("/status", "Run system diagnostics"),
            ("/doctor", "Check installation and runtime health"),
            ("/doctor fix", "Apply safe doctor fixes"),
            ("/selftest", "Run subsystem self-tests"),
            ("/crashes", "Show recent crash reports"),
            ("/audit", "Review recent tool calls and approvals"),
            ("/audit verify", "Verify the audit log chain"),
            ("/perf", "Show model speed and resource statistics"),
            ("/resources", "Show resource governor status"),
            ("/backup", "Back up memory"),
            ("/memstats", "Show memory statistics"),
            ("/prune", "Remove duplicate and old memories"),
            ("/clear", "Clear chat history"),
            ("/newchat", "Start a new saved chat"),
            ("/chats", "List saved chats"),
        ]
        if not entries and not quick_commands:
            QMessageBox.information(self, "Actions", "No actions are currently available.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Aether actions and commands ({len(entries) + len(quick_commands)})")
        dialog.resize(560, 620)
        dialog.setStyleSheet(f"""
            QDialog {{ background: {C.DARK}; color: {C.TEXT}; }}
            QLineEdit, QComboBox, QPlainTextEdit {{
                background: #000d14; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 5px;
            }}
            QPushButton {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 5px 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Choose a task, fill in its details, and run it. "
            "Search by task or capability; approval gates still apply to sensitive actions."
        )
        intro.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        search = QLineEdit()
        search.setPlaceholderText("Search actions…")
        search.setClearButtonEnabled(True)
        layout.addWidget(search)

        categories = QComboBox()
        category_names = [
            "All categories",
            "Research & web",
            "Files & documents",
            "Computer & browser",
            "Media & travel",
            "Productivity",
            "Code & projects",
            "System & memory",
            "Chat commands",
        ]
        categories.addItems(category_names)
        layout.addWidget(categories)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C.BORDER}; }}")
        container = QWidget()
        buttons_layout = QVBoxLayout(container)
        buttons_layout.setContentsMargins(6, 6, 6, 6)
        buttons_layout.setSpacing(5)

        action_buttons = []
        for command, description in quick_commands:
            button = QPushButton(f"{command}\n{description}")
            button.setAccessibleName(f"Command {command}")
            button.setToolTip(description)
            button.setMinimumHeight(42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.ACC2};
                    border: 1px solid {C.BORDER}; border-radius: 4px;
                    padding: 6px 9px; text-align: left;
                }}
                QPushButton:hover {{ border: 1px solid {C.ACC2}; }}
            """)

            def _run_quick_command(_checked=False, selected=command):
                if not self.on_text_command:
                    QMessageBox.information(dialog, "Actions unavailable", "Aether is not ready.")
                    return
                dialog.accept()
                self.on_text_command(selected)

            button.clicked.connect(_run_quick_command)
            buttons_layout.addWidget(button)
            action_buttons.append(
                (button, f"{command} {description}".casefold(), "Chat commands")
            )

        for function in entries:
            name = function["name"]
            description = str(function.get("description", "")).strip()
            display_name = friendly_names.get(name, name.replace("_", " ").capitalize())
            summary = description if len(description) <= 110 else description[:107].rstrip() + "…"
            button = QPushButton(f"{display_name}\n{summary}")
            button.setAccessibleName(display_name)
            button.setToolTip(description)
            button.setMinimumHeight(56)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT};
                    border: 1px solid {C.BORDER}; border-radius: 4px;
                    padding: 6px 9px; text-align: left;
                }}
                QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI_DIM}; }}
            """)
            button.clicked.connect(
                lambda _checked=False, selected=function: (
                    dialog.accept(), self._open_action_form(selected)
                )
            )
            buttons_layout.addWidget(button)
            action_buttons.append(
                (
                    button,
                    f"{display_name} {name} {description}".casefold(),
                    self._action_category(name),
                )
            )
        buttons_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        def _filter_actions(query: str = "") -> None:
            needle = query.casefold().strip()
            selected_category = categories.currentText()
            for button, searchable, category in action_buttons:
                category_matches = (
                    selected_category == "All categories"
                    or category == selected_category
                )
                button.setVisible(category_matches and (not needle or needle in searchable))

        search.textChanged.connect(_filter_actions)
        categories.currentTextChanged.connect(lambda _category: _filter_actions(search.text()))
        _filter_actions()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.reject)
        layout.addWidget(close_btn)
        dialog.exec()

    @staticmethod
    def _action_category(name: str) -> str:
        categories = {
            "Research & web": {
                "web_search", "langchain", "open_interpreter", "llama_index",
                "mcp.call", "osint_lookup",
            },
            "Files & documents": {
                "file_controller", "file_processor", "pdf_tool", "rag_query",
                "memory_tool", "undo",
            },
            "Computer & browser": {
                "open_app", "browser_control", "computer_settings",
                "computer_control", "desktop_control", "screen_process",
            },
            "Media & travel": {
                "youtube_video", "flight_finder", "weather_report",
            },
            "Productivity": {
                "send_message", "reminder", "agent_task", "goal_manager",
                "goal_create", "goal_update", "goal_complete", "goal_cancel",
                "goal_list", "goal_add_subtask",
            },
            "Code & projects": {"code_helper", "dev_agent", "game_updater"},
            "System & memory": {
                "diagnostics", "crash_report", "obs_report", "observability",
                "shutdown_aether", "save_memory", "extra_tool",
            },
        }
        for category, names in categories.items():
            if name in names:
                return category
        return "System & memory"

    def _open_action_form(self, function: dict) -> None:
        name = function["name"]
        description = str(function.get("description", "")).strip()
        schema = function.get("parameters", {})
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        preset_args = dict(function.get("preset_args", {}))

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Run {name}")
        dialog.resize(560, 620)
        dialog.setStyleSheet(f"""
            QDialog {{ background: {C.DARK}; color: {C.TEXT}; }}
            QLineEdit, QComboBox, QPlainTextEdit {{
                background: #000d14; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 5px;
            }}
            QPushButton {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 5px 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        layout = QVBoxLayout(dialog)
        description_label = QLabel(description or "Run this action with the values below.")
        description_label.setTextFormat(Qt.TextFormat.PlainText)
        description_label.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        description_label.setWordWrap(True)
        layout.addWidget(description_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C.BORDER}; }}")
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        fields = {}

        for field_name, field_schema in properties.items():
            field_type = field_schema.get("type", "string")
            field_description = str(field_schema.get("description", "")).strip()
            label = QLabel(f"{field_name}{' *' if field_name in required else ''}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            label.setToolTip(field_description)
            default = field_schema.get("default")

            if field_type == "boolean":
                widget = QCheckBox()
                widget.setChecked(bool(default))
                widget.setToolTip(field_description)
            elif field_type in ("array", "object"):
                widget = QPlainTextEdit()
                widget.setPlaceholderText(
                    "Enter valid JSON " + ("array" if field_type == "array" else "object")
                )
                widget.setFixedHeight(72)
                if default is not None:
                    widget.setPlainText(json.dumps(default, ensure_ascii=False))
                widget.setToolTip(field_description)
            elif field_schema.get("enum"):
                widget = QComboBox()
                for choice in field_schema["enum"]:
                    widget.addItem(str(choice), userData=choice)
                if default is not None:
                    index = widget.findData(default)
                    if index >= 0:
                        widget.setCurrentIndex(index)
                widget.setToolTip(field_description)
            else:
                widget = QLineEdit()
                widget.setPlaceholderText(field_description or field_type)
                if default is not None:
                    widget.setText(str(default))
                widget.setToolTip(field_description)

            form.addRow(label, widget)
            fields[field_name] = (field_type, widget, field_name in required)

        scroll.setWidget(form_widget)
        layout.addWidget(scroll, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run action")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def _run_action() -> None:
            args = dict(preset_args)
            for field_name, (field_type, widget, is_required) in fields.items():
                if isinstance(widget, QCheckBox):
                    args[field_name] = widget.isChecked()
                    continue
                if isinstance(widget, QComboBox):
                    value = widget.currentData()
                elif isinstance(widget, QPlainTextEdit):
                    text = widget.toPlainText().strip()
                    if not text:
                        if is_required:
                            QMessageBox.warning(dialog, "Required value", f"{field_name} is required.")
                            return
                        continue
                    try:
                        value = json.loads(text)
                    except json.JSONDecodeError as exc:
                        QMessageBox.warning(dialog, "Invalid JSON", f"{field_name}: {exc}")
                        return
                    if field_type == "array" and not isinstance(value, list):
                        QMessageBox.warning(dialog, "Invalid value", f"{field_name} must be a JSON array.")
                        return
                    if field_type == "object" and not isinstance(value, dict):
                        QMessageBox.warning(dialog, "Invalid value", f"{field_name} must be a JSON object.")
                        return
                else:
                    value = widget.text().strip()
                    if not value:
                        if is_required:
                            QMessageBox.warning(dialog, "Required value", f"{field_name} is required.")
                            return
                        continue
                    if field_type == "integer":
                        try:
                            value = int(value)
                        except ValueError:
                            QMessageBox.warning(dialog, "Invalid value", f"{field_name} must be an integer.")
                            return
                    elif field_type == "number":
                        try:
                            value = float(value)
                        except ValueError:
                            QMessageBox.warning(dialog, "Invalid value", f"{field_name} must be a number.")
                            return
                        if not math.isfinite(value):
                            QMessageBox.warning(dialog, "Invalid value", f"{field_name} must be finite.")
                            return
                args[field_name] = value

            if not self.on_action_command:
                QMessageBox.information(dialog, "Actions unavailable", "Aether is not ready to run actions.")
                return
            try:
                self.on_action_command(name, args)
            except Exception as exc:
                QMessageBox.critical(dialog, "Action could not start", str(exc))
                return
            dialog.accept()

        buttons.accepted.connect(_run_action)
        dialog.exec()

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
        self._input = MessageInput()
        self._input.setFont(QFont(FONT_BODY, 10))
        self._input.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {C.PANEL}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 10px; padding: 10px 12px;
            }}
            QPlainTextEdit:focus {{ border: 1px solid {C.PRI_DIM}; }}
        """)
        self._input.sendRequested.connect(self._send)
        self._input.textChanged.connect(self._update_prompt_suggestions)
        row.addWidget(self._input)

        self._send_btn = QPushButton("Send")
        self._send_btn.setFixedSize(78, 48)
        self._send_btn.setAccessibleName("Send message")
        self._send_btn.setFont(QFont(FONT_BODY, 10, QFont.Weight.DemiBold))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI}; color: {C.BG};
                border: 1px solid {C.PRI}; border-radius: 9px;
            }}
            QPushButton:hover {{ background: {C.TEXT}; border-color: {C.TEXT}; }}
            QPushButton:disabled {{
                background: {C.PANEL}; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER};
            }}
        """)
        self._send_btn.setEnabled(False)
        self._input.textChanged.connect(self._update_send_button)
        self._send_btn.clicked.connect(self._send)
        row.addWidget(self._send_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setFixedSize(68, 40)
        self._stop_btn.setToolTip("Stop the current response")
        self._stop_btn.setFont(QFont(FONT_BODY, 9, QFont.Weight.DemiBold))
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.RED};
                border: 1px solid {C.RED}; border-radius: 9px;
            }}
            QPushButton:hover {{ background: {C.PANEL2}; }}
        """)
        self._stop_btn.clicked.connect(self._request_stop)
        self._stop_btn.hide()
        row.addWidget(self._stop_btn)
        return row

    def _build_prompt_suggestions(self, parent_layout: QVBoxLayout) -> None:
        self._prompt_suggestions = QWidget()
        self._prompt_suggestions.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self._prompt_suggestions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)

        label = QLabel("Try:")
        label.setFont(QFont(FONT_BODY, 8, QFont.Weight.DemiBold))
        label.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        row.addWidget(label)

        for title, prompt in (
            ("Get started", "What can you help me with?"),
            ("Research", "Research the latest AI news"),
        ):
            button = QPushButton(title)
            button.setAccessibleName(f"Use example: {prompt}")
            button.setToolTip(f"Put “{prompt}” in the message box")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER}; border-radius: 7px;
                    padding: 6px 10px;
                }}
                QPushButton:hover {{
                    color: {C.PRI}; border-color: {C.PRI_DIM};
                }}
            """)
            button.clicked.connect(
                lambda _checked=False, value=prompt: self._use_prompt(value)
            )
            row.addWidget(button)

        row.addStretch()
        parent_layout.addWidget(self._prompt_suggestions)

    def _update_prompt_suggestions(self) -> None:
        self._prompt_suggestions.setVisible(
            self.height() >= 700 and not bool(self._input.toPlainText().strip())
        )

    def _use_prompt(self, prompt: str) -> None:
        if self._input.toPlainText().strip():
            return
        self._input.setPlainText(prompt)
        self._input.setFocus()
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)

    def _update_send_button(self) -> None:
        self._send_btn.setEnabled(
            bool(self._input.toPlainText().strip())
        )

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(30)
        w.setStyleSheet(f"background: {C.PANEL}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(20, 0, 20, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont(FONT_BODY, 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("F4  Microphone"))
        lay.addStretch()
        lay.addWidget(_fl("Ctrl+Enter  Send"))
        lay.addStretch()
        lay.addWidget(_fl("Ctrl+Alt+A  Show app  ·  Esc  Hide system panel", C.TEXT_DIM))
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
            self._mute_btn.setText("Microphone muted")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL}; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 8px; padding: 5px 12px;
                }}
            """)
        else:
            self._mute_btn.setText("Microphone active")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL}; color: {C.GREEN};
                    border: 1px solid {C.BORDER}; border-radius: 8px; padding: 5px 12px;
                }}
                QPushButton:hover {{ border-color: {C.GREEN_D}; }}
            """)

    def _send(self):
        txt = self._input.toPlainText().strip()
        if not txt or not self.on_text_command:
            return
        self._input.clear()
        threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _focus_input(self):
        self._input.setFocus()

    def _apply_state(self, state: str):
        self.orb.state    = state
        self.orb.speaking = (state == "SPEAKING")
        status = {
            "LISTENING": ("Ready to help", C.GREEN),
            "THINKING": ("Thinking…", C.ACC),
            "PROCESSING": ("Working on it…", C.ACC),
            "SPEAKING": ("Speaking…", C.PRI),
            "MUTED": ("Microphone muted", C.TEXT_MED),
            "ERROR": ("Something went wrong — see the conversation log", C.RED),
        }.get(state, (state.replace("_", " ").title(), C.TEXT_MED))
        self._chat_status_lbl.setText(status[0])
        self._chat_status_lbl.setStyleSheet(
            f"color: {status[1]}; background: transparent;"
        )
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
    def on_action_catalog(self):
        return self._win.on_action_catalog

    @on_action_catalog.setter
    def on_action_catalog(self, cb):
        self._win.on_action_catalog = cb

    @property
    def on_action_command(self):
        return self._win.on_action_command

    @on_action_command.setter
    def on_action_command(self, cb):
        self._win.on_action_command = cb

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
