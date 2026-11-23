# cnc_ide.spec  –  PyInstaller para Windows 11
# Compilar desde la carpeta gui_windows con:
#   pyinstaller cnc_ide.spec

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # UI principal (WebEngine la sirve desde disco)
        ('index.html',   '.'),
        ('bridge.js',    '.'),
        ('sim3d.js',     '.'),
        # assets del backend (fuente logo, etc.)
        ('backend/assets', 'backend/assets'),
        # carpeta vendor (libs JS locales si las hay)
        ('vendor',       'vendor'),
        # datos persistentes vacíos para que exista la carpeta
        ('data',         'data'),
    ],
    hiddenimports=[
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
    console=False,          # sin consola negra
    # icon='cnc-ide.ico',   # descomenta si conviertes cnc-ide.png a .ico
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
