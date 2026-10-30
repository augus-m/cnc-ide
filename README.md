# CNC Proyect — Liquid Glass GUI

Nueva interfaz HTML para CNC Proyect. La lógica de comunicación y fabricación está copiada en `backend/` desde `GUI_Linux`; esa carpeta original no forma parte de esta aplicación y no se modifica al ejecutarla.

## Ejecutar

```bash
python3 -m pip install -r requirements.txt
python3 main.py
```

La interfaz se muestra en Qt WebEngine y usa Qt WebChannel para llamar al backend Python. Los archivos de ajustes auxiliares, registro y puente JSON se guardan en `data/`.

## MCP de solo lectura

`mcp_server.py` expone `get_cnc_status` y `read_cnc_terminal`. También ofrece `send_cnc_command`, `jog_cnc_axis` y `control_cnc_job`. Las herramientas de escritura están desactivadas por defecto; actívalas en **Ajustes > MCP** y cada llamada debe incluir `confirm=true`. El jog exige homing y valida los límites configurados. Instala la dependencia con el comando anterior. Para registrarlo en Codex, añade este bloque a `~/.codex/config.toml`:

```toml
[mcp_servers.cnc_gui]
command = "/usr/bin/python3"
args = ["/home/augus/Documentos/proyecto_cnc/gui_v/mcp_server.py"]
env = { CNC_MCP_CLIENT = "Codex" }
```

Para otra IA, registra el mismo servidor en su configuración MCP y cambia `CNC_MCP_CLIENT` (por ejemplo `Claude`). La pestaña MCP muestra los procesos cliente conectados. Reinicia la IA después de registrarlo. La GUI debe estar abierta para que MCP muestre el estado actualizado. La posición reportada y el estado de homing se entregan por separado.

## Alcance actual

La aplicación reutiliza el backend PySide6 existente para conexión serie, carga y conversión de archivos, consola, jog, envío de trabajos, simulación, capas y estadísticas. El monitor de motores de la plantilla sigue siendo visual, ya que la GUI Python no aporta telemetría real de motores.

### Revisión y simulación

El analizador común `backend/gcode_model.py` normaliza unidades a milímetros, interpreta coordenadas absolutas/incrementales y discretiza arcos I/J y R. Antes de enviar un trabajo, la GUI revisa conexión, homing, antigüedad de posición, límites XY, movimientos Z y comandos que el perfil del STM32 no reconoce. El firmware actual sigue siendo de dos ejes; una simulación visual de Z no habilita el movimiento físico de ese eje.

En **Vista 3D**, **Simular mecanizado** prepara un bloque visual con una malla superior de 5 mm por celda. **Polígonos** muestra u oculta esa malla y **Reiniciar pieza** repone el material. Es una vista aproximada: toma Z=0 como superficie y recorta con herramienta plana; los segmentos rápidos G0 no quitan material.

Los casos del analizador se pueden ejecutar con `python3 -m unittest discover -s tests -v` desde esta carpeta.
