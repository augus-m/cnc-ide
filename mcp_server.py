#!/usr/bin/env python3
"""Servidor MCP local para consultar y, con permiso explícito, controlar la GUI CNC."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "data" / "bridge" / "state.json"
TERMINAL_FILE = APP_DIR / "data" / "gui_consola.log"
MCP_DATA_DIR = APP_DIR / "data" / "mcp"
MCP_CONFIG_FILE = MCP_DATA_DIR / "config.json"
MCP_CLIENTS_FILE = MCP_DATA_DIR / "clients.json"
MCP_LOCK_FILE = MCP_DATA_DIR / "clients.lock"
INBOX = APP_DIR / "data" / "bridge" / "inbox"
OUTBOX = APP_DIR / "data" / "bridge" / "outbox"

mcp = MCPServer(
    "cnc-gui-readonly",
    instructions=(
        "Usa get_cnc_status y read_cnc_terminal para consultar la GUI. Las herramientas "
        "de escritura requieren permiso habilitado en Ajustes > MCP y confirm=true."
    ),
)


def _read_state() -> dict[str, Any]:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        age = max(0.0, time.time() - STATE_FILE.stat().st_mtime)
        state["gui_running"] = age < 4.0
        state["state_age_seconds"] = round(age, 2)
        state["mcp_write_enabled"] = _writes_enabled()
        state["mcp_clients"] = _connected_clients()
        return state
    except FileNotFoundError:
        return {"gui_running": False, "message": "Aún no hay estado; abre gui_v/main.py."}
    except (OSError, json.JSONDecodeError) as exc:
        return {"gui_running": False, "message": f"No se pudo leer el estado: {exc}"}


@mcp.tool()
def get_cnc_status() -> dict[str, Any]:
    """Devuelve conexión, puerto, coordenadas reportadas por STM32, homing y trabajo."""
    return _read_state()


def _is_polling_noise(row: str) -> bool:
    return ("[RX] ST " in row) or ("[RX] POS" in row) or ("[TX] M114" in row) or ("[TX] LIMITS" in row)


@mcp.tool()
def read_cnc_terminal(lines: int = 100, important_only: bool = True) -> str:
    """Lee las últimas líneas de la terminal; no modifica ni envía comandos.
    Con important_only (por defecto) omite el sondeo periódico (ST, POS, M114, LIMITS)."""
    count = max(1, min(1000, int(lines)))
    try:
        with TERMINAL_FILE.open("rb") as stream:
            stream.seek(0, 2)
            end = stream.tell()
            stream.seek(max(0, end - 262144))
            content = stream.read().decode("utf-8", errors="replace")
        rows = content.splitlines()
        if important_only:
            rows = [r for r in rows if not _is_polling_noise(r)]
        return "\n".join(rows[-count:]) or "La terminal todavía no tiene líneas."
    except FileNotFoundError:
        return "No existe el registro de terminal; abre gui_v/main.py y conecta la CNC."
    except OSError as exc:
        return f"No se pudo leer la terminal: {exc}"


HISTORY_DIR = APP_DIR / 'data' / 'historial'
HISTORY_KINDS = ('all', 'err', 'tx', 'rx', 'sys')


@mcp.tool()
def read_cnc_history(lines: int = 200, kind: str = 'all') -> str:
    """Lee el historial semanal de la GUI; no modifica ni envía comandos.
    kind: all (todo), err (errores y alarmas), tx (codigo enviado), rx (respuestas), sys (eventos)."""
    count = max(1, min(2000, int(lines)))
    kind = str(kind).strip().lower()
    if kind not in HISTORY_KINDS:
        return 'kind no valido: usa ' + ', '.join(HISTORY_KINDS)
    try:
        files = sorted(HISTORY_DIR.glob('semana_*.log')) if HISTORY_DIR.is_dir() else []
        if not files:
            return 'No hay historial esta semana todavia.'
        latest = files[-1]
        rows = latest.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError as exc:
        return 'No se pudo leer el historial: ' + str(exc)
    if kind == 'err':
        rows = [r for r in rows if '[ERR]' in r or 'ALARM' in r or 'ERROR' in r]
    elif kind != 'all':
        tag = '[' + kind.upper() + ']'
        rows = [r for r in rows if tag in r]
    header = latest.name + ' · ' + str(len(rows)) + ' lineas (' + kind + ')'
    body = '\n'.join(rows[-count:]) or 'Sin lineas para este filtro.'
    return header + '\n' + body


TRABAJOS_DIR = APP_DIR / "data" / "trabajos"


@mcp.tool()
def read_cnc_job(job: str = '', errors_lines: int = 40) -> str:
    """Lee el resumen de un trabajo y sus ultimos errores. Solo lectura, pocos tokens.
    job: nombre de la carpeta (AAAA-MM-DD_HHMMSS). Vacio = el trabajo mas reciente.
    errors_lines: cuantas lineas de errores.log mostrar (max 200)."""
    try:
        carpetas = sorted(p for p in TRABAJOS_DIR.iterdir() if p.is_dir()) if TRABAJOS_DIR.is_dir() else []
    except OSError as exc:
        return 'No se pudo leer data/trabajos: ' + str(exc)
    if not carpetas:
        return 'Todavia no hay trabajos registrados.'
    nombre = str(job).strip()
    if nombre:
        match = [p for p in carpetas if p.name == nombre]
        if not match:
            return 'No existe el trabajo ' + nombre + '. Recientes: ' + ', '.join(p.name for p in carpetas[-5:])
        carpeta = match[0]
    else:
        carpeta = carpetas[-1]
    n = max(1, min(200, int(errors_lines)))
    partes = []
    try:
        resumen = carpeta / 'resumen.md'
        partes.append(resumen.read_text(encoding='utf-8', errors='replace').strip()
                      if resumen.is_file() else 'Falta resumen.md en ' + carpeta.name)
        errores = carpeta / 'errores.log'
        if errores.is_file():
            filas = errores.read_text(encoding='utf-8', errors='replace').splitlines()
            partes.append('\nUltimos errores (' + str(min(n, len(filas))) + ' de ' + str(len(filas)) + '):\n'
                          + ('\n'.join(filas[-n:]) or '(sin errores)'))
        else:
            partes.append('\nSin errores registrados.')
    except OSError as exc:
        return 'No se pudo leer el trabajo ' + carpeta.name + ': ' + str(exc)
    return 'Trabajo: ' + carpeta.name + '\n' + '\n'.join(partes)

def _writes_enabled() -> bool:
    try:
        config = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
        return bool(config.get("write_enabled", False))
    except (OSError, json.JSONDecodeError):
        return False


def _connected_clients() -> list[dict[str, Any]]:
    try:
        clients = json.loads(MCP_CLIENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    now = time.time()
    connected = []
    for item in clients.values() if isinstance(clients, dict) else []:
        if not isinstance(item, dict):
            continue
        try:
            if now - float(item.get("last_seen", 0)) < 5.0:
                connected.append({"name": item.get("name", "Cliente MCP"), "pid": item.get("pid")})
        except (TypeError, ValueError):
            continue
    return connected


def _require_write_permission(confirm: bool) -> None:
    if not _writes_enabled():
        raise PermissionError("Activa el control MCP en Ajustes > MCP de la GUI.")
    if not confirm:
        raise PermissionError("La acción requiere confirm=true.")


async def _call_gui(action: str, args: dict[str, Any]) -> Any:
    state = _read_state()
    if not state.get("gui_running"):
        raise RuntimeError("La GUI no está abierta o su estado dejó de actualizarse.")
    MCP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    command_id = uuid.uuid4().hex
    request_path = INBOX / f"{command_id}.json"
    temporary = INBOX / f".{command_id}.tmp"
    request = {
        "id": command_id,
        "action": action,
        "args": args,
        "expires_at": time.time() + 20.0,
    }
    temporary.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, request_path)

    result_path = OUTBOX / f"{command_id}.json"
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            finally:
                result_path.unlink(missing_ok=True)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "La GUI rechazó la acción."))
            return result.get("result")
        await asyncio.sleep(0.1)
    request_path.unlink(missing_ok=True)
    raise TimeoutError("La GUI no respondió a tiempo; la orden caducará en 20 segundos.")


@mcp.tool()
async def send_cnc_command(command: str, confirm: bool = False) -> dict[str, Any]:
    """Envía una sola línea al controlador. Requiere permiso MCP y confirm=true."""
    _require_write_permission(confirm)
    command = str(command).strip()
    if not command or len(command) > 160 or any(char in command for char in "\r\n\0"):
        raise ValueError("Escribe un único comando de hasta 160 caracteres.")
    result = await _call_gui("send_line", {"line": command, "confirm": True})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def jog_cnc_axis(
    axis: str, direction: int, step_mm: float = 1.0,
    speed_mm_min: int = 300, confirm: bool = False,
) -> dict[str, Any]:
    """Mueve un eje una distancia discreta; requiere MCP habilitado, homing y confirmación."""
    _require_write_permission(confirm)
    result = await _call_gui("jog_axis", {
        "axis": axis, "direction": direction, "step_mm": step_mm,
        "speed_mm_min": speed_mm_min, "confirm": True,
    })
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def control_cnc_job(action: str, confirm: bool = False) -> dict[str, Any]:
    """Inicia, pausa, reanuda, detiene o para en emergencia un trabajo CNC."""
    _require_write_permission(confirm)
    action = str(action).strip().lower()
    if action not in {"start", "pause", "resume", "stop", "emergency_stop"}:
        raise ValueError("action debe ser start, pause, resume, stop o emergency_stop.")
    result = await _call_gui("control_job", {"action": action, "confirm": True})
    return result if isinstance(result, dict) else {"result": result}


PROJECT_ROOT = Path.home() / "Documentos" / "proyecto_cnc"


def _safe_project_path(path: str) -> str:
    """Solo rutas dentro de ~/Documentos/proyecto_cnc, para no leer ni abrir archivos ajenos."""
    resolved = Path(path).expanduser().resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise PermissionError(f"ruta fuera del proyecto: {resolved}")
    if not resolved.exists():
        raise FileNotFoundError(f"no existe: {resolved}")
    return str(resolved)


# ---------- lectura: monitor, editor, ajustes y simulación ----------
@mcp.tool()
def get_motor_monitor() -> dict[str, Any]:
    """Pulsos acumulados y frecuencia STEP (Hz) por eje, tal como los muestra el monitor de motores."""
    state = _read_state()
    return state.get("motor_pulses", {"telemetry": False, "stats": {}})


@mcp.tool()
async def get_gcode_editor(start: int = 0, count: int = 200) -> dict[str, Any]:
    """Lee líneas del editor de G-code (máximo 1000 por llamada)."""
    result = await _call_gui("get_editor", {"start": start, "count": count})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def get_setting(key: str) -> dict[str, Any]:
    """Lee un ajuste guardado de la GUI."""
    result = await _call_gui("get_setting", {"key": key})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def get_gui_console(lines: int = 100) -> dict[str, Any]:
    """Últimas líneas del registro de consola de la GUI (lectura)."""
    result = await _call_gui("get_console", {"lines": lines})
    return result if isinstance(result, dict) else {"result": result}


# ---------- escritura (requieren permiso MCP y confirm=true) ----------
@mcp.tool()
async def set_gcode_editor_text(text: str, confirm: bool = False) -> dict[str, Any]:
    """Reemplaza el texto del editor de G-code. Guarda copia previa en data/mcp/backups."""
    _require_write_permission(confirm)
    result = await _call_gui("set_editor_text", {"text": text, "confirm": True})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def open_gcode(path: str, confirm: bool = False) -> dict[str, Any]:
    """Carga un archivo G-code del proyecto en la GUI."""
    _require_write_permission(confirm)
    result = await _call_gui("open_gcode", {"path": _safe_project_path(path)})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def open_gerber(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> dict[str, Any]:
    """Convierte y carga un Gerber del proyecto en la GUI."""
    _require_write_permission(confirm)
    result = await _call_gui("open_gerber", {"path": _safe_project_path(path), "params": params or {}})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def set_setting(key: str, value: Any, confirm: bool = False) -> dict[str, Any]:
    """Cambia un ajuste de la GUI. Los ajustes MCP no se cambian por esta vía."""
    _require_write_permission(confirm)
    result = await _call_gui("set_setting_checked", {"key": key, "value": value, "confirm": True})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def clear_gui_console(confirm: bool = False) -> dict[str, Any]:
    """Limpia la consola visible de la GUI (el registro en disco no se borra)."""
    _require_write_permission(confirm)
    result = await _call_gui("clear_console_checked", {"confirm": True})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def toggle_simulation(confirm: bool = False) -> dict[str, Any]:
    """Inicia o detiene la simulación, igual que el botón Simular."""
    _require_write_permission(confirm)
    result = await _call_gui("toggle_simulation", {})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def set_simulation_speed(position: int, confirm: bool = False) -> dict[str, Any]:
    """Fija la velocidad de simulación (0 a 1000)."""
    _require_write_permission(confirm)
    result = await _call_gui("set_simulation_speed", {"position": position})
    return result if isinstance(result, dict) else {"result": result}


@mcp.tool()
async def export_simulation(name: str = "simulacion.json", confirm: bool = False) -> dict[str, Any]:
    """Exporta la simulación preparada a data/mcp/exports con el nombre indicado."""
    _require_write_permission(confirm)
    result = await _call_gui("export_simulation_checked", {"name": name, "confirm": True})
    return result if isinstance(result, dict) else {"result": result}


def _update_client_registry(instance_id: str, record: dict[str, Any] | None) -> None:
    MCP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with MCP_LOCK_FILE.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                clients = json.loads(MCP_CLIENTS_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                clients = {}
            if record is None:
                clients.pop(instance_id, None)
            else:
                clients[instance_id] = record
            temporary = MCP_DATA_DIR / f".clients-{os.getpid()}.tmp"
            temporary.write_text(json.dumps(clients, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, MCP_CLIENTS_FILE)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _run_server() -> None:
    instance_id = uuid.uuid4().hex
    client_name = os.environ.get("CNC_MCP_CLIENT", "Cliente MCP").strip() or "Cliente MCP"
    stopping = threading.Event()
    started = time.time()

    def heartbeat() -> None:
        while not stopping.is_set():
            try:
                _update_client_registry(instance_id, {
                    "name": client_name, "pid": os.getpid(),
                    "started_at": started, "last_seen": time.time(),
                })
            except OSError:
                pass
            stopping.wait(1.0)

    thread = threading.Thread(target=heartbeat, name="mcp-client-heartbeat", daemon=True)
    thread.start()
    try:
        mcp.run(transport="stdio")
    finally:
        stopping.set()
        thread.join(timeout=2)
        _update_client_registry(instance_id, None)


if __name__ == "__main__":
    _run_server()
