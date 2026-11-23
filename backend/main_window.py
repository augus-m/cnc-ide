"""Ventana principal de CNC IDE - Fase 2 con editor integrado."""

import os
import re
import math
from datetime import datetime
from pathlib import Path

# Copia de la consola en un archivo de texto (para leerla desde fuera de la GUI).
# Cualquier error de escritura se ignora: nunca debe afectar a la consola.
import collections
import sys
import subprocess

# Lineas de G-code enviadas sin esperar su OK (anticipacion del planner).
def estimate_job_seconds(lines):
    """Tiempo estimado (s) por distancia y feed de cada G1; G0 a 1200 mm/min."""
    import math
    x = y = None
    feed = 200.0
    total = 0.0
    for ln in lines:
        t = ln.strip().upper()
        if not (t.startswith("G1") or t.startswith("G0")):
            continue
        vals = dict((k, float(v)) for k, v in re.findall(r"([XYF])(-?\d+(?:\.\d+)?)", t))
        if "F" in vals:
            feed = vals["F"]
        nx = vals.get("X", x)
        ny = vals.get("Y", y)
        if nx is not None and ny is not None and x is not None and y is not None:
            d = math.hypot(nx - x, ny - y)
            f = 1200.0 if t.startswith("G0") else feed
            if f > 0:
                total += d / (f / 60.0)
        x, y = nx, ny
    return total


# Mantener una ventana corta evita llenar la cola USB/planner del STM32.
# Los G-code de Gerber tienen muchos segmentos pequeños y una ventana de 32
# puede dejar al firmware sin margen para emitir estado y OK.
JOB_WINDOW = 8

CONSOLE_LOG_PATH = Path(__file__).resolve().parents[1] / "data" / "gui_consola.log"

HISTORY_DIR = Path(__file__).resolve().parents[1] / "data" / "historial"

TRABAJOS_DIR = Path(__file__).resolve().parents[1] / "data" / "trabajos"

def _es_error(text):
    t = text.upper()
    return any(w in t for w in ("ALARM", "ERROR", "ABORTADO", "NO CONFIABLE", "E-STOP"))

class _JobRecorder:
    """Una carpeta por trabajo con archivos separados y legibles:
    resumen.md, gcode.nc, envios.log, respuestas.log, eventos.log, errores.log.
    Se cierra (deja de escribir) solo cuando empieza otro trabajo."""

    def __init__(self):
        self.dir = None
        self.t0 = None
        self.t1 = None
        self.estado = "sin trabajo"
        self.n_total = 0
        self.sent = 0
        self.rx = 0
        self.errors = 0
        self.first_error = ""
        self.last_error = ""
        self.last_tx = ""

    def start(self, lines):
        try:
            ts = datetime.now()
            d = TRABAJOS_DIR / ts.strftime("%Y-%m-%d_%H%M%S")
            d.mkdir(parents=True, exist_ok=True)
            self.dir = d
            self.t0 = ts
            self.t1 = None
            self.estado = "en curso"
            self.n_total = len(lines)
            self.sent = self.rx = self.errors = 0
            self.first_error = self.last_error = self.last_tx = ""
            with open(d / "gcode.nc", "w", encoding="utf-8") as f:
                for i, l in enumerate(lines, 1):
                    f.write(f"{i:05d}  {l.rstrip()}\n")
            self._resumen()
        except Exception:
            self.dir = None

    def _append(self, name, text):
        if self.dir is None:
            return
        try:
            with open(self.dir / name, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def write(self, kind, text):
        if self.dir is None:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        if kind == "TX":
            self.sent += 1
            self.last_tx = text
            self._append("envios.log", f"{ts}  {text}")
        elif kind == "RX":
            self.rx += 1
            self._append("respuestas.log", f"{ts}  {text}")
        else:
            self._append("eventos.log", f"{ts}  [{kind}] {text}")
        if kind == "ERR" or _es_error(text):
            self.errors += 1
            msg = f"{ts}  [{kind}] {text}"
            if not self.first_error:
                self.first_error = msg
            self.last_error = msg
            self._append("errores.log", msg)

    def finish(self, estado):
        if self.dir is None:
            return
        self.estado = estado
        self.t1 = datetime.now()
        self._resumen()

    def _resumen(self):
        try:
            dur = ((self.t1 or datetime.now()) - self.t0).total_seconds()
            txt = (
                f"# Trabajo {self.t0.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"- Estado: {self.estado}\n"
                f"- Inicio: {self.t0.strftime('%H:%M:%S')}\n"
                f"- Fin: {self.t1.strftime('%H:%M:%S') if self.t1 else 'en curso'}\n"
                f"- Duracion: {dur:.0f} s\n"
                f"- Lineas del G-code: {self.n_total}\n"
                f"- Lineas enviadas: {self.sent}\n"
                f"- Respuestas recibidas: {self.rx}\n"
                f"- Errores/alarmas: {self.errors}\n"
                f"- Ultima linea enviada: {self.last_tx or '-'}\n"
                f"- Primer error: {self.first_error or '-'}\n"
                f"- Ultimo error: {self.last_error or '-'}\n\n"
                "Archivos:\n"
                "- gcode.nc: programa completo, numerado\n"
                "- envios.log: lo que la GUI envio a la placa\n"
                "- respuestas.log: lo que responde la placa (ST, BUSY, OK, DONE, ALARM)\n"
                "- eventos.log: mensajes de la GUI\n"
                "- errores.log: solo errores y alarmas\n"
            )
            with open(self.dir / "resumen.md", "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception:
            pass

JOB_RECORDER = _JobRecorder()

HISTORY_WORDS = ("ALARM", "ERROR", "HOME", "MODO", "E-STOP", "Job", "Trabajo", "Archivo", "Abr", "Conect", "Desconect", "Fabric", "Simul")

def _history_wanted(kind, text):
    if kind == "ERR":
        return True
    if kind == "TX":
        code = text.strip().upper()
        if code.startswith(("M114", "LIMITS")):
            return False
        return code.startswith(("G", "M", "$"))
    if kind == "RX":
        return any(w in text for w in ("ALARM", "ERROR", "HOME", "MODO", "E-STOP"))
    return any(w in text for w in HISTORY_WORDS)

def _append_history(kind, text):
    try:
        if not _history_wanted(kind, text):
            return
        tag = datetime.now().strftime("%G-W%V")
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        for old in HISTORY_DIR.glob("semana_*.log"):
            if old.name != f"semana_{tag}.log":
                old.unlink(missing_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(HISTORY_DIR / f"semana_{tag}.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} [{kind}] {text}\n")
    except Exception:
        pass

def _append_console_log(kind, text):
    try:
        CONSOLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(CONSOLE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} [{kind}] {text}\n")
    except Exception:
        pass
    _append_history(kind, text)
    JOB_RECORDER.write(kind, text)

from PySide6.QtCore import Qt, QThread, QTimer, QSettings, Signal, Slot, QPointF, QObject
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPixmap, QFontDatabase, QIcon
from PySide6.QtGui import QPainterPath, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QLineEdit, QFrame,
    QSplitter, QListWidget, QListWidgetItem, QSizePolicy,
    QProgressBar, QScrollArea, QMessageBox, QFileDialog,
    QComboBox, QSlider, QTabWidget,
)

from dialogs import GlassDialog, ConnectDialog, SettingsDialog
from serial_worker import SerialWorker
from converter import svg_to_paths, dxf_to_paths, paths_to_gcode
from gerber_parser import gerber_to_gcode
from excellon_parser import excellon_to_gcode
from cnc_bridge import CncBridge
from gcode_editor import GCodeEditor, parse_gcode_stats, parse_gcode_toolpath


# Colores
C_BG_0     = "#0d0e12"
C_BG_1     = "#16171c"
C_BG_2     = "#1c1e24"
C_BG_3     = "#23252c"
C_BORDER   = "#2a2d36"
C_TEXT_0   = "#e8e8eb"
C_TEXT_1   = "#9aa0ac"
C_TEXT_2   = "#5d6270"
C_ACCENT   = "#ff4422"
C_GREEN    = "#4ade80"
C_YELLOW   = "#fbbf24"
C_RED      = "#ef4444"
C_BLUE     = "#60a5fa"
C_AXIS     = "#a855f7"
C_RAPID    = "#fbbf24"
C_CUT      = "#ff8c42"
C_ARC      = "#a855f7"

ANIM_SPEED = 0.18
ANIM_FPS   = 60


# ============================================================
# LOGO
# ============================================================
def _load_logo_font():
    """Carga Playfair Display desde assets. Fallback a Georgia."""
    assets = Path(__file__).parent / 'assets' / 'PlayfairDisplay.ttf'
    if assets.exists():
        fid = QFontDatabase.addApplicationFont(str(assets))
        if fid >= 0:
            families = QFontDatabase.applicationFontFamilies(fid)
            if families:
                return families[0]
    return 'Georgia'


_LOGO_FONT_FAMILY = None


def make_logo_pixmap(size, dark=False):
    """Dibuja el logo C. dark=True -> fondo oscuro + borde naranja.\n    dark=False -> fondo naranja + C cream."""
    global _LOGO_FONT_FAMILY
    if _LOGO_FONT_FAMILY is None:
        _LOGO_FONT_FAMILY = _load_logo_font()

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    r = size * 0.22   # radio de las esquinas

    if dark:
        # Version 6: fondo oscuro + borde + C naranja
        p.setBrush(QColor('#0d0e12'))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, size, size, r, r)
        # Borde naranja
        bw = max(1, size * 0.04)
        pen = QPen(QColor('#ff8243'))
        pen.setWidthF(bw)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        inset = bw / 2.0
        p.drawRoundedRect(inset, inset, size - 2*inset, size - 2*inset,
                          r - inset * 0.5, r - inset * 0.5)
        text_color = '#ff8243'
    else:
        # Version 3: fondo naranja + C cream
        p.setBrush(QColor('#ff8243'))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, size, size, r, r)
        text_color = '#fdf6e3'

    # La C
    font = QFont(_LOGO_FONT_FAMILY, int(size * 0.7))
    font.setWeight(QFont.Bold)
    p.setFont(font)
    p.setPen(QColor(text_color))
    # Ajustar posicion: baseline + centrado visual
    p.drawText(pm.rect(), Qt.AlignCenter, 'C')
    p.end()
    return pm


# ============================================================
# VISOR 2D con toolpath coloreado
# ============================================================


def _is_excellon(path, text):
    """True si el archivo es de taladros (Excellon): extension .drl/.xln o cabecera M48."""
    ext = os.path.splitext(path)[1].lower()
    return ext in (".drl", ".xln") or text.lstrip().startswith("M48")


def _excellon_to_gerber_text(drl_text):
    """Convierte taladros en un Gerber de circulos (un flash por agujero)."""
    from excellon_parser import ExcellonParser
    hs = ExcellonParser().parse(drl_text) or []
    dias = sorted({round(d, 3) for _, _, d in hs})
    out = ["%FSLAX46Y46*%", "%MOMM*%", "G01*"]
    code = {}
    for i, dv in enumerate(dias):
        code[dv] = 10 + i
        out.append(f"%ADD{10 + i}C,{dv:.3f}*%")
    for dv in dias:
        out.append(f"D{code[dv]}*")
        for hx, hy, hd in hs:
            if round(hd, 3) == dv:
                out.append(f"X{int(round(hx * 1e6))}Y{int(round(hy * 1e6))}D03*")
    out.append("M02*")
    return "\n".join(out)


def _board_name(filename):
    """Nombre de la placa: el prefijo antes del ultimo '-' (555-F_Cu.gbr -> 555)."""
    base = os.path.splitext(os.path.basename(filename))[0]
    return base.rsplit('-', 1)[0] if '-' in base else base


def _detect_gerber_layer(filename):
    """Detecta que capa es segun el nombre del archivo."""
    fn = filename.lower()
    if "f_cu" in fn or "-f.cu" in fn or ".gtl" in fn: return "F.Cu"
    if "b_cu" in fn or "-b.cu" in fn or ".gbl" in fn: return "B.Cu"
    if "edge_cuts" in fn or "edge.cuts" in fn or ".gm1" in fn: return "Edge.Cuts"
    if "f_mask" in fn or ".gts" in fn: return "F.Mask"
    if "b_mask" in fn or ".gbs" in fn: return "B.Mask"
    if "f_silk" in fn or ".gto" in fn: return "F.SilkS"
    if "b_silk" in fn or ".gbo" in fn: return "B.SilkS"
    return "F.Cu"


def _detect_drill_layer(filename):
    fn = filename.lower()
    if "npth" in fn: return "NPTH"
    return "PTH"


