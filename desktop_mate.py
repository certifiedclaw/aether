"""
desktop_mate.py - floating desktop companion for Aether (PyQt6).

A small always-on-top character that mirrors what Aether is doing and shows
the start of each reply in a speech bubble. MainWindow in ui.py owns it:

    self._mate = DesktopMate(assets_dir=str(BASE_DIR / "assets" / "mate"))
    self._mate.show()
    self._mate.on_app_state("THINKING")      # called from MainWindow._apply_state
    self._mate.on_log_line("Aether: Done.")  # called for every log line

Everything it needs arrives on the Qt UI thread (MainWindow's signals), so the
public methods are not meant to be called from worker threads directly.

Art: assets/mate/<state>/0.png, 1.png, ... (transparent PNGs, ~235px wide) for
any of the STATES below. One frame is bobbed by the mate itself; several frames
are cycled as an animation. A state with no art falls back to the built-in
vector hooded cat, so partial or missing art is fine.

Config keys read by MainWindow (config file, both optional):
    desktop_mate          bool, default true  - create the mate at startup
    desktop_mate_bubbles  bool, default true  - show reply text in the bubble
"""
from __future__ import annotations

import math
import random
import re
from pathlib import Path

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

STATES = (
    "idle", "happy", "excited", "thinking", "speaking", "listening", "working",
    "sleeping", "sad", "angry", "confused", "hiding", "error",
)
W, H = 240, 290  # the bubble lives in the top 90px, the character below it
SLEEP_AFTER_MS = 5 * 60 * 1000
BUBBLE_MAX_CHARS = 140

# Bubble geometry. The text is trimmed to fit this box with real font metrics
# (see DesktopMate._fit_bubble) so a wider fallback font can't overflow it.
BUBBLE_PAD = 8
BUBBLE_MAX_H = 78
BUBBLE_BOTTOM = 82
BUBBLE_INNER_W = W - 16 - 2 * BUBBLE_PAD
_WRAP = int(Qt.AlignmentFlag.AlignLeft.value | Qt.TextFlag.TextWordWrap.value)

# Qt's plain word-wrap can't break inside a token that has no spaces (a long
# URL or file path would run off the bubble), and its wrap-anywhere mode
# would split ordinary words too. So only tokens of 20+ characters get
# invisible zero-width-space break points, every 12 characters.
_LONG_TOKEN = re.compile(r"\S{20,}")


def _soft_wrap(text: str) -> str:
    return _LONG_TOKEN.sub(
        lambda m: "​".join(m.group(0)[i:i + 12] for i in range(0, len(m.group(0)), 12)),
        text,
    )

# Aether's UI states (core/conversation_core.py, core/tool_dispatch.py,
# main.py) -> mate states. LISTENING is the app's *resting* state - it is set
# after every reply - not "the mic is hot", so it maps to idle rather than the
# pulsing "listening" ring. The ring stays available via set_state("listening")
# for when the app grows a distinct mic-active signal.
APP_STATE_MAP = {
    "LISTENING": "idle",
    "THINKING": "thinking",
    "PROCESSING": "working",
    "SPEAKING": "speaking",
    "MUTED": "sleeping",
    "ERROR": "error",
}

