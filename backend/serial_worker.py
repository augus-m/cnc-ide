"""Worker de serial que corre en un QThread separado.

Envia/recibe por el puerto serie sin bloquear la UI.
"""

import serial
import serial.tools.list_ports

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class SerialWorker(QObject):
    """Vive en un QThread. Se comunica con la UI por signals."""

    # Señales hacia la UI
    connected = Signal(str)      # nombre del puerto
    disconnected = Signal()
    error = Signal(str)          # mensaje de error
    line_received = Signal(str)  # línea completa recibida
    line_sent = Signal(str)      # línea completa enviada

    def __init__(self):
        super().__init__()
        self.serial = None
        self.read_timer = None
        self._rx_buf = b""

    @Slot()
    def setup(self):
        """Se llama desde thread.started. Crea el QTimer en el hilo correcto."""
        self.read_timer = QTimer()
        self.read_timer.setInterval(20)  # 20 ms polling
        self.read_timer.timeout.connect(self._poll)

    @Slot(str, int)
    def connect_port(self, port: str, baud: int):
        """Abre el puerto serie."""
        if self.serial and self.serial.is_open:
            self.error.emit("Ya hay un puerto abierto")
            return

        try:
            self.serial = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
            )
            self._rx_buf = b""
            self.connected.emit(port)
            self.read_timer.start()
        except Exception as e:
            self.serial = None
            self.error.emit(f"No se pudo abrir {port}: {e}")

    @Slot()
    def disconnect_port(self):
        """Cierra el puerto."""
        if self.read_timer:
            self.read_timer.stop()

        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
            except Exception:
                pass

        self.serial = None
        self.disconnected.emit()

    @Slot(str)
    def send(self, cmd: str):
        """Envía una línea por el puerto."""
        if not self.serial or not self.serial.is_open:
            self.error.emit("No hay puerto abierto")
            return

        try:
            payload = (cmd.strip() + "\r\n").encode("ascii", errors="replace")
            self.serial.write(payload)
            self.line_sent.emit(cmd.strip())
        except Exception as e:
            self.error.emit(f"Error al enviar: {e}")

    def _poll(self):
        """Lee líneas disponibles del puerto."""
        if not self.serial or not self.serial.is_open:
            return

        try:
            # readline() con timeout=0 devuelve fragmentos si la linea
            # llega partida entre paquetes USB (p.ej. un "OK" cortado).
            # Se acumula en un buffer y solo se emiten lineas completas.
            n = self.serial.in_waiting
            if n:
                self._rx_buf += self.serial.read(n)
            while b"\n" in self._rx_buf:
                raw, self._rx_buf = self._rx_buf.split(b"\n", 1)
                text = raw.decode("utf-8", errors="replace").rstrip("\r")
                if text:
                    self.line_received.emit(text)
        except Exception as e:
            self.error.emit(f"Error al leer: {e}")
            self.disconnect_port()


def list_ports():
    """Devuelve la lista de puertos serie disponibles."""
    ports = []
    for p in serial.tools.list_ports.comports():
        # p.device = /dev/ttyACM0
        # p.description = "STM32 Virtual COM Port"
        ports.append({
            "device": p.device,
            "description": p.description or "—",
        })
    return ports
