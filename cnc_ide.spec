# cnc_ide.spec  –  PyInstaller para Windows 11
# Compilar desde la carpeta gui_windows con:
#   pyinstaller cnc_ide.spec

import os, pathlib

block_cipher = None

# La carpeta data/ la crea la app en runtime — no se incluye en el spec
datas = [
    ('index.html',        '.'),
    ('bridge.js',         '.'),
    ('sim3d.js',          '.'),
    ('backend/assets',    'backend/assets'),
    ('vendor',            'vendor'),
]

a = Analysis(
    ['main.py'],
    pathex=['.', 'backend'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'main_window',
        'dialogs',
        'serial_worker',
        'converter',
        'gerber_parser',
        'excellon_parser',
        'cnc_bridge',
        'gcode_editor',
        'gcode_model',
        'pcb_preview',
        'image_to_gcode',
        'cv2',
        'numpy',
        'serial.tools.list_ports',
        'serial.tools',
        'shapely',
        'shapely.geometry',
        'shapely.ops',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtSvg',
        'PySide6.QtPrintSupport',
        'winreg',
        'mcp',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CNC_IDE',
    debug=False,
    strip=False,
    upx=True,
    console=False,
    # icon='cnc-ide.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CNC_IDE',
)
