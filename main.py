#!/usr/bin/env python3
"""CNC Proyect: plantilla Liquid Glass conectada al backend PySide6."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, QTimer, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR / "backend"
MCP_DATA_DIR = APP_DIR / "data" / "mcp"
sys.path.insert(0, str(BACKEND_DIR))

from main_window import MainWindow as NativeBackendWindow  # noqa: E402
from serial_worker import list_ports  # noqa: E402
from gcode_model import parse_program  # noqa: E402


class CncWebBridge(QObject):
    """API acotada de JavaScript hacia las operaciones ya existentes en Python."""

    stateChanged = Signal(dict)
    pulseStatsChanged = Signal(dict)
    consoleChanged = Signal(str)
    programChanged = Signal(dict)
    positionChanged = Signal(float, float, float, bool, bool)

    def __init__(self, window: "LiquidGlassWindow"):
        super().__init__(window)
        self.window = window
        self.backend = window.backend
        self._last_console = None
        self._last_program_signature = None

    def _log(self, message: str, level: str = "info") -> None:
        self.backend._log_system(message, level=level)

    @Slot(result="QVariantList")
    def availablePorts(self) -> list[dict[str, str]]:
        return list_ports()

    @Slot(result="QVariantMap")
    def initialState(self) -> dict[str, Any]:
        return self.buildState()

    @Slot(result="QVariantMap")
    def currentProgram(self) -> dict[str, Any]:
        return self.buildProgram()

    @Slot(str, int)
    def connectToPort(self, port: str, baud: int) -> None:
        if self.backend.connected:
            self.backend.request_disconnect.emit()
            return
        if not port:
            self._log("Selecciona un puerto serie", level="error")
            return
        baud = max(1200, min(2_000_000, int(baud)))
        self.backend.settings.setValue("last_port", port)
        self.backend.settings.setValue("last_baud", str(baud))
        self._log(f"Conectando a {port} @ {baud}...")
        self.backend.request_connect.emit(port, baud)

    @Slot()
    def disconnectCnc(self) -> None:
        if self.backend.connected:
            self.backend.request_disconnect.emit()

    @Slot(bool)
    def setPolling(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.backend.polling_enabled != enabled:
            self.backend._toggle_polling()

    @Slot(bool, bool, bool)
    def setEnabledAxes(self, x_enabled: bool, y_enabled: bool, z_enabled: bool) -> None:
        self.backend.settings.setValue("axis_enabled_x", bool(x_enabled))
        self.backend.settings.setValue("axis_enabled_y", bool(y_enabled))
        self.backend.settings.setValue("axis_enabled_z", bool(z_enabled))
        self.backend.settings.sync()

    @Slot(bool)
    def setIgnoreZForXY(self, enabled: bool) -> None:
        self.backend.settings.setValue("ignore_z_for_xy", bool(enabled))
        self.backend.settings.sync()

    @staticmethod
    def _strip_z_motion(lines: list[str]) -> list[str]:
        """Prepara un programa XY: omite Z y limita rápidos a F50 mm/min."""
        result = []
        z_word = re.compile(r"(?<![A-Z])Z\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)", re.I)
        motion = re.compile(r"\bG0?\s*(?:G)?[0-3]\b", re.I)
        xy_word = re.compile(r"(?<![A-Z])[XY]\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)", re.I)
        m_word = re.compile(r"\bM\d+(?:\.\d+)?\b", re.I)
        for raw in lines:
            code, separator, comment = raw.partition(";")
            protected = []
            def hold(match):
                protected.append(match.group(0))
                return f"__COMMENT_{len(protected)-1}__"
            code = re.sub(r"\([^)]*\)", hold, code)
            code = z_word.sub("", code)
            code = re.sub(r"__COMMENT_(\d+)__", lambda m: protected[int(m.group(1))], code)
            if re.match(r"^\s*G0+\b", code, re.I) and xy_word.search(code):
                code = re.sub(r"^\s*G0+\b", "G1", code, count=1, flags=re.I)
                code = re.sub(r"(?<![A-Z])F\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)", "", code, flags=re.I)
                code = code.rstrip() + " F50.0"
            if motion.search(code) and not xy_word.search(code):
                code = " ".join(m_word.findall(code))
            output = re.sub(r"\s+", " ", code).strip()
            if separator:
                output = f"{output} ;{comment}" if output else f";{comment}"
            if output.strip():
                result.append(output)
        return result

    @Slot(str)
    def sendCommand(self, command: str) -> None:
        command = str(command).strip()
        if not command:
            return
        if command.upper() == "M112":
            self.backend._on_estop()
        else:
            self.backend._send_cmd(command)

    @Slot(str, int, float, int)
    def jogAxis(self, axis: str, direction: int, step_mm: float, speed: int) -> None:
        if axis not in {"X", "Y", "Z"}:
            self._log(f"Eje no válido: {axis}", level="error")
            return
        step_index = min(range(self.backend.step_combo.count()),
                         key=lambda i: abs(float(self.backend.step_combo.itemData(i)) - float(step_mm)))
        speed_index = min(range(self.backend.speed_combo.count()),
                          key=lambda i: abs(int(self.backend.speed_combo.itemData(i)) - int(speed)))
        self.backend.step_combo.setCurrentIndex(step_index)
        self.backend.speed_combo.setCurrentIndex(speed_index)
        self.backend._jog(axis, 1 if direction >= 0 else -1)

    @Slot(float)
    def setJogStep(self, step_mm: float) -> None:
        if self.backend.step_combo.count():
            idx = min(range(self.backend.step_combo.count()),
                      key=lambda i: abs(float(self.backend.step_combo.itemData(i)) - float(step_mm)))
            self.backend.step_combo.setCurrentIndex(idx)

    @Slot(int)
    def setJogSpeed(self, speed: int) -> None:
        if self.backend.speed_combo.count():
            idx = min(range(self.backend.speed_combo.count()),
                      key=lambda i: abs(int(self.backend.speed_combo.itemData(i)) - int(speed)))
            self.backend.speed_combo.setCurrentIndex(idx)

    @Slot(str)
    def setMotionMode(self, mode: str) -> None:
        idx = self.backend.mode_combo.findData(str(mode).upper())
        if idx >= 0:
            self.backend.mode_combo.setCurrentIndex(idx)

    @Slot(int)
    def setFeedOverride(self, value: int) -> None:
        self.backend.fo_slider.setValue(max(10, min(200, int(value))))

    @Slot(str, str, float, float)
    def machineCommand(self, name: str, command: str, x: float, y: float) -> None:
        del name, x, y
        self.sendCommand(command)

    @Slot(str)
    def startJob(self, editor_text: str) -> None:
        text = str(editor_text).strip()
        lines = [line.rstrip() for line in text.splitlines() if line.strip()] if text else list(self.backend.gcode_lines)
        report = self.preflightJob("\n".join(lines))
        if report["errors"]:
            self._log("Trabajo bloqueado: " + "; ".join(report["errors"]), level="error")
            return
        ignore_z = self.backend.settings.value("ignore_z_for_xy", False, type=bool)
        if ignore_z:
            lines = self._strip_z_motion(lines)
            if not lines:
                self._log("No quedan movimientos X/Y después de omitir Z", level="error")
                return
        if text or ignore_z:
            name = "Trabajo XY (Z omitido)" if ignore_z else "Editor G-code"
            self.backend._load_gcode(lines, name, add_to_list=False)
        if lines:
            # _on_start_job vuelve a leer el editor Qt oculto; sincronizarlo
            # evita que un programa antiguo sustituya el Gerber activo.
            self.backend.editor.load_lines(lines)
        self.backend._on_start_job()
        self.publishProgram()

    @Slot(str, result="QVariantMap")
    def preflightJob(self, source: str) -> dict[str, Any]:
        lines = [s for s in str(source).splitlines() if s.strip()]
        model = parse_program(lines)
        stats = model["stats"]
        errors, warnings = [], [f"Línea {w['line']}: {w['text']}" for w in model["warnings"]]
        if not lines:
            errors.append("El programa está vacío")
        if not self.backend.connected:
            errors.append("STM32 desconectado")
        if not getattr(self.backend, "_pos_valid", False):
            errors.append("Falta fijar el origen con homing ($H)")
        last_report = float(getattr(self.backend, "_last_position_reported", 0.0))
        freshness_limit = max(2.5, float(self.backend.poll_interval) / 1000.0 * 3.0)
        if self.backend.connected and (not getattr(self.backend, "_position_reported", False) or
                                       time.monotonic() - last_report > freshness_limit):
            errors.append("La posición de la placa no está actualizada; revisa el polling")
        has_z = any(abs(s[7] - s[6]) > 1e-6 for s in model["segments"])
        ignore_z = self.backend.settings.value("ignore_z_for_xy", False, type=bool)
        if has_z and ignore_z:
            warnings.append("Se omitirá Z; la herramienta queda a su altura física actual y los rápidos XY se limitan a G1 F50 mm/min")
        elif has_z:
            errors.append("El programa mueve Z; el firmware actual solo ejecuta X/Y. Activa ‘Ejecutar solo X/Y e ignorar Z’ para continuar")
        ax, ay = float(self.backend.canvas_2d.area_x), float(self.backend.canvas_2d.area_y)
        if stats["min_x"] is not None and (stats["min_x"] < 0 or stats["max_x"] > ax):
            errors.append(f"X fuera del área configurada (0–{ax:g} mm)")
        if stats["min_y"] is not None and (stats["min_y"] < 0 or stats["max_y"] > ay):
            errors.append(f"Y fuera del área configurada (0–{ay:g} mm)")
        if not model["segments"]:
            errors.append("No hay movimientos G0–G3 para ejecutar")
        for warning in model["warnings"]:
            if warning["code"] == "modal_motion":
                errors.append(f"Línea {warning['line']}: el firmware requiere G0/G1/G2/G3 explícito")
            elif warning["code"] == "unsupported_g":
                errors.append(f"Línea {warning['line']}: comando {warning['text']}")
        return {"ok": not errors, "errors": errors, "warnings": warnings,
                "segments": len(model["segments"]), "distance": stats["distance"]}

    @Slot()
    def pauseJob(self) -> None:
        self.backend._on_pause_job()

    @Slot()
    def stopJob(self) -> None:
        self.backend._on_stop_job()

    @Slot()
    def emergencyStop(self) -> None:
        self.backend._on_estop()

    @Slot()
    def toggleSimulation(self) -> None:
        self.backend._on_simulate_clicked()

    @Slot()
    def startSimulation(self) -> None:
        if not self.backend.simulator.running:
            self.backend._on_simulate_clicked()

    @Slot()
    def stopSimulation(self) -> None:
        if self.backend.simulator.running:
            self.backend._on_simulate_clicked()

    @Slot()
    def pauseSimulation(self) -> None:
        simulator = self.backend.simulator
        if not simulator.running:
            return
        simulator.paused = not simulator.paused
        if not simulator.paused:
            simulator._last_tick_time = time.monotonic()
        self.backend._log_system("Simulación reanudada" if not simulator.paused else "Simulación pausada")

    @Slot(int)
    def setSimulationSpeed(self, value: int) -> None:
        self.backend.sim_speed_slider.setValue(max(0, min(1000, int(value))))

    @Slot(str, bool)
    def setLayerVisibility(self, layer: str, visible: bool) -> None:
        checkbox = self.backend.layer_checkboxes.get(str(layer))
        if checkbox is not None:
            checkbox.setChecked(bool(visible))

    @Slot(str)
    def showOnlyLayer(self, layer: str) -> None:
        self.backend._show_only_layers(None if layer in ("", "null", "all") else layer)

    @Slot()
    def orderedFabrication(self) -> None:
        self.backend._ordered_fabrication()
        self.publishProgram()

    @Slot()
    def clearAll(self) -> None:
        self.backend._clear_all_layers()
        self.publishProgram()

    @Slot(bool, bool)
    def setMotionVisibility(self, show_rapid: bool, show_cuts: bool) -> None:
        self.backend.chk_show_rapids.setChecked(bool(show_rapid))
        self.backend.chk_show_cuts.setChecked(bool(show_cuts))
        # El backend reconstruye los segmentos visibles a partir del toolpath
        # completo; volver a publicarlos permite al canvas HTML mostrar G0/G1.
        self.publishProgram()

    @Slot()
    def resetView(self) -> None:
        self.backend._reset_view()
        self.backend.canvas_3d.reset_view()

    @Slot(str)
    def openFile(self, kind: str) -> None:
        actions = {
            "gcode": self.backend._open_gcode_file,
            "svg": self.backend._open_svg_file,
            "dxf": self.backend._open_dxf_file,
            "gerber": self.backend._open_gerber_file,
            "excellon": self.backend._open_excellon_file,
            "save": self.backend._save_gcode_file,
        }
        action = actions.get(str(kind))
        if action is None:
            self._log(f"Acción de archivo no válida: {kind}", level="error")
            return
        action()
        self.publishProgram()

    @Slot(int)
    def openRecent(self, index: int) -> None:
        if 0 <= index < self.backend.file_list.count():
            self.backend._on_file_double_clicked(self.backend.file_list.item(index))
            self.publishProgram()

    @Slot()
    def saveGcode(self) -> None:
        self.backend._save_gcode_file()

    @Slot(str)
    def previewEditor(self, text: str) -> None:
        lines = [line.rstrip() for line in str(text).splitlines() if line.strip()]
        if not lines:
            self._log("El editor no contiene G-code", level="error")
            return
        self.backend._load_gcode(lines, "Editor G-code", add_to_list=False)
        self.publishProgram()

    @Slot(str)
    def saveEditor(self, text: str) -> None:
        lines = [line.rstrip() for line in str(text).splitlines() if line.strip()]
        if lines:
            self.backend.editor.load_lines(lines)
            self.backend.gcode_lines = lines
            self.backend._save_gcode_file()

    @Slot(str, float, float, int)
    def savePreferences(self, poll_text: str, area_x: float, area_y: float, grid_alpha: int) -> None:
        try:
            digits = "".join(ch for ch in str(poll_text) if ch.isdigit())
            interval = max(50, min(5000, int(digits or 500)))
            self.backend.poll_interval = interval
            self.backend.settings.setValue("poll_interval", interval)
            self.backend.poll_timer.setInterval(interval)
            area_x = max(10.0, min(5000.0, float(area_x)))
            area_y = max(10.0, min(5000.0, float(area_y)))
            grid_alpha = max(2, min(60, int(grid_alpha)))
            self.backend.settings.setValue("cfg_work_area_x", area_x)
            self.backend.settings.setValue("cfg_work_area_y", area_y)
            self.backend.settings.setValue("cfg_grid_alpha", grid_alpha)
            self.backend.settings.sync()
            self.backend.canvas_2d.area_x = float(area_x)
            self.backend.canvas_2d.area_y = float(area_y)
            self.backend.canvas_3d.area_x = float(area_x)
            self.backend.canvas_3d.area_y = float(area_y)
            self.backend.canvas_2d.update()
            self.backend.canvas_3d.update()
            self._log(f"Ajustes guardados - área {area_x:.0f}x{area_y:.0f} mm")
        except Exception as exc:
            self._log(f"No se pudieron guardar los ajustes: {exc}", level="error")

    @Slot(str)
    def setTheme(self, theme: str) -> None:
        theme = str(theme)
        self.backend.settings.setValue("web_theme", theme)
        self.backend.settings.sync()

    @Slot(bool)
    def saveMcpConfig(self, write_enabled: bool) -> None:
        MCP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = MCP_DATA_DIR / "config.json"
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps({"write_enabled": bool(write_enabled)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        self._log("Escritura MCP habilitada" if write_enabled else "Escritura MCP deshabilitada")

    @Slot(str)
    def setEditorText(self, text: str) -> None:
        self.backend.editor.setPlainText(str(text))

    @Slot()
    def clearConsole(self) -> None:
        self.backend.console_body.clear()

    @Slot(str)
    def copyText(self, text: str) -> None:
        QApplication.clipboard().setText(str(text))

    @Slot()
    def selectAllConsole(self) -> None:
        self.backend.console_body.selectAll()

    @Slot()
    def copyConsole(self) -> None:
        self.backend._copy_console()

    def buildState(self) -> dict[str, Any]:
        w = self.backend
        position_reported = bool(getattr(w, "_position_reported", False))
        simulation_running = bool(w.simulator.running)
        position_fresh = (position_reported and w.connected and
                          time.monotonic() - float(getattr(w, "_last_position_reported", 0.0))
                          <= max(2.5, float(w.poll_interval) / 1000.0 * 3.0))
        position_available = position_fresh or simulation_running
        mcp_config = {}
        mcp_clients = {}
        try:
            mcp_config = json.loads((MCP_DATA_DIR / "config.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        try:
            mcp_clients = json.loads((MCP_DATA_DIR / "clients.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        now = time.time()
        connected_mcp_clients = []
        for client in mcp_clients.values() if isinstance(mcp_clients, dict) else []:
            if not isinstance(client, dict):
                continue
            try:
                if now - float(client.get("last_seen", 0)) < 5.0:
                    connected_mcp_clients.append({
                        "name": str(client.get("name", "Cliente MCP")),
                        "pid": int(client.get("pid", 0)),
                    })
            except (TypeError, ValueError):
                continue
        return {
            "axesEnabled": {
                "X": w.settings.value("axis_enabled_x", True, type=bool),
                "Y": w.settings.value("axis_enabled_y", True, type=bool),
                "Z": w.settings.value("axis_enabled_z", False, type=bool),
            },
            "ignoreZForXY": bool(w.settings.value("ignore_z_for_xy", False, type=bool)),
            "connected": bool(w.connected),
            "port": str(w.current_port),
            "polling": bool(w.polling_enabled),
            "pollInterval": int(w.poll_interval),
            "areaX": float(w.canvas_2d.area_x),
            "areaY": float(w.canvas_2d.area_y),
            "theme": str(w.settings.value("web_theme", "black", type=str)),
            "position": {
                "x": float(w.canvas_2d.current_x) if simulation_running else (self._number(w.lbl_x.text()) if position_reported else None),
                "y": float(w.canvas_2d.current_y) if simulation_running else (self._number(w.lbl_y.text()) if position_reported else None),
                "z": float(getattr(w, "current_z", 0.0)) if position_available else None,
            },
            "pulseStats": {
                axis: dict(values)
                for axis, values in getattr(w, "_motor_pulse_stats", {}).items()
            },
            "pulseTelemetry": bool(getattr(w, "_pulse_telemetry_received", False)),
            "positionValid": position_available,
            "positionFresh": position_fresh or simulation_running,
            "positionState": ("simulación" if simulation_running else
                              "fresca" if position_fresh else
                              "desactualizada" if position_reported else "sin posición"),
            "homed": bool(getattr(w, "_pos_valid", False)),
            "activeLine": (int(w.simulator.segments[w.simulator.current_idx][5]) + 1
                           if simulation_running and w.simulator.current_idx < len(w.simulator.segments)
                           else int(getattr(w, "job_current", 0)) if w.job_running else 0),
            "mcp": {
                "write_enabled": bool(mcp_config.get("write_enabled", False)),
                "clients": connected_mcp_clients,
            },
            "showRapid": bool(w.chk_show_rapids.isChecked()),
            "showCuts": bool(w.chk_show_cuts.isChecked()),
            "jobRunning": bool(w.job_running),
            "jobPaused": bool(w.job_paused),
            "simulationRunning": bool(w.simulator.running),
            "progress": int(w.progress_bar.value()),
            "lineCount": w.lbl_line_count.text(),
            "elapsed": w.lbl_elapsed.text(),
            "remaining": w.lbl_eta.text(),
            "machineState": w.state_badge.text(),
            "stats": {
                "total": w.lbl_st_total.text(), "g0": w.lbl_st_g0.text(),
                "g1": w.lbl_st_g1.text(), "arcs": w.lbl_st_arcs.text(),
                "mcodes": w.lbl_st_mcodes.text(), "distance": w.lbl_st_distance.text(),
                "bounds": w.lbl_st_bounds.text(), "boundary": w.lbl_st_status.text(),
            },
            "feedOverride": int(w.fo_slider.value()),
            "step": float(w.step_combo.currentData() or 1.0),
            "speed": int(w.speed_combo.currentData() or 300),
            "mode": str(w.mode_combo.currentData() or "NORMAL"),
            "files": [
                {"name": w.file_list.item(i).text(),
                 "path": (w.file_list.item(i).data(256) or {}).get("path", "")}
                for i in range(w.file_list.count())
            ],
        }

    def buildProgram(self) -> dict[str, Any]:
        w = self.backend
        # _load_gcode conserva el programa activo en gcode_lines; el editor
        # nativo permanece oculto en esta GUI y puede no contener ese texto.
        program_text = "\n".join(w.gcode_lines) if w.gcode_lines else w.editor.toPlainText()
        model = parse_program(program_text.splitlines())
        layers = {}
        for name, layer in w.canvas_2d.layers.items():
            layers[name] = {
                "visible": bool(layer.get("visible", True)),
                "color": str(layer.get("color", "#ff8c42")),
                "segments": [list(segment) for segment in layer.get("segments", [])],
            }
        holes = {}
        for name, layer in w.canvas_3d.hole_layers.items():
            holes[name] = {
                "visible": bool(layer.get("visible", True)),
                "color": str(layer.get("color", "#00ffff")),
                "holes": [list(hole) for hole in layer.get("holes", [])],
            }
        return {
            "text": program_text,
            "layers": layers,
            "holes": holes,
            "name": (w.file_list.item(w.file_list.count() - 1).text()
                     if w.file_list.count() else ""),
            "motion": model["segments"],
            "preflight": model["warnings"],
        }

    @staticmethod
    def _number(text: str) -> float:
        try:
            return float(str(text).strip().replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    def publishState(self) -> None:
        self.stateChanged.emit(self.buildState())
        text = self.backend.console_body.toPlainText()
        if text != self._last_console:
            self._last_console = text
            self.consoleChanged.emit(text)

    @Slot(float, float, float, bool)
    def publishPosition(self, x: float, y: float, z: float, valid: bool, cutting: bool) -> None:
        self.positionChanged.emit(float(x), float(y), float(z), bool(valid), bool(cutting))

    def publishProgram(self) -> None:
        payload = self.buildProgram()
        signature = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if signature != self._last_program_signature:
            self._last_program_signature = signature
            self.programChanged.emit(payload)
            self.publishState()


class LiquidGlassWindow(NativeBackendWindow):
    """Reutiliza la lógica CNC, sustituyendo la interfaz nativa por la plantilla."""

    positionUpdated = Signal(float, float, float, bool, bool)

    def __init__(self):
        super().__init__()
        # LiquidGlassWindow también es el backend nativo: conserva sus
        # operaciones y widgets ocultos, mientras reemplaza la vista central.
        self.backend = self
        self.setWindowTitle("CNC Proyect — Liquid Glass")
        self.resize(1500, 940)

        self._native_backend_ui = self.takeCentralWidget()
        self._native_backend_ui.setParent(self)
        self._native_backend_ui.hide()
        # Estos widgets nativos quedan ocultos; el visor HTML dibuja ambos paneles.
        for canvas in (self.canvas_2d, self.canvas_3d):
            timer = getattr(canvas, "_anim_timer", None)
            if timer is not None:
                timer.stop()

        self.web = QWebEngineView(self)
        self.web.setObjectName("LiquidGlassWebView")
        self.setCentralWidget(self.web)

        self.web_channel = QWebChannel(self.web.page())
        self.web_bridge = CncWebBridge(self)
        self.positionUpdated.connect(self.web_bridge.publishPosition)
        self.web_channel.registerObject("cnc", self.web_bridge)
        self.web.page().setWebChannel(self.web_channel)

        self.web.loadFinished.connect(self._on_page_loaded)
        self.web.setUrl(QUrl.fromLocalFile(str(APP_DIR / "index.html")))

        self._web_state_timer = QTimer(self)
        self._web_state_timer.setInterval(200)
        self._web_state_timer.timeout.connect(self.web_bridge.publishState)
        self._web_state_timer.start()

    def _update_position(self, x, y):
        super()._update_position(x, y)
        if hasattr(self, "web_bridge"):
            self.positionUpdated.emit(float(x), float(y), float(self.current_z), True, False)

    def _on_sim_position(self, x, y, z=0.0, cutting=False):
        # La interfaz visible la dibuja el visor HTML; no repintar los dos
        # canvases Qt ocultos en cada tick de la simulación.
        self.canvas_2d.current_x = self.canvas_2d.target_x = float(x)
        self.canvas_2d.current_y = self.canvas_2d.target_y = float(y)
        self.canvas_3d.current_x = self.canvas_3d.target_x = float(x)
        self.canvas_3d.current_y = self.canvas_3d.target_y = float(y)
        self.current_z = float(z)
        self.lbl_x.setText(f"{x:.2f}")
        self.lbl_y.setText(f"{y:.2f}")
        if hasattr(self, "web_bridge"):
            self.positionUpdated.emit(float(x), float(y), float(z), True, bool(cutting))

    def _on_sim_line(self, line_num):
        # Mantener el indicador de línea sin desplazar un editor nativo oculto.
        if line_num >= 0:
            self.editor.set_job_line(line_num)

    @Slot(bool)
    def _on_page_loaded(self, ok: bool) -> None:
        if ok:
            self.web_bridge.publishState()
            self.web_bridge.publishProgram()
        else:
            QMessageBox.critical(self, "Error", "No se pudo cargar la interfaz HTML.")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("CNC IDE Web")
    app.setOrganizationName("CNC Proyect")
    window = LiquidGlassWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