_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_LINE_PREFIX = re.compile(r"(?m)^\s{0,3}(?:#{1,6}|>|[-*•])\s+")
_MD_EMPHASIS = re.compile(r"[*`]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def bubble_text(reply: str, limit: int = BUBBLE_MAX_CHARS) -> str:
    """Turn a raw reply ("Aether: **Done.** Moved 3 files...") into a short,
    plain-text snippet for the speech bubble: strip the speaker prefix and
    markdown, then keep whole sentences while they fit in `limit` characters.
    A first sentence longer than `limit` is cut with an ellipsis."""
    text = (reply or "").strip()
    if text.lower().startswith("aether:"):
        text = text[len("aether:"):]
    text = _CODE_BLOCK.sub(" (code) ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_LINE_PREFIX.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    text = " ".join(text.split())
    if not text:
        return ""

    out = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    if out:
        return out
    return text[: max(1, limit - 1)].rstrip() + "…"


class DesktopMate(QWidget):
    activated = pyqtSignal()  # double-click: ask the app to show its main window

    def __init__(self, assets_dir="assets/mate", parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar entry
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # A floating companion must never keep the app alive on its own.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFixedSize(W, H)

        self.show_bubbles = True
        self._bubble_font = QFont("Segoe UI", 9)
        self._state = "idle"
        self._tick = 0
        self._bubble = ""
        self._drag = None
        self._press_pos = None
        self._wander = False
        self._wander_dir = 1
        self._frames = self._load_frames(Path(assets_dir))

        self._timer = QTimer(self, interval=40, timeout=self._on_tick)
        self._bubble_timer = QTimer(self, singleShot=True, timeout=self._clear_bubble)
        self._sleep_timer = QTimer(self, singleShot=True, timeout=self._maybe_sleep)
        self._sleep_timer.start(SLEEP_AFTER_MS)

        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - W - 40, scr.bottom() - H - 10)

    # ---------- public API (UI thread) ----------
    def set_state(self, state: str):
        if state in STATES:
            self._set(state)
            self._sleep_timer.start(SLEEP_AFTER_MS)

    def say(self, text: str, ms: int = 6000):
        text = bubble_text(text)
        if not text:
            return
        self._bubble = self._fit_bubble(_soft_wrap(text))
        self._bubble_timer.start(ms)
        self.update()

    def set_wander(self, on: bool):
        self._wander = on

    def on_app_state(self, state: str):
        """Mirror one of Aether's UI states (LISTENING, THINKING, ...).
        Unknown states are ignored so a new app state can't break the mate."""
        key = str(state).upper()
        mate_state = APP_STATE_MAP.get(key)
        if mate_state is None:
            return
        self.set_state(mate_state)
        if key == "ERROR" and self.show_bubbles:
            self.say("Something went wrong - check the log.")

    def on_log_line(self, text: str):
        """Feed every log line; only assistant replies ("Aether: ...") are shown."""
        if self.show_bubbles and text.lstrip().lower().startswith("aether:"):
            self.say(text)

    # ---------- internals ----------
    def _set(self, s):
        self._state = s

    def _maybe_sleep(self):
        # Only doze off from a calm state; a long task must never put it to sleep.
        if self._state in ("idle", "happy"):
            self._set("sleeping")

    def _clear_bubble(self):
        self._bubble = ""
        self.update()

    def _fit_bubble(self, text: str) -> str:
        """Trim `text` (with an ellipsis, at a word boundary) until it wraps
        into the bubble's text box using the actual font's metrics."""
        fm = QFontMetrics(self._bubble_font)
        max_h = BUBBLE_MAX_H - 2 * BUBBLE_PAD

        def height(s: str) -> int:
            return fm.boundingRect(QRect(0, 0, BUBBLE_INNER_W, 10000), _WRAP, s).height()

        if height(text) <= max_h:
            return text
        lo, hi = 0, len(text)
        while lo < hi:  # longest prefix that still fits once "..." is added
            mid = (lo + hi + 1) // 2
            if height(text[:mid].rstrip() + "…") <= max_h:
                lo = mid
            else:
                hi = mid - 1
        cut = text[:lo]
        space = cut.rfind(" ")
        if space > lo * 0.6:
            cut = cut[:space]
        return cut.rstrip(" ,;:-") + "…"

    def _load_frames(self, root: Path):
        out = {}
        for st in STATES:
            d = root / st
            if d.is_dir():
                pics = [QPixmap(str(p)) for p in sorted(d.glob("*.png"))]
                pics = [p for p in pics if not p.isNull()]
                if pics:
                    out[st] = pics
        return out

    # No animation work while hidden (Aether is meant to stay light on resources).
    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _on_tick(self):
        self._tick += 1
        if self._wander and self._state in ("idle", "sleeping") and self._drag is None:
            scr = QApplication.primaryScreen().availableGeometry()
            x = self.x() + self._wander_dir * 2
            if x < scr.left() or x > scr.right() - W or random.random() < 0.003:
                self._wander_dir *= -1
            self.move(x, self.y())
        self.update()

    # ---------- painting ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frames = self._frames.get(self._state)
        if frames:
            pm = frames[(self._tick // 4) % len(frames)]
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            bob = self._sprite_bob() if len(frames) == 1 else 0.0  # multi-frame art animates itself
            p.drawPixmap(int((W - pm.width()) / 2), int(H - pm.height() - 6 + bob), pm)
        else:
            self._draw_default(p)
        self._draw_bubble(p)  # on top, so the bubble never sits under the character

    def _sprite_bob(self) -> float:
        """Vertical motion for single-frame art, so a still picture still feels alive."""
        t, st = self._tick, self._state
        if st == "excited":
            return -abs(math.sin(t * 0.28)) * 10
        if st in ("sleeping", "hiding"):
            return math.sin(t * 0.06) * 1.5
        if st == "error":
            return random.choice((-2, 0, 2)) if (t % 40) < 4 else 0.0
        return math.sin(t * 0.08) * 3

    def _draw_bubble(self, p):
        if not self._bubble:
            return
        p.setFont(self._bubble_font)
        pad, bottom = BUBBLE_PAD, BUBBLE_BOTTOM
        need = QFontMetrics(self._bubble_font).boundingRect(
            QRect(0, 0, BUBBLE_INNER_W, 10000), _WRAP, self._bubble)
        h = min(BUBBLE_MAX_H, need.height() + 2 * pad)
        r = QRectF(8, bottom - h, W - 16, h)

        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        tail = QPainterPath()
        tail.moveTo(W / 2 - 8, bottom - 1)
        tail.lineTo(W / 2, bottom + 8)
        tail.lineTo(W / 2 + 8, bottom - 1)
        tail.closeSubpath()
        path = path.united(tail)

        p.setPen(QPen(QColor(120, 140, 255), 1.5))
        p.setBrush(QColor(20, 24, 40, 235))
        p.drawPath(path)
        p.setPen(QColor(230, 235, 255))
        p.drawText(r.adjusted(pad, pad - 2, -pad, -(pad - 2)),
                   int(Qt.AlignmentFlag.AlignVCenter.value | _WRAP),
                   self._bubble)

    # ---------- the built-in character: a hooded night-cat ----------
    # state -> (eye colour, aura colour)
    _LOOK = {
        "idle":      ((120, 205, 255), (110, 110, 255)),
        "happy":     ((150, 200, 255), (170, 120, 255)),
        "excited":   ((120, 245, 255), (80, 230, 255)),
        "thinking":  ((120, 190, 255), (150, 110, 255)),
        "speaking":  ((120, 210, 255), (100, 140, 255)),
        "listening": ((120, 215, 255), (90, 200, 255)),
        "working":   ((110, 190, 255), (90, 140, 255)),
        "sleeping":  ((90, 150, 230),  (70, 80, 170)),
        "sad":       ((110, 160, 255), (70, 80, 170)),
        "angry":     ((255, 80, 80),   (255, 60, 60)),
        "confused":  ((120, 205, 255), (130, 110, 255)),
        "hiding":    ((120, 205, 255), (90, 90, 220)),
        "error":     ((255, 95, 120),  (255, 60, 90)),
    }
    _RED_STATES = ("angry", "error")

    def _draw_default(self, p):
        p.save()
        p.translate(0, H - 250)  # the character is laid out for a 250px-tall widget
        try:
            self._draw_character(p, self._tick, self._state)
        finally:
            p.restore()

    @staticmethod
    def _rgba(c, a):
        return QColor(c.red(), c.green(), c.blue(), int(a))

    def _glow(self, p, x, y, col, r=18, a=110):
        g = QRadialGradient(x, y, r)
        g.setColorAt(0, self._rgba(col, a))
        g.setColorAt(1, self._rgba(col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x, y), r, r)

    def _draw_character(self, p, t, st):
        eye, aura = (QColor(*c) for c in self._LOOK[st])
        red = st in self._RED_STATES
        asleep = st == "sleeping"
        cx = W / 2

        # Gentle float; excited bounces; sleeping breathes slowly.
        if st == "excited":
            bob = -abs(math.sin(t * 0.28)) * 12
        else:
            bob = math.sin(t * (0.06 if asleep else 0.08)) * (1.5 if asleep else 3)
        if st == "error" and (t % 40) < 4:  # glitch jitter
            p.translate(random.choice((-3, 3)), 0)

        # Ground shadow + aura.
        sh = 1.0 - (bob + 12) / 60
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(70 * sh)))
        p.drawEllipse(QPointF(cx, 241), 48 * sh, 6 * sh)
        glow = QRadialGradient(cx, 170 + bob, 104)
        a0 = 34 if asleep else int(64 + 22 * math.sin(t * 0.09))
        glow.setColorAt(0, self._rgba(aura, a0))
        glow.setColorAt(1, self._rgba(aura, 0))
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, 170 + bob), 104, 104)

        if st == "hiding":
            self._draw_hiding(p, cx, eye, t)
            return

        hy = 150 + bob + (9 if asleep else 0)  # head centre

        self._draw_cloak(p, cx, bob, st, eye, red)
        self._draw_ears(p, cx, hy, st, t, red)
        self._draw_hood(p, cx, hy, red)
        self._draw_eyes(p, cx, hy + 6, st, eye, t)

        # Front paws.
        p.setPen(QPen(QColor(110, 96, 230, 110), 1.3))
        p.setBrush(QColor(26, 24, 58))
        tap = abs(math.sin(t * 0.4)) * 3 if st == "working" else 0
        p.drawEllipse(QPointF(cx - 22, 231 + bob * 0.3), 14, 8.5 - tap)
        p.drawEllipse(QPointF(cx + 22, 231 + bob * 0.3 + (tap if st == "working" else 0)), 14, 8.5)

        self._draw_extras(p, cx, hy, st, eye, t)

    # -- parts ---------------------------------------------------------------
    def _draw_cloak(self, p, cx, bob, st, eye, red):
        top = 178 + bob
        body = QPainterPath(QPointF(cx - 64, 238))
        body.cubicTo(QPointF(cx - 74, 204), QPointF(cx - 46, top + 2), QPointF(cx, top))
        body.cubicTo(QPointF(cx + 46, top + 2), QPointF(cx + 74, 204), QPointF(cx + 64, 238))
        body.closeSubpath()
        g = QLinearGradient(0, top, 0, 240)
        g.setColorAt(0, QColor(72, 28, 40) if red else QColor(58, 52, 140))
        g.setColorAt(1, QColor(26, 10, 22) if red else QColor(20, 18, 52))
        p.setPen(QPen(QColor(255, 110, 110, 90) if red else QColor(130, 118, 255, 100), 1.6))
        p.setBrush(QBrush(g))
        p.drawPath(body)
        # Cloak folds.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(8, 6, 24, 110), 2))
        for dx in (-30, 0, 30):
            f = QPainterPath(QPointF(cx + dx * 0.6, top + 22))
            f.quadTo(QPointF(cx + dx, 218), QPointF(cx + dx * 1.15, 236))
            p.drawPath(f)
        # Glowing rune on the chest.
        p.setPen(QPen(self._rgba(eye, 190), 1.6))
        rune = QPainterPath(QPointF(cx, 207))
        rune.lineTo(cx - 6, 218)
        rune.lineTo(cx + 6, 218)
        rune.closeSubpath()
        p.drawPath(rune)
        p.drawLine(QPointF(cx, 211), QPointF(cx, 215))

    def _ear_pose(self, st, side, t):
        dx = dy = 0.0
        if st == "listening":
            dy = -5 + math.sin(t * 0.16 + side) * 2.5
            dx = side * (3 + math.sin(t * 0.2) * 2)
        elif st in ("sad", "sleeping"):
            dy, dx = 24, side * 12
        elif st == "angry":
            dy, dx = 16, side * 18
        elif st == "excited":
            dy = math.sin(t * 0.5 + side) * 5
        elif st == "thinking":
            dy, dx = (-7, -3) if side == 1 else (4, 0)
        elif st == "confused":
            dy, dx = (24, -side * 4) if side == -1 else (-8, 0)
        elif st == "happy":
            dy = math.sin(t * 0.2 + side) * 3
        return dx, dy

    def _draw_ears(self, p, cx, hy, st, t, red):
        hi = QColor(255, 120, 120) if red else QColor(160, 140, 255)
        lo = QColor(160, 30, 50) if red else QColor(70, 55, 205)
        for side in (-1, 1):
            dx, dy = self._ear_pose(st, side, t)
            tipx, tipy = cx + side * 52 + dx, hy - 86 + dy
            ear = QPainterPath(QPointF(cx + side * 56, hy - 6))
            ear.quadTo(QPointF(cx + side * 68 + dx * 0.5, hy - 50 + dy * 0.5), QPointF(tipx, tipy))
            ear.quadTo(QPointF(cx + side * 30 + dx * 0.5, hy - 66 + dy * 0.6), QPointF(cx + side * 10, hy - 40))
            ear.closeSubpath()
            og = QLinearGradient(tipx, tipy, cx + side * 30, hy - 30)
            og.setColorAt(0, QColor(44, 38, 100))
            og.setColorAt(1, QColor(12, 10, 30))
            p.setPen(QPen(self._rgba(hi, 120), 1.4))
            p.setBrush(QBrush(og))
            p.drawPath(ear)
            inner = QPainterPath(QPointF(cx + side * 51, hy - 18))
            inner.quadTo(QPointF(cx + side * 60 + dx * 0.5, hy - 48 + dy * 0.5),
                         QPointF(cx + side * 49 + dx * 0.9, hy - 74 + dy * 0.9))
            inner.quadTo(QPointF(cx + side * 31 + dx * 0.4, hy - 56 + dy * 0.5),
                         QPointF(cx + side * 21, hy - 38))
            inner.closeSubpath()
            ig = QLinearGradient(tipx, tipy, cx + side * 34, hy - 24)
            ig.setColorAt(0, hi)
            ig.setColorAt(1, lo)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(ig))
            p.drawPath(inner)

    def _draw_hood(self, p, cx, hy, red):
        hood = QPainterPath()
        hood.addRoundedRect(QRectF(cx - 55, hy - 43, 110, 88), 46, 42)
        g = QLinearGradient(0, hy - 43, 0, hy + 45)
        g.setColorAt(0, QColor(60, 22, 34) if red else QColor(44, 40, 104))
        g.setColorAt(1, QColor(14, 8, 20) if red else QColor(12, 11, 32))
        p.setPen(QPen(QColor(255, 110, 110, 120) if red else QColor(125, 112, 255, 120), 2))
        p.setBrush(QBrush(g))
        p.drawPath(hood)
        # Face opening.
        p.setPen(QPen(QColor(0, 0, 0, 160), 2))
        p.setBrush(QColor(3, 3, 10))
        p.drawEllipse(QRectF(cx - 42, hy - 25, 84, 62))

    def _draw_hiding(self, p, cx, eye, t):
        breathe = math.sin(t * 0.06) * 1.5
        top = 196 + breathe
        dome = QPainterPath(QPointF(cx - 70, 238))
        dome.cubicTo(QPointF(cx - 74, 204), QPointF(cx - 40, top - 4), QPointF(cx, top - 6))
        dome.cubicTo(QPointF(cx + 40, top - 4), QPointF(cx + 74, 204), QPointF(cx + 70, 238))
        dome.closeSubpath()
        g = QLinearGradient(0, top, 0, 240)
        g.setColorAt(0, QColor(70, 62, 160))
        g.setColorAt(1, QColor(26, 22, 70))
        p.setPen(QPen(QColor(140, 128, 255, 110), 1.6))
        p.setBrush(QBrush(g))
        p.drawPath(dome)
        for side in (-1, 1):
            x, y = cx + side * 17, 217 + breathe
            self._glow(p, x, y, eye, 14, 90)
            p.save()
            p.translate(x, y)
            p.rotate(side * 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(eye)
            p.drawEllipse(QPointF(0, 0), 8, 2.4)
            p.restore()

    def _draw_eyes(self, p, cx, ey, st, col, t):
        blink = (t % 190) < 5
        round_pen = QPen(col, 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)

        def fill_oval(x, y, rx, ry):
            g = QRadialGradient(x, y - ry * 0.3, ry * 1.2)
            g.setColorAt(0, QColor(235, 250, 255))
            g.setColorAt(0.45, col)
            g.setColorAt(1, col.darker(130))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(x, y), rx, ry)

        def slit(x, y, rx, ry, rot):
            p.save()
            p.translate(x, y)
            p.rotate(rot)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(0, 0), rx, ry)
            p.restore()

        for side in (-1, 1):
            x = cx + side * 20
            self._glow(p, x, ey, col, 22, 70 if st == "sleeping" else 100)
            if st in ("idle", "happy"):
                p.setPen(QPen(col, 3.8 if st == "happy" else 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(x - 9, ey - 5, 18, 14), 25 * 16, 130 * 16)
            elif st == "excited":
                pts = QPainterPath()
                for k in range(8):
                    r = 10.5 if k % 2 == 0 else 3.0
                    ang = math.pi / 2 * (k / 2) if k % 2 == 0 else math.pi / 4 + math.pi / 2 * ((k - 1) / 2)
                    pt = QPointF(x + math.cos(ang) * r, ey + math.sin(ang) * r)
                    pts.moveTo(pt) if k == 0 else pts.lineTo(pt)
                pts.closeSubpath()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(235, 252, 255))
                p.drawPath(pts)
            elif blink and st in ("thinking", "speaking", "listening", "confused"):
                p.setPen(round_pen)
                p.drawLine(QPointF(x - 6, ey), QPointF(x + 6, ey))
            elif st == "thinking":
                fill_oval(x + 3, ey - 2, 6, 9.5)
            elif st == "listening":
                fill_oval(x, ey, 6, 10.5)
            elif st == "speaking":  # "0 0" ring eyes
                p.setPen(QPen(col, 3.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, ey), 6.2, 9.2)
            elif st == "confused":
                big = side == 1
                fill_oval(x, ey + (0 if big else 2), 7.4 if big else 5.4, 11 if big else 8)
            elif st == "working":
                slit(x, ey, 8.5, 5.4, -side * 9)
            elif st == "angry":
                slit(x, ey, 9.5, 4.8, -side * 22)
            elif st == "error":
                slit(x + 2.2, ey, 9.5, 5, -side * 16)  # cyan ghost, glitchy
                p.setOpacity(0.55)
                p.setBrush(QColor(80, 240, 255))
                p.save()
                p.translate(x - 2.2, ey + 0.8)
                p.rotate(-side * 16)
                p.drawEllipse(QPointF(0, 0), 9.5, 5)
                p.restore()
                p.setOpacity(1.0)
            elif st == "sad":
                slit(x, ey + 2, 6.6, 7.6, side * 15)
                p.setBrush(QColor(235, 250, 255, 200))
                p.drawEllipse(QPointF(x - 1.5, ey), 1.8, 1.8)
            elif st == "sleeping":
                slit(x, ey + 1, 8.4, 2.6, side * 10)

        # Mouth.
        my = ey + 21
        if st == "speaking":
            h = 1.6 + 4.2 * abs(math.sin(t * 0.5))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._rgba(col, 215))
            p.drawEllipse(QPointF(cx, my), 5.2, h)
        elif st == "excited":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._rgba(col, 220))
            p.drawChord(QRectF(cx - 7, my - 6, 14, 12), 180 * 16, 180 * 16)
        elif st in ("idle", "happy"):
            p.setPen(QPen(self._rgba(col, 190), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - 6, my - 6, 12, 8), 210 * 16, 120 * 16)
        elif st == "sad":
            p.setPen(QPen(self._rgba(col, 170), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - 5, my, 10, 6), 30 * 16, 120 * 16)

    # -- little scene props per state -------------------------------------------
    def _chat_icon(self, p, x, y, col, t):
        path = QPainterPath()
        path.addRoundedRect(QRectF(x - 15, y - 11, 30, 21), 9, 9)
        path.moveTo(x - 8, y + 8)
        path.lineTo(x - 14, y + 17)
        path.lineTo(x - 1, y + 9)
        p.setPen(QPen(self._rgba(col, 230), 2))
        p.setBrush(QColor(18, 18, 48, 215))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(3):
            lit = int(t / 8) % 3 == k
            p.setBrush(self._rgba(col, 255 if lit else 120))
            p.drawEllipse(QPointF(x - 7 + k * 7, y - 0.5 - (2 if lit else 0)), 2.1, 2.1)

    def _note(self, p, x, y, col):
        p.setPen(QPen(col, 2))
        p.setBrush(col)
        p.drawEllipse(QPointF(x, y), 3.8, 2.8)
        p.drawLine(QPointF(x + 3.4, y), QPointF(x + 3.4, y - 13))
        p.setBrush(Qt.BrushStyle.NoBrush)
        flag = QPainterPath(QPointF(x + 3.4, y - 13))
        flag.quadTo(QPointF(x + 10, y - 11), QPointF(x + 8, y - 5))
        p.drawPath(flag)

    def _draw_extras(self, p, cx, hy, st, eye, t):
        if st == "happy":
            f = math.sin(t * 0.12) * 3
            self._note(p, cx + 66, hy - 58 + f, QColor(190, 160, 255))
            self._note(p, cx - 74, hy - 14 - f, QColor(190, 160, 255))
        elif st == "excited":
            a = 120 + 100 * abs(math.sin(t * 0.3))
            p.setPen(QPen(self._rgba(eye, a), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for side in (-1, 1):
                for ang in (-65, -35, -5):
                    r = math.radians(ang)
                    ca, sa = math.cos(r) * side, math.sin(r)
                    p.drawLine(QPointF(cx + side * 58 + ca * 6, hy - 40 + sa * 6),
                               QPointF(cx + side * 58 + ca * 16, hy - 40 + sa * 16))
        elif st == "thinking":
            self._chat_icon(p, cx + 66, hy - 62, eye, t)
        elif st == "speaking":
            self._chat_icon(p, cx + 68, hy - 28, eye, t)
        elif st == "listening":
            for k in range(3):
                ph = ((t * 0.05) + k / 3.0) % 1.0
                p.setPen(QPen(self._rgba(eye, 220 * (1 - ph)), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                r = 8 + ph * 16
                p.drawArc(QRectF(cx + 60 - r * 0.5, hy - 12 - r, r, r * 2), -50 * 16, 100 * 16)
        elif st == "working":
            p.save()
            p.translate(cx + 38, 214)
            p.rotate(-7)
            screen = QLinearGradient(0, -24, 0, 6)
            screen.setColorAt(0, QColor(70, 120, 230, 210))
            screen.setColorAt(1, QColor(30, 60, 150, 190))
            p.setPen(QPen(self._rgba(eye, 230), 1.6))
            p.setBrush(QBrush(screen))
            p.drawRoundedRect(QRectF(-26, -24, 52, 30), 3, 3)
            p.setBrush(QColor(15, 20, 50, 230))
            p.drawRoundedRect(QRectF(-30, 6, 60, 5), 2, 2)
            p.setPen(QPen(QColor(220, 240, 255, 220), 1.5))
            tri = QPainterPath(QPointF(0, -17))
            tri.lineTo(-6, -7)
            tri.lineTo(6, -7)
            tri.closeSubpath()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(tri)
            p.restore()
        elif st == "sleeping":
            p.setPen(QColor(190, 200, 255, 230))
            p.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
            for k in range(3):
                ph = ((t / 80.0) + k / 3.0) % 1.0
                p.setOpacity(1 - ph)
                p.drawText(QPointF(cx + 52 + ph * 20, hy - 56 - ph * 34), "z")
            p.setOpacity(1.0)
        elif st == "sad":
            cy = hy - 98
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(52, 58, 96, 235))
            for ox, oy, rx, ry in ((-20, 2, 19, 11), (0, -4, 23, 14), (21, 3, 18, 10), (0, 6, 38, 9)):
                p.drawEllipse(QPointF(cx + ox, cy + oy), rx, ry)
            p.setPen(QPen(QColor(140, 170, 255, 170), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for k in range(6):
                ph = ((t * 0.035) + k * 0.17) % 1.0
                x = cx - 30 + k * 12
                y = cy + 14 + ph * 40
                p.drawLine(QPointF(x, y), QPointF(x - 1.5, y + 6))
        elif st == "angry":
            a = 150 + 100 * abs(math.sin(t * 0.3))
            p.setPen(QPen(QColor(255, 70, 70, int(a)), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            x, y = cx + 66, hy - 52
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                p.drawLine(QPointF(x + dx * 3, y + dy * 3), QPointF(x + dx * 9, y + dy * 9))
        elif st == "confused":
            p.setPen(QColor(150, 190, 255, 240))
            p.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
            p.drawText(QPointF(cx + 52, hy - 54 + math.sin(t * 0.12) * 3), "?")
        elif st == "error":
            x, y = cx + 64, hy - 56
            tri = QPainterPath(QPointF(x, y - 14))
            tri.lineTo(x - 14, y + 11)
            tri.lineTo(x + 14, y + 11)
            tri.closeSubpath()
            p.setPen(QPen(QColor(255, 120, 130), 2))
            p.setBrush(QColor(60, 10, 22, 230))
            p.drawPath(tri)
            p.setPen(QColor(255, 140, 150))
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            p.drawText(QRectF(x - 14, y - 6, 28, 18), Qt.AlignmentFlag.AlignCenter, "!")
            if (t % 55) < 6:  # glitch bars across the face
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(80, 240, 255, 150))
                p.drawRect(QRectF(cx - 48, hy - 6, 96, 3))
                p.setBrush(QColor(255, 70, 100, 150))
                p.drawRect(QRectF(cx - 48, hy + 14, 96, 3))

    # ---------- interaction ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = e.globalPosition().toPoint()
            if self._state == "sleeping":
                self._set("idle")
            self._sleep_timer.start(SLEEP_AFTER_MS)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        # A click (not a drag) pets it: a short happy reaction, only from calm states.
        moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength() if self._press_pos else 99
        if moved < 5 and self._state in ("idle", "sleeping", "happy"):
            self.set_state("happy")
            QTimer.singleShot(1600, self._end_pet)
        self._drag = None
        self._press_pos = None

    def _end_pet(self):
        if self._state == "happy":
            self._set("idle")

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        w = QAction("Wander", m, checkable=True, checked=self._wander)
        w.toggled.connect(self.set_wander)
        m.addAction(w)
        b = QAction("Speech bubbles", m, checkable=True, checked=self.show_bubbles)
        b.toggled.connect(lambda on: setattr(self, "show_bubbles", on))
        m.addAction(b)
        prev = m.addMenu("Preview mood")
        for st in STATES:
            prev.addAction(st.capitalize(), lambda st=st: self.set_state(st))
        m.addAction("Hide (Ctrl+Shift+M to bring back)", self.hide)
        m.exec(e.globalPos())
