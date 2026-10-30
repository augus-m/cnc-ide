"""Editor de G-code con numeros de linea, syntax highlighting y analisis."""

import re
from gcode_model import parse_program
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import (
    QColor, QPainter, QTextFormat, QSyntaxHighlighter, QTextCharFormat,
    QFont, QTextCursor
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit


# Colores
C_EDITOR_BG       = "#0d0e12"
C_EDITOR_TEXT     = "#e8e8eb"
C_EDITOR_LINENUM  = "#3a3f4a"
C_EDITOR_LINECUR  = "#1a1c22"
C_EDITOR_JOBLINE  = "#3a1818"
C_EDITOR_G0       = "#fbbf24"   # amarillo - rapid
C_EDITOR_G1       = "#ff8c42"   # naranja - corte
C_EDITOR_G2       = "#a855f7"   # morado - arcos
C_EDITOR_M        = "#4ade80"   # verde - M-codes
C_EDITOR_COORD    = "#60a5fa"   # azul - coordenadas
C_EDITOR_FEED     = "#f472b6"   # rosa - feed
C_EDITOR_IJK      = "#fb923c"   # naranja claro - I/J/K
C_EDITOR_COMMENT  = "#5d6270"   # gris - comentarios
C_EDITOR_NLINE    = "#5d6270"   # gris - N lineas


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


class GCodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)

        # Inicializar estado ANTES de conectar señales que llaman a
        # highlight_current_line() -- si no, peta
        self._job_line = -1
        self._error_lines = set()

        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)

        self.update_line_number_area_width(0)

        self._highlighter = GCodeHighlighter(self.document())

        self.highlight_current_line()

        # Fuente monoespaciada
        font = QFont("SF Mono", 11)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.setTabStopDistance(40)

    def line_number_area_width(self):
        digits = 1
        max_val = max(1, self.blockCount())
        while max_val >= 10:
            max_val //= 10
            digits += 1
        return 12 + self.fontMetrics().horizontalAdvance('9') * digits

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(),
                                          self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(cr.left(), cr.top(),
                  self.line_number_area_width(), cr.height()))

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor(C_EDITOR_BG))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                if block_number == self._job_line:
                    painter.setPen(QColor("#ff4422"))
                elif block_number in self._error_lines:
                    painter.setPen(QColor("#ef4444"))
                else:
                    painter.setPen(QColor(C_EDITOR_LINENUM))
                painter.drawText(0, top,
                                 self.line_number_area.width() - 6,
                                 self.fontMetrics().height(),
                                 Qt.AlignRight, number)
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def highlight_current_line(self):
        selections = []

        # Linea actual del cursor
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(C_EDITOR_LINECUR))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        selections.append(sel)

        # Linea del job en ejecucion
        if self._job_line >= 0:
            doc_block = self.document().findBlockByNumber(self._job_line)
            if doc_block.isValid():
                sel2 = QTextEdit.ExtraSelection()
                sel2.format.setBackground(QColor(C_EDITOR_JOBLINE))
                sel2.format.setProperty(QTextFormat.FullWidthSelection, True)
                sel2.cursor = QTextCursor(doc_block)
                sel2.cursor.clearSelection()
                selections.append(sel2)

        # Lineas con error
        for ln in self._error_lines:
            doc_block = self.document().findBlockByNumber(ln)
            if doc_block.isValid():
                sel3 = QTextEdit.ExtraSelection()
                sel3.format.setBackground(QColor("#3a0808"))
                sel3.format.setProperty(QTextFormat.FullWidthSelection, True)
                sel3.cursor = QTextCursor(doc_block)
                sel3.cursor.clearSelection()
                selections.append(sel3)

        self.setExtraSelections(selections)

    def set_job_line(self, line_number):
        """Marca la linea N (0-indexed) o -1 para ninguna."""
        self._job_line = line_number
        self.highlight_current_line()

    def set_error_lines(self, lines_set):
        """Lista de numeros de linea (0-indexed) a marcar en rojo."""
        self._error_lines = set(lines_set)
        self.highlight_current_line()

    def scroll_to_line(self, line_number):
        """Hace scroll a la linea indicada."""
        if line_number < 0 or line_number >= self.blockCount():
            return
        doc_block = self.document().findBlockByNumber(line_number)
        if doc_block.isValid():
            cursor = QTextCursor(doc_block)
            self.setTextCursor(cursor)
            self.centerCursor()

    def load_lines(self, lines):
        self.setPlainText("\n".join(lines))
        self._job_line = -1
        self._error_lines = set()

    def get_lines(self):
        text = self.toPlainText()
        return [ln for ln in text.split("\n")]


class GCodeHighlighter(QSyntaxHighlighter):
    def __init__(self, parent):
        super().__init__(parent)
        self.rules = []

        def add_rule(pattern, color, bold=False):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Bold)
            self.rules.append((re.compile(pattern), fmt))

        # N lineas al inicio
        add_rule(r"^N\d+\s*", C_EDITOR_NLINE)

        # Comentarios
        add_rule(r";[^\n]*", C_EDITOR_COMMENT)
        add_rule(r"\([^)]*\)", C_EDITOR_COMMENT)

        # G-codes especificos
        add_rule(r"\bG0\b", C_EDITOR_G0, True)
        add_rule(r"\bG1\b", C_EDITOR_G1, True)
        add_rule(r"\bG2\b", C_EDITOR_G2, True)
        add_rule(r"\bG3\b", C_EDITOR_G2, True)
        add_rule(r"\bG\d+(\.\d+)?\b", C_EDITOR_G2, True)

        # M-codes
        add_rule(r"\bM\d+\b", C_EDITOR_M, True)

        # Coordenadas
        add_rule(r"\b[XYZ][-+]?\d*\.?\d+", C_EDITOR_COORD)

        # I/J/K
        add_rule(r"\b[IJK][-+]?\d*\.?\d+", C_EDITOR_IJK)

        # Feed
        add_rule(r"\b[FS][-+]?\d*\.?\d+", C_EDITOR_FEED)

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, fmt)


# ============================================================
# PARSER DE G-CODE
# ============================================================

def _extract_param(line, letter):
    """Extrae el valor de un parametro tipo 'X10.5' o 'X-3'."""
    m = re.search(rf"{letter}([-+]?\d*\.?\d+)", line)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_gcode_stats(lines):
    """Estadisticas producidas por el mismo analizador que alimenta el visor."""
    return parse_program(lines)["stats"]


def parse_gcode_toolpath(lines):
    """Devuelve la trayectoria normalizada y muestreada, conservando indices."""
    return parse_program(lines)["segments"]
