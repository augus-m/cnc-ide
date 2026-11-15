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
