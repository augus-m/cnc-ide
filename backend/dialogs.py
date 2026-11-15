"""Dialogos: conectar al puerto y ajustes. Con conversion de tipo segura."""

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QSpinBox, QDoubleSpinBox, QPushButton,
    QCheckBox, QMessageBox, QColorDialog, QSlider,
    QWidget, QTabWidget,
)
from PySide6.QtGui import QColor

from serial_worker import list_ports


DEFAULTS = {
    "work_area_x": 200.0,
    "work_area_y": 200.0,
    "bg_0":       "#0d0e12",
    "bg_1":       "#16171c",
    "bg_2":       "#1c1e24",
    "bg_3":       "#23252c",
    "border":     "#2a2d36",
    "text_0":     "#e8e8eb",
    "text_1":     "#9aa0ac",
    "text_2":     "#5d6270",
    "accent":     "#ff4422",
    "green":      "#4ade80",
    "yellow":     "#fbbf24",
    "red":        "#ef4444",
    "blue":       "#60a5fa",
    "axis_color": "#a855f7",
    "grid_alpha": 12,
    "anim_speed": 0.15,
    "show_cursor_coord": True,
    "show_grid": True,
    "show_axes": True,
    "smooth_anim": True,
}


def _settings():
    return QSettings("CNC Proyect", "CNC IDE Web")


def _get(key):
    """Lee un valor de QSettings con conversion de tipo correcta."""
    val = _settings().value(f"cfg_{key}", DEFAULTS[key])

    # Convertir segun el tipo del default
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        return bool(val)
    if isinstance(default, float):
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(default)
    if isinstance(default, int):
        try:
            return int(val)
        except (ValueError, TypeError):
            return int(default)
    return val


def _set(key, value):
    _settings().setValue(f"cfg_{key}", value)


