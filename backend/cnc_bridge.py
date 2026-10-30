"""Puente de control: la GUI obedece ordenes JSON de una carpeta y publica su estado.

Carpetas (dentro de ~/Documentos/proyecto_cnc/bridge):
  inbox/       ordenes *.json pendientes; la GUI las ejecuta y las mueve a done/
  done/        ordenes ya procesadas
  outbox/      resultado de cada orden: <id>.json con ok + result o error
  state.json   estado actual de la GUI (se actualiza cada segundo)

Formato de una orden:  {"id": "abc", "action": "get_state", "args": {}}
Solo se permiten las acciones definidas como _do_<nombre> en esta clase.
"""
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "data" / "bridge"
INBOX = BRIDGE_DIR / "inbox"
OUTBOX = BRIDGE_DIR / "outbox"
DONE = BRIDGE_DIR / "done"
STATE = BRIDGE_DIR / "state.json"
CONSOLE_LOG = Path(__file__).resolve().parents[1] / "data" / "gui_consola.log"
MCP_BACKUPS = Path(__file__).resolve().parents[1] / "data" / "mcp" / "backups"
MCP_EXPORTS = Path(__file__).resolve().parents[1] / "data" / "mcp" / "exports"


def _atomic_write_json(path, data):
    path = Path(path)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


