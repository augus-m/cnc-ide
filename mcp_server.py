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


@mcp.tool()
def read_cnc_terminal(lines: int = 100) -> str:
    """Lee las últimas líneas de la terminal; no modifica ni envía comandos."""
    count = max(1, min(1000, int(lines)))
    try:
        with TERMINAL_FILE.open("rb") as stream:
            stream.seek(0, 2)
            end = stream.tell()
            stream.seek(max(0, end - 262144))
            content = stream.read().decode("utf-8", errors="replace")
        return "\n".join(content.splitlines()[-count:]) or "La terminal todavía no tiene líneas."
    except FileNotFoundError:
        return "No existe el registro de terminal; abre gui_v/main.py y conecta la CNC."
    except OSError as exc:
        return f"No se pudo leer la terminal: {exc}"


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