class ColorButton(QPushButton):
    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setFixedHeight(28)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._pick)

    def _update_style(self):
        color = _get(self.key)
        self.setText(color)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                color: white;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 4px 12px;
                font-family: monospace;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{ border-color: #ff4422; }}
        """)

    def _pick(self):
        current = QColor(_get(self.key))
        c = QColorDialog.getColor(current, self, f"Color para {self.key}")
        if c.isValid():
            _set(self.key, c.name())
            self._update_style()


class ConnectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conectar a la CNC")
        self.setMinimumWidth(420)

        self.settings = _settings()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Conexion serie")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        hint = QLabel("Selecciona el puerto donde esta conectada la CNC.")
        hint.setStyleSheet("color: #5d6270; font-size: 11px;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)

        self.port_combo = QComboBox()
        self._refresh_ports()
        form.addRow("Puerto:", self.port_combo)

        refresh_btn = QPushButton("Refrescar")
        refresh_btn.setObjectName("DlgSecondary")
        refresh_btn.clicked.connect(self._refresh_ports)
        form.addRow("", refresh_btn)

        self.baud_combo = QComboBox()
        for b in [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]:
            self.baud_combo.addItem(str(b), b)
        self.baud_combo.setCurrentText("115200")
        form.addRow("Baudrate:", self.baud_combo)

        layout.addLayout(form)
        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.setObjectName("DlgSecondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("Conectar")
        ok.setObjectName("DlgPrimary")
        ok.setDefault(True)
        ok.clicked.connect(self._on_ok)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        last_port = self.settings.value("last_port", "", type=str)
        if last_port:
            idx = self.port_combo.findData(last_port)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)

        last_baud = self.settings.value("last_baud", "115200", type=str)
        idx = self.baud_combo.findText(last_baud)
        if idx >= 0:
            self.baud_combo.setCurrentIndex(idx)

    def _refresh_ports(self):
        self.port_combo.clear()
        ports = list_ports()
        if not ports:
            self.port_combo.addItem("(ningun puerto detectado)", None)
            return
        for p in ports:
            label = f"{p['device']}  -  {p['description']}"
            self.port_combo.addItem(label, p["device"])

    def _on_ok(self):
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "Sin puerto", "No hay ningun puerto seleccionado.")
            return
        self.settings.setValue("last_port", port)
        self.settings.setValue("last_baud", self.baud_combo.currentText())
        self.accept()

    def get_values(self):
        return (self.port_combo.currentData(), self.baud_combo.currentData())


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajustes")
        self.setMinimumSize(560, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Ajustes de CNC IDE")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._tab_connection(), "Conexion")
        tabs.addTab(self._tab_machine(), "Maquina")
        tabs.addTab(self._tab_viewer(), "Visor 2D")
        tabs.addTab(self._tab_colors(), "Colores")
        tabs.addTab(self._tab_console(), "Consola")
        layout.addWidget(tabs)

        buttons = QHBoxLayout()
        buttons.addStretch()

        reset = QPushButton("Restaurar por defecto")
        reset.setObjectName("DlgSecondary")
        reset.clicked.connect(self._reset_defaults)
        buttons.addWidget(reset)

        cancel = QPushButton("Cancelar")
        cancel.setObjectName("DlgSecondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        save = QPushButton("Guardar")
        save.setObjectName("DlgPrimary")
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        buttons.addWidget(save)

        layout.addLayout(buttons)

    # ---------------- Pestana: Conexion ----------------
    def _tab_connection(self):
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        s = _settings()
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(50, 5000)
        self.poll_spin.setSingleStep(50)
        self.poll_spin.setSuffix(" ms")
        self.poll_spin.setValue(s.value("poll_interval", 500, type=int))
        layout.addRow("Intervalo de polling (M114):", self.poll_spin)

        self.autoconnect_check = QCheckBox("Conectar al ultimo puerto al arrancar")
        self.autoconnect_check.setChecked(s.value("autoconnect", False, type=bool))
        layout.addRow("", self.autoconnect_check)

        return w

    # ---------------- Pestana: Maquina ----------------
    def _tab_machine(self):
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        self.area_x_spin = QDoubleSpinBox()
        self.area_x_spin.setRange(10.0, 5000.0)
        self.area_x_spin.setSingleStep(10.0)
        self.area_x_spin.setDecimals(1)
        self.area_x_spin.setSuffix(" mm")
        self.area_x_spin.setValue(_get("work_area_x"))
        layout.addRow("Area de trabajo X:", self.area_x_spin)

        self.area_y_spin = QDoubleSpinBox()
        self.area_y_spin.setRange(10.0, 5000.0)
        self.area_y_spin.setSingleStep(10.0)
        self.area_y_spin.setDecimals(1)
        self.area_y_spin.setSuffix(" mm")
        self.area_y_spin.setValue(_get("work_area_y"))
        layout.addRow("Area de trabajo Y:", self.area_y_spin)

        info = QLabel(
            "Estas dimensiones se usan para:\n"
            "  - Dibujar los ejes del visor 2D\n"
            "  - El boundary check del G-code"
        )
        info.setStyleSheet("color: #5d6270; font-size: 11px;")
        layout.addRow("", info)

        return w

    # ---------------- Pestana: Visor 2D ----------------
    def _tab_viewer(self):
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        self.smooth_anim_check = QCheckBox("Animacion suave")
        self.smooth_anim_check.setChecked(_get("smooth_anim"))
        layout.addRow("", self.smooth_anim_check)

        self.anim_speed_slider = QSlider(Qt.Horizontal)
        self.anim_speed_slider.setRange(5, 50)
        self.anim_speed_slider.setValue(int(_get("anim_speed") * 100))
        self.anim_speed_label = QLabel(f"{_get('anim_speed') * 100:.0f}%")
        self.anim_speed_slider.valueChanged.connect(
            lambda v: self.anim_speed_label.setText(f"{v}%")
        )
        row = QHBoxLayout()
        row.addWidget(self.anim_speed_slider)
        row.addWidget(self.anim_speed_label)
        layout.addRow("Velocidad de animacion:", row)

        self.show_grid_check = QCheckBox("Mostrar grid")
        self.show_grid_check.setChecked(_get("show_grid"))
        layout.addRow("", self.show_grid_check)

        self.grid_alpha_spin = QSpinBox()
        self.grid_alpha_spin.setRange(2, 60)
        self.grid_alpha_spin.setSuffix(" / 255")
        self.grid_alpha_spin.setValue(_get("grid_alpha"))
        layout.addRow("Opacidad del grid:", self.grid_alpha_spin)

        self.show_axes_check = QCheckBox("Mostrar ejes X/Y")
        self.show_axes_check.setChecked(_get("show_axes"))
        layout.addRow("", self.show_axes_check)

        self.show_cursor_coord_check = QCheckBox("Mostrar coordenadas junto a la herramienta")
        self.show_cursor_coord_check.setChecked(_get("show_cursor_coord"))
        layout.addRow("", self.show_cursor_coord_check)

        return w

    # ---------------- Pestana: Colores ----------------
    def _tab_colors(self):
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        colors = [
            ("axis_color", "Color de los ejes"),
            ("accent", "Acento (naranja)"),
            ("green", "Herramienta / Verde"),
            ("red", "Alarma / Rojo"),
            ("bg_0", "Fondo"),
            ("text_0", "Texto principal"),
        ]
        for key, label in colors:
            btn = ColorButton(key)
            layout.addRow(label + ":", btn)

        return w

    # ---------------- Pestana: Consola ----------------
    def _tab_console(self):
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        s = _settings()

        self.autoscroll_check = QCheckBox("Autoscroll al recibir lineas")
        self.autoscroll_check.setChecked(s.value("autoscroll", True, type=bool))
        layout.addRow("", self.autoscroll_check)

        self.timestamps_check = QCheckBox("Mostrar timestamps")
        self.timestamps_check.setChecked(s.value("timestamps", True, type=bool))
        layout.addRow("", self.timestamps_check)

        self.echo_check = QCheckBox("Eco de comandos enviados")
        self.echo_check.setChecked(s.value("echo", True, type=bool))
        layout.addRow("", self.echo_check)

        return w

    # ---------------- Guardar ----------------
    def _on_save(self):
        s = _settings()
        s.setValue("poll_interval", self.poll_spin.value())
        s.setValue("autoconnect", self.autoconnect_check.isChecked())
        s.setValue("autoscroll", self.autoscroll_check.isChecked())
        s.setValue("timestamps", self.timestamps_check.isChecked())
        s.setValue("echo", self.echo_check.isChecked())

        _set("work_area_x", self.area_x_spin.value())
        _set("work_area_y", self.area_y_spin.value())
        _set("smooth_anim", self.smooth_anim_check.isChecked())
        _set("anim_speed", self.anim_speed_slider.value() / 100.0)
        _set("show_grid", self.show_grid_check.isChecked())
        _set("grid_alpha", self.grid_alpha_spin.value())
        _set("show_axes", self.show_axes_check.isChecked())
        _set("show_cursor_coord", self.show_cursor_coord_check.isChecked())

        # Asegurar que se escriben a disco
        s.sync()

        self.accept()

    def _reset_defaults(self):
        for key, value in DEFAULTS.items():
            _set(key, value)
        _settings().sync()
        QMessageBox.information(
            self, "Restaurado",
            "Valores por defecto guardados.\nCierra y reabre la app."
        )
        self.accept()

    def get_settings(self):
        s = _settings()
        return {
            "poll_interval": s.value("poll_interval", 500, type=int),
            "autoconnect": s.value("autoconnect", False, type=bool),
            "autoscroll": s.value("autoscroll", True, type=bool),
            "timestamps": s.value("timestamps", True, type=bool),
            "echo": s.value("echo", True, type=bool),
        }


class GlassDialog(QDialog):
    """Dialogo estilo cristal: sin marco del sistema, botones tipo macOS
    (cerrar, minimizar, zoom) y arrastre desde la barra superior."""

    def __init__(self, title, parent=None):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect,
                                       QHBoxLayout, QLabel, QPushButton, QVBoxLayout)
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle(title)
        self._drag_offset = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)   # margen para la sombra

        self.card = QFrame()
        self.card.setObjectName("GlassCard")
        self.card.setStyleSheet(
            "#GlassCard { background-color: rgba(30, 30, 35, 0.92);"
            " border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 14px; }"
            "QLabel { color: #e8e8eb; background: transparent; }"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        body = QVBoxLayout(self.card)
        body.setContentsMargins(0, 0, 0, 18)
        body.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(14, 10, 14, 4)
        dots = QHBoxLayout()
        dots.setSpacing(8)
        for color, handler in (("#FF5F56", self.reject),
                               ("#FFBD2E", self.showMinimized),
                               ("#27C93F", lambda: None)):
            b = QPushButton()
            b.setFixedSize(12, 12)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background-color: {color}; border: none; border-radius: 6px; }}"
                "QPushButton:hover { opacity: 0.8; }")
            b.clicked.connect(handler)
            dots.addWidget(b)
        bar.addLayout(dots)
        lbl = QLabel(title)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #8e8e93; font-size: 12px; font-weight: 600; background: transparent;")
        bar.addWidget(lbl, 1)
        spacer = QFrame()
        spacer.setFixedWidth(12 * 3 + 16)
        bar.addWidget(spacer)
        body.addLayout(bar)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(20, 12, 20, 0)
        self.content_layout.setSpacing(12)
        body.addLayout(self.content_layout)

        self._title_bar_height = 40

    def mousePressEvent(self, event):
        from PySide6.QtCore import Qt
        if event.button() == Qt.LeftButton and event.position().y() <= self._title_bar_height:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)
