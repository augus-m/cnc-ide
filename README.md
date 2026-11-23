# CNC IDE — Liquid Glass

Firmware y software para CNC. Perfil de estudiante, nada serio.

## Estructura

- `main.py` — arranque, WebEngine + bridge Qt↔JS
- `index.html` / `bridge.js` / `sim3d.js` — UI Liquid Glass
- `backend/` — lógica: serial, parsers Gerber/Excellon, G-code
- `vendor/` — three.js + OrbitControls (local, sin CDN)

## Requisitos

```
pip install PySide6 PySide6-WebEngine pyserial shapely "mcp>=2.1,<3"
```

## Ejecutar (Linux)

```bash
python3 main.py
```

## Compilar para Windows 11

```
pip install pyinstaller
pyinstaller cnc_ide.spec
```

El ejecutable queda en `dist/CNC_IDE/CNC_IDE.exe`.