class ViewerCanvas2D(QWidget):
    """Visor 2D con soporte de capas multiples con colores."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ViewerCanvas")
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.current_x = 0.0
        self.current_y = 0.0
        self.target_x = 0.0
        self.target_y = 0.0
        self.area_x = 200.0
        self.area_y = 200.0

        # Capas: nombre -> {segments, color, visible, width}
        self.layers = {
            "F.Cu":      {"segments": [], "color": "#cc3333", "visible": True, "width": 2},
            "B.Cu":      {"segments": [], "color": "#3366cc", "visible": True, "width": 2},
            "Edge.Cuts": {"segments": [], "color": "#ffcc00", "visible": True, "width": 2},
            "F.SilkS":   {"segments": [], "color": "#ffffff", "visible": True, "width": 1},
            "F.Mask":    {"segments": [], "color": "#a855f7", "visible": True, "width": 1},
            "GCode":     {"segments": [], "color": "#ff8c42", "visible": True, "width": 2},
        }
        # Capas de taladros: nombre -> {holes, color, visible}
        self.hole_layers = {
            "PTH":  {"holes": [], "color": "#00ffff", "visible": True},
            "NPTH": {"holes": [], "color": "#ff00ff", "visible": True},
        }

        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._dragging = False
        self._drag_last = None
        self._mouse_world = (0.0, 0.0)
        self._pan_dirty = 0

        self._toolpath_cache = None
        self._toolpath_cache_key = None

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(int(1000 / ANIM_FPS))
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start()

    # ---------- API ----------
    def _invalidate_cache(self):
        self._toolpath_cache = None
        self._toolpath_cache_key = None

    def set_position(self, x, y):
        self.target_x = x
        self.target_y = y

    def set_position_immediate(self, x, y):
        self.current_x = x
        self.current_y = y
        self.target_x = x
        self.target_y = y
        self.update()

    def set_layer_segments(self, layer_name, segments):
        if layer_name not in self.layers:
            self.layers[layer_name] = {"segments": [], "color": "#ffffff",
                                        "visible": True, "width": 2}
        self.layers[layer_name]["segments"] = list(segments)
        self._invalidate_cache()
        self.update()

    def set_layer_holes(self, layer_name, holes):
        if layer_name not in self.hole_layers:
            self.hole_layers[layer_name] = {"holes": [], "color": "#00ffff", "visible": True}
        self.hole_layers[layer_name]["holes"] = list(holes)
        self._invalidate_cache()
        self.update()

    def set_layer_visible(self, layer_name, visible):
        if layer_name in self.layers:
            self.layers[layer_name]["visible"] = bool(visible)
        if layer_name in self.hole_layers:
            self.hole_layers[layer_name]["visible"] = bool(visible)
        self._invalidate_cache()
        self.update()

    def set_layer_color(self, layer_name, color_hex):
        if layer_name in self.layers:
            self.layers[layer_name]["color"] = color_hex
        if layer_name in self.hole_layers:
            self.hole_layers[layer_name]["color"] = color_hex
        self._invalidate_cache()
        self.update()

    def clear_layer(self, layer_name):
        if layer_name in self.layers:
            self.layers[layer_name]["segments"] = []
        if layer_name in self.hole_layers:
            self.hole_layers[layer_name]["holes"] = []
        self._invalidate_cache()
        self.update()

    def clear_all_layers(self):
        for name in self.layers:
            self.layers[name]["segments"] = []
        for name in self.hole_layers:
            self.hole_layers[name]["holes"] = []
        self._invalidate_cache()
        self.update()

    # NUEVA: property de compatibilidad para codigo antiguo
    # que hacia self.canvas_2d.toolpath_segments.
    # Devuelve los segmentos de todas las capas visibles.
    @property
    def toolpath_segments(self):
        segs = []
        for ld in self.layers.values():
            if ld.get('visible', True) and ld.get('segments'):
                segs.extend(ld['segments'])
        return segs

    # Legacy API para .gcode
    def set_toolpath_segments(self, segments):
        self.set_layer_segments("GCode", segments)

    def set_holes(self, holes):
        self.set_layer_holes("PTH", holes)

    def reset_view(self):
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._invalidate_cache()
        self.update()

    def _animate(self):
        dx = self.target_x - self.current_x
        dy = self.target_y - self.current_y
        if abs(dx) < 0.005 and abs(dy) < 0.005:
            if self.current_x != self.target_x or self.current_y != self.target_y:
                self.current_x = self.target_x
                self.current_y = self.target_y
                self.update()
            return
        self.current_x += dx * ANIM_SPEED
        self.current_y += dy * ANIM_SPEED
        self.update()

    def wheelEvent(self, event):
        self._update_mouse_world(event.position())
        wx_before, wy_before = self._mouse_world
        factor = 1.15 if event.angleDelta().y() > 0 else (1.0 / 1.15)
        self.zoom *= factor
        self.zoom = max(0.05, min(100.0, self.zoom))
        w = self.width(); h = self.height()
        margin = 40
        base_scale = min((w - 2 * margin) / self.area_x, (h - 2 * margin) / self.area_y)
        scale = base_scale * self.zoom
        cx_px = event.position().x()
        cy_px = event.position().y()
        self.pan_x = cx_px - (w - self.area_x * scale) / 2 - wx_before * scale
        self.pan_y = cy_px - (h - self.area_y * scale) / 2 - (self.area_y - wy_before) * scale
        self._invalidate_cache()
        self.update()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._dragging = True
            self._drag_last = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        self._update_mouse_world(event.position())
        if self._dragging and self._drag_last is not None:
            delta = event.position() - self._drag_last
            self.pan_x += delta.x()
            self.pan_y += delta.y()
            self._drag_last = event.position()
            self._pan_dirty += abs(delta.x()) + abs(delta.y())
            if self._pan_dirty > 15:
                self._invalidate_cache()
                self._pan_dirty = 0
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._dragging = False
            self.setCursor(Qt.ArrowCursor)
            self._invalidate_cache()
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.reset_view()

    def _update_mouse_world(self, pos):
        w = self.width(); h = self.height()
        margin = 40
        base_scale = min((w - 2 * margin) / self.area_x, (h - 2 * margin) / self.area_y)
        scale = base_scale * self.zoom
        x0 = (w - self.area_x * scale) / 2 + self.pan_x
        y0 = (h - self.area_y * scale) / 2 + self.pan_y
        wx = (pos.x() - x0) / scale
        wy = (y0 + self.area_y * scale - pos.y()) / scale
        self._mouse_world = (wx, wy)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        try:
            self._paint(p)
        except Exception as e:
            print(f"paintEvent error: {e}")
        finally:
            p.end()

    def _paint(self, p):
        if not (math.isfinite(self.current_x) and math.isfinite(self.current_y)):
            self.current_x = 0.0
            self.current_y = 0.0

        w = self.width(); h = self.height()
        p.fillRect(0, 0, w, h, QColor(C_BG_0))

        margin = 40
        base_scale = min((w - 2 * margin) / self.area_x, (h - 2 * margin) / self.area_y)
        scale = base_scale * self.zoom
        x0 = (w - self.area_x * scale) / 2 + self.pan_x
        y0 = (h - self.area_y * scale) / 2 + self.pan_y

        def w2s(wx, wy):
            return x0 + wx * scale, y0 + (self.area_y - wy) * scale

        # Grid
        world_step_px = 50 / scale
        candidates = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000]
        grid_step = candidates[-1]
        for c in candidates:
            if c >= world_step_px:
                grid_step = c
                break
        p.setPen(QPen(QColor(255, 255, 255, 12)))
        world_left = (0 - x0) / scale
        world_right = (w - x0) / scale
        world_bottom = (y0 + self.area_y * scale - h) / scale
        world_top = (y0 + self.area_y * scale - 0) / scale
        vx = math.floor(world_left / grid_step) * grid_step
        while vx <= world_right:
            sx, _ = w2s(vx, 0)
            p.drawLine(int(sx), 0, int(sx), h)
            vx += grid_step
        vy = math.floor(world_bottom / grid_step) * grid_step
        while vy <= world_top:
            _, sy = w2s(0, vy)
            p.drawLine(0, int(sy), w, int(sy))
            vy += grid_step

        # Ejes
        ox, oy = w2s(0, 0)
        axes_pen = QPen(QColor(C_AXIS))
        axes_pen.setWidth(2)
        p.setPen(axes_pen)
        if 0 <= oy <= h: p.drawLine(0, int(oy), w, int(oy))
        if 0 <= ox <= w: p.drawLine(int(ox), 0, int(ox), h)
        if 0 <= oy <= h:
            p.drawLine(int(w - 4), int(oy), int(w - 16), int(oy - 5))
            p.drawLine(int(w - 4), int(oy), int(w - 16), int(oy + 5))
            p.drawLine(int(w - 16), int(oy - 5), int(w - 16), int(oy + 5))
        if 0 <= ox <= w:
            p.drawLine(int(ox), 4, int(ox - 5), 16)
            p.drawLine(int(ox), 4, int(ox + 5), 16)
            p.drawLine(int(ox - 5), 16, int(ox + 5), 16)
        p.setPen(QColor(C_AXIS))
        p.setFont(QFont("sans-serif", 10, QFont.Bold))
        if 0 <= oy <= h: p.drawText(w - 25, int(oy) - 8, "X")
        if 0 <= ox <= w: p.drawText(int(ox) + 8, 22, "Y")
        p.setFont(QFont("monospace", 8))
        vx = math.floor(world_left / grid_step) * grid_step
        while vx <= world_right:
            if vx != 0:
                sx, _ = w2s(vx, 0)
                if 15 <= sx <= w - 15 and 0 <= oy <= h:
                    p.drawText(int(sx) - 10, int(oy) + 14, f"{int(vx)}")
            vx += grid_step
        vy = math.floor(world_bottom / grid_step) * grid_step
        while vy <= world_top:
            if vy != 0:
                _, sy = w2s(0, vy)
                if 15 <= sy <= h - 15 and 0 <= ox <= w:
                    p.drawText(int(ox) + 6, int(sy) + 4, f"{int(vy)}")
            vy += grid_step
        if 0 <= ox <= w and 0 <= oy <= h:
            p.drawText(int(ox) + 6, int(oy) + 14, "0")

        # Capas
        total_segs = sum(len(ld["segments"]) for ld in self.layers.values())
        if total_segs > 0:
            cache_key = [w, h, round(self.zoom, 4),
                         round(self.pan_x, 2), round(self.pan_y, 2),
                         round(self.area_x, 2), round(self.area_y, 2)]
            for name, ld in self.layers.items():
                cache_key.extend([name, ld["visible"], len(ld["segments"])])
            cache_key = tuple(cache_key)

            if self._toolpath_cache is None or self._toolpath_cache_key != cache_key:
                self._toolpath_cache = QPixmap(w, h)
                self._toolpath_cache.fill(Qt.transparent)
                cp = QPainter(self._toolpath_cache)
                cp.setRenderHint(QPainter.Antialiasing)

                for layer_name, ld in self.layers.items():
                    if not ld["visible"] or not ld["segments"]:
                        continue
                    pen = QPen(QColor(ld["color"]))
                    pen.setWidth(ld.get("width", 2))
                    cp.setPen(pen)
                    for seg in ld["segments"]:
                        n = len(seg)
                        if n >= 4:
                            x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
                        else:
                            continue
                        sx1, sy1 = w2s(x1, y1)
                        sx2, sy2 = w2s(x2, y2)
                        if (max(sx1, sx2) < 0 or min(sx1, sx2) > w or
                            max(sy1, sy2) < 0 or min(sy1, sy2) > h):
                            continue
                        cp.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))
                cp.end()
                self._toolpath_cache_key = cache_key
            p.drawPixmap(0, 0, self._toolpath_cache)
        else:
            box_pen = QPen(QColor(C_TEXT_2))
            box_pen.setStyle(Qt.DashLine)
            p.setPen(box_pen)
            corners = [(0, 0), (self.area_x, 0), (self.area_x, self.area_y), (0, self.area_y)]
            for i in range(4):
                ax, ay = w2s(*corners[i])
                bx, by = w2s(*corners[(i + 1) % 4])
                p.drawLine(int(ax), int(ay), int(bx), int(by))

        # Taladros
        for layer_name, ld in self.hole_layers.items():
            if not ld["visible"]:
                continue
            color = QColor(ld["color"])
            for (hx, hy, hd) in ld["holes"]:
                if not (math.isfinite(hx) and math.isfinite(hy)):
                    continue
                sx, sy = w2s(hx, hy)
                r_px = max(2, int((hd / 2.0) * scale))
                p.setPen(QPen(color, 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(sx, sy), r_px, r_px)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(QPointF(sx, sy), 2, 2)

        # Herramienta: siempre visible. El crosshair sigue la
        # posicion interpolada que manda el firmware cada 100 ms
        # (mensajes POS), asi que durante un job se mueve suave.
        cx, cy = w2s(self.current_x, self.current_y)
        p.setPen(QPen(QColor(C_GREEN), 2))
        p.drawLine(int(cx) - 14, int(cy), int(cx) - 5, int(cy))
        p.drawLine(int(cx) + 5, int(cy), int(cx) + 14, int(cy))
        p.drawLine(int(cx), int(cy) - 14, int(cx), int(cy) - 5)
        p.drawLine(int(cx), int(cy) + 5, int(cx), int(cy) + 14)
        p.setBrush(QColor(C_GREEN))
        p.drawEllipse(QPointF(cx, cy), 4, 4)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 8, 8)

        # Burbuja coordenadas
        coord_text = f"({self.current_x:.2f}, {self.current_y:.2f})"
        p.setFont(QFont("monospace", 9, QFont.Bold))
        metrics = p.fontMetrics()
        text_w = metrics.horizontalAdvance(coord_text) + 14
        text_h = metrics.height() + 8
        bx = cx + 15
        by = cy - text_h - 6
        if bx + text_w > w - 4: bx = cx - text_w - 15
        if by < 4: by = cy + 15
        if bx < 4: bx = 4
        p.setBrush(QColor(C_BG_1))
        p.setPen(QPen(QColor(C_GREEN), 1))
        p.drawRoundedRect(int(bx), int(by), int(text_w), int(text_h), 6, 6)
        p.setPen(QColor(C_GREEN))
        p.drawText(int(bx) + 7, int(by) + text_h - 8, coord_text)

        # Info
        p.setPen(QColor(C_TEXT_2))
        p.setFont(QFont("monospace", 9))
        info = (f"Zoom: {self.zoom * 100:.0f}%  |  "
                f"Pos: ({self.current_x:.2f}, {self.current_y:.2f})  |  "
                f"Cursor: ({self._mouse_world[0]:.1f}, {self._mouse_world[1]:.1f})")
        p.drawText(12, h - 12, info)


class ViewerCanvas3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ViewerCanvas")
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.current_x = 0.0; self.current_y = 0.0; self.current_z = 0.0
        self.target_x  = 0.0; self.target_y  = 0.0; self.target_z  = 0.0
        self.area_x = 200.0; self.area_y = 200.0
        self.toolpath_segments = []
        self.scale_factor = 1.5

        self.rot_z = 30.0     # yaw
        self.rot_x = 45.0     # pitch: 0..89

        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        # Paths en coords de mundo
        self._grid_path = None
        self._frame_path = None
        self._tps_paths = None
        self._paths_key = None
        # Cache del pixmap estatico (para no redibujar el
        # toolpath en cada frame de animacion)
        self._cache_pixmap = None
        self._cache_key = None

        # Taladros (mismos colores que el visor 2D)
        self.hole_layers = {
            "PTH":  {"holes": [], "color": "#00ffff", "visible": True},
            "NPTH": {"holes": [], "color": "#ff00ff", "visible": True},
        }
        self._holes_version = 0

        self._dragging = False
        self._drag_last = None
        self._drag_button = Qt.MiddleButton
        self._dragging_orbit = False   # para bajar AA durante orbit

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(int(1000 / ANIM_FPS))
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start()

    # ---------- API ----------
    def set_position(self, x, y, z=0.0):
        self.target_x = x; self.target_y = y; self.target_z = z

    def set_position_immediate(self, x, y, z=0.0):
        self.current_x = x; self.current_y = y; self.current_z = z
        self.target_x = x; self.target_y = y; self.target_z = z
        self.update()

    def set_toolpath_segments(self, segments):
        self.toolpath_segments = list(segments)
        self._paths_key = None      # forzar reconstruccion
        self.fit_to_content()
        self.update()

    def set_layer_holes(self, layer_name, holes):
        if layer_name not in self.hole_layers:
            self.hole_layers[layer_name] = {"holes": [], "color": "#00ffff", "visible": True}
        self.hole_layers[layer_name]['holes'] = list(holes)
        self._holes_version += 1
        self._cache_key = None
        self.update()

    def set_holes(self, holes):
        self.set_layer_holes("PTH", holes)

    def reset_view(self):
        self.rot_z = 30.0
        self.rot_x = 45.0
        self.fit_to_content()
        self.update()

    def fit_to_content(self):
        segs = self.toolpath_segments
        if not segs or self.width() < 50 or self.height() < 50:
            self.zoom = 1.0
            self.pan_x = 0.0
            self.pan_y = 0.0
            return

        xs = []; ys = []
        for s in segs:
            xs.append(s[0]); xs.append(s[2])
            ys.append(s[1]); ys.append(s[3])
        if not xs:
            return

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        world_cx = (min_x + max_x) / 2.0
        world_cy = (min_y + max_y) / 2.0

        margin = 70
        vw = max(self.width() - margin * 2, 100)
        vh = max(self.height() - margin * 2, 100)

        old_zoom = self.zoom
        self.zoom = 1.0
        proj = [self._project_raw(x, y) for (x, y) in
                [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]]
        self.zoom = old_zoom

        pxs = [p[0] for p in proj]
        pys = [p[1] for p in proj]
        sw = max(pxs) - min(pxs)
        sh = max(pys) - min(pys)
        if sw < 1e-3 or sh < 1e-3:
            return

        target = min(vw / sw, vh / sh)
        self.zoom = max(0.05, min(30.0, target))

        self.pan_x = 0.0
        self.pan_y = 0.0
        sx, sy = self._project_raw(world_cx, world_cy)
        self.pan_x = -sx
        self.pan_y = -sy

    # ---------- Proyeccion a mano (para puntos sueltos) ----------
    def _project_raw(self, x, y):
        sf = self.scale_factor * self.zoom
        mx = (x - self.area_x / 2.0) * sf
        my = (y - self.area_y / 2.0) * sf

        yaw = math.radians(self.rot_z)
        cyw, syw = math.cos(yaw), math.sin(yaw)
        rx = mx * cyw - my * syw
        ry = mx * syw + my * cyw

        pitch = math.radians(self.rot_x)
        cp, sp = math.cos(pitch), math.sin(pitch)
        screen_y_up = ry * sp
        return rx, -screen_y_up

    def _project(self, x, y, z, w, h):
        sx, sy = self._project_raw(x, y)
        # Z: proyeccion vertical pura (siempre hacia arriba)
        sf = self.scale_factor * self.zoom
        pitch = math.radians(self.rot_x)
        z_px = z * sf * math.cos(pitch)
        return QPointF(w / 2.0 + self.pan_x + sx,
                       h / 2.0 + self.pan_y + sy - z_px)

    # ---------- Transformacion afin mundo -> pantalla ----------
    def _get_transform(self, w, h):
        sf = self.scale_factor * self.zoom
        yaw = math.radians(self.rot_z)
        pitch = math.radians(self.rot_x)
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)

        ax = self.area_x / 2.0
        ay = self.area_y / 2.0

        m11 = sf * cy
        m21 = -sf * sy
        m12 = -sf * sy * sp
        m22 = -sf * cy * sp

        dx = -ax * m11 - ay * m21 + w / 2.0 + self.pan_x
        dy = -ax * m12 - ay * m22 + h / 2.0 + self.pan_y

        return QTransform(m11, m12, m21, m22, dx, dy)

    # ---------- Reconstruccion de paths ----------
    def _rebuild_paths_if_needed(self):
        key = (round(self.area_x, 3), round(self.area_y, 3),
               len(self.toolpath_segments))
        if self._paths_key == key and self._tps_paths is not None:
            return
        self._paths_key = key

        # Grid
        gp = QPainterPath()
        step = 10.0
        n_x = int(self.area_x / step) + 1
        n_y = int(self.area_y / step) + 1
        for i in range(n_x):
            x = i * step
            gp.moveTo(x, 0); gp.lineTo(x, self.area_y)
        for i in range(n_y):
            y = i * step
            gp.moveTo(0, y); gp.lineTo(self.area_x, y)
        self._grid_path = gp

        # Marco
        fp = QPainterPath()
        fp.moveTo(0, 0)
        fp.lineTo(self.area_x, 0)
        fp.lineTo(self.area_x, self.area_y)
        fp.lineTo(0, self.area_y)
        fp.lineTo(0, 0)
        self._frame_path = fp

        # Toolpath agrupado por COLOR (cada segmento puede traer
        # su propio color en un 7º elemento; si no, se usa el
        # color por tipo)
        by_color = {}
        color_for_type = {
            "rapid": C_RAPID,
            "cut":   C_CUT,
            "arc":   C_ARC,
        }
        for seg in self.toolpath_segments:
            n = len(seg)
            if n >= 5:
                x1, y1, x2, y2, tipo = seg[0], seg[1], seg[2], seg[3], seg[4]
            elif n == 4:
                x1, y1, x2, y2 = seg
                tipo = "cut"
            else:
                continue
            if n >= 7:
                color = seg[6]
            else:
                color = color_for_type.get(tipo, C_CUT)
            p = by_color.get(color)
            if p is None:
                p = QPainterPath()
                by_color[color] = p
            p.moveTo(x1, y1)
            p.lineTo(x2, y2)

        self._tps_paths = by_color

    # ---------- Interaccion ----------
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        mods = event.modifiers()
        if mods & Qt.ShiftModifier:
            self.rot_z = (self.rot_z + (2.0 if delta > 0 else -2.0)) % 360.0
            self.update()
            return
        if mods & Qt.ControlModifier:
            self.rot_x = max(0.0, min(89.0,
                             self.rot_x + (2.0 if delta > 0 else -2.0)))
            self.update()
            return

        pos = event.position()
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        dx = pos.x() - (cx + self.pan_x)
        dy = pos.y() - (cy + self.pan_y)

        factor = 1.12 if delta > 0 else (1.0 / 1.12)
        new_zoom = max(0.05, min(30.0, self.zoom * factor))
        actual = new_zoom / self.zoom

        self.pan_x = self.pan_x * actual + dx * (1.0 - actual)
        self.pan_y = self.pan_y * actual + dy * (1.0 - actual)
        self.zoom = new_zoom
        self.update()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.LeftButton, Qt.RightButton):
            self.setFocus()
            self._dragging = True
            self._drag_last = event.position()
            self._drag_button = event.button()
            if event.button() == Qt.LeftButton:
                self._dragging_orbit = True
                self.setCursor(Qt.ClosedHandCursor)
            else:
                self.setCursor(Qt.SizeAllCursor)

    def mouseMoveEvent(self, event):
        if not self._dragging or self._drag_last is None:
            return
        delta = event.position() - self._drag_last

        if self._drag_button == Qt.LeftButton:
            k = 0.25 if (event.modifiers() & Qt.ShiftModifier) else 0.5
            self.rot_z = (self.rot_z + delta.x() * k) % 360.0
            self.rot_x = max(0.0, min(89.0, self.rot_x + delta.y() * k))
        else:
            k = 0.5 if (event.modifiers() & Qt.ShiftModifier) else 1.0
            self.pan_x += delta.x() * k
            self.pan_y += delta.y() * k

        self._drag_last = event.position()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.LeftButton, Qt.RightButton):
            self._dragging = False
            self._dragging_orbit = False
            self.setCursor(Qt.ArrowCursor)
            self.update()   # repintar con AA completo

    def mouseDoubleClickEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.RightButton, Qt.MiddleButton):
            self.reset_view()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_1:
            self.rot_x = 89.0; self.rot_z = 0.0
        elif key == Qt.Key_2:
            self.rot_x = 0.0; self.rot_z = 0.0
        elif key == Qt.Key_3:
            self.rot_x = 0.0; self.rot_z = 90.0
        elif key == Qt.Key_4:
            self.rot_x = 45.0; self.rot_z = 30.0
        elif key == Qt.Key_0:
            self.reset_view(); return
        elif key == Qt.Key_F:
            self.fit_to_content(); self.update(); return
        else:
            super().keyPressEvent(event); return
        self.update()

    # ---------- Animacion ----------
    def _animate(self):
        dx = self.target_x - self.current_x
        dy = self.target_y - self.current_y
        dz = self.target_z - self.current_z
        if abs(dx) < 0.005 and abs(dy) < 0.005 and abs(dz) < 0.005:
            return
        self.current_x += dx * ANIM_SPEED
        self.current_y += dy * ANIM_SPEED
        self.current_z += dz * ANIM_SPEED
        self.update()

    # ---------- Paint ----------
    def paintEvent(self, event):
        p = QPainter(self)
        try:
            self._paint(p)
        except Exception as e:
            print(f"3D paintEvent error: {e}")
        finally:
            p.end()

    def _paint(self, p):
        w = self.width(); h = self.height()

        # Clave de cache: depende SOLO de la vista, NO de la
        # posicion de la herramienta. Asi animar la herramienta
        # no regenera el pixmap cacheado.
        key = (w, h,
               round(self.zoom, 5),
               round(self.pan_x, 2),
               round(self.pan_y, 2),
               round(self.rot_z, 3),
               round(self.rot_x, 3),
               round(self.area_x, 3),
               round(self.area_y, 3),
               len(self.toolpath_segments),
               self._holes_version)

        if self._cache_pixmap is None or self._cache_key != key:
            self._cache_pixmap = QPixmap(w, h)
            self._cache_pixmap.fill(Qt.transparent)
            cp = QPainter(self._cache_pixmap)
            cp.setRenderHint(QPainter.Antialiasing,
                             not self._dragging_orbit)
            self._paint_static_layer(cp, w, h)
            cp.end()
            self._cache_key = key

        p.drawPixmap(0, 0, self._cache_pixmap)

        # Capa dinamica: herramienta + info
        self._draw_tool(p, w, h)
        self._draw_info(p, w, h)

    def _paint_static_layer(self, p, w, h):
        p.fillRect(0, 0, w, h, QColor(C_BG_0))

        self._rebuild_paths_if_needed()
        t = self._get_transform(w, h)

        # Grid + marco (transformados)
        p.save()
        p.setTransform(t)
        pen = QPen(QColor(255, 255, 255, 14))
        pen.setWidthF(1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawPath(self._grid_path)

        pen = QPen(QColor(C_AXIS))
        pen.setWidthF(2.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawPath(self._frame_path)
        p.restore()

        # Toolpath (transformado, un drawPath por color)
        p.save()
        p.setTransform(t)
        for color, path in self._tps_paths.items():
            if path.isEmpty():
                continue
            pen = QPen(QColor(color))
            pen.setWidthF(2.0)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPath(path)
        p.restore()

        # Taladros
        self._draw_holes(p, w, h)

        # Ejes + brujula (estaticos respecto a la vista)
        self._draw_axes(p, w, h)
        self._draw_compass(p, w, h)

    def _draw_info(self, p, w, h):
        p.setPen(QColor(C_TEXT_2))
        p.setFont(QFont("monospace", 9))
        info = (f"Zoom: {self.zoom * 100:.0f}%  |  "
                f"Yaw: {self.rot_z:.0f}\u00b0  Pitch: {self.rot_x:.0f}\u00b0  |  "
                f"Izq: orbit  Der/Medio: pan  Rueda: zoom al cursor  "
                f"F: encuadrar  1-4: vistas")
        p.drawText(12, h - 12, info)

    # ---------- Elementos dibujados a mano (pocos puntos) ----------
    def _draw_axes(self, p, w, h):
        origin = self._project(0, 0, 0, w, h)
        L = max(min(self.area_x, self.area_y) * 0.1, 15.0)

        x_end = self._project(L, 0, 0, w, h)
        p.setPen(QPen(QColor("#ef4444"), 3))
        p.drawLine(origin, x_end)
        p.setFont(QFont("monospace", 11, QFont.Bold))
        p.setPen(QColor("#ef4444"))
        p.drawText(int(x_end.x()) + 4, int(x_end.y()) - 2, "X")

        y_end = self._project(0, L, 0, w, h)
        p.setPen(QPen(QColor("#22c55e"), 3))
        p.drawLine(origin, y_end)
        p.setPen(QColor("#22c55e"))
        p.drawText(int(y_end.x()) + 4, int(y_end.y()) - 2, "Y")

        z_end = self._project(0, 0, L, w, h)
        p.setPen(QPen(QColor("#3b82f6"), 3))
        p.drawLine(origin, z_end)
        p.setPen(QColor("#3b82f6"))
        p.drawText(int(z_end.x()) + 4, int(z_end.y()) - 2, "Z")

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C_TEXT_0))
        p.drawEllipse(origin, 3, 3)
        p.setBrush(Qt.NoBrush)

    def _draw_holes(self, p, w, h):
        """Dibuja los taladros como circulos en Z=0."""
        sf = self.scale_factor * self.zoom
        for name, ld in self.hole_layers.items():
            if not ld.get('visible', True):
                continue
            color = QColor(ld['color'])
            for (hx, hy, hd) in ld['holes']:
                if not (math.isfinite(hx) and math.isfinite(hy)):
                    continue
                pt = self._project(hx, hy, 0, w, h)
                r_px = max(3.0, (hd / 2.0) * sf)
                pen = QPen(color)
                pen.setWidthF(2.0)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r_px, r_px)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(pt, 2.0, 2.0)
                p.setBrush(Qt.NoBrush)

    def _draw_tool(self, p, w, h):
        base = self._project(self.current_x, self.current_y, 0, w, h)
        top  = self._project(self.current_x, self.current_y, self.current_z, w, h)

        p.setPen(QPen(QColor(C_GREEN), 1, Qt.DashLine))
        p.drawLine(base, top)

        p.setPen(QPen(QColor(C_GREEN), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(top, 6, 6)
        p.setBrush(QColor(C_GREEN))
        p.setPen(Qt.NoPen)
        p.drawEllipse(top, 3, 3)
        p.setBrush(Qt.NoBrush)

        coord_text = f"({self.current_x:.2f}, {self.current_y:.2f}, {self.current_z:.2f})"
        p.setFont(QFont("monospace", 9, QFont.Bold))
        metrics = p.fontMetrics()
        tw = metrics.horizontalAdvance(coord_text) + 14
        th = metrics.height() + 8
        bx = top.x() + 15
        by = top.y() - th - 6
        if bx + tw > w - 4: bx = top.x() - tw - 15
        if by < 4: by = top.y() + 15
        if bx < 4: bx = 4
        p.setBrush(QColor(C_BG_1))
        p.setPen(QPen(QColor(C_GREEN), 1))
        p.drawRoundedRect(int(bx), int(by), int(tw), int(th), 6, 6)
        p.setPen(QColor(C_GREEN))
        p.drawText(int(bx) + 7, int(by) + th - 8, coord_text)
        p.setBrush(Qt.NoBrush)

    def _draw_compass(self, p, w, h):
        cx = w - 55
        cy = 55
        L = 24
        p.setRenderHint(QPainter.Antialiasing, True)

        p.setBrush(QColor(22, 23, 28, 200))
        p.setPen(QPen(QColor(C_BORDER), 1))
        p.drawEllipse(QPointF(cx, cy), 34, 34)
        p.setBrush(Qt.NoBrush)

        yaw = math.radians(self.rot_z)
        pitch = math.radians(self.rot_x)
        cyw, syw = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)

        def project_axis(vx, vy, vz):
            rx = vx * cyw - vy * syw
            ry = vx * syw + vy * cyw
            screen_y_up = ry * sp + vz * cp
            return QPointF(cx + rx * L, cy - screen_y_up * L)

        x_tip = project_axis(1, 0, 0)
        y_tip = project_axis(0, 1, 0)
        z_tip = project_axis(0, 0, 1)

        p.setPen(QPen(QColor('#ef4444'), 2))
        p.drawLine(QPointF(cx, cy), x_tip)
        p.setPen(QPen(QColor('#22c55e'), 2))
        p.drawLine(QPointF(cx, cy), y_tip)
        p.setPen(QPen(QColor('#3b82f6'), 2))
        p.drawLine(QPointF(cx, cy), z_tip)

        p.setFont(QFont('monospace', 8, QFont.Bold))
        p.setPen(QColor('#ef4444'))
        p.drawText(x_tip + QPointF(4, 3), 'X')
        p.setPen(QColor('#22c55e'))
        p.drawText(y_tip + QPointF(4, 3), 'Y')
        p.setPen(QColor('#3b82f6'))
        p.drawText(z_tip + QPointF(4, 3), 'Z')


# ============================================================
# CARD
# ============================================================

def _make_file_item(path, name, tipo):
    """Crea un QListWidgetItem con metadata del archivo."""
    from PySide6.QtCore import Qt
    item = QListWidgetItem(name)
    item.setData(Qt.UserRole, {"path": path, "type": tipo})
    item.setToolTip(f"{tipo}: {path}")
    return item

class Card(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        lbl = QLabel(title.upper())
        lbl.setObjectName("CardTitle")
        layout.addWidget(lbl)
        self.body = QVBoxLayout()
        self.body.setSpacing(4)
        layout.addLayout(self.body)

    def add_coord_row(self, label, value="-"):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setObjectName("CoordLabel")
        val = QLabel(value)
        val.setObjectName("CoordValue")
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        self.body.addLayout(row)
        return val

    def add_info_row(self, label, value="-"):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {C_TEXT_2}; font-size: 11px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {C_TEXT_1}; font-family: monospace; font-size: 11px;")
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        self.body.addLayout(row)
        return val


# ============================================================
# VENTANA PRINCIPAL
# ============================================================


# ============================================================
# SIMULADOR DE TOOLPATH
# ============================================================
class SimulationController(QObject):
    """Recorre segmentos del toolpath con avance basado en tiempo real.

    No interactua con el puerto serie. Solo mueve la herramienta
    visualmente para previsualizar el trabajo.
    """
    position_changed = Signal(float, float, float, bool)
    line_changed = Signal(int)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments = []
        self.current_idx = 0
        self.segment_progress = 0.0
        self.speed_mm_s = 500.0
        self.running = False
        self.paused = False
        self.current_cutting = False
        self._last_tick_time = None

        self.timer = QTimer(self)
        self.timer.setInterval(16)  # ~60 Hz, con avance independiente del intervalo.
        self.timer.timeout.connect(self._tick)

    def start(self, segments, speed_mm_s=500.0):
        if not segments:
            return
        self.segments = segments
        self.current_idx = 0
        self.segment_progress = 0.0
        self.speed_mm_s = speed_mm_s
        self.running = True
        self.paused = False
        self._last_tick_time = __import__("time").monotonic()

        seg = self.segments[0]
        self.current_cutting = len(seg) > 4 and seg[4] != "rapid"
        self.position_changed.emit(seg[0], seg[1], seg[6] if len(seg) > 6 else 0.0, self.current_cutting)
        self.line_changed.emit(seg[5] if len(seg) > 5 else 0)
        self.progress.emit(0, len(self.segments))

        self.timer.start()

    def stop(self):
        self.running = False
        self.paused = False
        self._last_tick_time = None
        self.timer.stop()

    def set_speed(self, mm_s):
        self.speed_mm_s = max(1.0, float(mm_s))

    def _tick(self):
        if not self.running or self.paused:
            return

        now = __import__("time").monotonic()
        if self._last_tick_time is None:
            self._last_tick_time = now
        dt = min(0.05, max(0.001, now - self._last_tick_time))
        self._last_tick_time = now
        budget = self.speed_mm_s * dt
        safety = 10000
        position = None
        position_cutting = False
        latest_line = None
        progress_changed = False

        while budget > 0.0 and safety > 0:
            safety -= 1

            if self.current_idx >= len(self.segments):
                self.timer.stop()
                self.running = False
                if position is not None:
                    self.position_changed.emit(*position, position_cutting)
                if progress_changed:
                    self.progress.emit(self.current_idx, len(self.segments))
                self.finished.emit()
                return

            seg = self.segments[self.current_idx]
            x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
            tipo = seg[4] if len(seg) > 4 else "cut"
            line_num = seg[5] if len(seg) > 5 else -1

            z1 = seg[6] if len(seg) > 6 else 0.0
            z2 = seg[7] if len(seg) > 7 else z1
            dx = x2 - x1
            dy = y2 - y1
            dz = z2 - z1
            length = (dx * dx + dy * dy + dz * dz) ** 0.5

            if length < 0.001:
                self.current_idx += 1
                self.segment_progress = 0.0
                progress_changed = True
                continue

            # Los rapidos consumen 1/5 del presupuesto (van 5x mas rapido)
            speed_factor = 5.0 if tipo == "rapid" else 1.0
            remaining = length * (1.0 - self.segment_progress)
            remaining_cost = remaining / speed_factor

            if budget >= remaining_cost:
                # Consumimos el segmento entero y seguimos con el siguiente
                budget -= remaining_cost
                self.current_idx += 1
                self.segment_progress = 0.0
                position = (x2, y2, z2)
                position_cutting = tipo != "rapid"
                progress_changed = True
                if self.current_idx < len(self.segments):
                    next_seg = self.segments[self.current_idx]
                    latest_line = next_seg[5] if len(next_seg) > 5 else -1
            else:
                # Avance parcial dentro del segmento actual
                self.segment_progress += (budget * speed_factor) / length
                px = x1 + dx * self.segment_progress
                py = y1 + dy * self.segment_progress
                pz = z1 + dz * self.segment_progress
                position = (px, py, pz)
                position_cutting = tipo != "rapid"
                latest_line = line_num
                budget = 0.0

        if position is not None:
            self.current_cutting = position_cutting
            self.position_changed.emit(*position, position_cutting)
        if latest_line is not None:
            self.line_changed.emit(latest_line)
        if progress_changed:
            self.progress.emit(self.current_idx, len(self.segments))


class MainWindow(QMainWindow):
    request_connect = Signal(str, int)
    request_disconnect = Signal()
    request_send = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CNC Proyect")
        self.setWindowIcon(QIcon(make_logo_pixmap(256, dark=False)))
        self.resize(1400, 900)
        self.settings = QSettings("CNC Proyect", "CNC IDE Web")
        self.connected = False
        self.current_port = ""
        self.poll_interval = self.settings.value("poll_interval", 500, type=int)
        self.current_z = 0.0
        self._position_reported = False
        self._last_position_reported = 0.0
        self._motor_pulse_stats = {
            axis: {"count": 0, "rate": 0.0} for axis in ("X", "Y", "Z")
        }
        self._pulse_telemetry_received = False
        self.gcode_lines = []
        self.layer_gcodes = {}
        self._fabrication_layers = {}

        self.polling_enabled = True
        self._is_auto_m114 = False

        self.job_running = False
        self.job_paused = False
        self.job_lines = []
        self.job_current = 0
        self.job_next = 0
        self._job_pending = collections.deque()
        self._job_ack_events = collections.deque()
        self.job_total = 0
        self._last_sent_was_job = False
        self._job_start_time = 0.0

        self.jog_step = 1.0
        self.jog_speed = 300.0
        self.feed_override = 100

        self._setup_worker()
        self._build_ui()
        self._bridge = CncBridge(self)

        self.poll_timer = QTimer()
        self.poll_timer.setInterval(self.poll_interval)
        self.poll_timer.timeout.connect(self._poll_position)

        # Finales de carrera: se consultan en segundo plano y solo se
        # muestran los cambios (LIM_X1=1 / LIM_X1=0).
        self._limits_last = None
        self._limits_auto = False
        self._limits_skip_ok = False
        self._limits_timer = QTimer()
        self._limits_timer.setInterval(300)
        self._limits_timer.timeout.connect(self._poll_limits)
        self._limits_timer.start()

        self.time_timer = QTimer()
        self.time_timer.setInterval(500)
        self.time_timer.timeout.connect(self._update_job_clock)

        # Simulador de toolpath
        self.simulator = SimulationController(self)
        self.simulator.position_changed.connect(self._on_sim_position)
        self.simulator.line_changed.connect(self._on_sim_line)
        self.simulator.progress.connect(self._on_sim_progress)
        self.simulator.finished.connect(self._on_sim_finished)

        # Aplicar tema guardado (por defecto oscuro)
        saved_theme = self.settings.value("theme", "dark", type=str)
        QTimer.singleShot(0, lambda: self._apply_theme(saved_theme))

    def _setup_worker(self):
        self.thread = QThread(self)
        self.worker = SerialWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.setup)
        self.request_connect.connect(self.worker.connect_port)
        self.request_disconnect.connect(self.worker.disconnect_port)
        self.request_send.connect(self.worker.send)
        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.error.connect(self._on_error)
        self.worker.line_received.connect(self._on_line_received)
        self.worker.line_sent.connect(self._on_line_sent)
        self.thread.start()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())

        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setHandleWidth(1)

        top_splitter = QSplitter(Qt.Horizontal)
        top_splitter.setHandleWidth(1)
        top_splitter.addWidget(self._build_sidebar())
        top_splitter.addWidget(self._build_viewer())
        top_splitter.addWidget(self._build_right_panel())
        top_splitter.setSizes([220, 850, 300])

        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(self._build_bottom_tabs())
        main_splitter.setSizes([600, 280])

        root.addWidget(main_splitter)

    def _build_topbar(self):
        topbar = QFrame()
        topbar.setObjectName("Topbar")
        topbar.setFixedHeight(50)
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self.logo_mark = QLabel()
        self.logo_mark.setObjectName("LogoMark")
        self.logo_mark.setFixedSize(28, 28)
        self.logo_mark.setPixmap(make_logo_pixmap(28, dark=True))
        layout.addWidget(self.logo_mark)
        logo_text = QLabel("CNC Proyect")
        logo_text.setObjectName("LogoText")
        layout.addWidget(logo_text)
        layout.addSpacing(8)

        self.conn_label = QLabel("Desconectado")
        self.conn_label.setObjectName("ConnStatus")
        self.conn_label.setProperty("connected", "false")
        layout.addWidget(self.conn_label)

        layout.addStretch()

        self.poll_btn = QPushButton("Polling: ON")
        self.poll_btn.setObjectName("TopbarBtn")
        self.poll_btn.clicked.connect(self._toggle_polling)
        layout.addWidget(self.poll_btn)

        self.connect_btn = QPushButton("Conectar")
        self.connect_btn.setObjectName("TopbarBtn")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        layout.addWidget(self.connect_btn)

        self.theme_btn = QPushButton("Modo claro")
        self.theme_btn.setObjectName("TopbarBtn")
        self.theme_btn.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_btn)

        settings_btn = QPushButton("Ajustes")
        settings_btn.setObjectName("TopbarBtn")
        settings_btn.clicked.connect(self._on_settings_clicked)
        layout.addWidget(settings_btn)

        return topbar

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(180)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(0)

        lbl1 = QLabel("ARCHIVOS RECIENTES")
        lbl1.setObjectName("SectionTitle")
        layout.addWidget(lbl1)

        self.file_list = QListWidget()
        self.file_list.setObjectName("FileList")
        self.file_list.setFrameShape(QFrame.NoFrame)
        self.file_list.itemDoubleClicked.connect(self._on_file_double_clicked)
        layout.addWidget(self.file_list)

        lbl_capas = QLabel("CAPAS")
        lbl_capas.setObjectName("SectionTitle")
        layout.addWidget(lbl_capas)

        # Botones de vista rapida
        quick_row = QWidget()
        qrl = QHBoxLayout(quick_row)
        qrl.setContentsMargins(12, 2, 12, 4)
        qrl.setSpacing(4)

        quick_style = (
            f"QPushButton {{ "
            f"background-color: {C_BG_2}; border: 1px solid {C_BORDER}; "
            f"color: {C_TEXT_1}; border-radius: 4px; "
            f"padding: 3px 8px; font-size: 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ "
            f"border-color: {C_ACCENT}; color: {C_TEXT_0}; }}"
            f"QPushButton:checked {{ "
            f"background-color: {C_ACCENT}; color: {C_BG_0}; }}"
        )

        btn_all = QPushButton("Todas")
        btn_all.setStyleSheet(quick_style)
        btn_all.setCursor(Qt.PointingHandCursor)
        btn_all.setToolTip("Mostrar todas las capas")
        btn_all.clicked.connect(lambda: self._show_only_layers(None))
        qrl.addWidget(btn_all)

        for _lname, _lcolor in (("F.Cu", "#cc3333"), ("B.Cu", "#3366cc"),
                                 ("Edge.Cuts", "#ffcc00")):
            _btn = QPushButton(_lname)
            _btn.setStyleSheet(quick_style + f"QPushButton {{ color: {_lcolor}; }}")
            _btn.setCursor(Qt.PointingHandCursor)
            _btn.setToolTip(f"Ver solo {_lname}")
            _btn.clicked.connect(
                lambda checked=False, ln=_lname: self._show_only_layers(ln)
            )
            qrl.addWidget(_btn)

        qrl.addStretch()
        layout.addWidget(quick_row)

        self.layer_checkboxes = {}
        for layer_name, layer_info in [
            ("F.Cu", "#cc3333"),
            ("B.Cu", "#3366cc"),
            ("Edge.Cuts", "#ffcc00"),
            ("F.SilkS", "#ffffff"),
            ("F.Mask", "#a855f7"),
            ("PTH", "#00ffff"),
            ("NPTH", "#ff00ff"),
        ]:
            row = QHBoxLayout()
            row.setContentsMargins(16, 2, 16, 2)
            row.setSpacing(6)

            from PySide6.QtWidgets import QCheckBox
            chk = QCheckBox(layer_name)
            chk.setChecked(True)
            chk.setStyleSheet(f"color: {layer_info}; font-size: 11px; font-weight: 600;")
            chk.stateChanged.connect(
                lambda state, ln=layer_name: self.canvas_2d.set_layer_visible(ln, state != 0)
            )
            row.addWidget(chk)
            row.addStretch()
            self.layer_checkboxes[layer_name] = chk

            w_container = QWidget()
            w_container.setLayout(row)
            layout.addWidget(w_container)

        lbl2 = QLabel("ABRIR / CONVERTIR")
        lbl2.setObjectName("SectionTitle")
        layout.addWidget(lbl2)

        for label, handler in [
            ("Abrir G-code", self._open_gcode_file),
            ("Convertir SVG", self._open_svg_file),
            ("Convertir DXF", self._open_dxf_file),
            ("Convertir Gerber (PCB)", self._open_gerber_file),
            ("Convertir Excellon (drill)", self._open_excellon_file),
            ("Guardar G-code", self._save_gcode_file),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("FileItem")
            btn.setCursor(Qt.PointingHandCursor)
            # clicked emite un bool; sin esto ese False llegaba como 'path'
            btn.clicked.connect(lambda _checked=False, h=handler: h())
            layout.addWidget(btn)

        # Boton Fabricacion ordenada
        fab_btn = QPushButton("\u25b6  Fabricacion ordenada")
        fab_btn.setObjectName("FileItem")
        fab_btn.setCursor(Qt.PointingHandCursor)
        fab_btn.setToolTip(
            "Concatena las capas en orden de fabricacion:\n"
            "F.Cu -> B.Cu -> Taladros -> Otras -> Edge.Cuts"
        )
        fab_btn.setStyleSheet(
            "QPushButton { color: #4ade80; font-weight: 700; }"
            "QPushButton:hover { color: #6ee7a0; }"
        )
        fab_btn.clicked.connect(self._ordered_fabrication)
        layout.addWidget(fab_btn)

        # Boton Limpiar todo
        clear_all_btn = QPushButton("Limpiar todo")
        clear_all_btn.setObjectName("FileItem")
        clear_all_btn.setCursor(Qt.PointingHandCursor)
        clear_all_btn.setToolTip("Borra todas las capas, taladros y el editor")
        clear_all_btn.setStyleSheet(
            "QPushButton { color: #ef4444; }"
            "QPushButton:hover { color: #ff6644; }"
        )
        clear_all_btn.clicked.connect(self._clear_all_layers)
        layout.addWidget(clear_all_btn)

        layout.addStretch()
        return sidebar

    def _build_viewer(self):
        viewer = QFrame()
        viewer.setMinimumWidth(500)
        layout = QVBoxLayout(viewer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tabs = QFrame()
        tabs.setObjectName("ViewerTabs")
        tabs.setFixedHeight(36)
        tabs_layout = QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(8, 0, 8, 0)
        tabs_layout.setSpacing(0)

        self.tab_2d = QPushButton("Toolpath 2D")
        self.tab_2d.setObjectName("ViewerTab")
        self.tab_2d.setProperty("active", "true")
        self.tab_2d.setCursor(Qt.PointingHandCursor)
        self.tab_2d.clicked.connect(lambda: self._switch_view("2d"))
        tabs_layout.addWidget(self.tab_2d)

        self.tab_3d = QPushButton("Vista 3D")
        self.tab_3d.setObjectName("ViewerTab")
        self.tab_3d.setProperty("active", "false")
        self.tab_3d.setCursor(Qt.PointingHandCursor)
        self.tab_3d.clicked.connect(lambda: self._switch_view("3d"))
        tabs_layout.addWidget(self.tab_3d)

        tabs_layout.addStretch()

        reset_btn = QPushButton("Reset vista")
        reset_btn.setObjectName("ViewerTab")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_view)
        tabs_layout.addWidget(reset_btn)

        layout.addWidget(tabs)

        # Machine toolbar
        mtoolbar = QFrame()
        mtoolbar.setFixedHeight(42)
        mtoolbar.setStyleSheet(f"QFrame {{ background-color: {C_BG_2}; border-bottom: 1px solid {C_BORDER}; }}")
        mt_layout = QHBoxLayout(mtoolbar)
        mt_layout.setContentsMargins(12, 6, 12, 6)
        mt_layout.setSpacing(6)

        btn_style = f"""
            QPushButton {{
                background-color: {C_BG_1}; border: 1px solid {C_BORDER};
                color: {C_TEXT_0}; border-radius: 6px;
                padding: 5px 12px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {C_BG_3}; border-color: {C_ACCENT}; }}
            QPushButton:pressed {{ background-color: {C_ACCENT}; color: {C_BG_0}; }}
        """

        for label, cmd in [("Home", "$H"), ("Zero aqui", "G92 X0 Y0"),
                            ("Ir a 0,0", "$H"), ("Origen maquina", "G53 G0 X0 Y0")]:
            btn = QPushButton(label)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, c=cmd: self._send_cmd(c))
            mt_layout.addWidget(btn)

        mt_layout.addStretch()

        lbl_fo = QLabel("Feed:")
        lbl_fo.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        mt_layout.addWidget(lbl_fo)

        self.fo_slider = QSlider(Qt.Horizontal)
        self.fo_slider.setRange(10, 200)
        self.fo_slider.setValue(100)
        self.fo_slider.setFixedWidth(140)
        self.fo_slider.valueChanged.connect(self._on_feed_override)
        mt_layout.addWidget(self.fo_slider)

        self.fo_label = QLabel("100%")
        self.fo_label.setStyleSheet(f"color: {C_ACCENT}; font-family: monospace; font-size: 11px; font-weight: 600;")
        self.fo_label.setFixedWidth(40)
        mt_layout.addWidget(self.fo_label)

        mt_layout.addSpacing(20)

        # Toggle mostrar/ocultar G0 rapidos
        from PySide6.QtWidgets import QCheckBox
        self.chk_show_rapids = QCheckBox("Ver G0 (rapidos)")
        self.chk_show_rapids.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        self.chk_show_rapids.setChecked(True)
        self.chk_show_rapids.setToolTip(
            "Movimientos rapidos entre cortes. Se ven como lineas largas\n"
            "cruzando la placa. Normalmente desmarcado para ver solo cortes."
        )
        self.chk_show_rapids.stateChanged.connect(self._on_toggle_rapids)
        mt_layout.addWidget(self.chk_show_rapids)

        self.chk_show_cuts = QCheckBox("Ver G1 (cortes)")
        self.chk_show_cuts.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        self.chk_show_cuts.setChecked(True)
        self.chk_show_cuts.setToolTip(
            "Movimientos de corte/fresado. Deja este marcado para\n"
            "revisar el trabajo real que hara la maquina."
        )
        self.chk_show_cuts.stateChanged.connect(self._on_toggle_rapids)
        mt_layout.addWidget(self.chk_show_cuts)

        layout.addWidget(mtoolbar)

        # Stack de canvas
        self.viewer_stack = QWidget()
        stack_layout = QVBoxLayout(self.viewer_stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.setSpacing(0)

        self.canvas_2d = ViewerCanvas2D()
        self.canvas_3d = ViewerCanvas3D()
        self.canvas_3d.hide()

        # Aplicar el area de trabajo desde QSettings (con fallback)
        try:
            s = QSettings("CNC Proyect", "CNC IDE Web")
            ax = float(s.value("cfg_work_area_x", 200.0, type=float))
            ay = float(s.value("cfg_work_area_y", 200.0, type=float))
            self.canvas_2d.area_x = ax
            self.canvas_2d.area_y = ay
            self.canvas_3d.area_x = ax
            self.canvas_3d.area_y = ay
        except Exception as e:
            print(f"No se pudo leer area de trabajo: {e}")
        stack_layout.addWidget(self.canvas_2d, stretch=1)
        stack_layout.addWidget(self.canvas_3d, stretch=1)

        # Jog
        jog_frame = QFrame()
        jog_frame.setFixedHeight(46)
        jog_frame.setStyleSheet(f"QFrame {{ background-color: {C_BG_1}; border-top: 1px solid {C_BORDER}; }}")
        jog_layout = QHBoxLayout(jog_frame)
        jog_layout.setContentsMargins(12, 6, 12, 6)
        jog_layout.setSpacing(6)

        jog_btn_style = f"""
            QPushButton {{
                background-color: {C_BG_2}; border: 1px solid {C_BORDER};
                color: {C_TEXT_0}; border-radius: 6px;
                padding: 4px 10px; font-size: 12px;
                font-family: monospace; font-weight: 700; min-width: 40px;
            }}
            QPushButton:hover {{ background-color: {C_BG_3}; border-color: {C_GREEN}; }}
            QPushButton:pressed {{ background-color: {C_GREEN}; color: {C_BG_0}; }}
        """

        lbl_jog = QLabel("Jog:")
        lbl_jog.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        jog_layout.addWidget(lbl_jog)

        for label, axis, sign in [("←X", "X", -1), ("X→", "X", +1),
                                    ("←Y", "Y", -1), ("Y→", "Y", +1),
                                    ("Z↓", "Z", -1), ("Z↑", "Z", +1)]:
            btn = QPushButton(label)
            btn.setStyleSheet(jog_btn_style)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, a=axis, s=sign: self._jog(a, s))
            jog_layout.addWidget(btn)

        jog_layout.addSpacing(16)

        lbl_step = QLabel("Paso:")
        lbl_step.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        jog_layout.addWidget(lbl_step)

        self.step_combo = QComboBox()
        for val, label in [(0.1, "0.1 mm"), (0.5, "0.5 mm"), (1.0, "1 mm"),
                            (5.0, "5 mm"), (10.0, "10 mm"), (50.0, "50 mm")]:
            self.step_combo.addItem(label, val)
        self.step_combo.setCurrentIndex(2)
        self.step_combo.setFixedWidth(90)
        self.step_combo.currentIndexChanged.connect(self._on_step_changed)
        jog_layout.addWidget(self.step_combo)

        lbl_speed = QLabel("Vel:")
        lbl_speed.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        jog_layout.addWidget(lbl_speed)

        self.speed_combo = QComboBox()
        for val, label in [(200, "Lento"), (450, "Normal"), (550, "Rapido"), (5000, "Turbo")]:
            self.speed_combo.addItem(label, val)
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.setFixedWidth(90)
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        jog_layout.addWidget(self.speed_combo)

        lbl_mode = QLabel("Modo:")
        lbl_mode.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        jog_layout.addWidget(lbl_mode)

        self.mode_combo = QComboBox()
        for val, label in [("NORMAL", "Normal 1/8"),
                           ("PRECISION", "Precision 1/32"),
                           ("FAST", "Rapido 1/4")]:
            self.mode_combo.addItem(label, val)
        self.mode_combo.setCurrentIndex(0)
        self.mode_combo.setFixedWidth(130)
        self.mode_combo.setToolTip("Microstep del DRV8825. Solo se cambia en reposo.")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        jog_layout.addWidget(self.mode_combo)

        jog_layout.addStretch()
        stack_layout.addWidget(jog_frame)

        # Botones de control
        controls_frame = QFrame()
        controls_frame.setObjectName("ControlsBar")
        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(6)

        self.start_btn = QPushButton("Iniciar")
        self.start_btn.setObjectName("CtrlStart")
        self.start_btn.clicked.connect(self._on_start_job)

        self.pause_btn = QPushButton("Pausar")
        self.pause_btn.setObjectName("CtrlPause")
        self.pause_btn.clicked.connect(self._on_pause_job)

        self.stop_btn = QPushButton("Detener")
        self.stop_btn.setObjectName("CtrlStop")
        self.stop_btn.clicked.connect(self._on_stop_job)

        self.estop_btn = QPushButton("E-STOP")
        self.estop_btn.setObjectName("CtrlEstop")
        self.estop_btn.clicked.connect(self._on_estop)

        controls_layout.addWidget(self.start_btn)
        controls_layout.addWidget(self.pause_btn)
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addWidget(self.estop_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {C_BORDER};")
        controls_layout.addWidget(sep)

        self.sim_btn = QPushButton("Simular")
        self.sim_btn.setObjectName("CtrlStart")
        self.sim_btn.setCursor(Qt.PointingHandCursor)
        self.sim_btn.clicked.connect(self._on_simulate_clicked)
        controls_layout.addWidget(self.sim_btn)

        lbl_sp = QLabel("Vel sim:")
        lbl_sp.setStyleSheet(f"color: {C_TEXT_1}; font-size: 11px;")
        controls_layout.addWidget(lbl_sp)

        self.sim_speed_slider = QSlider(Qt.Horizontal)
        self.sim_speed_slider.setRange(0, 1000)
        self.sim_speed_slider.setValue(500)
        self.sim_speed_slider.setSingleStep(1)
        self.sim_speed_slider.setPageStep(10)
        self.sim_speed_slider.setFixedWidth(180)
        self.sim_speed_slider.valueChanged.connect(self._on_sim_speed_changed)
        controls_layout.addWidget(self.sim_speed_slider)

        self.sim_speed_label = QLabel("550")
        self.sim_speed_label.setStyleSheet(f"color: {C_GREEN}; font-family: monospace; font-size: 11px; font-weight: 600;")
        self.sim_speed_label.setFixedWidth(45)
        controls_layout.addWidget(self.sim_speed_label)

        ctrl_wrap = QWidget()
        ctrl_wrap_layout = QHBoxLayout(ctrl_wrap)
        ctrl_wrap_layout.setContentsMargins(0, 0, 0, 12)
        ctrl_wrap_layout.addStretch()
        ctrl_wrap_layout.addWidget(controls_frame)
        ctrl_wrap_layout.addStretch()

        stack_layout.addWidget(ctrl_wrap)
        layout.addWidget(self.viewer_stack)
        return viewer

    def _build_right_panel(self):
        right = QFrame()
        right.setObjectName("RightPanel")
        right.setMinimumWidth(240)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # Posicion
        card_pos = Card("Posicion actual")
        self.lbl_x = card_pos.add_coord_row("X", "0.00")
        self.lbl_y = card_pos.add_coord_row("Y", "0.00")
        self.lbl_z = card_pos.add_coord_row("Z", "-")
        self.lbl_z.setObjectName("CoordValueDim")
        layout.addWidget(card_pos)

        # Estadisticas G-code (nuevo)
        card_stats = Card("Estadisticas G-code")
        self.lbl_st_total    = card_stats.add_info_row("Total lineas", "-")
        self.lbl_st_g0       = card_stats.add_info_row("G0 rapid", "-")
        self.lbl_st_g1       = card_stats.add_info_row("G1 corte", "-")
        self.lbl_st_arcs     = card_stats.add_info_row("G2/G3 arcos", "-")
        self.lbl_st_mcodes   = card_stats.add_info_row("M-codes", "-")
        self.lbl_st_distance = card_stats.add_info_row("Distancia", "-")
        self.lbl_st_bounds   = card_stats.add_info_row("Bounds", "-")
        self.lbl_st_status   = card_stats.add_info_row("Boundary", "-")
        layout.addWidget(card_stats)

        # Estado
        card_state = Card("Estado")
        state_row = QHBoxLayout()
        self.state_badge = QLabel("Listo")
        self.state_badge.setObjectName("StateBadge")
        self.state_badge.setProperty("state", "idle")
        self.state_badge.setAlignment(Qt.AlignCenter)
        state_row.addWidget(self.state_badge)
        state_row.addStretch()
        self.lbl_line_count = QLabel("-")
        state_row.addWidget(self.lbl_line_count)
        card_state.body.addLayout(state_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("Progress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        card_state.body.addWidget(self.progress_bar)
        layout.addWidget(card_state)

        # Tiempo
        card_time = Card("Tiempo")
        self.lbl_elapsed = card_time.add_coord_row("Transcurrido", "00:00:00")
        self.lbl_elapsed.setObjectName("CoordValueDim")
        self.lbl_eta = card_time.add_coord_row("Restante", "-")
        self.lbl_eta.setObjectName("CoordValueDim")
        layout.addWidget(card_time)

        # Carga del H723 (telemetria DWT del firmware: lineas PERF1/PERF2)
        card_perf = Card("Carga H723")
        self.lbl_perf_load = card_perf.add_coord_row("CPU lazo", "-")
        self.lbl_perf_isr = card_perf.add_coord_row("ISR TIM6", "-")
        self.lbl_perf_loop = card_perf.add_coord_row("Lazo max", "-")
        self.lbl_perf_sect = card_perf.add_coord_row("Mayor seccion", "-")
        self.lbl_perf_enq = card_perf.add_coord_row("Planner enqueue", "-")
        self.lbl_perf_mov = card_perf.add_coord_row("MoveXY", "-")
        self.lbl_perf_prod = card_perf.add_coord_row("Productor max", "-")
        self.lbl_perf_ur = card_perf.add_coord_row("Underruns", "-")
        self.lbl_perf_free = card_perf.add_coord_row("Cola libre", "-")
        layout.addWidget(card_perf)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(right)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return right

    def _build_bottom_tabs(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                background-color: {C_BG_1};
                border: none;
                border-top: 1px solid {C_BORDER};
            }}
            QTabBar::tab {{
                background-color: {C_BG_2};
                color: {C_TEXT_2};
                padding: 6px 16px;
                border: none;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 1px;
            }}
            QTabBar::tab:selected {{
                background-color: {C_BG_1};
                color: {C_ACCENT};
                border-bottom: 2px solid {C_ACCENT};
            }}
            QTabBar::tab:hover {{
                color: {C_TEXT_0};
            }}
        """)

        # TAB: CONSOLA
        console = QFrame()
        console.setObjectName("Console")
        cl = QVBoxLayout(console)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self.console_body = QTextEdit()
        self.console_body.setObjectName("ConsoleBody")
        self.console_body.setReadOnly(True)
        cl.addWidget(self.console_body, stretch=1)

        input_row = QFrame()
        il = QHBoxLayout(input_row)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)

        self.cmd_input = QLineEdit()
        self.cmd_input.setObjectName("ConsoleInput")
        self.cmd_input.setPlaceholderText("Comando (PING, M114, G0 X10 Y10, $H...)")
        self.cmd_input.returnPressed.connect(self._on_send_clicked)
        il.addWidget(self.cmd_input, stretch=1)

        send_btn = QPushButton("Enviar")
        send_btn.setObjectName("ConsoleSendBtn")
        send_btn.clicked.connect(self._on_send_clicked)
        il.addWidget(send_btn)

        cl.addWidget(input_row)

        # Botón limpiar consola
        console_header = QFrame()
        console_header.setFixedHeight(30)
        console_header.setStyleSheet(
            f"background-color: {C_BG_2}; "
            f"border-bottom: 1px solid {C_BORDER};"
        )
        chl = QHBoxLayout(console_header)
        chl.setContentsMargins(8, 4, 8, 4)
        chl.setSpacing(6)
        chl.addStretch()

        btn_style = (
            f"QPushButton {{ "
            f"background-color: {C_BG_3}; border: 1px solid {C_BORDER}; "
            f"color: {C_TEXT_1}; border-radius: 4px; "
            f"padding: 3px 12px; font-size: 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ "
            f"background-color: {C_BG_2}; border-color: {C_ACCENT}; "
            f"color: {C_TEXT_0}; }}"
            f"QPushButton:pressed {{ "
            f"background-color: {C_ACCENT}; color: {C_BG_0}; }}"
        )

        select_btn = QPushButton("Seleccionar todo")
        select_btn.setStyleSheet(btn_style)
        select_btn.setCursor(Qt.PointingHandCursor)
        select_btn.setToolTip("Selecciona todo el texto de la consola")
        select_btn.clicked.connect(self.console_body.selectAll)
        chl.addWidget(select_btn)

        self.copy_btn = QPushButton("Copiar")
        self.copy_btn.setStyleSheet(btn_style)
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setToolTip("Copia toda la consola al portapapeles")
        self.copy_btn.clicked.connect(self._copy_console)
        chl.addWidget(self.copy_btn)

        clear_btn = QPushButton("Limpiar")
        clear_btn.setStyleSheet(btn_style)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setToolTip("Borra el contenido de la consola")
        clear_btn.clicked.connect(lambda: self.console_body.clear())
        chl.addWidget(clear_btn)

        cl.addWidget(console_header)

        tabs.addTab(console, "CONSOLA")

        # TAB: EDITOR
        self.editor = GCodeEditor()
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {C_BG_0};
                color: {C_TEXT_0};
                border: none;
                padding: 4px;
                selection-background-color: #2a3a5a;
            }}
        """)
        tabs.addTab(self.editor, "EDITOR")

        self.bottom_tabs = tabs
        self._log_system("CNC IDE v0.7 - Fase 2: editor + boundary + preview")
        return tabs

    # ============================================================
    # JOG
    # ============================================================
    def _add_file_to_list_unique(self, name, path, tipo, layer_key=None):
        """Anade un archivo a la lista solo si no existe ya. layer_key = capa que genera."""
        for i in range(self.file_list.count()):
            if self.file_list.item(i).text() == name:
                return
        item = _make_file_item(path, name, tipo)
        item.setData(258, layer_key)
        self.file_list.addItem(item)

    def remove_recent_file(self, index):
        """Quita un archivo de la lista y borra solo su capa del visor."""
        if not (0 <= index < self.file_list.count()):
            return False
        item = self.file_list.item(index)
        key = item.data(258)
        name = item.text()
        self.file_list.takeItem(index)
        if key and key != "GCode":
            self.layer_gcodes.pop(key, None)
            if hasattr(self, "_fabrication_layers"):
                self._fabrication_layers.pop(key, None)
            self.canvas_2d.clear_layer(key)
        self._on_toggle_rapids()
        self._log_system(f"Archivo quitado: {name}")
        return True

    def _show_only_layers(self, only):
        """Muestra solo la capa indicada, respetando el filtro
        de rapidos (Ver G0)."""
        # Actualizar checkbox de capas
        for name, chk in self.layer_checkboxes.items():
            chk.blockSignals(True)
            if only is None:
                chk.setChecked(True)
            else:
                chk.setChecked(name == only)
            chk.blockSignals(False)

        # Re-aplicar el filtro rapido/corte. Esto repuebla cada
        # capa con sus segmentos filtrados (o todos si no hay
        # fabricacion cargada).
        self._on_toggle_rapids()

        # Aplicar visibilidad (algunas capas pueden haberse
        # repoblado y quedado visibles)
        for name, chk in self.layer_checkboxes.items():
            self.canvas_2d.set_layer_visible(name, chk.isChecked())

        # Contar rapidos de la capa activa para informar al usuario
        fab = getattr(self, '_fabrication_layers', {})
        if fab and only is not None:
            segs = fab.get(only, [])
            n_r = sum(1 for s in segs if len(s) > 4 and s[4] == 'rapid')
            n_c = len(segs) - n_r
            self._log_system(f"Vista: {only}  ({n_c} cortes, {n_r} rapidos)")

        self.canvas_2d._invalidate_cache()
        self.canvas_2d.update()
        if hasattr(self, 'canvas_3d'):
            self.canvas_3d.update()

    def _copy_console(self):
        """Copia el texto de la consola al portapapeles con feedback."""
        content = self.console_body.toPlainText()
        if not content.strip():
            self.copy_btn.setText("Vacio")
        else:
            QApplication.clipboard().setText(content)
            n_lines = content.count('\n') + 1
            self.copy_btn.setText(f"Copiadas {n_lines}")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("Copiar"))

    def _clear_all_layers(self):
        """Borra todas las capas, taladros y el editor."""
        self.canvas_2d.clear_all_layers()
        self.canvas_3d.set_toolpath_segments([])
        self.canvas_3d.set_holes([])
        self.canvas_3d.update()
        self._all_segments = []
        self._current_layer = "GCode"
        self.gcode_lines = []
        self.layer_gcodes = {}
        self._fabrication_layers = {}
        self.editor.setPlainText("")
        self.file_list.clear()
        self._log_system("Todo limpiado (capas, taladros, editor, archivos recientes)")

    def _ordered_fabrication(self):
        """Concatena los G-code en orden de fabricacion."""
        order = [
            "F.Cu",
            "B.Cu",
            "PTH",
            "NPTH",
            "F.SilkS",
            "B.SilkS",
            "F.Mask",
            "B.Mask",
            "Edge.Cuts",
        ]

        extra = [k for k in self.layer_gcodes.keys()
                 if k not in order and k != "GCode"]

        combined = []
        combined.append("; ========================================")
        combined.append(";  FABRICACION ORDENADA - CNC IDE")
        combined.append("; ========================================")
        combined.append(f"; Capas cargadas: {len(self.layer_gcodes)}")
        combined.append("")

        included = 0
        for layer in order[:-1]:
            if layer in self.layer_gcodes:
                combined.append(f"; ---- {layer} ----")
                combined.extend(self.layer_gcodes[layer])
                combined.append("M0  ; pausa - cambiar herramienta si aplica")
                combined.append("")
                included += 1

        for layer in extra:
            combined.append(f"; ---- {layer} (extra) ----")
            combined.extend(self.layer_gcodes[layer])
            combined.append("M0")
            combined.append("")
            included += 1

        if "Edge.Cuts" in self.layer_gcodes:
            combined.append("; ---- Edge.Cuts (corte final) ----")
            combined.extend(self.layer_gcodes["Edge.Cuts"])
            combined.append("")
            included += 1

        if included == 0:
            self._log_system("No hay capas cargadas para fabricar", level="error")
            return

        self.editor.load_lines(combined)
        self.gcode_lines = combined

        # Cargar el toolpath combinado MANTENIENDO colores por capa
        try:
            from gcode_editor import parse_gcode_toolpath

            # Colores del visor 2D (deben coincidir con la definicion
            # inicial en ViewerCanvas2D.layers)
            layer_colors = {
                "F.Cu":      "#cc3333",
                "B.Cu":      "#3366cc",
                "Edge.Cuts": "#ffcc00",
                "F.SilkS":   "#ffffff",
                "B.SilkS":   "#dddddd",
                "F.Mask":    "#a855f7",
                "B.Mask":    "#7c3aed",
                "PTH":       "#00ffff",
                "NPTH":      "#ff00ff",
            }

            all_segs_2d = []   # para GCode (compatibilidad)
            all_segs_3d = []   # con color embebido
            self._fabrication_layers = {}

            # Limpiar capas 2D antes de repartir
            for _ln in layer_colors.keys():
                if _ln in self.canvas_2d.layers:
                    self.canvas_2d.layers[_ln]["segments"] = []

            for _layer in order:
                if _layer not in self.layer_gcodes:
                    continue
                _segs = parse_gcode_toolpath(self.layer_gcodes[_layer])
                self._fabrication_layers[_layer] = list(_segs)
                _color = layer_colors.get(_layer, "#ff8c42")
                # 2D: por capa
                if _layer in self.canvas_2d.layers:
                    self.canvas_2d.layers[_layer]["segments"] = list(_segs)
                all_segs_2d.extend(_segs)
                # 3D: con color embebido en un 7º campo
                for s in _segs:
                    all_segs_3d.append(tuple(list(s) + [_color]))

            for _layer in extra:
                _segs = parse_gcode_toolpath(self.layer_gcodes[_layer])
                self._fabrication_layers[_layer] = list(_segs)
                _color = "#94a3b8"
                all_segs_2d.extend(_segs)
                for s in _segs:
                    all_segs_3d.append(tuple(list(s) + [_color]))

            self._all_segments = all_segs_2d
            self._current_layer = "GCode"

            # Marcar checkboxes sin disparar signals
            for _name, _chk in self.layer_checkboxes.items():
                _chk.blockSignals(True)
                _chk.setChecked(True)
                _chk.blockSignals(False)

            self.chk_show_rapids.blockSignals(True)
            self.chk_show_cuts.blockSignals(True)
            self.chk_show_rapids.setChecked(True)
            self.chk_show_cuts.setChecked(True)
            self.chk_show_rapids.blockSignals(False)
            self.chk_show_cuts.blockSignals(False)

            # Aplicar reparto por capas sin perder colores
            self._on_toggle_rapids()

            self.canvas_2d.reset_view()

            n_rapid = sum(1 for s in all_segs_2d if len(s) > 4 and s[4] == "rapid")
            n_cut = len(all_segs_2d) - n_rapid
            self._log_system(f"Toolpath unificado: {n_cut} cortes + {n_rapid} rapidos")
            self._log_system("Vista configurada: capas con sus colores. 'Ver G0' oculto.")
        except Exception as e:
            print(f"[FAB] Error parseando combinado: {e}")

        self._log_system(f"Fabricacion ordenada: {included} capas, "
                         f"{len(combined)} lineas en el editor")
        self._log_system("Pulsa Simular para previsualizar, o Iniciar si estas conectado")

    def _on_step_changed(self, idx):
        self.jog_step = self.step_combo.currentData()

    def _on_speed_changed(self, idx):
        self.jog_speed = self.speed_combo.currentData()

    # ---------- Modo de microstep (DRV8825) ----------
    # Comando propio: G1000 M1 MODO=<NOMBRE>. Consulta: G1000 M1 MODO?
    def _on_mode_changed(self, idx):
        if getattr(self, "_mode_syncing", False):
            return
        if not getattr(self, "connected", False):
            return
        mode = self.mode_combo.currentData()
        if self.job_running:
            self._log_system("No se puede cambiar el modo durante un trabajo", level="error")
            self._set_mode_combo(getattr(self, "_mode_applied", "NORMAL"))
            return
        self._mode_pending = mode
        self._send_cmd(f"G1000 M1 MODO={mode}")
        self._log_system(f"Pidiendo modo {mode}...")

    def _set_mode_combo(self, mode):
        i = self.mode_combo.findData(mode)
        if i < 0:
            return
        self._mode_syncing = True
        self.mode_combo.setCurrentIndex(i)
        self._mode_syncing = False

    def _jog(self, axis, sign):
        if not self.connected:
            self._log_system("No conectado", level="error")
            return
        if self.job_running:
            self._log_system("Trabajo en curso, no se puede hacer jog", level="error")
            return

        dist = self.jog_step * sign
        f = int(self.jog_speed)

        self._send_cmd("G91")
        if axis == "X":
            self._send_cmd(f"G1 X{dist} F{f}")
        elif axis == "Y":
            self._send_cmd(f"G1 Y{dist} F{f}")
        elif axis == "Z":
            self._send_cmd(f"G1 Z{dist} F{f}")
        self._send_cmd("G90")

    # ============================================================
    # FEED OVERRIDE
    # ============================================================
    def _on_feed_override(self, value):
        self.fo_label.setText(f"{value}%")
        self.feed_override = value
        if self.connected and self.job_running:
            self._job_ack_events.append(None)  # ACK de control, no de una línea G-code.
            self._send_cmd(f"M220 S{value}")

    # ============================================================
    # JOB CONTROL
    # ============================================================
    def _hold_sleep_inhibit(self):
        """Impide que la PC se duerma mientras hay un trabajo en curso."""
        self._release_sleep_inhibit()
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
                self._sleep_inhibit = "win32"
            except Exception as e:
                self._sleep_inhibit = None
                self._log_system("No se pudo bloquear el suspender (win32): %s" % e, level="error")
        else:
            try:
                self._sleep_inhibit = subprocess.Popen(
                    ["systemd-inhibit", "--what=sleep:idle", "--who=CNC IDE",
                     "--why=Trabajo CNC en curso", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                self._sleep_inhibit = None
                self._log_system("No se pudo bloquear el suspender: %s" % e, level="error")

    def _release_sleep_inhibit(self):
        proc = getattr(self, "_sleep_inhibit", None)
        if proc == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
        elif proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._sleep_inhibit = None

    def _on_start_job(self):
        if not getattr(self, "_pos_valid", False):
            self._log_system("No se puede iniciar: haz $H para fijar el origen primero", level="error")
            return
        # Re-leer el editor por si el usuario editó
        editor_lines = self.editor.get_lines()
        if editor_lines and any(ln.strip() for ln in editor_lines):
            self.gcode_lines = [ln for ln in editor_lines if ln.strip()]
        else:
            editor_lines = []

        if not self.gcode_lines:
            self._log_system("No hay G-code cargado", level="error")
            return
        if not self.connected:
            self._log_system("No conectado. Usa Simular para previsualizar sin la maquina.", level="error")
            return
        if self.job_running:
            self._log_system("Ya hay un trabajo en curso", level="error")
            return

        # Boundary check antes de empezar
        stats = parse_gcode_stats(self.gcode_lines)
        if stats["error_lines"]:
            r = QMessageBox.warning(
                self, "Fuera de limites",
                f"{len(stats['error_lines'])} lineas se salen del area de trabajo.\n"
                "Continuar de todos modos?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if r != QMessageBox.Yes:
                return

        self.job_lines = list(self.gcode_lines)
        self._job_ack_timeouts_ms = self._estimate_job_ack_timeouts(self.job_lines)
        self.job_current = 0
        self.job_next = 0
        self._cnc_busy = True
        self._job_seen_busy = False
        self._job_pending = collections.deque()
        self._job_ack_events = collections.deque([None])
        self.job_total = len(self.job_lines)
        self.job_running = True
        # Aplicar el override antes del G-code; su OK ocupa el primer lugar
        # de la secuencia FIFO, pero no confirma una línea del programa.
        JOB_RECORDER.start(self.job_lines)
        self._send_cmd(f"M220 S{self.feed_override}")
        self._hold_sleep_inhibit()
        self.job_paused = False
        self._job_start_time = __import__("time").time()
        self._job_est_total_s = estimate_job_seconds(self.job_lines)

        self.progress_bar.setValue(0)
        self.lbl_line_count.setText(f"0/{self.job_total}")
        self._set_state("run")
        self.pause_btn.setText("Pausar")
        self.time_timer.start()
        self.editor.set_job_line(0)
        self._log_system(f"Trabajo iniciado ({self.job_total} lineas)")

        self._send_next_job_line()

    @staticmethod
    def _is_noop_job_line(line):
        t = line.strip()
        return (not t) or t.startswith(";") or t.startswith("(")

    def _send_next_job_line(self):
        if not self.job_running or self.job_paused:
            return
        pend = self._job_pending
        # Saltar lineas vacias o comentarios (no llevan OK).
        while self.job_next < self.job_total and self._is_noop_job_line(self.job_lines[self.job_next]):
            self.job_next += 1
        # Llenar la ventana: hasta JOB_WINDOW lineas sin OK.
        while len(pend) < JOB_WINDOW and self.job_next < self.job_total:
            idx = self.job_next
            line = self.job_lines[idx]
            self.job_next += 1
            pend.append(idx)
            self._job_ack_events.append(idx)
            self.request_send.emit(line)
            if len(pend) == 1:
                self._arm_ack_watchdog(line, idx)
            while self.job_next < self.job_total and self._is_noop_job_line(self.job_lines[self.job_next]):
                self.job_next += 1
        if pend:
            self._last_sent_was_job = True
            self.editor.set_job_line(pend[0])
            self.editor.scroll_to_line(pend[0])
        elif self.job_next >= self.job_total:
            self._job_wait_motion = True
            if getattr(self, "_job_seen_busy", False) and not getattr(self, "_cnc_busy", True):
                self._job_wait_motion = False
                self._job_finished()

    def _estimate_job_ack_timeouts(self, lines):
        """Da más margen a movimientos lentos que pueden tardar en confirmar."""
        try:
            x = float(self.lbl_x.text().replace(",", "."))
            y = float(self.lbl_y.text().replace(",", "."))
        except (TypeError, ValueError):
            x = y = 0.0
        absolute = True
        unit_scale = 1.0
        feed_mm_min = 200.0
        feed_override = max(10.0, float(self.feed_override)) / 100.0
        last_motion = 1
        timeouts = [15000] * len(lines)
        motion_seconds = [0.0] * len(lines)

        for index, source in enumerate(lines):
            line = re.sub(r"\([^)]*\)", "", str(source).split(";", 1)[0]).upper()
            words = re.findall(r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", line)
            values = {}
            for letter, value in words:
                try:
                    values[letter] = float(value)
                except ValueError:
                    continue
            gcodes = [int(float(value)) for value in re.findall(r"\bG\s*(\d+(?:\.\d+)?)", line)]
            if 20 in gcodes:
                unit_scale = 25.4
            if 21 in gcodes:
                unit_scale = 1.0
            if 90 in gcodes:
                absolute = True
            if 91 in gcodes:
                absolute = False
            if "F" in values and values["F"] > 0:
                feed_mm_min = values["F"] * unit_scale
            if 0 in gcodes:
                last_motion = 0
            elif 1 in gcodes:
                last_motion = 1

            if last_motion not in (0, 1) or not ({"X", "Y"} & values.keys()):
                continue
            nx = values.get("X", x) * unit_scale if absolute else x + values.get("X", 0.0) * unit_scale
            ny = values.get("Y", y) * unit_scale if absolute else y + values.get("Y", 0.0) * unit_scale
            distance = math.hypot(nx - x, ny - y)
            x, y = nx, ny
            if distance <= 0.001:
                continue
            if last_motion == 0:
                # 5 mm/s es el límite de rápidos del modo de precisión.
                seconds = distance / 5.0
            else:
                effective_feed = max(1.0, feed_mm_min * feed_override)
                seconds = distance * 60.0 / effective_feed
            motion_seconds[index] = seconds
            timeouts[index] = min(3_600_000, max(15_000, int((seconds * 1.8 + 10.0) * 1000)))

        # M2/M30 detienen el parser del STM32 mientras vacían el planner.
        # El timeout contempla los últimos bloques que caben en la cola.
        for index, source in enumerate(lines):
            if source.strip().upper().split(";", 1)[0].strip() in {"M2", "M30", "M400"}:
                pending_seconds = sum(motion_seconds[max(0, index - JOB_WINDOW):index])
                timeouts[index] = min(3_600_000, max(120_000, int((pending_seconds * 1.8 + 15.0) * 1000)))
            elif source.strip().upper().startswith("G4"):
                timeouts[index] = 120_000

        # Si el firmware está drenando el planner, una línea nueva puede no
        # recibir OK hasta que terminen movimientos anteriores. El watchdog
        # de esa línea debe contemplar la ventana completa pendiente, no solo
        # su longitud local (especialmente con feed override bajo).
        for index in range(len(lines)):
            queue_start = max(0, index - JOB_WINDOW + 1)
            queued_seconds = sum(motion_seconds[queue_start:index + 1])
            queue_timeout = min(3_600_000, int((queued_seconds * 1.8 + 15.0) * 1000))
            timeouts[index] = max(timeouts[index], queue_timeout)
        return timeouts

    def _arm_ack_watchdog(self, line, line_index=None):
        """Si el firmware no responde OK en un tiempo razonable, detener
        el job con un mensaje claro en vez de quedarse esperando para siempre."""
        if not hasattr(self, "_ack_timer"):
            self._ack_timer = QTimer(self)
            self._ack_timer.setSingleShot(True)
            self._ack_timer.timeout.connect(self._on_ack_timeout)
        if line_index is None:
            line_index = self.job_current
        timeouts = getattr(self, "_job_ack_timeouts_ms", [])
        timeout_ms = timeouts[line_index] if 0 <= line_index < len(timeouts) else 15000
        self._ack_timeout_current_ms = timeout_ms
        self._ack_timer.start(timeout_ms)

    def _on_ack_timeout(self):
        if not self.job_running or self.job_paused or not self._last_sent_was_job:
            return
        if self.job_current < self.job_total:
            text = self.job_lines[self.job_current].strip()
        else:
            text = "?"
        self._log_system(
            f"Sin OK del firmware tras {self._ack_timeout_current_ms / 1000:.0f} s "
            f"en la linea {self.job_current + 1}/{self.job_total}: {text}",
            level="error",
        )
        self._on_stop_job()

    def _on_job_line_ack(self):
        pend = self._job_pending
        if not pend:
            return
        idx = pend.popleft()
        self.job_current = idx + 1
        while (self.job_current < self.job_next and self.job_current < self.job_total
               and self._is_noop_job_line(self.job_lines[self.job_current])):
            self.job_current += 1
        self._update_progress()
        if pend:
            # El watchdog sigue a la linea mas antigua que aun espera OK.
            self._arm_ack_watchdog(self.job_lines[pend[0]], pend[0])
        else:
            self._last_sent_was_job = False
            if hasattr(self, "_ack_timer"):
                self._ack_timer.stop()
        if not self.job_running or self.job_paused:
            return
        self._send_next_job_line()

    def _update_progress(self):
        if self.job_total <= 0:
            return
        pct = int((self.job_current / self.job_total) * 100)
        self.progress_bar.setValue(pct)
        self.lbl_line_count.setText(f"{self.job_current}/{self.job_total}")

    def _on_pause_job(self):
        if not self.job_running:
            return
        self.job_paused = not self.job_paused
        if self.job_paused:
            if hasattr(self, "_ack_timer"):
                self._ack_timer.stop()
            self.pause_btn.setText("Reanudar")
            self._set_state("paused")
            # Decirle al firmware que pare el movimiento en curso.
            if self.connected:
                self.request_send.emit("PAUSE")
            self._log_system("Trabajo pausado")
        else:
            self.pause_btn.setText("Pausar")
            self._set_state("run")
            # Decirle al firmware que reanude el movimiento pausado.
            if self.connected:
                self.request_send.emit("RESUME")
            self._log_system("Trabajo reanudado")
            if self._job_pending:
                self._arm_ack_watchdog(self.job_lines[self._job_pending[0]], self._job_pending[0])
            self._send_next_job_line()

    def _on_stop_job(self):
        # Si ya no hay job, no hacer nada (evita M112 spam
        # si el usuario pulsa el boton varias veces).
        if not self.job_running:
            return
        if hasattr(self, "_ack_timer"):
            self._ack_timer.stop()
        self._job_pending = collections.deque()
        self._job_ack_events = collections.deque()
        self._job_wait_motion = False
        self.job_running = False
        self._release_sleep_inhibit()
        self.job_paused = False
        self._last_sent_was_job = False
        self.pause_btn.setText("Pausar")
        self.progress_bar.setValue(0)
        self.lbl_line_count.setText("-")
        self._set_state("idle")
        self.time_timer.stop()
        self.editor.set_job_line(-1)
        self._log_system("Trabajo detenido")
        JOB_RECORDER.finish("detenido por usuario")
        if self.connected:
            self._send_cmd("M112")

    def _job_finished(self):
        if hasattr(self, "_ack_timer"):
            self._ack_timer.stop()
        self._job_pending = collections.deque()
        self._job_ack_events = collections.deque()
        self._job_wait_motion = False
        self.job_running = False
        self._release_sleep_inhibit()
        self.job_paused = False
        self._last_sent_was_job = False
        self.pause_btn.setText("Pausar")
        self.progress_bar.setValue(100)
        self.lbl_line_count.setText(f"{self.job_total}/{self.job_total}")
        self._set_state("idle")
        self.time_timer.stop()
        self.editor.set_job_line(-1)
        self._log_system(f"Trabajo completado ({self.job_total} lineas)")
        JOB_RECORDER.finish("completado")

    def _update_job_clock(self):
        if not self.job_running or self._job_start_time == 0:
            return
        elapsed = __import__("time").time() - self._job_start_time
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        self.lbl_elapsed.setText(f"{h:02d}:{m:02d}:{s:02d}")
        if getattr(self, "_job_est_total_s", 0) > 0:
            remaining = max(0.0, self._job_est_total_s - elapsed)
            rh = int(remaining // 3600)
            rm = int((remaining % 3600) // 60)
            rs = int(remaining % 60)
            self.lbl_eta.setText(f"{rh:02d}:{rm:02d}:{rs:02d}")

    # ============================================================
    # RESTO
    # ============================================================
    def _toggle_theme(self):
        """Alterna entre modo claro y oscuro."""
        current = self.settings.value("theme", "dark", type=str)
        new = "light" if current == "dark" else "dark"
        self.settings.setValue("theme", new)
        self._apply_theme(new)
        self._log_system(f"Tema cambiado a modo {'claro' if new == 'light' else 'oscuro'}")

    def _apply_theme(self, theme):
        """Aplica el tema 'dark' o 'light' a la app y a todos los canvas."""
        import theme as _theme_mod

        is_light = (theme == "light")

        # 1. Colores de módulo (canvas 2D, canvas 3D, textos)
        if is_light:
            import main_window as _mw
            _mw.C_BG_0   = "#f8f4ec"    # fondo papel
            _mw.C_BG_1   = "#fdfbf7"    # paneles claros
            _mw.C_BG_2   = "#f0eadd"    # secundario
            _mw.C_BG_3   = "#e8dfcc"    # hover/borde
            _mw.C_BORDER = "#e2d9c8"
            _mw.C_TEXT_0 = "#2a2620"    # texto principal
            _mw.C_TEXT_1 = "#5a544c"
            _mw.C_TEXT_2 = "#8a8276"
            _mw.C_AXIS   = "#7c3aed"
            _mw.C_RAPID  = "#b45309"
            _mw.C_CUT    = "#d9691f"
            _mw.C_ARC    = "#7c3aed"
        else:
            import main_window as _mw
            _mw.C_BG_0   = "#0d0e12"
            _mw.C_BG_1   = "#16171c"
            _mw.C_BG_2   = "#1c1e24"
            _mw.C_BG_3   = "#23252c"
            _mw.C_BORDER = "#2a2d36"
            _mw.C_TEXT_0 = "#e8e8eb"
            _mw.C_TEXT_1 = "#9aa0ac"
            _mw.C_TEXT_2 = "#5d6270"
            _mw.C_AXIS   = "#a855f7"
            _mw.C_RAPID  = "#fbbf24"
            _mw.C_CUT    = "#ff8c42"
            _mw.C_ARC    = "#a855f7"

        # 2. Stylesheet global
        from PySide6.QtWidgets import QApplication
        qss = _theme_mod.STYLESHEET_LIGHT if is_light else _theme_mod.STYLESHEET_DARK
        QApplication.instance().setStyleSheet(qss)

        # 3. Colores de las capas del canvas 2D
        if is_light:
            layer_map = {
                "F.Cu":      "#b91c1c",
                "B.Cu":      "#1d4ed8",
                "Edge.Cuts": "#a16207",
                "F.SilkS":   "#5a544c",
                "F.Mask":    "#7c3aed",
                "GCode":     "#d9691f",
            }
            hole_map = {
                "PTH":  "#0e7490",
                "NPTH": "#a21caf",
            }
        else:
            layer_map = {
                "F.Cu":      "#cc3333",
                "B.Cu":      "#3366cc",
                "Edge.Cuts": "#ffcc00",
                "F.SilkS":   "#ffffff",
                "F.Mask":    "#a855f7",
                "GCode":     "#ff8c42",
            }
            hole_map = {
                "PTH":  "#00ffff",
                "NPTH": "#ff00ff",
            }

        for name, color in layer_map.items():
            if name in self.canvas_2d.layers:
                self.canvas_2d.layers[name]["color"] = color
        for name, color in hole_map.items():
            if name in self.canvas_2d.hole_layers:
                self.canvas_2d.hole_layers[name]["color"] = color

        # 4. Editor G-code
        import gcode_editor as _ge
        if is_light:
            _ge.C_EDITOR_BG      = "#fafafa"
            _ge.C_EDITOR_TEXT    = "#1a1a1e"
            _ge.C_EDITOR_LINENUM = "#b8b8be"
            _ge.C_EDITOR_LINECUR = "#ececef"
            _ge.C_EDITOR_JOBLINE = "#ffe4d0"
            _ge.C_EDITOR_COMMENT = "#909098"
        else:
            _ge.C_EDITOR_BG      = "#0d0e12"
            _ge.C_EDITOR_TEXT    = "#e8e8eb"
            _ge.C_EDITOR_LINENUM = "#3a3f4a"
            _ge.C_EDITOR_LINECUR = "#1a1c22"
            _ge.C_EDITOR_JOBLINE = "#3a1818"
            _ge.C_EDITOR_COMMENT = "#5d6270"

        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {_ge.C_EDITOR_BG};
                color: {_ge.C_EDITOR_TEXT};
                border: none;
                padding: 4px;
                selection-background-color: {'#d4d4f0' if is_light else '#2a3a5a'};
            }}
        """)

        # 5. Repintar todo
        self.canvas_2d._invalidate_cache()
        self.canvas_2d.update()
        self.canvas_3d._cache_key = None
        self.canvas_3d.update()
        self.editor.update()
        self._refresh_style(self.logo_mark)
        self.console_body.update()

        # 6. Actualizar texto del boton
        if hasattr(self, "theme_btn"):
            self.theme_btn.setText("Modo claro" if is_light else "Modo oscuro")

    def _reset_view(self):
        self.canvas_2d.reset_view()
        self._log_system("Vista restablecida")

    def _toggle_polling(self):
        self.polling_enabled = not self.polling_enabled
        if self.polling_enabled:
            self.poll_btn.setText("Polling: ON")
            if self.connected:
                self.poll_timer.start()
            self._log_system("Polling ON")
        else:
            self.poll_btn.setText("Polling: OFF")
            self.poll_timer.stop()
            self._log_system("Polling OFF")

    def _switch_view(self, which):
        if which == "2d":
            self.canvas_2d.show(); self.canvas_3d.hide()
            self.tab_2d.setProperty("active", "true")
            self.tab_3d.setProperty("active", "false")
        else:
            self.canvas_2d.hide(); self.canvas_3d.show()
            self.tab_2d.setProperty("active", "false")
            self.tab_3d.setProperty("active", "true")
            self.canvas_3d.update()
        self._refresh_style(self.tab_2d)
        self._refresh_style(self.tab_3d)

    def _on_connect_clicked(self):
        if self.connected:
            self.request_disconnect.emit()
            return
        dlg = ConnectDialog(self)
        if dlg.exec() == ConnectDialog.Accepted:
            port, baud = dlg.get_values()
            self._log_system(f"Conectando a {port} @ {baud}...")
            self.request_connect.emit(port, baud)

    def _on_settings_clicked(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == SettingsDialog.Accepted:
            s = dlg.get_settings()
            self.poll_interval = s["poll_interval"]
            self.poll_timer.setInterval(self.poll_interval)

            # Refrescar el area de trabajo en los canvas
            try:
                s2 = QSettings("CNC Proyect", "CNC IDE Web")
                ax = float(s2.value("cfg_work_area_x", 200.0, type=float))
                ay = float(s2.value("cfg_work_area_y", 200.0, type=float))
                self.canvas_2d.area_x = ax
                self.canvas_2d.area_y = ay
                self.canvas_3d.area_x = ax
                self.canvas_3d.area_y = ay
                self.canvas_2d.update()
                self.canvas_3d.update()
                self._log_system(f"Ajustes guardados - Area {ax:.0f}x{ay:.0f} mm")
            except Exception as e:
                self._log_system(f"Ajustes guardados (error area: {e})", level="error")

    def _on_send_clicked(self):
        text = self.cmd_input.text().strip()
        if not text:
            return
        self.cmd_input.clear()
        self._is_auto_m114 = False
        self._send_cmd(text)

    def _send_cmd(self, cmd):
        if not self.connected:
            self._log_system("No conectado", level="error")
            return
        # M112 desde cualquier origen (consola o E-STOP) tambien debe parar
        # la alimentacion del job. Antes solo llegaba al firmware y la GUI
        # seguia enviando lineas, que el firmware volvia a ejecutar.
        if cmd.strip().upper() == "M112" and self.job_running:
            self._on_stop_job()   # _on_stop_job envia el M112
            return
        self.request_send.emit(cmd)

    def _on_estop(self):
        if self.job_running:
            self._on_stop_job()   # para el job y envia M112
        elif self.connected:
            self.request_send.emit("M112")
        self._log_system("E-STOP pulsado", level="error")
        self._set_state("alarm")

    def _open_excellon_file(self):
        """Taladrado: misma ventana que fresado PCB (pluma, posicion, area, vista)."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir Excellon (taladros)", "",
            "Excellon (*.drl *.xln);;Todos (*)"
        )
        if not path:
            return

        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
            QComboBox, QDoubleSpinBox, QCheckBox, QDialogButtonBox,
            QPushButton, QLabel, QWidget, QFrame,
        )
        from PySide6.QtCore import QSettings
        from pcb_preview import ExcellonPreview
        from excellon_parser import ExcellonParser, transform_holes

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            drl_text = f.read()
        try:
            holes_raw = ExcellonParser().parse(drl_text)
        except Exception as e:
            QMessageBox.critical(self, "Error Excellon", str(e))
            return
        if not holes_raw:
            QMessageBox.warning(self, "Excellon", "No se encontraron taladros en el archivo.")
            return

        cfg = QSettings("CNC Project", "gui_v_taladro")
        board = _board_name(path)
        board_refs = self.__dict__.get("_board_refs", {})
        g_rot = int(self.__dict__.get("_last_gerber_rotation", 0) or 0)
        g_mir = tuple(self.__dict__.get("_last_gerber_mirror", (False, False)))
        has_ref = board in board_refs

        answers = self.__dict__.setdefault("_align_answers", {})
        ask_align = False
        if has_ref:
            if board not in answers:
                resp = QMessageBox.question(
                    self, "Alinear taladros",
                    f"Estos taladros parecen ser de la placa '{board}'.\n\n"
                    "¿Alinearlos con las capas ya cargadas?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                answers[board] = (resp == QMessageBox.Yes)
            ask_align = answers[board]

        dlg = GlassDialog("Parametros de fresado de PCB", self)
        dlg.setMinimumWidth(1000)
        dlg.resize(1040, 660)
        dlg.setStyleSheet(
            "QLabel { color: #ffffff; background: transparent; font-size: 12px; }"
            "QGroupBox { color: #ffffff; font-weight: 600; border: 1px solid rgba(255,255,255,0.12);"
            " border-radius: 12px; margin-top: 14px; padding: 14px 10px 10px 10px;"
            " background: rgba(255,255,255,0.03); }"
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #8e8e93; }"
            "QDoubleSpinBox, QComboBox { background-color: rgba(255,255,255,0.08);"
            " border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 4px 8px;"
            " color: #ffffff; min-height: 22px; }"
            "QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #0A84FF; }"
            "QComboBox QAbstractItemView { background: #1c1c1e; color: #ffffff; selection-background-color: #0A84FF; }"
            "QCheckBox { color: #ffffff; background: transparent; }"
            "QPushButton { border-radius: 8px; padding: 7px 20px; font-size: 12px; border: none; }"
            "QPushButton#DlgSecondary { background-color: rgba(255,255,255,0.10); color: #ffffff; }"
            "QPushButton#DlgSecondary:hover { background-color: rgba(255,255,255,0.16); }"
        )
        root = dlg.content_layout
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(16)
        preview = ExcellonPreview(drl_text)
        body.addWidget(preview, 3)

        right = QVBoxLayout()
        right.setSpacing(10)

        def card(title):
            g = QGroupBox(title)
            f = QFormLayout(g)
            f.setSpacing(8)
            right.addWidget(g)
            return f

        def dspin(lo, hi, step, dec, val, suffix=" mm"):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(step)
            w.setDecimals(dec)
            w.setValue(val)
            w.setSuffix(suffix)
            return w

        # Herramienta
        f_tool = card("Herramienta")
        sp_dia = dspin(0.10, 6.00, 0.05, 2, float(cfg.value("broca", 0.80)))
        cb_type = QComboBox()
        cb_type.addItems(["PTH", "NPTH"])
        cb_type.setCurrentIndex(0 if str(cfg.value("tipo", "PTH")) == "PTH" else 1)
        f_tool.addRow("Diametro (broca):", sp_dia)
        f_tool.addRow("Tipo de hoja:", cb_type)

        # Servo: mismos campos que fresado. Ojo: el taladro emite M301.
        f_pen = card("Servo")
        cb_pen_mode = QComboBox()
        cb_pen_mode.addItems(["M280 personalizado", "M301 (automatico)"])
        cb_pen_mode.setCurrentIndex(int(cfg.value("pen_mode", 0)))
        sp_pen_up = dspin(0.0, 180.0, 1.0, 1, float(cfg.value("pen_up", 10.0)), " °")
        sp_pen_dn = dspin(0.0, 180.0, 1.0, 1, float(cfg.value("pen_dn", 45.0)), " °")
        cb_pen_fmt = QComboBox()
        cb_pen_fmt.addItems(["M280 S<angulo>", "M280 P0 S<angulo>"])
        cb_pen_fmt.setCurrentIndex(int(cfg.value("pen_fmt", 0)))
        sp_pen_speed = dspin(1.0, 240.0, 1.0, 0, float(cfg.value("pen_speed", 25)), " °/s")
        f_pen.addRow("Modo:", cb_pen_mode)
        f_pen.addRow("Arriba (S):", sp_pen_up)
        f_pen.addRow("Abajo (S):", sp_pen_dn)
        f_pen.addRow("Formato:", cb_pen_fmt)
        f_pen.addRow("Velocidad bajada:", sp_pen_speed)

        # Posicion y transformacion
        f_pos = card("Posicion y transformacion")
        _go = self.__dict__.get("_last_gerber_origin")
        sp_ox = dspin(-500.0, 500.0, 0.1, 2, float(_go[0]) if _go else float(cfg.value("origin_x", 0.0)))
        sp_oy = dspin(-500.0, 500.0, 0.1, 2, float(_go[1]) if _go else float(cfg.value("origin_y", 0.0)))
        cb_rot = QComboBox()
        for deg in (0, 90, 180, 270):
            cb_rot.addItem(f"{deg}°", deg)
        cb_rot.setCurrentIndex(cb_rot.findData(g_rot if g_rot in (0, 90, 180, 270) else 0))
        cb_mx = QCheckBox("Espejo en X (capa inferior B.Cu)")
        cb_mx.setChecked(bool(g_mir[0]))
        cb_my = QCheckBox("Espejo en Y")
        cb_my.setChecked(bool(g_mir[1]))
        cb_align = QCheckBox("Alinear con capas de la placa")
        f_pos.addRow("Origen X:", sp_ox)
        f_pos.addRow("Origen Y:", sp_oy)
        f_pos.addRow("Rotacion:", cb_rot)
        f_pos.addRow(cb_mx)
        f_pos.addRow(cb_my)
        f_pos.addRow(cb_align)
        lbl_warn = QLabel("")
        lbl_warn.setWordWrap(True)
        lbl_warn.setStyleSheet("color:#ff9f0a;font-size:10px;")
        f_pos.addRow(lbl_warn)

        # Area de trabajo
        f_area = card("Area de trabajo")
        sp_w = dspin(10.0, 1000.0, 1.0, 1, float(cfg.value("area_w", 170.0)))
        sp_h = dspin(10.0, 1000.0, 1.0, 1, float(cfg.value("area_h", 170.0)))
        f_area.addRow("Ancho X:", sp_w)
        f_area.addRow("Alto Y:", sp_h)
        lbl_fit = QLabel("")
        lbl_fit.setWordWrap(True)
        f_area.addRow(lbl_fit)

        # Profundidad
        f_depth = card("Profundidad")
        sp_depth = dspin(-20.0, 0.0, 0.1, 2, float(cfg.value("depth", -1.8)))
        sp_safe = dspin(0.5, 20.0, 0.5, 2, float(cfg.value("safe_z", 2.0)))
        f_depth.addRow("Profundidad (Z):", sp_depth)
        f_depth.addRow("Z de seguridad:", sp_safe)

        btn_center = QPushButton("Centrar en taladros")
        btn_center.setObjectName("DlgSecondary")
        right.addWidget(btn_center)
        right.addStretch(1)
        from PySide6.QtWidgets import QScrollArea
        _inner = QWidget()
        _inner.setLayout(right)
        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setWidget(_inner)
        _scroll.setFrameShape(QFrame.NoFrame)
        _scroll.setStyleSheet("QScrollArea { background: transparent; }")
        body.addWidget(_scroll, 2)
        root.addLayout(body)

        def cur_transform():
            return cb_rot.currentData(), cb_mx.isChecked(), cb_my.isChecked()

        def refresh():
            rot, mx, my = cur_transform()
            th = transform_holes(holes_raw, rot, mx, my)
            preview.set_holes(th)
            preview.set_work(sp_w.value(), sp_h.value())
            preview.set_origin(sp_ox.value(), sp_oy.value())
            xs = [h[0] for h in th]
            ys = [h[1] for h in th]
            bw, bh = max(xs) - min(xs), max(ys) - min(ys)
            ox, oy = sp_ox.value(), sp_oy.value()
            fits = (ox + bw <= sp_w.value()) and (oy + bh <= sp_h.value())
            lbl_fit.setText(f"Taladros {bw:.1f} x {bh:.1f} mm en origen ({ox:.1f}, {oy:.1f}): "
                            + ("CABE" if fits else "NO CABE"))
            lbl_fit.setStyleSheet("color:#27c93f;" if fits else "color:#ff5f56;")
            same = (rot == g_rot and (mx, my) == g_mir)
            if has_ref and not same:
                lbl_warn.setText("Rotacion o espejo distintos del Gerber: no se puede alinear.")
            else:
                lbl_warn.setText("")
            cb_align.setEnabled(has_ref and same)
            cb_align.setChecked(ask_align and has_ref and same)

        for w in (sp_ox, sp_oy, sp_w, sp_h):
            w.valueChanged.connect(lambda _v: refresh())
        for w in (cb_rot,):
            w.currentIndexChanged.connect(lambda _i: refresh())
        for w in (cb_mx, cb_my):
            w.toggled.connect(lambda _b: refresh())
        preview.originPicked.connect(lambda x, y: (sp_ox.setValue(round(x, 2)), sp_oy.setValue(round(y, 2))))
        btn_center.clicked.connect(preview.center_on_holes)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Generar")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root.addWidget(btns)

        refresh()
        if dlg.exec() != QDialog.Accepted:
            return

        cfg.setValue("broca", sp_dia.value())
        cfg.setValue("tipo", cb_type.currentText())
        cfg.setValue("pen_mode", cb_pen_mode.currentIndex())
        cfg.setValue("pen_up", sp_pen_up.value())
        cfg.setValue("pen_dn", sp_pen_dn.value())
        cfg.setValue("pen_fmt", cb_pen_fmt.currentIndex())
        cfg.setValue("pen_speed", sp_pen_speed.value())
        cfg.setValue("origin_x", sp_ox.value())
        cfg.setValue("origin_y", sp_oy.value())
        cfg.setValue("area_w", sp_w.value())
        cfg.setValue("area_h", sp_h.value())
        cfg.setValue("depth", sp_depth.value())
        cfg.setValue("safe_z", sp_safe.value())

        origin_x = sp_ox.value()
        origin_y = sp_oy.value()
        rot, mx, my = cur_transform()
        ref = board_refs[board] if (cb_align.isChecked() and cb_align.isEnabled()) else None

        try:
            # Los taladros se dibujan como un Gerber de "flashes" (un circulo por agujero),
            # asi usan el mismo convertidor que el cobre (rotacion, espejo, origen, servo).
            def _holes_to_gerber(hs):
                dias = sorted({round(d, 3) for _, _, d in hs})
                out = ["%FSLAX46Y46*%", "%MOMM*%", "G01*"]
                code = {}
                for i, dv in enumerate(dias):
                    code[dv] = 10 + i
                    out.append(f"%ADD{10 + i}C,{dv:.3f}*%")
                for dv in dias:
                    out.append(f"D{code[dv]}*")
                    for hx, hy, hd in hs:
                        if round(hd, 3) == dv:
                            out.append(f"X{int(round(hx * 1e6))}Y{int(round(hy * 1e6))}D03*")
                out.append("M02*")
                return "\n".join(out)
            from gerber_parser import gerber_to_gcode as _g2g
            _res = _g2g(_holes_to_gerber(holes_raw), tool_diameter=sp_dia.value(), clearance=0.0,
                        feed=300.0, origin_x=origin_x, origin_y=origin_y,
                        rotation=rot, mirror_x=mx, mirror_y=my,
                        pen_mode="m280", servo_up=sp_pen_up.value(), servo_down=sp_pen_dn.value(),
                        servo_p0=(cb_pen_fmt.currentIndex() == 1),
                        servo_down_speed=sp_pen_speed.value(),
                        modo="contorno", ref_bounds=ref)
            gcode = _res[0] if isinstance(_res, tuple) else _res
            info = {}
        except Exception as e:
            QMessageBox.critical(self, "Error Excellon", str(e))
            return

        self._log_system(f"Taladros: {len(holes_raw)} agujeros (dibujados como Gerber)")

        th = transform_holes(holes_raw, rot, mx, my)
        bx = ref[0] if ref is not None else min(h[0] for h in th)
        by = ref[1] if ref is not None else min(h[1] for h in th)
        holes = [(x - bx + origin_x, y - by + origin_y, d) for (x, y, d) in th]

        self._load_gcode(gcode, os.path.basename(path) + ".gcode", add_to_list=True)
        self.canvas_2d.set_holes(holes)
        self.canvas_3d.set_holes(holes)

    def _open_gerber_file(self, path=None, params=None):
        """Abre un archivo Gerber. Sin params muestra el dialogo de parametros."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Abrir Gerber (PCB)", "",
                "Gerber y taladros (*.gbr *.gtl *.gbl *.gts *.gbs *.gto *.gbo *.ger *.art *.drl *.xln);;"
                "Todos (*)"
            )
        if not path:
            return

        if params is None:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                pre_text = f.read()
            if _is_excellon(path, pre_text):
                pre_text = _excellon_to_gerber_text(pre_text)

            # Dialogo de parametros: plano 2D a la izquierda, ajustes en tarjetas a la derecha
            from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                                           QVBoxLayout, QLabel, QFormLayout, QComboBox, QCheckBox,
                                           QGroupBox)
            from PySide6.QtCore import QSettings
            from dialogs import GlassDialog
            from pcb_preview import PcbPreview

            cfg = QSettings("CNC Project", "gui_v_pcb")
            dlg = GlassDialog("Parametros de fresado de PCB", self)
            dlg.setMinimumWidth(1000)
            dlg.resize(1040, 660)
            dlg.setStyleSheet(
                "QLabel { color: #ffffff; background: transparent; font-size: 12px; }"
                "QGroupBox { color: #ffffff; font-weight: 600; border: 1px solid rgba(255,255,255,0.12);"
                " border-radius: 12px; margin-top: 14px; padding: 14px 10px 10px 10px;"
                " background: rgba(255,255,255,0.03); }"
                "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #8e8e93; }"
                "QDoubleSpinBox, QComboBox { background-color: rgba(255,255,255,0.08);"
                " border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 4px 8px;"
                " color: #ffffff; min-height: 22px; }"
                "QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #0A84FF; }"
                "QComboBox QAbstractItemView { background: #1c1c1e; color: #ffffff; selection-background-color: #0A84FF; }"
                "QCheckBox { color: #ffffff; background: transparent; }"
                "QPushButton { border-radius: 8px; padding: 7px 20px; font-size: 12px; border: none; }"
                "QPushButton#DlgPrimary { background-color: #0A84FF; color: #ffffff; font-weight: 600; }"
                "QPushButton#DlgPrimary:hover { background-color: #2b94ff; }"
                "QPushButton#DlgSecondary { background-color: rgba(255,255,255,0.10); color: #ffffff; }"
                "QPushButton#DlgSecondary:hover { background-color: rgba(255,255,255,0.16); }"
            )
            root = dlg.content_layout
            root.setSpacing(12)

            body = QHBoxLayout()
            body.setSpacing(16)
            preview = PcbPreview(pre_text)
            body.addWidget(preview, 3)

            right = QVBoxLayout()
            right.setSpacing(10)

            def card(title):
                g = QGroupBox(title)
                f = QFormLayout(g)
                f.setSpacing(8)
                right.addWidget(g)
                return f

            # Tarjeta 1: herramienta
            f_tool = card("Herramienta")
            cb_preset = QComboBox()
            cb_preset.addItems(["Personalizada", "V-bit 0.10 mm", "V-bit 0.20 mm",
                                "V-bit 0.30 mm", "V-bit 0.50 mm", "Fresa plana 0.80 mm"])
            presets = [None, 0.10, 0.20, 0.30, 0.50, 0.80]
            sp_tool = QDoubleSpinBox()
            sp_tool.setRange(0.05, 5.0)
            sp_tool.setSingleStep(0.05)
            sp_tool.setDecimals(2)
            sp_tool.setValue(float(cfg.value("tool", 0.20)))
            sp_tool.setSuffix(" mm")
            sp_clear = QDoubleSpinBox()
            sp_clear.setRange(0.0, 2.0)
            sp_clear.setSingleStep(0.05)
            sp_clear.setDecimals(2)
            sp_clear.setValue(float(cfg.value("clearance", 0.15)))
            sp_clear.setSuffix(" mm")
            sp_tol = QDoubleSpinBox()
            sp_tol.setRange(0.0, 0.2)
            sp_tol.setSingleStep(0.005)
            sp_tol.setDecimals(3)
            sp_tol.setValue(float(cfg.value("simplify_tol", 0.02)))
            sp_tol.setSuffix(" mm")
            sp_tol.setToolTip("Error maximo al simplificar curvas. 0 desactiva la simplificacion.")
            f_tool.addRow("Preset:", cb_preset)
            f_tool.addRow("Diametro (V-bit):", sp_tool)
            f_tool.addRow("Clearance:", sp_clear)
            f_tool.addRow("Simplificacion:", sp_tol)
            sp_min = QDoubleSpinBox()
            sp_min.setRange(0.0, 1.0)
            sp_min.setSingleStep(0.01)
            sp_min.setDecimals(3)
            sp_min.setValue(float(cfg.value("min_seg", 0.05)))
            sp_min.setSuffix(" mm")
            sp_min.setToolTip("Descarta puntos separados menos de este valor (quita micro-segmentos).")
            cb_arc = QCheckBox("Usar arcos G2/G3 en curvas")
            cb_arc.setChecked(str(cfg.value("arc_fit", "true")).lower() == "true")
            f_tool.addRow("Segmento minimo:", sp_min)
            f_tool.addRow(cb_arc)
            cb_modo = QComboBox()
            cb_modo.addItems(['Dibujo: contorno', 'Dibujo: relleno', 'Dibujo: contorno + relleno', 'Fresado: aislamiento', 'Fresado: aislamiento + Edge.Cuts', 'Plumon: pistas + relleno', 'Plumon: KiCad (pasadas + pads rellenos)'])
            _modo_idx = {k:i for i,k in enumerate(['contorno', 'relleno', 'ambos', 'aislamiento', 'aislamiento_edge', 'pistas_relleno', 'plumon_kicad'])}
            cb_modo.setCurrentIndex(_modo_idx.get(str(cfg.value("modo", "contorno")), 0))
            sp_pitch = QDoubleSpinBox()
            sp_pitch.setRange(0.02, 2.0)
            sp_pitch.setSingleStep(0.01)
            sp_pitch.setDecimals(2)
            sp_pitch.setValue(float(cfg.value("hatch_pitch", 0.15)))
            sp_pitch.setSuffix(" mm")
            sp_pitch.setToolTip("Separacion entre lineas de relleno. Tipico: igual o menor al diametro de la fresa.")
            lbl_edge_path = QLabel("")
            lbl_edge_path.setWordWrap(True)
            lbl_edge_path.setStyleSheet("color:#8e8e93;font-size:10px;")
            btn_edge = QPushButton("Seleccionar Edge.Cuts…")
            btn_edge.setObjectName("DlgSecondary")
            _edge_data = {"path": None, "text": None}
            def _pick_edge():
                from PySide6.QtWidgets import QFileDialog as _QFD
                p, _ = _QFD.getOpenFileName(dlg, "Seleccionar Edge.Cuts Gerber",
                                            str(Path(path).parent), "Gerber (*.gbr *.ger *.GBR)")
                if p:
                    _edge_data["path"] = p
                    with open(p, encoding="utf-8", errors="replace") as _f:
                        _edge_data["text"] = _f.read()
                    lbl_edge_path.setText(Path(p).name)
            btn_edge.clicked.connect(_pick_edge)
            def _update_edge_vis():
                on = cb_modo.currentIndex() >= 3
                btn_edge.setVisible(on)
                lbl_edge_path.setVisible(on)
            cb_modo.currentIndexChanged.connect(lambda _: _update_edge_vis())
            f_tool.addRow("Modo:", cb_modo)
            f_tool.addRow("", btn_edge)
            f_tool.addRow("", lbl_edge_path)
            f_tool.addRow("Paso relleno:", sp_pitch)
            _update_edge_vis()
            cb_preset.currentIndexChanged.connect(
                lambda i: sp_tool.setValue(presets[i]) if presets[i] else None)

            # Tarjeta 2: posicion y transformacion
            f_pos = card("Posicion y transformacion")
            rot_opts = [0, 90, 180, 270]
            sp_ox = QDoubleSpinBox()
            sp_ox.setRange(-500.0, 500.0)
            sp_ox.setSingleStep(1.0)
            sp_ox.setDecimals(2)
            sp_ox.setValue(float(cfg.value("origin_x", 0.0)))
            sp_ox.setSuffix(" mm")
            sp_oy = QDoubleSpinBox()
            sp_oy.setRange(-500.0, 500.0)
            sp_oy.setSingleStep(1.0)
            sp_oy.setDecimals(2)
            sp_oy.setValue(float(cfg.value("origin_y", 0.0)))
            sp_oy.setSuffix(" mm")
            cb_rot = QComboBox()
            cb_rot.addItems(["0\u00b0", "90\u00b0", "180\u00b0", "270\u00b0"])
            try:
                rot_val = int(float(cfg.value("rotation", 0)))
            except (TypeError, ValueError):
                rot_val = 0
            cb_rot.setCurrentIndex(rot_opts.index(rot_val) if rot_val in rot_opts else 0)
            cb_mx = QCheckBox("Espejo en X (capa inferior B.Cu)")
            cb_mx.setChecked(str(cfg.value("mirror_x", "false")).lower() == "true")
            cb_my = QCheckBox("Espejo en Y")
            cb_my.setChecked(str(cfg.value("mirror_y", "false")).lower() == "true")
            f_pos.addRow("Origen X:", sp_ox)
            f_pos.addRow("Origen Y:", sp_oy)
            f_pos.addRow("Rotacion:", cb_rot)
            f_pos.addRow(cb_mx)
            f_pos.addRow(cb_my)

            # Tarjeta 3: area de trabajo
            f_area = card("Area de trabajo")
            sp_ww = QDoubleSpinBox()
            sp_ww.setRange(10.0, 1000.0)
            sp_ww.setSingleStep(5.0)
            sp_ww.setDecimals(1)
            sp_ww.setValue(float(cfg.value("work_w", 170.0)))
            sp_ww.setSuffix(" mm")
            sp_wh = QDoubleSpinBox()
            sp_wh.setRange(10.0, 1000.0)
            sp_wh.setSingleStep(5.0)
            sp_wh.setDecimals(1)
            sp_wh.setValue(float(cfg.value("work_h", 150.0)))
            sp_wh.setSuffix(" mm")
            lbl_fit = QLabel("")
            lbl_fit.setWordWrap(True)
            f_area.addRow("Ancho X:", sp_ww)
            f_area.addRow("Alto Y:", sp_wh)
            f_area.addRow(lbl_fit)

            # Tarjeta 4: velocidades
            f_vel = card("Velocidades (mm/min)")
            sp_feed = QDoubleSpinBox()
            sp_feed.setRange(10, 2000)
            sp_feed.setSingleStep(50)
            sp_feed.setValue(float(cfg.value("feed", 200)))
            sp_feed.setSuffix(" mm/min")
            sp_rapid = QDoubleSpinBox()
            sp_rapid.setRange(500, 10000)
            sp_rapid.setSingleStep(500)
            sp_rapid.setValue(float(cfg.value("rapid", 3000)))
            sp_rapid.setSuffix(" mm/min")
            f_vel.addRow("Corte:", sp_feed)
            f_vel.addRow("Rapido:", sp_rapid)

            # Tarjeta 5: pluma (servo)
            f_pen = card("Pluma (servo)")
            cb_pen = QComboBox()
            cb_pen.addItems(["M280 personalizado", "M301 firmware (angulos fijos)"])
            cb_pen.setCurrentIndex(0 if str(cfg.value("pen_mode", "m280")) == "m280" else 1)
            sp_up = QDoubleSpinBox()
            sp_up.setRange(0.0, 180.0)
            sp_up.setSingleStep(1.0)
            sp_up.setDecimals(1)
            sp_up.setValue(float(cfg.value("servo_up", 0.0)))
            sp_up.setSuffix(" \u00b0")
            sp_down = QDoubleSpinBox()
            sp_down.setRange(0.0, 180.0)
            sp_down.setSingleStep(1.0)
            sp_down.setDecimals(1)
            sp_down.setValue(float(cfg.value("servo_down", 95.0)))
            sp_down.setSuffix(" \u00b0")
            cb_p0 = QComboBox()
            cb_p0.addItems(["M280 P0 S<angulo>", "M280 S<angulo>"])
            cb_p0.setCurrentIndex(0 if str(cfg.value("servo_p0", "true")).lower() == "true" else 1)
            lbl_pen = QLabel("Con M301 el firmware ignora arriba/abajo y usa 60/30 fijos (sin calibrar).")
            lbl_pen.setWordWrap(True)
            lbl_pen.setStyleSheet("color: #8e8e93; font-size: 10px;")
            f_pen.addRow("Modo:", cb_pen)
            f_pen.addRow("Arriba (S):", sp_up)
            f_pen.addRow("Abajo (S):", sp_down)
            f_pen.addRow("Formato:", cb_p0)
            sp_vdown = QDoubleSpinBox()
            sp_vdown.setRange(20.0, 240.0)
            sp_vdown.setSingleStep(5.0)
            sp_vdown.setDecimals(0)
            sp_vdown.setValue(float(cfg.value("servo_down_speed", 60.0)))
            sp_vdown.setSuffix(" \u00b0/s")
            sp_vdown.setToolTip("Velocidad al bajar el plumon (20 a 240). Menos = contacto mas suave. Minimo 20: con menos el movimiento supera el timeout de 5 s del firmware.")
            f_pen.addRow("Velocidad bajada:", sp_vdown)
            f_pen.addRow(lbl_pen)

            def pen_enabled():
                on = cb_pen.currentIndex() == 0
                sp_up.setEnabled(on)
                sp_down.setEnabled(on)
                cb_p0.setEnabled(on)
            cb_pen.currentIndexChanged.connect(lambda _i: pen_enabled())
            pen_enabled()

            # Tarjeta 6: modo de maquina
            f_mach = card("Modo de maquina")
            conn = bool(getattr(self, "connected", False))
            lbl_conn = QLabel("Conexion: " + ("conectada" if conn else "desconectada"))
            lbl_conn.setStyleSheet("color: %s; background: transparent;" % ("#27C93F" if conn else "#FF5F56"))
            lbl_mode = QLabel("Modo plotter: M301 al inicio, M302 al final")
            lbl_mode.setWordWrap(True)
            f_mach.addRow(lbl_conn)
            f_mach.addRow(lbl_mode)

            right.addStretch(1)
            from PySide6.QtWidgets import QFrame, QWidget as _QW
            inner = _QW()
            inner.setLayout(right)
            inner.setStyleSheet("background: transparent;")
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            scroll.setWidget(inner)
            body.addWidget(scroll, 2)
            root.addLayout(body)

            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btns.button(QDialogButtonBox.Ok).setObjectName("DlgPrimary")
            btns.button(QDialogButtonBox.Ok).setText("Generar")
            btns.button(QDialogButtonBox.Cancel).setObjectName("DlgSecondary")
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            root.addWidget(btns)

            # Conexiones: cada cambio actualiza el plano
            def push_options():
                preview.set_options(tool=sp_tool.value(), clearance=sp_clear.value(),
                                    simplify_tol=sp_tol.value(),
                                    modo=["contorno", "relleno", "ambos", "aislamiento", "aislamiento_edge", "pistas_relleno", "plumon_kicad"][cb_modo.currentIndex()],
                                    hatch_pitch=sp_pitch.value(),
                                    mirror_x=cb_mx.isChecked(), mirror_y=cb_my.isChecked(),
                                    rotation=rot_opts[cb_rot.currentIndex()])

            def push_origin():
                preview.set_origin(sp_ox.value(), sp_oy.value())

            def push_work():
                preview.set_work(sp_ww.value(), sp_wh.value())

            def show_status(txt, ok):
                lbl_fit.setText(txt)
                lbl_fit.setStyleSheet("color: %s; background: transparent; font-weight: 600;"
                                      % ("#27C93F" if ok else "#FF5F56"))

            for w_ in (sp_tool, sp_clear, sp_tol):
                w_.valueChanged.connect(push_options)
            for w_ in (cb_mx, cb_my):
                w_.toggled.connect(push_options)
            cb_rot.currentIndexChanged.connect(push_options)
            for w_ in (sp_ox, sp_oy):
                w_.valueChanged.connect(push_origin)
            for w_ in (sp_ww, sp_wh):
                w_.valueChanged.connect(push_work)
            preview.originPicked.connect(lambda x, y: (sp_ox.setValue(x), sp_oy.setValue(y)))
            preview.statusChanged.connect(show_status)

            push_options()
            push_origin()
            push_work()

            if dlg.exec() != QDialog.Accepted:
                self._log_system("Generacion cancelada: no se aceptaron los ajustes del Gerber", level="error")
                return

            tool = sp_tool.value()
            clearance = sp_clear.value()
            feed = sp_feed.value()
            rapid = sp_rapid.value()
            ox = sp_ox.value()
            oy = sp_oy.value()
            tol = sp_tol.value()
            min_seg = sp_min.value()
            _mk = ['contorno', 'relleno', 'ambos', 'aislamiento', 'aislamiento_edge', 'pistas_relleno', 'plumon_kicad']
            modo = _mk[cb_modo.currentIndex()] if cb_modo.currentIndex() < len(_mk) else "contorno"
            hatch_pitch = sp_pitch.value()
            cfg.setValue("modo", modo)
            _edge_text = _edge_data["text"] if modo == "aislamiento_edge" else None
            cfg.setValue("hatch_pitch", hatch_pitch)
            arc_fit = cb_arc.isChecked()
            cfg.setValue("min_seg", min_seg)
            cfg.setValue("arc_fit", arc_fit)
            mirror_x = cb_mx.isChecked()
            mirror_y = cb_my.isChecked()
            rotation = rot_opts[cb_rot.currentIndex()]
            work_w = sp_ww.value()
            work_h = sp_wh.value()
            pen_mode = "m280" if cb_pen.currentIndex() == 0 else "plotter"
            servo_up = sp_up.value()
            servo_down = sp_down.value()
            servo_p0 = cb_p0.currentIndex() == 0
            servo_down_speed = sp_vdown.value()
            cfg.setValue("servo_down_speed", servo_down_speed)
            cfg.setValue("pen_mode", pen_mode)
            cfg.setValue("servo_up", servo_up)
            cfg.setValue("servo_down", servo_down)
            cfg.setValue("servo_p0", servo_p0)
            for key, val in (("tool", tool), ("clearance", clearance), ("feed", feed),
                             ("rapid", rapid), ("origin_x", ox), ("origin_y", oy),
                             ("simplify_tol", tol), ("mirror_x", mirror_x), ("mirror_y", mirror_y),
                             ("rotation", rotation), ("work_w", work_w), ("work_h", work_h)):
                cfg.setValue(key, val)
        else:
            tool = float(params.get("tool", 0.20))
            clearance = float(params.get("clearance", 0.15))
            feed = float(params.get("feed", 200))
            rapid = float(params.get("rapid", 3000))
            ox = float(params.get("origin_x", 0.0))
            oy = float(params.get("origin_y", 0.0))
            tol = float(params.get("simplify_tol", 0.02))
            min_seg = float(params.get("min_seg", 0.05))
            _mk2 = ['contorno', 'relleno', 'ambos', 'aislamiento', 'aislamiento_edge', 'pistas_relleno', 'plumon_kicad']
            modo = str(params.get("modo", "contorno"))
            _edge_text = None
            hatch_pitch = float(params.get("hatch_pitch", 0.15))
            arc_fit = bool(params.get("arc_fit", True))
            mirror_x = bool(params.get("mirror_x", False))
            mirror_y = bool(params.get("mirror_y", False))
            rotation = int(params.get("rotation", 0))
            work_w = float(params.get("work_w", 170.0))
            work_h = float(params.get("work_h", 150.0))
            pen_mode = str(params.get("pen_mode", "m280"))
            servo_up = float(params.get("servo_up", 0.0))
            servo_down = float(params.get("servo_down", 95.0))
            servo_p0 = bool(params.get("servo_p0", True))
            servo_down_speed = float(params.get("servo_down_speed", 60.0))

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                gerber_text = f.read()
            if _is_excellon(path, gerber_text):
                gerber_text = _excellon_to_gerber_text(gerber_text)

            self._log_system(f"Parseando {os.path.basename(path)}...")

            # Guardar el offset del Gerber ANTES de generar el G-code
            from gerber_parser import GerberParser
            gp = GerberParser()
            gp.parse(gerber_text)
            if gp.bounds:
                self._last_offset = (-gp.bounds[0], -gp.bounds[1])
                print(f"[GERBER] Offset guardado: {self._last_offset}")

            # Detectar capa segun nombre de archivo
            layer_name = _detect_gerber_layer(os.path.basename(path))
            self._pending_layer = layer_name
            print(f"[GERBER] Capa detectada: {layer_name}")

            # Capas de una misma placa: mismo prefijo de nombre. La primera capa
            # fija el marco comun; las siguientes preguntan si se alinean con el.
            self._last_gerber_rotation = rotation
            self._last_gerber_mirror = (bool(mirror_x), bool(mirror_y))
            board = _board_name(path)
            # Guardar el texto de cada capa por placa, para dibujarlas en la vista de taladro.
            self.__dict__.setdefault("_board_layers", {}).setdefault(board, {})[layer_name] = gerber_text
            board_refs = self.__dict__.setdefault("_board_refs", {})
            ref_bounds = None
            answers = self.__dict__.setdefault("_align_answers", {})
            if board in board_refs:
                if board not in answers:
                    resp = QMessageBox.question(
                        self, "Alinear capas",
                        f"Estas capas parecen ser de la placa '{board}'.\n\n"
                        "¿Alinearlas con la misma referencia (contorno o la primera capa cargada)?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                    answers[board] = (resp == QMessageBox.Yes)
                if answers[board]:
                    ref_bounds = board_refs[board]
            else:
                # Marco en el MISMO espacio que usa la conversion (rotacion y espejo
                # incluidos). Los limites crudos del parser no coinciden si hay rotacion.
                from gerber_parser import gerber_to_paths
                _p, _b, _i = gerber_to_paths(gerber_text, tool, clearance,
                                             simplify_tol=tol, mirror_x=mirror_x,
                                             mirror_y=mirror_y, rotation=rotation)
                if _b:
                    board_refs[board] = (_b[0], _b[1])

            # El contorno Edge.Cuts fija el marco de la placa: siempre gana sobre la primera capa.
            if layer_name == "Edge.Cuts":
                from gerber_parser import gerber_to_paths as _gtp_e
                _pe, _eb, _ie = _gtp_e(gerber_text, tool, clearance, simplify_tol=tol,
                                       mirror_x=mirror_x, mirror_y=mirror_y, rotation=rotation)
                if _eb:
                    board_refs[board] = (_eb[0], _eb[1])
                ref_bounds = None
            self._last_gerber_origin = (ox, oy)
            _drill_list = [(float(h[0]), float(h[1]), float(h[2])) for _ld in getattr(self, 'hole_layers', {}).values() for h in _ld.get('holes', []) if len(h) >= 3]
            gcode, info = gerber_to_gcode(
                gerber_text,
                tool_diameter=tool,
                clearance=clearance,
                feed=feed,
                rapid=rapid,
                origin_x=ox,
                origin_y=oy,
                ref_bounds=ref_bounds,
                simplify_tol=tol,
                mirror_x=mirror_x,
                mirror_y=mirror_y,
                rotation=rotation,
                work_w=work_w,
                work_h=work_h,
                pen_mode=pen_mode,
                min_seg=min_seg,
                modo="aislamiento" if modo in ("aislamiento","aislamiento_edge") else modo,
                hatch_pitch=hatch_pitch,
                drill_text=_drill_list,
                fill_min_width=2.0,
                edge_gerber=_edge_text if modo == "aislamiento_edge" else None,
                arc_fit=arc_fit,
                servo_up=servo_up,
                servo_down=servo_down,
                servo_p0=servo_p0,
                servo_down_speed=servo_down_speed,
            )
            # Simplificacion de trayectoria (Douglas-Peucker, 0.01 mm).
            try:
                from gcode_optimizer import optimize_lines
                gcode, _gst = optimize_lines(list(gcode), tol=0.02)
            except Exception as opt_err:
                self._log_system("Optimizador Gerber omitido: %s" % opt_err, level="error")

            # Ya NO se limpian los taladros al cargar un Gerber
            # nuevo. Se preservan para superponer varias capas.
            # Para limpiar, usa el boton 'Limpiar todo'.

            self._log_system(
                f"Gerber parseado: {info['primitives']} primitivas, "
                f"{info['paths']} paths"
            )
            if info.get("warnings"):
                for w in info["warnings"][:3]:
                    self._log_system(f"Aviso: {w}", level="error")

            _fname = os.path.basename(path) + ".gcode"
            self._load_gcode(gcode, _fname,
                             add_to_list=False, layer_name=layer_name)

            # Guardar para doble clic (sin duplicar)
            self._add_file_to_list_unique(_fname, path, "gerber", layer_name)

        except Exception as e:
            QMessageBox.critical(self, "Error Gerber", str(e))

    def _open_gcode_file(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Abrir G-code", "", "G-code (*.gcode *.nc *.gc *.tap);;Todos (*)"
            )
        if not path: return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = [ln.rstrip() for ln in f if ln.strip()]
            # Simplificacion de trayectoria con tolerancia 0.01 mm (Douglas-Peucker).
            try:
                from gcode_optimizer import optimize_lines
                new_lines, stats = optimize_lines(lines, tol=0.01)
                if stats:
                    msg = "Optimizado: G1 %d -> %d (tolerancia %s mm)" % (
                        stats["g1_antes"], stats["g1_despues"], stats["tol_mm"])
                    self._log_system(msg)
                    lines = new_lines
            except Exception as opt_err:
                self._log_system("Optimizador omitido: %s" % opt_err, level="error")
            self._load_gcode(lines, os.path.basename(path))
            if self.file_list.count():
                self.file_list.item(self.file_list.count() - 1).setData(Qt.UserRole, {"path": path, "type": "gcode"})
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _open_image_to_gcode(self):
        """Convertir PDF / imagen a G-code (dibujo con la pluma)."""
        from image_to_gcode import open_image_dialog
        res = open_image_dialog(self)
        if res is None:
            return
        lines, name = res
        self._load_gcode(lines, name, add_to_list=True)
        self._log_system(f"Dibujo generado: {name}")

    def _open_svg_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir SVG", "", "SVG (*.svg);;Todos (*)"
        )
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                svg_text = f.read()
            self._log_system(f"Parseando {os.path.basename(path)}...")
            paths = svg_to_paths(svg_text, scale_mm_per_unit=1.0)
            if not paths:
                QMessageBox.warning(self, "Sin paths", "El SVG no tiene formas soportadas.")
                return
            gcode = paths_to_gcode(paths, feed=300, rapid=3000)
            self._load_gcode(gcode, os.path.basename(path) + ".gcode")
        except Exception as e:
            QMessageBox.critical(self, "Error SVG", str(e))

    def _open_dxf_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir DXF", "", "DXF (*.dxf);;Todos (*)"
        )
        if not path: return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                dxf_text = f.read()
            paths = dxf_to_paths(dxf_text, scale_mm_per_unit=1.0)
            if not paths:
                QMessageBox.warning(self, "Sin paths", "El DXF no tiene entidades soportadas.")
                return
            gcode = paths_to_gcode(paths, feed=300, rapid=3000)
            self._load_gcode(gcode, os.path.basename(path) + ".gcode")
        except Exception as e:
            QMessageBox.critical(self, "Error DXF", str(e))

    def _save_gcode_file(self):
        lines = self.editor.get_lines()
        if not lines or not any(ln.strip() for ln in lines):
            QMessageBox.warning(self, "Vacio", "No hay G-code.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar G-code", "salida.gcode", "G-code (*.gcode);;Todos (*)"
        )
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self._log_system(f"Guardado en {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_toggle_rapids(self):
        """Filtra por rapido/corte. Si hay fabricacion, filtra
        cada capa por separado manteniendo sus colores."""
        show_rapid = self.chk_show_rapids.isChecked()
        show_cuts = self.chk_show_cuts.isChecked()

        fab = getattr(self, '_fabrication_layers', {})
        if fab:
            layer_colors = {
                "F.Cu":      "#cc3333",
                "B.Cu":      "#3366cc",
                "Edge.Cuts": "#ffcc00",
                "F.SilkS":   "#ffffff",
                "B.SilkS":   "#dddddd",
                "F.Mask":    "#a855f7",
                "B.Mask":    "#7c3aed",
                "PTH":       "#00ffff",
                "NPTH":      "#ff00ff",
            }
            all_3d = []
            for lname, segs in fab.items():
                filt = []
                for seg in segs:
                    tipo = seg[4] if len(seg) > 4 else 'cut'
                    if tipo == 'rapid' and show_rapid:
                        filt.append(seg)
                    elif tipo != 'rapid' and show_cuts:
                        filt.append(seg)
                self.canvas_2d.set_layer_segments(lname, filt)
                color = layer_colors.get(lname, '#94a3b8')
                for s in filt:
                    all_3d.append(tuple(list(s) + [color]))
            self.canvas_2d._invalidate_cache()
            self.canvas_2d.update()
            self.canvas_3d.set_toolpath_segments(all_3d)
            return

        # Modo normal: una sola capa
        all_segs = getattr(self, '_all_segments', [])
        layer_name = getattr(self, '_current_layer', 'GCode')
        filtered = []
        for seg in all_segs:
            tipo = seg[4] if len(seg) > 4 else 'cut'
            if tipo == 'rapid' and show_rapid:
                filtered.append(seg)
            elif tipo != 'rapid' and show_cuts:
                filtered.append(seg)

        if layer_name == 'GCode':
            self.canvas_2d.clear_layer('GCode')
        self.canvas_2d.set_layer_segments(layer_name, filtered)
        self.canvas_3d.set_toolpath_segments(filtered)

    def _load_gcode(self, lines, name, add_to_list=True, layer_name="GCode"):
        self.gcode_lines = lines
        self.layer_gcodes[layer_name] = list(lines)
        # El trabajo se envia desde el editor: sin esto, Convertir se ignora
        # y se envia el texto viejo del editor (sin G0).
        self.editor.load_lines(list(lines))

        # Solo añadir a la lista la PRIMERA vez
        if add_to_list:
            _it = QListWidgetItem(name)
            _it.setData(258, layer_name)
            self.file_list.addItem(_it)

        n = len(lines)
        self.lbl_line_count.setText(f"0/{n}")
        self._log_system(f"Cargado: {name} ({n} lineas)")
        if self.connected and not self.polling_enabled:
            self._toggle_polling()

        # Parsear y guardar TODOS los segmentos (sin filtrar)
        try:
            from gcode_editor import parse_gcode_stats, parse_gcode_toolpath
            stats = parse_gcode_stats(lines)
            self._update_stats_panel(stats, name)
            segments = parse_gcode_toolpath(lines)
            self._all_segments = segments
            self._current_layer = layer_name

            if segments:
                xs = []
                ys = []
                for s in segments:
                    xs.extend([s[0], s[2]])
                    ys.extend([s[1], s[3]])
                # Resetear zoom al cargar: antes el zoom se
                # quedaba en 2489% y no se veia el toolpath.
                self.canvas_2d.zoom = 1.0
                self.canvas_2d.pan_x = 0.0
                self.canvas_2d.pan_y = 0.0
                print(f"[LOAD] Toolpath bounds: X[{min(xs):.2f}, {max(xs):.2f}] "
                      f"Y[{min(ys):.2f}, {max(ys):.2f}]  ({len(segments)} segmentos)")

                width = max(xs) - min(xs)
                height = max(ys) - min(ys)
                margin = max(width, height) * 0.1
                if margin < 5:
                    margin = 5
                self.canvas_2d.area_x = max(width + 2 * margin, 10)
                self.canvas_2d.area_y = max(height + 2 * margin, 10)

            self._on_toggle_rapids()

        except Exception as e:
            print(f"[LOAD] Error al parsear: {e}")


    def _update_stats_panel(self, stats, name):
        self.lbl_st_total.setText(str(stats["total"]))
        self.lbl_st_g0.setText(str(stats["g0"]))
        self.lbl_st_g1.setText(str(stats["g1"]))
        self.lbl_st_arcs.setText(str(stats["g2"] + stats["g3"]))
        self.lbl_st_mcodes.setText(str(stats["m_codes"]))
        self.lbl_st_distance.setText(f"{stats['distance']:.1f} mm")

        # Bounds
        if stats["min_x"] is not None:
            bx = f"X[{stats['min_x']:.1f}, {stats['max_x']:.1f}]"
            by = f"Y[{stats['min_y']:.1f}, {stats['max_y']:.1f}]"
            self.lbl_st_bounds.setText(f"{bx} {by}")
        else:
            self.lbl_st_bounds.setText("-")

        # Boundary check contra el area configurada (con tolerancia)
        s = QSettings("CNC Proyect", "CNC IDE Web")
        AREA_X = float(s.value("cfg_work_area_x", 200.0, type=float))
        AREA_Y = float(s.value("cfg_work_area_y", 200.0, type=float))
        TOL = 1.0  # mm de margen (numeros negativos pequenos son OK)

        out = 0
        if stats["min_x"] is not None:
            if stats["min_x"] < -TOL or stats["max_x"] > AREA_X + TOL:
                out += 1
            if stats["min_y"] < -TOL or stats["max_y"] > AREA_Y + TOL:
                out += 1

        if out == 0:
            self.lbl_st_status.setText("OK dentro del area")
            self.lbl_st_status.setStyleSheet(f"color: {C_GREEN}; font-size: 11px;")
        else:
            self.lbl_st_status.setText("FUERA del area")
            self.lbl_st_status.setStyleSheet(f"color: {C_RED}; font-size: 11px; font-weight: 700;")

        # Marcar lineas con error (fuera de limites) en el editor
        error_lines = []
        for idx, raw in enumerate(self.gcode_lines):
            l = raw.split(";")[0].strip().upper()
            nx = None
            ny = None
            m = re.search(r"X([-+]?\d*\.?\d+)", l)
            if m:
                try: nx = float(m.group(1))
                except: pass
            m = re.search(r"Y([-+]?\d*\.?\d+)", l)
            if m:
                try: ny = float(m.group(1))
                except: pass
            if nx is not None and (nx < -TOL or nx > AREA_X + TOL):
                error_lines.append(idx)
            elif ny is not None and (ny < -TOL or ny > AREA_Y + TOL):
                error_lines.append(idx)

        self.editor.set_error_lines(error_lines)
        stats["error_lines"] = error_lines

    @Slot(str)
    def _on_connected(self, port):
        self.connected = True
        self._pos_valid = False   # hasta que haya un $H (HOME_OK)
        self._position_reported = False
        self.lbl_x.setText("?")
        self.lbl_y.setText("?")
        self.current_port = port
        self.conn_label.setText(f"Conectado - {port}")
        self.conn_label.setProperty("connected", "true")
        self._refresh_style(self.conn_label)
        self.connect_btn.setText("Desconectar")
        self._log_system(f"Conectado a {port}")
        self._send_cmd("G1000 M1 MODO?")
        if self.polling_enabled:
            self.poll_timer.start()

    @Slot()
    def _on_disconnected(self):
        self.connected = False
        self.current_port = ""
        self.conn_label.setText("Desconectado")
        self.conn_label.setProperty("connected", "false")
        self._refresh_style(self.conn_label)
        self.connect_btn.setText("Conectar")
        self.poll_timer.stop()
        if self.job_running:
            self.job_running = False
            self.time_timer.stop()
            self.editor.set_job_line(-1)
            self._set_state("idle")
        self._log_system("Desconectado")

    @Slot(str)
    def _on_error(self, msg):
        self._log_system(msg, level="error")

    @Slot(str)
    def _on_line_sent(self, line):
        if self._is_auto_m114 and line == "M114":
            return
        if self._limits_auto and line == "LIMITS":
            return
        if self.settings.value("echo", True, type=bool):
            self._log(line, direction="tx")

    @Slot(str)
    def _on_line_received(self, line):
        if line.startswith("PERF1 ") or line.startswith("PERF2 "):
            self._handle_perf_line(line)
            return
        if line.startswith("PULSE "):
            values = {key.lower(): value for key, value in
                      re.findall(r"([A-Za-z]+)=([-+]?\d+(?:\.\d+)?)", line)}
            try:
                self._motor_pulse_stats["X"] = {
                    "count": int(values.get("x", 0)),
                    "rate": float(values.get("sx", 0.0)),
                }
                self._motor_pulse_stats["Y"] = {
                    "count": int(values.get("y", 0)),
                    "rate": float(values.get("sy", 0.0)),
                }
                self._pulse_telemetry_received = True
                bridge = getattr(self, "web_bridge", None)
                if bridge is not None:
                    bridge.pulseStatsChanged.emit({
                        "stats": {axis: dict(value) for axis, value in self._motor_pulse_stats.items()},
                        "telemetryAvailable": True,
                    })
            except (TypeError, ValueError):
                pass
            return
        if line.startswith("LIM "):
            self._handle_limits_reply(line)
            # Toda respuesta LIM va seguida de OK: ese OK no se muestra.
            self._limits_auto = False
            self._limits_skip_ok = True
            return
        if line == "OK" and self._limits_skip_ok:
            self._limits_skip_ok = False
            return
        is_pos_response = line.startswith("X:") and "Y:" in line
        if is_pos_response:
            # Respuesta de M114 (automatica o manual): se usa para la posicion,
            # pero no se muestra en la consola.
            self._parse_incoming(line)
            self._is_auto_m114 = False
            return

        # Respuestas del modo de microstep (G1000 M1)
        if line.startswith("MODO "):
            self._mode_applied = line.split(" ", 1)[1].strip()
            self._set_mode_combo(self._mode_applied)
            self._log(line, direction="rx")
            return
        if getattr(self, "_mode_pending", None):
            if line == "OK":
                self._mode_applied = self._mode_pending
                self._mode_pending = None
                self._set_mode_combo(self._mode_applied)
                self._log_system(f"Modo aplicado: {self._mode_applied}")
                return
            if line.startswith("error") or line.startswith("ERROR"):
                self._log(line, direction="rx")
                self._log_system(f"Firmware no aplico el modo {self._mode_pending}: {line}", level="error")
                self._mode_pending = None
                self._set_mode_combo(getattr(self, "_mode_applied", "NORMAL"))
                return

        if line == "OK" and self.job_running:
            # Diagnostico: hora de cada OK durante el trabajo (para medir la latencia).
            try:
                import time as _tm
                _t = _tm.time()
                _idx = self._job_pending[0] if self._job_pending else -1
                with open(CONSOLE_LOG_PATH.parent / "ok_tiempos.log", "a", encoding="utf-8") as _f:
                    _f.write(f"{_tm.strftime('%H:%M:%S', _tm.localtime(_t))}.{int((_t % 1) * 1000):03d} OK linea={_idx} pend={len(self._job_pending)}\n")
            except Exception:
                pass
            if self._job_ack_events:
                ack_event = self._job_ack_events.popleft()
                if ack_event is not None:
                    self._on_job_line_ack()
            return

        # El firmware sigue vivo mientras drena su cola: BUSY, ST y POS
        # reinician el timeout de OK. Antes, un planner lleno abortaba el
        # job aunque la maquina seguia moviendose.
        if (self.job_running and self._last_sent_was_job and
                (line.startswith("BUSY") or line.startswith("ST ") or
                 line.startswith("POS ")) and
                hasattr(self, "_ack_timer")):
            self._ack_timer.start(getattr(self, "_ack_timeout_current_ms", 15000))

        # QUEUE_FULL: firmware saturado. Esperar y reintentar la MISMA
        # linea (no abortar). El firmware necesita tiempo para drenar.
        if self.job_running and "QUEUE_FULL" in line:
            self._log(line, direction="rx")
            QTimer.singleShot(80, self._send_next_job_line)
            return

        # Otros errores del firmware: abortar el job.
        if self.job_running and (line.startswith("error:") or
                                  line.startswith("ERROR:")):
            self._log(line, direction="rx")
            self._abort_job_on_error(line)
            return

        if line.startswith("ERROR:QUEUE_FULL") and self.job_running:
            self._abort_job_on_error("cola del firmware llena: se perderian segmentos")
            return
        # POS: posicion en vivo durante el movimiento.
        # El firmware la manda cada 100 ms. No ensuciar el log.
        if line.startswith("POS "):
            self._parse_incoming(line)
            return

        # DONE con posicion: parsea X/Y y log compacto
        if line.startswith("DONE"):
            self._parse_incoming(line)
            self._log("DONE", direction="rx")
            self._cnc_busy = False
            if getattr(self, "_job_wait_motion", False) and self.job_running and getattr(self, "_job_seen_busy", False):
                self._job_wait_motion = False
                self._job_finished()
            return

        if line.startswith("ST "):
            # Telemetria del movimiento: solo al archivo de log, no a la consola.
            _append_console_log("RX", line)
            self._cnc_busy = "mbusy=1" in line
            if self._cnc_busy:
                self._job_seen_busy = True
            if getattr(self, "_job_wait_motion", False) and self.job_running and getattr(self, "_job_seen_busy", False) and not self._cnc_busy:
                self._job_wait_motion = False
                self._job_finished()
            self._parse_incoming(line)
            return
        self._log(line, direction="rx")
        self._parse_incoming(line)

    def _abort_job_on_error(self, reason):
        if not self.job_running:
            return

        if hasattr(self, "_ack_timer"):
            self._ack_timer.stop()

        self._job_pending = collections.deque()
        self._job_wait_motion = False
        self.job_running = False
        self._release_sleep_inhibit()
        self.job_paused = False
        self._last_sent_was_job = False
        self.pause_btn.setText("Pausar")
        self.progress_bar.setValue(0)
        self.lbl_line_count.setText("-")
        self._set_state("alarm")
        self.time_timer.stop()
        self.editor.set_job_line(-1)
        self._log_system(
            f"Job abortado en linea {self.job_current}/{self.job_total}: {reason}",
            level="error",
        )
        JOB_RECORDER.finish("abortado: " + reason)

    def _handle_perf_line(self, line):
        """Telemetria DWT del firmware. Solo al log de consola y al panel;
        nunca a la consola visible ni a la cuenta de OK del trabajo."""
        _append_console_log("RX", line)
        values = {k: int(v) for k, v in re.findall(r"([A-Za-z_]+)=(\d+)", line)}

        def us(key):
            return values.get(key, 0) / 1000.0

        if line.startswith("PERF1 "):
            self.lbl_perf_load.setText(f"{values.get('load', 0)} %")
            self.lbl_perf_isr.setText(f"{values.get('isr', 0)} %  (interrupcion)")
            self.lbl_perf_loop.setText(f"{us('loop_max'):.1f} us  (media {us('loop_avg'):.1f})")
            secciones = {k: values.get(k, 0) for k in
                         ("poll", "prot", "usb", "cam", "plan", "cnc", "led", "scr")}
            top = max(secciones, key=secciones.get)
            self.lbl_perf_sect.setText(f"{top}: {us(top):.1f} us")
        else:
            self.lbl_perf_enq.setText(
                f"media {us('enq_avg'):.1f} / max {us('enq_max'):.1f} us (n={values.get('enq_n', 0)})")
            self.lbl_perf_mov.setText(
                f"media {us('mov_avg'):.1f} / max {us('mov_max'):.1f} us (n={values.get('mov_n', 0)})")
            self.lbl_perf_prod.setText(f"{us('prod_max'):.1f} us")
            self.lbl_perf_ur.setText(str(values.get('ur', 0)))
            self.lbl_perf_free.setText(f"{values.get('free', 0)} / 256")

        bridge = getattr(self, "web_bridge", None)
        if bridge is not None:
            kind = "PERF1" if line.startswith("PERF1 ") else "PERF2"
            bridge.perfChanged.emit({"kind": kind, "values": values})

    def _parse_incoming(self, line):
        # re.search en vez de re.match: DONE X:... Y:... tambien encaja
        m = re.search(r"X:(-?\d+\.?\d*)\s+Y:(-?\d+\.?\d*)", line)
        if m:
            try:
                self._update_position(float(m.group(1)), float(m.group(2)))
            except ValueError:
                pass
            return
        if line.startswith("HOME_OK"):
            self._pos_valid = True
            home_pos = re.search(r"X=([-+]?\d*\.?\d+)\s+Y=([-+]?\d*\.?\d+)", line)
            if home_pos:
                self._update_position(float(home_pos.group(1)), float(home_pos.group(2)))
            self._log_system("Origen fijado: posicion valida")
            return
        if line.startswith("ALARM:"):
            # La placa parada: la posicion interna ya no es fiable.
            self._pos_valid = False
            self._set_state("alarm")
            if self.job_running:
                self._abort_job_on_error("alarma de la placa: " + line)
            self._log_system("Posicion no confiable: haz $H antes de continuar", level="error")
            return
        if line.lower().startswith("error"):
            self._log_system(f"Aviso: {line}", level="error")

    def _update_position(self, x, y):
        # La vista sigue la telemetría del STM32 incluso antes del homing.
        # El permiso para iniciar trabajos sigue dependiendo de _pos_valid/$H.
        self._position_reported = True
        self._last_position_reported = __import__("time").monotonic()
        self.lbl_x.setText(f"{x:.2f}")
        self.lbl_y.setText(f"{y:.2f}")
        self.canvas_2d.set_position(x, y)
        self.canvas_3d.set_position(x, y, self.current_z)

    def _set_state(self, state):
        labels = {
            "idle": ("Listo", "idle"),
            "run": ("Ejecutando", "run"),
            "paused": ("Pausa", "paused"),
            "alarm": ("ALARMA", "alarm"),
        }
        text, prop = labels.get(state, ("-", "idle"))
        self.state_badge.setText(text)
        self.state_badge.setProperty("state", prop)
        self._refresh_style(self.state_badge)

    def _poll_limits(self):
        if not self.connected or self.job_running:
            return
        self._limits_auto = True
        self.request_send.emit("LIMITS")

    def _handle_limits_reply(self, line):
        vals = {}
        for part in line.split()[1:]:
            if "=" in part:
                k, v = part.split("=", 1)
                vals[k] = v
        now = {k: vals.get(k) for k in ("X1", "X2", "Y1", "Y2")}
        # La placa da 0 con el final pulsado: se muestra invertido (pulsado = 1).
        shown = {k: (None if v is None else ("1" if v == "0" else "0")) for k, v in now.items()}
        if self._limits_last is None:
            self._log_system("Finales: X1=%s X2=%s Y1=%s Y2=%s" % (shown["X1"], shown["X2"], shown["Y1"], shown["Y2"]))
            self._limits_last = now
            return
        for k, v in now.items():
            if v is not None and v != self._limits_last.get(k):
                self._log_system(f"LIM_{k}={shown[k]}")
        self._limits_last = now

    def _poll_position(self):
        if not self.connected or not self.polling_enabled:
            return
        # No molestar al job con M114 durante un trabajo en curso
        if self.job_running:
            return
        self._is_auto_m114 = True
        self.request_send.emit("M114")

    def _log(self, text, direction="rx"):
        ts = datetime.now().strftime("%H:%M:%S")
        if direction == "tx":
            arrow = f'<span style="color:{C_BLUE};">-&gt;</span>'
            body = f'<span style="color:{C_TEXT_0};">{self._escape(text)}</span>'
        elif direction == "rx":
            arrow = f'<span style="color:{C_GREEN};">&lt;-</span>'
            body = f'<span style="color:{C_TEXT_1};">{self._escape(text)}</span>'
        else:
            arrow = f'<span style="color:{C_RED};">!</span>'
            body = f'<span style="color:{C_RED};">{self._escape(text)}</span>'
        time_html = ""
        if self.settings.value("timestamps", True, type=bool):
            time_html = f'<span style="color:{C_TEXT_2};">[{ts}]</span> '
        html = f'<div style="font-family: monospace; font-size: 11px;">{time_html}{arrow} {body}</div>'
        _append_console_log({"tx": "TX", "rx": "RX"}.get(direction, "ERR"), text)
        self.console_body.append(html)
        if self.settings.value("autoscroll", True, type=bool):
            sb = self.console_body.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _log_system(self, text, level="info"):
        color = C_TEXT_2 if level == "info" else C_RED
        arrow = f'<span style="color:{color};">.</span>'
        body = f'<span style="color:{color};">{self._escape(text)}</span>'
        time_html = ""
        if self.settings.value("timestamps", True, type=bool):
            ts = datetime.now().strftime("%H:%M:%S")
            time_html = f'<span style="color:{C_TEXT_2};">[{ts}]</span> '
        html = f'<div style="font-family: monospace; font-size: 11px;">{time_html}{arrow} {body}</div>'
        _append_console_log("SYS" if level == "info" else "ERR", text)
        self.console_body.append(html)
        sb = self.console_body.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _escape(self, s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def _refresh_style(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    # ============================================================
    # SIMULACION DEL TOOLPATH
    # ============================================================
    def _on_simulate_clicked(self):
        if self.simulator.running:
            self.simulator.stop()
            self.sim_btn.setText("Simular")
            self.sim_btn.setObjectName("CtrlStart")
            self._refresh_style(self.sim_btn)
            self.editor.set_job_line(-1)
            self._set_state("idle")
            self._log_system("Simulacion detenida")
            return

        if self.job_running:
            self._log_system("Hay un trabajo en curso", level="error")
            return

        # Construir la lista de segmentos en orden de fabricacion.
        # Filtra los rapidos si "Ver G0" esta desmarcado.
        show_rapid = self.chk_show_rapids.isChecked()
        show_cuts = self.chk_show_cuts.isChecked()

        fab = getattr(self, '_fabrication_layers', {})
        segments = []
        layer_list = []

        if fab:
            for _lname, _lsegs in fab.items():
                layer_list.append(_lname)
                for _s in _lsegs:
                    _tipo = _s[4] if len(_s) > 4 else 'cut'
                    if _tipo == 'rapid' and not show_rapid:
                        continue
                    if _tipo != 'rapid' and not show_cuts:
                        continue
                    segments.append(_s)
        else:
            layer_name = getattr(self, '_current_layer', 'GCode')
            raw_segs = list(self.canvas_2d.layers.get(layer_name, {}).get('segments', []))
            if not raw_segs:
                raw_segs = list(getattr(self, '_all_segments', []))
            for _s in raw_segs:
                _tipo = _s[4] if len(_s) > 4 else 'cut'
                if _tipo == 'rapid' and not show_rapid:
                    continue
                if _tipo != 'rapid' and not show_cuts:
                    continue
                segments.append(_s)

        if not segments:
            self._log_system("No hay toolpath para simular", level="error")
            return

        speed = self._slider_pos_to_speed(self.sim_speed_slider.value())
        self.simulator.set_speed(speed)
        self._last_sim_segments = segments
        self.simulator.start(segments, speed)

        self.sim_btn.setText("Detener sim")
        self.sim_btn.setObjectName("CtrlStop")
        self._refresh_style(self.sim_btn)
        self._set_state("run")

        if fab:
            _src = "fabricacion: " + " -> ".join(layer_list)
        else:
            _src = "capa " + getattr(self, '_current_layer', 'GCode')
        self._log_system(f"Simulacion ({_src}) - {len(segments)} segmentos, {speed:.0f} mm/s")

    def _slider_pos_to_speed(self, pos):
        """Mapea slider 0-1000 a velocidad logaritmica 10-30000 mm/s.
        pos=0 -> 10 mm/s, pos=500 -> ~550 mm/s, pos=1000 -> 30000 mm/s.
        """
        import math
        min_s = 10.0
        max_s = 30000.0
        t = max(0.0, min(1.0, pos / 1000.0))
        return min_s * ((max_s / min_s) ** t)

    def _on_sim_speed_changed(self, pos):
        speed = self._slider_pos_to_speed(pos)
        self.sim_speed_label.setText(f"{speed:.0f}")
        if self.simulator.running:
            self.simulator.set_speed(speed)

    @Slot(float, float, float, bool)
    def _on_sim_position(self, x, y, z=0.0, cutting=False):
        self.canvas_2d.set_position_immediate(x, y)
        self.canvas_3d.set_position_immediate(x, y, z)
        self.lbl_x.setText(f"{x:.2f}")
        self.lbl_y.setText(f"{y:.2f}")

    @Slot(int)
    def _on_sim_line(self, line_num):
        if line_num >= 0:
            self.editor.set_job_line(line_num)
            self.editor.scroll_to_line(line_num)

    @Slot(int, int)
    def _on_sim_progress(self, current, total):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress_bar.setValue(pct)
            self.lbl_line_count.setText(f"SIM {current}/{total}")

    @Slot()
    def _on_sim_finished(self):
        self.sim_btn.setText("Simular")
        self.sim_btn.setObjectName("CtrlStart")
        self._refresh_style(self.sim_btn)
        self.progress_bar.setValue(100)
        self.lbl_line_count.setText("SIM OK")
        self.editor.set_job_line(-1)
        self._set_state("idle")
        self._log_system("Simulacion completada")

    def _on_file_double_clicked(self, item):
        """Carga el archivo al doble clic segun su tipo."""
        from PySide6.QtCore import Qt as _Qt
        meta = item.data(_Qt.UserRole)
        if not meta:
            self._log_system(f"Sin metadata: {item.text()}")
            return

        path = meta.get("path", "")
        tipo = meta.get("type", "")

        if not path or not os.path.exists(path):
            self._log_system(f"Archivo no existe: {path}", level="error")
            return

        self._log_system(f"Cargando: {os.path.basename(path)} [{tipo}]")

        try:
            if tipo == "gcode":
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = [ln.rstrip() for ln in f if ln.strip()]
                self._load_gcode(lines, os.path.basename(path), add_to_list=False)

            elif tipo == "gerber":
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                from gerber_parser import gerber_to_gcode, GerberParser

                # Guardar el offset del Gerber
                gp = GerberParser()
                gp.parse(text)
                if gp.bounds:
                    self._last_offset = (-gp.bounds[0], -gp.bounds[1])

                gcode, info = gerber_to_gcode(text,
                                               tool_diameter=0.2,
                                               clearance=0.15)
                try:
                    from gcode_optimizer import optimize_lines
                    gcode, _gst = optimize_lines(list(gcode), tol=0.02)
                except Exception as opt_err:
                    self._log_system("Optimizador Gerber omitido: %s" % opt_err, level="error")
                layer_name = _detect_gerber_layer(os.path.basename(path))
                self._load_gcode(gcode, os.path.basename(path) + ".gcode",
                                 add_to_list=False, layer_name=layer_name)

                # El Gerber borra los taladros anteriores
                self.canvas_2d.set_holes([])
                self.canvas_3d.set_holes([])

            elif tipo == "excellon":
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                from excellon_parser import excellon_to_gcode, ExcellonParser

                excel_parser = ExcellonParser()
                holes_raw = excel_parser.parse(text)

                # Aplicar el mismo offset que usa el Gerber
                ox, oy = getattr(self, '_last_offset', (0.0, 0.0))
                holes = [(x + ox, y + oy, d) for (x, y, d) in holes_raw]

                self.canvas_2d.set_holes(holes)
                self.canvas_3d.set_holes(holes)
                self._log_system(f"{len(holes)} taladros superpuestos")

                # Solo cargar el G-code si NO hay un Gerber ya
                if not self.canvas_2d.toolpath_segments:
                    gcode, info = excellon_to_gcode(text)
                    self._load_gcode(gcode, os.path.basename(path) + ".gcode",
                                     add_to_list=False)

            else:
                self._log_system(f"Tipo desconocido: {tipo}", level="error")

        except Exception as e:
            self._log_system(f"Error: {e}", level="error")

    def closeEvent(self, event):
        self.poll_timer.stop()
        self.time_timer.stop()
        if self.connected:
            self.request_disconnect.emit()
            QThread.msleep(100)
        self.thread.quit()
        self.thread.wait(2000)
        event.accept()
