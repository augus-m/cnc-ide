@echo off
:: build_win.bat — compila CNC IDE (Liquid Glass) para Windows 11
:: Ejecutar desde la carpeta gui_windows con Python 3.11+ en el PATH
:: -------------------------------------------------------------------

echo [1/4] Instalando dependencias...
pip install --upgrade PySide6 PySide6-WebEngine pyserial shapely "mcp>=2.1,<3" pyinstaller
if errorlevel 1 (echo ERROR al instalar dependencias & pause & exit /b 1)

echo [2/4] Limpiando builds anteriores...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo [3/4] Compilando con PyInstaller...
pyinstaller cnc_ide.spec
if errorlevel 1 (echo ERROR en PyInstaller & pause & exit /b 1)

echo [4/4] Listo!
echo.
echo   Ejecutable:  dist\CNC_IDE\CNC_IDE.exe
echo.
echo   NOTA: el STM32 aparece como COMx en Windows.
echo   Si no aparece instala el driver VCP de ST desde:
echo   https://www.st.com/en/development-tools/stsw-stm32102.html
echo.
pause
