"""PDF / imagen -> G-code para dibujar con la pluma (esqueleto de trazos).

Flujo: pagina (PDF) o imagen -> recorte elegido -> binarizar -> adelgazar
(Zhang-Suen) -> trazos de 1 pixel -> milimetros -> G-code con servo.
"""
import os
import shutil
import subprocess
import tempfile

import cv2
import numpy as np
from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QDoubleSpinBox, QSpinBox, QPushButton, QFileDialog, QMessageBox,
    QDialogButtonBox, QComboBox, QWidget,
)


def zhang_suen(img):
    """Adelgazamiento Zhang-Suen. img: 0/1 uint8. Devuelve 0/1."""
    im = (img > 0).astype(np.uint8)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            P = np.pad(im, 1)
            p2 = P[:-2, 1:-1]; p3 = P[:-2, 2:]; p4 = P[1:-1, 2:]; p5 = P[2:, 2:]
            p6 = P[2:, 1:-1]; p7 = P[2:, :-2]; p8 = P[1:-1, :-2]; p9 = P[:-2, :-2]
            B = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
            if step == 0:
                c1 = (p2 * p4 * p6) == 0
                c2 = (p4 * p6 * p8) == 0
            else:
                c1 = (p2 * p4 * p8) == 0
                c2 = (p2 * p6 * p8) == 0
            m = (im == 1) & (B >= 2) & (B <= 6) & (A == 1) & c1 & c2
            if m.any():
                im[m] = 0
                changed = True
    return im