class CncBridge(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        for d in (INBOX, OUTBOX, DONE):
            d.mkdir(parents=True, exist_ok=True)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._process_inbox)
        self._poll.start(500)
        self._state_timer = QTimer(self)
        self._state_timer.timeout.connect(self._write_state)
        self._state_timer.start(1000)
        self._write_state()

    # ---------- estado ----------
    def build_state(self):
        w = self.w
        try:
            layers = list(w.canvas_2d.layers.keys())
        except Exception:
            layers = []
        position = {"x": None, "y": None}
        position_known = bool(getattr(w, "_position_reported", False))
        if position_known:
            try:
                position = {
                    "x": float(w.lbl_x.text().replace(",", ".")),
                    "y": float(w.lbl_y.text().replace(",", ".")),
                }
            except (TypeError, ValueError):
                position_known = False
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "connected": bool(w.connected),
            "port": str(getattr(w, "current_port", "")),
            "polling": bool(getattr(w, "polling_enabled", False)),
            "position": position,
            "position_known": position_known,
            "homed": bool(getattr(w, "_pos_valid", False)),
            "machine_state": str(w.state_badge.text()),
            "job_running": bool(w.job_running),
            "simulation_running": bool(w.simulator.running),
            "simulation_segments": len(getattr(w, "_last_sim_segments", None) or []),
            "layers": layers,
            "last_offset": getattr(w, "_last_offset", None),
            "console_log": str(CONSOLE_LOG),
            "motor_pulses": {
                "telemetry": bool(getattr(w, "_pulse_telemetry_received", False)),
                "stats": {k: dict(v) for k, v in (getattr(w, "_motor_pulse_stats", {}) or {}).items()},
            },
            "editor_lines": len(w.editor.get_lines()) if hasattr(w, "editor") else 0,
        }

    def _write_state(self):
        try:
            _atomic_write_json(STATE, self.build_state())
        except Exception:
            pass

    # ---------- ordenes ----------
    def _process_inbox(self):
        for f in sorted(INBOX.glob("*.json")):
            cid = f.stem
            try:
                cmd = json.loads(f.read_text(encoding="utf-8"))
                cid = str(cmd.get("id") or f.stem)
                action = str(cmd.get("action", ""))
                args = cmd.get("args") or {}
                expires_at = float(cmd.get("expires_at", 0) or 0)
                if expires_at and time.time() > expires_at:
                    raise TimeoutError("La orden MCP caducó antes de que la GUI pudiera ejecutarla.")
                if not action.replace("_", "").isalnum():
                    raise ValueError(f"accion invalida: {action!r}")
                handler = getattr(self, "_do_" + action, None)
                if handler is None:
                    raise ValueError(f"accion no permitida: {action!r}")
                reply = {"id": cid, "ok": True, "result": handler(**args)}
            except Exception as e:
                reply = {"id": cid, "ok": False, "error": f"{type(e).__name__}: {e}"}
            try:
                _atomic_write_json(OUTBOX / f"{cid}.json", reply)
                os.replace(f, DONE / f.name)
            except Exception:
                pass

    def _do_get_state(self):
        return self.build_state()

    def _do_open_gcode(self, path):
        self.w._open_gcode_file(path=path)
        return {"opened": path}

    def _do_open_gerber(self, path, params=None):
        self.w._open_gerber_file(path=path, params=params or {})
        return {"opened": path, "params": params or {}}

    def _do_send_line(self, line, confirm=False):
        if not confirm:
            raise PermissionError("send_line requiere confirm=true")
        if not self.w.connected:
            raise RuntimeError("no conectado a la placa")
        if self.w.job_running:
            raise RuntimeError("hay un trabajo en curso")
        line = str(line).strip()
        if re.match(r"^G0*[0-3]\b", line, re.IGNORECASE):
            raise PermissionError("Para movimiento usa jog_axis o inicia un trabajo validado en la GUI.")
        self.w._send_cmd(line)
        return {"sent": line}

    def _do_jog_axis(self, axis, direction, step_mm=1.0, speed_mm_min=300, confirm=False):
        if not confirm:
            raise PermissionError("jog_axis requiere confirm=true")
        axis = str(axis).upper()
        direction = int(direction)
        step_mm = float(step_mm)
        speed_mm_min = int(speed_mm_min)
        if axis not in {"X", "Y", "Z"} or direction not in {-1, 1}:
            raise ValueError("Eje o dirección no válidos.")
        if step_mm not in {0.1, 0.5, 1.0, 5.0, 10.0, 50.0}:
            raise ValueError("El paso debe ser 0.1, 0.5, 1, 5, 10 o 50 mm.")
        if speed_mm_min not in {200, 300, 2000, 5000}:
            raise ValueError("Velocidad no admitida; usa 200, 300, 2000 o 5000 mm/min.")
        if not self.w.connected:
            raise RuntimeError("no conectado a la placa")
        if not getattr(self.w, "_pos_valid", False):
            raise PermissionError("Haz homing ($H) antes de mover ejes.")
        if self.w.job_running:
            raise RuntimeError("hay un trabajo en curso")
        current_x = float(self.w.lbl_x.text().replace(",", "."))
        current_y = float(self.w.lbl_y.text().replace(",", "."))
        next_x = current_x + (direction * step_mm if axis == "X" else 0.0)
        next_y = current_y + (direction * step_mm if axis == "Y" else 0.0)
        if not (0.0 <= next_x <= self.w.canvas_2d.area_x and 0.0 <= next_y <= self.w.canvas_2d.area_y):
            raise PermissionError("El movimiento MCP saldría del área de trabajo configurada.")
        step_index = self.w.step_combo.findData(step_mm)
        speed_index = self.w.speed_combo.findData(speed_mm_min)
        if step_index < 0 or speed_index < 0:
            raise ValueError("El paso o la velocidad no están disponibles en esta GUI.")
        self.w.step_combo.setCurrentIndex(step_index)
        self.w.speed_combo.setCurrentIndex(speed_index)
        self.w._jog(axis, direction)
        return {"axis": axis, "direction": direction, "step_mm": step_mm, "speed_mm_min": speed_mm_min}

    def _do_control_job(self, action, confirm=False):
        if not confirm:
            raise PermissionError("control_job requiere confirm=true")
        action = str(action).strip().lower()
        if not self.w.connected:
            raise RuntimeError("no conectado a la placa")
        if action == "start":
            if not getattr(self.w, "_pos_valid", False):
                raise PermissionError("Haz homing ($H) antes de iniciar un trabajo.")
            if self.w.job_running:
                raise RuntimeError("ya hay un trabajo en curso")
            if not self.w.gcode_lines and not any(line.strip() for line in self.w.editor.get_lines()):
                raise RuntimeError("no hay G-code cargado")
            from gcode_editor import parse_gcode_stats
            lines = self.w.editor.get_lines() or self.w.gcode_lines
            if parse_gcode_stats(lines).get("error_lines"):
                raise PermissionError("El G-code rebasa el área de trabajo; corrígelo en la GUI antes de iniciar.")
            self.w._on_start_job()
        elif action == "pause":
            if not self.w.job_running or self.w.job_paused:
                raise RuntimeError("no hay un trabajo activo que se pueda pausar")
            self.w._on_pause_job()
        elif action == "resume":
            if not self.w.job_running or not self.w.job_paused:
                raise RuntimeError("no hay un trabajo pausado que se pueda reanudar")
            self.w._on_pause_job()
        elif action == "stop":
            if not self.w.job_running:
                raise RuntimeError("no hay un trabajo activo")
            self.w._on_stop_job()
        elif action == "emergency_stop":
            self.w._on_estop()
        else:
            raise ValueError("acción de trabajo no reconocida")
        return {"action": action, "job_running": bool(self.w.job_running), "job_paused": bool(self.w.job_paused)}

    def _do_get_setting(self, key):
        return {"key": key, "value": self.w.settings.value(key)}

    def _do_set_setting(self, key, value):
        self.w.settings.setValue(key, value)
        self.w.settings.sync()
        return {"key": key, "value": value}

    def _do_clear_console(self):
        self.w.console_body.clear()
        return "ok"

    def _do_export_simulation(self, path):
        segs = getattr(self.w, "_last_sim_segments", None)
        if not segs:
            raise RuntimeError("no hay simulacion preparada")
        _atomic_write_json(path, {"segments": segs})
        return {"path": path, "segments": len(segs)}

    def _do_toggle_simulation(self):
        """Inicia la simulacion (o la detiene si ya esta corriendo), igual que el boton 'Simular'."""
        was_running = bool(self.w.simulator.running)
        self.w._on_simulate_clicked()
        return {"was_running": was_running, "running_now": bool(self.w.simulator.running)}

    def _do_set_simulation_speed(self, position=0):
        """Fija el slider de velocidad de simulacion (0-1000; 0 = minimo, 10 mm/s)."""
        pos = max(0, min(1000, int(position)))
        self.w.sim_speed_slider.setValue(pos)
        speed = self.w._slider_pos_to_speed(pos)
        return {"position": pos, "speed_mm_s": round(speed, 2)}

    # ---------- lectura de monitor, editor y consola ----------
    def _do_get_motor_monitor(self):
        stats = getattr(self.w, "_motor_pulse_stats", {}) or {}
        return {
            "telemetry": bool(getattr(self.w, "_pulse_telemetry_received", False)),
            "stats": {k: dict(v) for k, v in stats.items()},
        }

    def _do_get_editor(self, start=0, count=200):
        lines = self.w.editor.get_lines()
        start = max(0, int(start))
        count = max(1, min(1000, int(count)))
        return {
            "total_lines": len(lines),
            "start": start,
            "lines": lines[start:start + count],
        }

    def _do_get_console(self, lines=100):
        count = max(1, min(1000, int(lines)))
        try:
            text = CONSOLE_LOG.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return {"lines": []}
        return {"lines": text.splitlines()[-count:]}

    # ---------- escritura del editor (con copia previa) ----------
    def _do_set_editor_text(self, text, confirm=False):
        if not confirm:
            raise PermissionError("set_editor_text requiere confirm=true")
        if self.w.job_running:
            raise RuntimeError("hay un trabajo en curso; no se puede reemplazar el G-code")
        MCP_BACKUPS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = MCP_BACKUPS / f"editor-{stamp}.gcode"
        backup.write_text("\n".join(self.w.editor.get_lines()), encoding="utf-8")
        new_lines = str(text).splitlines()
        self.w.editor.load_lines(new_lines)
        return {"lines": len(new_lines), "backup": str(backup)}

    def _do_clear_console_checked(self, confirm=False):
        if not confirm:
            raise PermissionError("clear_console requiere confirm=true")
        return self._do_clear_console()

    def _do_set_setting_checked(self, key, value, confirm=False):
        if not confirm:
            raise PermissionError("set_setting requiere confirm=true")
        if "mcp" in str(key).lower():
            raise PermissionError("los ajustes MCP se cambian en la pestaña MCP, no por esta vía")
        return self._do_set_setting(key, value)

    def _do_export_simulation_checked(self, name="simulacion.json", confirm=False):
        if not confirm:
            raise PermissionError("export_simulation requiere confirm=true")
        MCP_EXPORTS.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(name))[:80] or "simulacion.json"
        return self._do_export_simulation(str(MCP_EXPORTS / safe))
