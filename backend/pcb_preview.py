"""Plano 2D del PCB para el dialogo de fresado.
Muestra el trazado con espejo y rotacion ya aplicados, el area de trabajo y el
origen. Un clic en el plano pide fijar el origen en ese punto."""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QPolygonF


class PcbPreview(QWidget):
    originPicked = Signal(float, float)
    statusChanged = Signal(str, bool)

    MARGIN = 28

    def __init__(self, gerber_text, parent=None):
        super().__init__(parent)
        self._text = gerber_text
        self._paths = []
        self._bounds = None
        self._origin = (0.0, 0.0)
        self._work = (170.0, 150.0)
        self._opts = {"tool": 0.20, "clearance": 0.15, "simplify_tol": 0.02,
                      "modo": "contorno", "hatch_pitch": 0.15,
                "incluir_plano": False,
                      "mirror_x": False, "mirror_y": False, "rotation": 0}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._recompute)
        self.setMinimumSize(460, 380)
        self.setCursor(Qt.CrossCursor)
        self._recompute()

    # ---- API publica ----
    def set_options(self, **kw):
        self._opts.update(kw)
        self._timer.start()

    def set_origin(self, x, y):
        self._origin = (float(x), float(y))
        self._emit_status()
        self.update()

    def set_work(self, w, h):
        self._work = (float(w), float(h))
        self._emit_status()
        self.update()

    def pcb_size(self):
        if not self._bounds:
            return None
        minx, miny, maxx, maxy = self._bounds
        return maxx - minx, maxy - miny

    def fits(self):
        size = self.pcb_size()
        if size is None:
            return False
        ox, oy = self._origin
        w, h = self._work
        pw, ph = size
        return ox >= 0 and oy >= 0 and ox + pw <= w and oy + ph <= h

    # ---- calculo ----
    def _recompute(self):
        from gerber_parser import gerber_to_paths
        o = self._opts
        try:
            paths, bounds, _info = gerber_to_paths(
                self._text,
                tool_diameter=o["tool"],
                clearance=o["clearance"],
                simplify_tol=o["simplify_tol"],
                mirror_x=o["mirror_x"],
                mirror_y=o["mirror_y"],
                rotation=o["rotation"],
                modo="aislamiento" if o["modo"] in ("aislamiento","aislamiento_edge") else o["modo"],
                incluir_plano=o["modo"] in ("aislamiento","aislamiento_edge"),
                hatch_pitch=o["hatch_pitch"],
            )
        except Exception:
            paths, bounds = [], None
        self._paths = paths or []
        self._bounds = bounds if self._paths else None
        self._emit_status()
        self.update()

    def _emit_status(self):
        size = self.pcb_size()
        if size is None:
            self.statusChanged.emit("Sin geometria cargada", False)
            return
        pw, ph = size
        ok = self.fits()
        txt = ("PCB %.1f x %.1f mm en origen (%.1f, %.1f): %s"
               % (pw, ph, self._origin[0], self._origin[1], "CABE" if ok else "NO CABE"))
        self.statusChanged.emit(txt, ok)

    # ---- mapeo mm <-> pixeles (origen abajo-izquierda) ----
    def _scale(self):
        w, h = self._work
        m = self.MARGIN
        return min((self.width() - 2 * m) / max(w, 1e-6),
                   (self.height() - 2 * m) / max(h, 1e-6))

    def _to_px(self, x, y, s):
        return self.MARGIN + x * s, self.height() - self.MARGIN - y * s

    def _to_mm(self, px, py, s):
        return (px - self.MARGIN) / s, (self.height() - self.MARGIN - py) / s

    # ---- eventos ----
    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        s = self._scale()
        if s <= 0:
            return
        x, y = self._to_mm(ev.position().x(), ev.position().y(), s)
        self.originPicked.emit(round(x, 2), round(y, 2))

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(20, 20, 22))
        s = self._scale()
        w, h = self._work
        x0, y0 = self._to_px(0, 0, s)
        x1, y1 = self._to_px(w, h, s)
        area = QRectF(QPointF(x0, y1), QPointF(x1, y0))
        p.fillRect(area, QColor(255, 255, 255, 14))

        # cuadricula cada 10 mm
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        for xi in range(0, int(w) + 1, 10):
            px, _ = self._to_px(xi, 0, s)
            p.drawLine(QPointF(px, y1), QPointF(px, y0))
        for yi in range(0, int(h) + 1, 10):
            _, py = self._to_px(0, yi, s)
            p.drawLine(QPointF(x0, py), QPointF(x1, py))

        # borde del area de trabajo
        p.setPen(QPen(QColor(10, 132, 255), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(area)

        ox, oy = self._origin
        if self._paths and self._bounds:
            minx, miny, maxx, maxy = self._bounds
            ok = self.fits()
            color = QColor(39, 201, 63) if ok else QColor(255, 95, 86)
            p.setPen(QPen(color, 1.2))
            for path in self._paths:
                if len(path) < 2:
                    continue
                poly = QPolygonF([QPointF(*self._to_px(pt[0] - minx + ox,
                                                       pt[1] - miny + oy, s))
                                  for pt in path])
                p.drawPolyline(poly)
            pw, ph = maxx - minx, maxy - miny
            bx0, by0 = self._to_px(ox, oy, s)
            bx1, by1 = self._to_px(ox + pw, oy + ph, s)
            p.setPen(QPen(color, 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(QPointF(bx0, by1), QPointF(bx1, by0)))

        # marcador de origen
        ax, ay = self._to_px(ox, oy, s)
        p.setPen(QPen(QColor(255, 189, 46), 2))
        p.drawLine(QPointF(ax - 8, ay), QPointF(ax + 8, ay))
        p.drawLine(QPointF(ax, ay - 8), QPointF(ax, ay + 8))

        p.setPen(QColor(142, 142, 147))
        p.drawText(QPointF(self.MARGIN, 16),
                   "Area %.0f x %.0f mm   |   clic = fijar origen" % (w, h))
        p.end()



from PySide6.QtWidgets import QWidget as _QW
from PySide6.QtGui import QPainter as _QP, QPen as _QPen, QColor as _QC
from PySide6.QtCore import Qt as _Qt, Signal as _Sig, QPointF as _QPF, QRectF as _QRF


class ExcellonPreview(_QW):
    originPicked = _Sig(float, float)
    WORK = (170.0, 170.0)
    MARGIN = 20

    def __init__(self, drl_text, parent=None):
        super().__init__(parent)
        from excellon_parser import ExcellonParser
        try:
            self._holes = ExcellonParser().parse(drl_text) or []
        except Exception:
            self._holes = []
        self._origin = (0.0, 0.0)
        self._work = self.WORK
        self._zoom = 1.0
        self._pan = [0.0, 0.0]
        self._drag = None
        self.setMinimumSize(360, 360)
        self.setMouseTracking(False)

    def set_origin(self, x, y):
        self._origin = (x, y)
        self.update()

    def set_holes(self, holes):
        self._holes = list(holes)
        self.update()

    def set_work(self, w, h):
        self._work = (max(1.0, float(w)), max(1.0, float(h)))
        self.update()

    def _base_scale(self):
        w, h = self._work
        aw = max(10, self.width() - 2 * self.MARGIN)
        ah = max(10, self.height() - 2 * self.MARGIN)
        return min(aw / w, ah / h)

    def _scale(self):
        return self._base_scale() * self._zoom

    def _to_px(self, x, y, s):
        return (self.MARGIN + self._pan[0] + x * s,
                self.height() - self.MARGIN - self._pan[1] - y * s)

    def _to_mm(self, px, py, s):
        return ((px - self.MARGIN - self._pan[0]) / s,
                (self.height() - self.MARGIN - self._pan[1] - py) / s)

    def center_on_holes(self):
        if not self._holes:
            self._pan = [0.0, 0.0]
            self._zoom = 1.0
            self.update()
            return
        xs = [h[0] for h in self._holes]
        ys = [h[1] for h in self._holes]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        s = self._scale()
        self._pan = [self.width() / 2.0 - self.MARGIN - cx * s,
                     self.height() / 2.0 - self.MARGIN - cy * s]
        self.update()

    def wheelEvent(self, ev):
        factor = 1.2 if ev.angleDelta().y() > 0 else 1 / 1.2
        new_zoom = max(0.2, min(40.0, self._zoom * factor))
        pos = ev.position()
        s_old = self._scale()
        mx, my = self._to_mm(pos.x(), pos.y(), s_old)
        self._zoom = new_zoom
        s_new = self._scale()
        self._pan[0] = pos.x() - self.MARGIN - mx * s_new
        self._pan[1] = self.height() - self.MARGIN - pos.y() - my * s_new
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() in (_Qt.RightButton, _Qt.MiddleButton):
            self._drag = (ev.position().x(), ev.position().y())
            return
        if ev.button() == _Qt.LeftButton:
            s = self._scale()
            x, y = self._to_mm(ev.position().x(), ev.position().y(), s)
            w, h = self._work
            x = max(0.0, min(w, x))
            y = max(0.0, min(h, y))
            self._origin = (x, y)
            self.originPicked.emit(x, y)
            self.update()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            px, py = ev.position().x(), ev.position().y()
            dx = px - self._drag[0]
            dy = py - self._drag[1]
            self._pan[0] += dx
            self._pan[1] -= dy
            self._drag = (px, py)
            self.update()

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def mouseDoubleClickEvent(self, ev):
        self.center_on_holes()

    def paintEvent(self, _ev):
        p = _QP(self)
        p.setRenderHint(_QP.Antialiasing)
        p.fillRect(self.rect(), _QC(20, 20, 22))
        s = self._scale()
        w, h = self._work
        x0, y0 = self._to_px(0, 0, s)
        x1, y1 = self._to_px(w, h, s)
        area = _QRF(_QPF(x0, y1), _QPF(x1, y0))
        p.fillRect(area, _QC(255, 255, 255, 14))
        p.setPen(_QPen(_QC(10, 132, 255), 1.5))
        p.setBrush(_Qt.NoBrush)
        p.drawRect(area)

        ox, oy = self._origin
        if self._holes:
            minx = min(hh[0] for hh in self._holes)
            miny = min(hh[1] for hh in self._holes)
            maxx = max(hh[0] for hh in self._holes)
            maxy = max(hh[1] for hh in self._holes)
            p.setPen(_QPen(_QC(39, 201, 63), 1.2))
            for (hx, hy, d) in self._holes:
                cx, cy = self._to_px(hx - minx + ox, hy - miny + oy, s)
                r = max(1.5, (d / 2.0) * s)
                p.drawEllipse(_QPF(cx, cy), r, r)
            bx0, by0 = self._to_px(ox, oy, s)
            bx1, by1 = self._to_px(ox + (maxx - minx), oy + (maxy - miny), s)
            p.setPen(_QPen(_QC(39, 201, 63), 1, _Qt.DashLine))
            p.drawRect(_QRF(_QPF(bx0, by1), _QPF(bx1, by0)))

        ax, ay = self._to_px(ox, oy, s)
        p.setPen(_QPen(_QC(255, 189, 46), 2))
        p.drawLine(_QPF(ax - 8, ay), _QPF(ax + 8, ay))
        p.drawLine(_QPF(ax, ay - 8), _QPF(ax, ay + 8))

        p.setPen(_QC(142, 142, 147))
        p.drawText(_QPF(self.MARGIN, 16),
                   "Taladros: %d   |   Area %.0f x %.0f mm   |   zoom %.1fx   |   rueda = zoom, arrastrar derecho = mover"
                   % (len(self._holes), w, h, self._zoom))
        p.end()