def render_pdf_page(pdf_path, page, dpi):
    """Renderiza una pagina del PDF a escala de grises con pdftoppm."""
    if shutil.which("pdftoppm") is None:
        raise RuntimeError(
            "No se encontro 'pdftoppm' (poppler-utils). En Windows instala "
            "Poppler para Windows y agrega su carpeta 'bin' al PATH para "
            "poder abrir archivos PDF."
        )
    tmp = tempfile.mkdtemp()
    base = os.path.join(tmp, "pagina")
    subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page),
                    "-gray", "-png", "-singlefile", pdf_path, base], check=True)
    img = cv2.imread(base + ".png", cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError("No se pudo renderizar la pagina del PDF")
    return img


def morph_skeleton(bw):
    """Esqueleto morfologico con OpenCV (rapido, en C). bw: 0/255 uint8."""
    img = (bw > 0).astype(np.uint8) * 255
    skel = np.zeros_like(img)
    k = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    while True:
        eroded = cv2.erode(img, k)
        opened = cv2.dilate(eroded, k)
        skel = cv2.bitwise_or(skel, cv2.subtract(img, opened))
        img = eroded
        if cv2.countNonZero(img) == 0:
            break
    return skel


def skeleton(gray, rect, threshold):
    """Esqueleto (1 pixel) de la zona rect=(x0,y0,x1,y1) de la imagen gris."""
    x0, y0, x1, y1 = rect
    crop = gray[y0:y1, x0:x1]
    _, bw = cv2.threshold(crop, threshold, 255, cv2.THRESH_BINARY_INV)
    # Cierre morfologico: une huecos de 1-2 px dentro de la misma linea
    # (asi el esqueleto no se corta en pedazos).
    kernel = np.ones((5, 5), np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
    return morph_skeleton(bw) > 0


def trace(skel, min_spur_px=0.0):
    """Convierte el esqueleto en trazos [(y, x), ...] sin perder ramas.

    Cada tramo entre extremos o uniones (pixeles con != 2 vecinos) es un trazo.
    Los bucles cerrados (sin uniones) se recorren aparte.
    """
    H, W = skel.shape
    ys, xs = np.nonzero(skel)
    pts = list(zip(ys.tolist(), xs.tolist()))
    S = set(pts)

    def nbrs(p):
        y, x = p
        out = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if (dy or dx) and (y + dy, x + dx) in S:
                    out.append((y + dy, x + dx))
        return out

    nb = {p: nbrs(p) for p in pts}

    def is_node(p):
        return len(nb[p]) != 2

    used_edges = set()
    visited = set()
    paths = []

    # 1) tramos que parten de extremos o uniones
    for p in pts:
        if not is_node(p):
            continue
        for q in nb[p]:
            key = frozenset((p, q))
            if key in used_edges:
                continue
            path = [p, q]
            used_edges.add(key)
            prev, cur = p, q
            while not is_node(cur):
                nxt = [r for r in nb[cur] if r != prev]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                used_edges.add(frozenset((prev, cur)))
                path.append(cur)
            visited.update(path)
            if len(path) >= 2:
                paths.append(path)

    # 2) bucles cerrados que no tienen ningun extremo ni union
    for p in pts:
        if p in visited:
            continue
        path = [p]
        visited.add(p)
        prev, cur = None, p
        while True:
            nxt = [r for r in nb[cur] if r != prev and r not in visited]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            visited.add(cur)
            path.append(cur)
        if len(path) >= 2:
            paths.append(path)

    # Quita ramas espurias: tramos que terminan en un extremo libre y son
    # mas cortos que min_spur_px (ganchos y picos de las uniones).
    def is_leaf(p):
        return len(nb[p]) == 1

    kept = []
    for pth in paths:
        if min_spur_px > 0 and len(pth) < min_spur_px:
            continue
        kept.append(pth)
    return [[(y, x) for (y, x) in pth] for pth in kept]


def paths_to_gcode(paths, skel_shape, mm_per_px, origin, tol_mm,
                   feed, servo_up, servo_down, servo_speed, min_len=1.0):
    """Trazos en pixeles -> lineas G-code en mm, con la esquina inferior en origin."""
    H = skel_shape[0]
    cv2_tol = max(0.3, tol_mm / mm_per_px)
    mm = []
    for p in paths:
        arr = np.array([[x * mm_per_px, (H - 1 - y) * mm_per_px] for y, x in p], np.float32)
        mm.append(arr)
    if not mm:
        raise RuntimeError("No se encontraron trazos en la zona seleccionada")
    # Descarta solo ruido: trazos menores de 1 mm (los punteados de la figura son cortos).
    mm = [a for a in mm if np.sum(np.hypot(*np.diff(a, axis=0).T)) >= min_len]
    if not mm:
        raise RuntimeError("No quedan trazos: sube el umbral o revisa la zona")
    # Orden de dibujo: cada trazo empieza en el extremo mas cercano a la pluma
    # (vecino mas proximo), para no saltar de un lado a otro de la pagina.
    ordered = []
    pen = np.array([0.0, 0.0], np.float32)
    rest = list(mm)
    while rest:
        best = None
        for i, a in enumerate(rest):
            d0 = np.hypot(*(a[0] - pen))
            d1 = np.hypot(*(a[-1] - pen))
            d = min(d0, d1)
            if best is None or d < best[0]:
                best = (d, i, d1 < d0)
        _, i, rev = best
        a = rest.pop(i)
        if rev:
            a = a[::-1]
        ordered.append(a)
        pen = a[-1]
    mm = ordered
    minx = min(a[:, 0].min() for a in mm)
    miny = min(a[:, 1].min() for a in mm)
    ox, oy = origin[0] - minx, origin[1] - miny
    lines = ["G21", "G90", f"; Dibujo desde imagen/PDF: {len(mm)} trazos",
             f"M280 S{servo_up:g} F300"]
    for a in mm:
        ap = cv2.approxPolyDP(a.reshape(-1, 1, 2), cv2_tol * mm_per_px / mm_per_px, False).reshape(-1, 2)
        ap[:, 0] += ox
        ap[:, 1] += oy
        lines.append(f"G0 X{ap[0][0]:.3f} Y{ap[0][1]:.3f}")
        lines.append(f"M280 S{servo_down:g} F{servo_speed:g}")
        for x, y in ap[1:]:
            lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feed:g}")
        lines.append(f"M280 S{servo_up:g} F300")
    lines.append("G0 X0 Y0")
    return lines, mm, (ox, oy)


class SelectView(QLabel):
    """Muestra la pagina/imagen y permite dibujar un rectangulo de seleccion."""
    rectChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img = None
        self._pix = None
        self._scale = 1.0
        self._sel = None
        self._drag = None
        self.setMinimumSize(520, 520)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#17171a;border-radius:10px;")

    def set_image(self, gray):
        self._img = gray
        h, w = gray.shape
        qimg = QImage(gray.data, w, h, w, QImage.Format_Grayscale8).copy()
        self._pix = QPixmap.fromImage(qimg)
        self._sel = None
        self._fit()
        self.update()

    def _fit(self):
        if self._img is None:
            return
        h, w = self._img.shape
        self._scale = min((self.width() - 12) / w, (self.height() - 12) / h)

    def resizeEvent(self, ev):
        self._fit()
        super().resizeEvent(ev)

    def image_rect(self):
        """Rectangulo seleccionado en pixeles de la imagen (x0,y0,x1,y1)."""
        if self._img is None:
            return None
        if self._sel is None:
            h, w = self._img.shape
            return (0, 0, w, h)
        return self._sel

    def _to_img(self, p):
        if self._img is None:
            return None
        h, w = self._img.shape
        ox = (self.width() - w * self._scale) / 2
        oy = (self.height() - h * self._scale) / 2
        x = int((p.x() - ox) / self._scale)
        y = int((p.y() - oy) / self._scale)
        return (max(0, min(w, x)), max(0, min(h, y)))

    def mousePressEvent(self, ev):
        if self._img is None or ev.button() != Qt.LeftButton:
            return
        q = self._to_img(ev.position().toPoint())
        self._drag = q
        self._sel = (q[0], q[1], q[0], q[1])

    def mouseMoveEvent(self, ev):
        if self._drag is None:
            return
        q = self._to_img(ev.position().toPoint())
        x0, y0 = self._drag
        self._sel = (min(x0, q[0]), min(y0, q[1]), max(x0, q[0]), max(y0, q[1]))
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is None:
            return
        self._drag = None
        s = self._sel
        if s and (s[2] - s[0] < 3 or s[3] - s[1] < 3):
            self._sel = None
        self.rectChanged.emit()
        self.update()

    def set_overlay(self, mm_lines_px=None):
        """Dibuja encima los trazos (lista de arrays Nx2 en pixeles de la imagen)."""
        self._overlay = mm_lines_px
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._pix is None:
            return
        p = QPainter(self)
        h, w = self._img.shape
        ox = (self.width() - w * self._scale) / 2
        oy = (self.height() - h * self._scale) / 2
        p.drawPixmap(QRect(int(ox), int(oy), int(w * self._scale), int(h * self._scale)), self._pix)
        if getattr(self, "_overlay", None):
            p.setPen(QPen(QColor(39, 201, 63), 1))
            for arr in self._overlay:
                for (x0, y0), (x1, y1) in zip(arr[:-1], arr[1:]):
                    p.drawLine(QPoint(int(ox + x0 * self._scale), int(oy + y0 * self._scale)),
                               QPoint(int(ox + x1 * self._scale), int(oy + y1 * self._scale)))
        if self._sel is not None:
            x0, y0, x1, y1 = self._sel
            p.setPen(QPen(QColor(10, 132, 255), 2, Qt.DashLine))
            p.drawRect(QRect(int(ox + x0 * self._scale), int(oy + y0 * self._scale),
                             int((x1 - x0) * self._scale), int((y1 - y0) * self._scale)))
        p.end()


def open_image_dialog(parent):
    """Abre el PDF o imagen, deja elegir la zona y devuelve (lineas_gcode, nombre) o None."""
    path, _ = QFileDialog.getOpenFileName(
        parent, "Abrir PDF o imagen", "",
        "PDF o imagen (*.pdf *.png *.jpg *.jpeg *.bmp);;Todos (*)")
    if not path:
        return None
    is_pdf = path.lower().endswith(".pdf")
    dlg = QDialog(parent)
    dlg.setWindowTitle("Convertir PDF / imagen a G-code")
    dlg.resize(1100, 700)
    root = QHBoxLayout(dlg)

    view = SelectView(dlg)
    root.addWidget(view, 3)

    side = QVBoxLayout()
    root.addLayout(side, 2)
    info = QLabel(os.path.basename(path))
    side.addWidget(info)

    g_src = QGroupBox("Origen")
    f_src = QFormLayout(g_src)
    sp_page = QSpinBox()
    sp_page.setRange(1, 999)
    sp_page.setValue(1)
    sp_dpi = QSpinBox()
    sp_dpi.setRange(72, 1200)
    sp_dpi.setValue(600)
    sp_thr = QSpinBox()
    sp_thr.setRange(0, 255)
    sp_thr.setValue(200)
    sp_real = QDoubleSpinBox()
    sp_real.setRange(1.0, 1000.0)
    sp_real.setDecimals(1)
    sp_real.setValue(50.0)
    sp_real.setSuffix(" mm")
    sp_real.setToolTip("Ancho real en mm de la zona seleccionada (solo para imagenes).")
    f_src.addRow("Pagina:", sp_page)
    f_src.addRow("Resolucion (ppp):", sp_dpi)
    f_src.addRow("Umbral de trazo:", sp_thr)
    if not is_pdf:
        f_src.addRow("Ancho real de la zona:", sp_real)
    side.addWidget(g_src)

    g_out = QGroupBox("Salida")
    f_out = QFormLayout(g_out)
    sp_fac = QDoubleSpinBox()
    sp_fac.setRange(0.1, 10.0)
    sp_fac.setSingleStep(0.5)
    sp_fac.setDecimals(2)
    sp_fac.setValue(1.0)
    sp_fac.setSuffix(" x")
    sp_ox = QDoubleSpinBox()
    sp_ox.setRange(0.0, 500.0)
    sp_ox.setValue(4.0)
    sp_ox.setSuffix(" mm")
    sp_oy = QDoubleSpinBox()
    sp_oy.setRange(0.0, 500.0)
    sp_oy.setValue(4.0)
    sp_oy.setSuffix(" mm")
    sp_tol = QDoubleSpinBox()
    sp_tol.setRange(0.05, 2.0)
    sp_tol.setSingleStep(0.05)
    sp_tol.setDecimals(2)
    sp_tol.setValue(0.20)
    sp_min = QDoubleSpinBox()
    sp_min.setRange(0.1, 10.0)
    sp_min.setSingleStep(0.1)
    sp_min.setDecimals(2)
    sp_min.setValue(0.50)
    sp_min.setSuffix(" mm")
    sp_min.setToolTip("Trazos mas cortos que esto se descartan (ruido).")
    sp_tol.setSuffix(" mm")
    sp_feed = QDoubleSpinBox()
    sp_feed.setRange(10, 3000)
    sp_feed.setValue(300)
    sp_feed.setSuffix(" mm/min")
    sp_up = QDoubleSpinBox()
    sp_up.setRange(0, 180)
    sp_up.setValue(10)
    sp_down = QDoubleSpinBox()
    sp_down.setRange(0, 180)
    sp_down.setValue(45)
    sp_spd = QDoubleSpinBox()
    sp_spd.setRange(1, 240)
    sp_spd.setValue(44)
    f_out.addRow("Factor de tamano:", sp_fac)
    f_out.addRow("Origen X:", sp_ox)
    f_out.addRow("Origen Y:", sp_oy)
    f_out.addRow("Tolerancia de trazo:", sp_tol)
    f_out.addRow("Trazo minimo:", sp_min)
    f_out.addRow("Avance:", sp_feed)
    f_out.addRow("Servo arriba (S):", sp_up)
    f_out.addRow("Servo abajo (S):", sp_down)
    f_out.addRow("Velocidad bajada (F):", sp_spd)
    side.addWidget(g_out)

    lbl_size = QLabel("")
    lbl_size.setWordWrap(True)
    side.addWidget(lbl_size)
    side.addStretch(1)

    btn_prev = QPushButton("Vista previa")
    side.addWidget(btn_prev)
    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    btns.button(QDialogButtonBox.Ok).setText("Generar")
    side.addWidget(btns)

    state = {"gray": None, "dpi": 300}

    def load_page():
        if is_pdf:
            state["dpi"] = sp_dpi.value()
            state["gray"] = render_pdf_page(path, sp_page.value(), sp_dpi.value())
        else:
            state["gray"] = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if state["gray"] is None:
            raise RuntimeError("No se pudo abrir el archivo")
        view.set_image(state["gray"])
        view.set_overlay(None)

    def mm_per_px():
        rect = view.image_rect()
        rw = max(1, rect[2] - rect[0])
        if is_pdf:
            return 25.4 / state["dpi"] * sp_fac.value()
        return sp_real.value() / rw * sp_fac.value()

    def size_text():
        rect = view.image_rect()
        if rect is None:
            return ""
        mpp = mm_per_px()
        w = (rect[2] - rect[0]) * mpp
        h = (rect[3] - rect[1]) * mpp
        fits = (sp_ox.value() + w <= 170.0) and (sp_oy.value() + h <= 170.0)
        return (f"Zona: {w:.1f} x {h:.1f} mm en origen ({sp_ox.value():.1f}, {sp_oy.value():.1f}): "
                + ("CABE" if fits else "NO CABE en 170 x 170"))

    def generate():
        rect = view.image_rect()
        skel = skeleton(state["gray"], rect, sp_thr.value())
        paths = trace(skel, min_spur_px=sp_min.value() / mm_per_px())
        lines, mm, _ = paths_to_gcode(paths, skel.shape, mm_per_px(),
                                      (sp_ox.value(), sp_oy.value()), sp_tol.value(),
                                      sp_feed.value(), sp_up.value(), sp_down.value(),
                                      sp_spd.value(), min_len=sp_min.value())
        return lines, mm

    def preview():
        rect = view.image_rect()
        skel = skeleton(state["gray"], rect, sp_thr.value())
        paths = trace(skel)
        if not paths:
            lbl_size.setText(size_text() + "\nNo hay trazos: baja el umbral o cambia la zona")
            return
        # Trazos en pixeles de la imagen completa, para dibujarlos encima de la vista.
        x0, y0 = rect[0], rect[1]
        tol_px = max(0.5, sp_tol.value() / mm_per_px())
        overlay = []
        for p in paths:
            arr = np.array([[x + x0, y + y0] for y, x in p], np.float32).reshape(-1, 1, 2)
            overlay.append(cv2.approxPolyDP(arr, tol_px, False).reshape(-1, 2))
        view.set_overlay(overlay)
        lbl_size.setText(size_text() + f"\nVista previa: {len(paths)} trazos")

    def on_rect():
        lbl_size.setText(size_text())

    view.rectChanged.connect(on_rect)
    for w in (sp_fac, sp_ox, sp_oy):
        w.valueChanged.connect(lambda _v: lbl_size.setText(size_text()))
    btn_prev.clicked.connect(preview)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    sp_page.valueChanged.connect(lambda _v: load_page() if is_pdf else None)
    sp_dpi.valueChanged.connect(lambda _v: load_page() if is_pdf else None)

    try:
        load_page()
    except Exception as e:
        QMessageBox.critical(parent, "Error", str(e))
        return None
    lbl_size.setText(size_text())

    if dlg.exec() != QDialog.Accepted:
        return None
    try:
        lines, _ = generate()
    except Exception as e:
        QMessageBox.critical(parent, "Error", str(e))
        return None
    name = os.path.splitext(os.path.basename(path))[0] + ".gcode"
    return lines, name
