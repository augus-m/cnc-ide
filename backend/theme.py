STYLESHEET_DARK = """
QWidget {
    background-color: #0d0e12;
    color: #e8e8eb;
    font-family: "Inter", "Segoe UI", sans-serif;
    font-size: 12px;
}
QMainWindow { background-color: #0d0e12; }
#Topbar { background-color: #16171c; border-bottom: 1px solid #2a2d36; }
#Topbar QLabel { background: transparent; }
#LogoMark { background-color: #ff4422; color: #0d0e12; border-radius: 5px; font-weight: 700; font-size: 12px; }
#LogoText { font-weight: 600; letter-spacing: 0.5px; font-size: 13px; }
#ConnStatus { background-color: #1c1e24; border: 1px solid #2a2d36; border-radius: 12px; padding: 4px 12px; color: #9aa0ac; font-size: 11px; }
#ConnStatus[connected="true"] { color: #4ade80; border-color: rgba(74, 222, 128, 0.4); }
#ConnStatus[connected="false"] { color: #ef4444; border-color: rgba(239, 68, 68, 0.4); }
QPushButton#TopbarBtn { background-color: #1c1e24; border: 1px solid #2a2d36; color: #e8e8eb; border-radius: 6px; padding: 6px 14px; font-size: 11px; font-weight: 500; }
QPushButton#TopbarBtn:hover { background-color: #23252c; border-color: #ff4422; }
#Sidebar { background-color: #16171c; border-right: 1px solid #2a2d36; }
#SectionTitle { color: #5d6270; font-size: 10px; font-weight: 700; letter-spacing: 2px; padding: 10px 16px 6px; background: transparent; }
#FileItem { padding: 7px 16px 7px 28px; color: #9aa0ac; font-size: 12px; border: none; text-align: left; }
#FileItem:hover { background-color: #1c1e24; color: #e8e8eb; }
#ViewerTabs { background-color: #16171c; border-bottom: 1px solid #2a2d36; }
#ViewerTab { background: transparent; color: #5d6270; padding: 8px 16px; font-size: 12px; border: none; border-bottom: 2px solid transparent; }
#ViewerTab:hover { color: #9aa0ac; }
#ViewerTab[active="true"] { color: #e8e8eb; border-bottom: 2px solid #ff4422; }
#ViewerCanvas { background-color: #0d0e12; }
#RightPanel { background-color: #16171c; border-left: 1px solid #2a2d36; }
#Card { background-color: #1c1e24; border: 1px solid #2a2d36; border-radius: 8px; }
#CardTitle { color: #5d6270; font-size: 10px; font-weight: 700; letter-spacing: 2px; background: transparent; }
#CoordLabel { color: #9aa0ac; font-size: 12px; background: transparent; }
#CoordValue { color: #ff4422; font-family: monospace; font-size: 16px; font-weight: 600; background: transparent; }
#CoordValueDim { color: #5d6270; font-family: monospace; font-size: 16px; font-weight: 600; background: transparent; }
#StateBadge { background-color: rgba(74, 222, 128, 0.15); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.3); border-radius: 12px; padding: 3px 12px; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
#StateBadge[state="idle"] { background-color: rgba(148, 163, 184, 0.15); color: #94a3b8; }
#StateBadge[state="run"] { background-color: rgba(96, 165, 250, 0.15); color: #60a5fa; }
#StateBadge[state="paused"] { background-color: rgba(251, 191, 36, 0.15); color: #fbbf24; }
#StateBadge[state="alarm"] { background-color: rgba(239, 68, 68, 0.15); color: #ef4444; }
QProgressBar#Progress { background-color: #23252c; border: none; border-radius: 3px; height: 6px; text-align: center; }
QProgressBar#Progress::chunk { background-color: #ff4422; border-radius: 3px; }
#Console { background-color: #16171c; border-top: 1px solid #2a2d36; }
#ConsoleHeader { background-color: #1c1e24; border-bottom: 1px solid #2a2d36; color: #5d6270; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
#ConsoleBody { background-color: #0d0e12; border: none; color: #9aa0ac; font-family: "SF Mono", Consolas, monospace; font-size: 11px; padding: 8px 12px; }
#ConsoleInput { background-color: #1c1e24; border: none; border-top: 1px solid #2a2d36; color: #e8e8eb; font-family: monospace; font-size: 12px; padding: 10px 14px; }
#ConsoleInput:focus { border-top: 1px solid #ff4422; }
#ConsoleSendBtn { background-color: #ff4422; color: #0d0e12; border: none; padding: 0 24px; font-weight: 700; font-size: 12px; }
#ConsoleSendBtn:hover { background-color: #ff5533; }
#ControlsBar { background-color: #16171c; border: 1px solid #2a2d36; border-radius: 12px; }
QPushButton#CtrlStart { background-color: #4ade80; color: #0d0e12; border: none; border-radius: 8px; padding: 8px 16px; font-size: 12px; font-weight: 700; }
QPushButton#CtrlPause { background-color: #fbbf24; color: #0d0e12; border: none; border-radius: 8px; padding: 8px 16px; font-size: 12px; font-weight: 700; }
QPushButton#CtrlStop { background-color: #ef4444; color: white; border: none; border-radius: 8px; padding: 8px 16px; font-size: 12px; font-weight: 700; }
QPushButton#CtrlEstop { background-color: #dc2626; color: white; border: 1px solid #7f1d1d; border-radius: 8px; padding: 8px 20px; font-size: 12px; font-weight: 700; }
QDialog { background-color: #16171c; }
QDialog QLabel { background: transparent; color: #e8e8eb; }
QComboBox, QSpinBox, QLineEdit { background-color: #1c1e24; border: 1px solid #2a2d36; border-radius: 6px; padding: 6px 10px; color: #e8e8eb; min-height: 20px; }
QComboBox:hover, QSpinBox:focus, QLineEdit:focus { border-color: #ff4422; }
QPushButton#DlgPrimary { background-color: #ff4422; color: #0d0e12; border: none; border-radius: 6px; padding: 8px 20px; font-weight: 700; }
QPushButton#DlgSecondary { background-color: #1c1e24; color: #e8e8eb; border: 1px solid #2a2d36; border-radius: 6px; padding: 8px 20px; }
QPushButton#DlgSecondary:hover { background-color: #23252c; border-color: #ff4422; }
QSlider::groove:horizontal { background: #23252c; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: #ff4422; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #ff4422; border-radius: 2px; }
QScrollBar:vertical { background-color: #0d0e12; width: 10px; border: none; }
QScrollBar::handle:vertical { background-color: #2a2d36; border-radius: 5px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background-color: #0d0e12; height: 10px; border: none; }
QScrollBar::handle:horizontal { background-color: #2a2d36; border-radius: 5px; min-width: 20px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
"""

# Alias para compatibilidad (importado por main.py)
STYLESHEET = STYLESHEET_DARK


# ============================================================
# TEMA CLARO - estilo papel, botones con acento naranja
# ============================================================
STYLESHEET_LIGHT = """
QWidget {
    background-color: #f8f4ec;
    color: #2a2620;
    font-family: "Inter", "Segoe UI", sans-serif;
    font-size: 12px;
}
QMainWindow { background-color: #f8f4ec; }

/* ---- Topbar ---- */
#Topbar { background-color: #f0eadd; border-bottom: 1px solid #e2d9c8; }
#Topbar QLabel { background: transparent; }
#LogoMark { background: transparent; }
#LogoText { font-weight: 600; letter-spacing: 0.5px; font-size: 13px; color: #2a2620; }
#ConnStatus { background-color: #fdfbf7; border: 1px solid #e2d9c8; border-radius: 12px; padding: 4px 12px; color: #6b6358; font-size: 11px; }
#ConnStatus[connected="true"] { color: #15803d; border-color: rgba(21, 128, 61, 0.5); }
#ConnStatus[connected="false"] { color: #b91c1c; border-color: rgba(185, 28, 28, 0.5); }

/* ---- Botones topbar: acento naranja ---- */
QPushButton#TopbarBtn {
    background-color: #fdfbf7;
    border: 1px solid #e2d9c8;
    color: #3a3428;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#TopbarBtn:hover {
    background-color: #ffe6d2;
    border-color: #ff8243;
    color: #b64a15;
}
QPushButton#TopbarBtn:pressed {
    background-color: #ff8243;
    color: #ffffff;
    border-color: #ff8243;
}

/* ---- Sidebar ---- */
#Sidebar { background-color: #f0eadd; border-right: 1px solid #e2d9c8; }
#SectionTitle { color: #8a8276; font-size: 10px; font-weight: 700; letter-spacing: 2px; padding: 10px 16px 6px; background: transparent; }

/* ---- FileItem del sidebar ---- */
#FileItem {
    padding: 7px 16px 7px 28px;
    color: #5a544c;
    font-size: 12px;
    border: none;
    text-align: left;
    background: transparent;
}
#FileItem:hover {
    background-color: #ffe6d2;
    color: #b64a15;
    border-left: 3px solid #ff8243;
    padding-left: 25px;
}
#FileItem:pressed {
    background-color: #ff8243;
    color: #ffffff;
}

/* ---- Tabs del visor ---- */
#ViewerTabs { background-color: #fdfbf7; border-bottom: 1px solid #e2d9c8; }
#ViewerTab { background: transparent; color: #8a8276; padding: 8px 16px; font-size: 12px; border: none; border-bottom: 2px solid transparent; }
#ViewerTab:hover { color: #b64a15; }
#ViewerTab[active="true"] { color: #b64a15; border-bottom: 2px solid #ff8243; font-weight: 600; }

/* ---- Canvas ---- */
#ViewerCanvas { background-color: #f8f4ec; }
#RightPanel { background-color: #fdfbf7; border-left: 1px solid #e2d9c8; }

/* ---- Cards ---- */
#Card { background-color: #fdfbf7; border: 1px solid #e2d9c8; border-radius: 8px; }
#CardTitle { color: #8a8276; font-size: 10px; font-weight: 700; letter-spacing: 2px; background: transparent; }
#CoordLabel { color: #5a544c; font-size: 12px; background: transparent; }
#CoordValue { color: #d9691f; font-family: monospace; font-size: 16px; font-weight: 600; background: transparent; }
#CoordValueDim { color: #8a8276; font-family: monospace; font-size: 16px; font-weight: 600; background: transparent; }

/* ---- Badge de estado ---- */
#StateBadge { background-color: rgba(21, 128, 61, 0.12); color: #15803d; border: 1px solid rgba(21, 128, 61, 0.35); border-radius: 12px; padding: 3px 12px; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
#StateBadge[state="idle"] { background-color: rgba(107, 99, 88, 0.12); color: #6b6358; }
#StateBadge[state="run"] { background-color: rgba(217, 105, 31, 0.15); color: #b64a15; }
#StateBadge[state="paused"] { background-color: rgba(180, 83, 9, 0.15); color: #b45309; }
#StateBadge[state="alarm"] { background-color: rgba(185, 28, 28, 0.12); color: #b91c1c; }

/* ---- Progress ---- */
QProgressBar#Progress { background-color: #e8dfcc; border: none; border-radius: 3px; height: 6px; text-align: center; }
QProgressBar#Progress::chunk { background-color: #ff8243; border-radius: 3px; }

/* ---- Consola ---- */
#Console { background-color: #fdfbf7; border-top: 1px solid #e2d9c8; }
#ConsoleHeader { background-color: #f0eadd; border-bottom: 1px solid #e2d9c8; color: #8a8276; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
#ConsoleBody { background-color: #fdfbf7; border: none; color: #5a544c; font-family: "SF Mono", Consolas, monospace; font-size: 11px; padding: 8px 12px; }
#ConsoleInput { background-color: #f0eadd; border: none; border-top: 1px solid #e2d9c8; color: #2a2620; font-family: monospace; font-size: 12px; padding: 10px 14px; }
#ConsoleInput:focus { border-top: 1px solid #ff8243; }
#ConsoleSendBtn { background-color: #ff8243; color: #ffffff; border: none; padding: 0 24px; font-weight: 700; font-size: 12px; }
#ConsoleSendBtn:hover { background-color: #ff9a66; }

/* ---- Controles de job ---- */
#ControlsBar { background-color: #fdfbf7; border: 1px solid #e2d9c8; border-radius: 12px; }
QPushButton#CtrlStart { background-color: #15803d; color: #ffffff; border: none; border-radius: 8px; padding: 8px 16px; font-size: 12px; font-weight: 700; }
QPushButton#CtrlStart:hover { background-color: #16a34a; }
QPushButton#CtrlPause { background-color: #ca8a04; color: #ffffff; border: none; border-radius: 8px; padding: 8px 16px; font-size: 12px; font-weight: 700; }
QPushButton#CtrlPause:hover { background-color: #eab308; }
QPushButton#CtrlStop { background-color: #dc2626; color: #ffffff; border: none; border-radius: 8px; padding: 8px 16px; font-size: 12px; font-weight: 700; }
QPushButton#CtrlStop:hover { background-color: #ef4444; }
QPushButton#CtrlEstop { background-color: #991b1b; color: #ffffff; border: 1px solid #7f1d1d; border-radius: 8px; padding: 8px 20px; font-size: 12px; font-weight: 700; }
QPushButton#CtrlEstop:hover { background-color: #b91c1c; }

/* ---- Dialogos ---- */
QDialog { background-color: #fdfbf7; }
QDialog QLabel { background: transparent; color: #2a2620; }

QComboBox, QSpinBox, QLineEdit {
    background-color: #fdfbf7;
    border: 1px solid #e2d9c8;
    border-radius: 6px;
    padding: 6px 10px;
    color: #2a2620;
    min-height: 20px;
}
QComboBox:hover, QSpinBox:focus, QLineEdit:focus { border-color: #ff8243; }

QPushButton#DlgPrimary { background-color: #ff8243; color: #ffffff; border: none; border-radius: 6px; padding: 8px 20px; font-weight: 700; }
QPushButton#DlgPrimary:hover { background-color: #ff9a66; }
QPushButton#DlgSecondary { background-color: #f0eadd; color: #3a3428; border: 1px solid #e2d9c8; border-radius: 6px; padding: 8px 20px; }
QPushButton#DlgSecondary:hover { background-color: #ffe6d2; border-color: #ff8243; color: #b64a15; }

/* ---- Tabs (QTabWidget) ---- */
QTabWidget::pane { background-color: #fdfbf7; border-top: 1px solid #e2d9c8; }
QTabBar::tab {
    background-color: #f0eadd;
    color: #8a8276;
    padding: 6px 16px;
    border: none;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}
QTabBar::tab:selected {
    background-color: #fdfbf7;
    color: #b64a15;
    border-bottom: 2px solid #ff8243;
}
QTabBar::tab:hover { color: #b64a15; }

/* ---- Checkboxes ---- */
QCheckBox { color: #3a3428; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid #c8bda5; background: #fdfbf7; }
QCheckBox::indicator:checked { background: #ff8243; border-color: #ff8243; }

/* ---- Sliders ---- */
QSlider::groove:horizontal { background: #e8dfcc; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: #ff8243; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #ff8243; border-radius: 2px; }

/* ---- Scrollbars ---- */
QScrollBar:vertical { background-color: #f8f4ec; width: 10px; border: none; }
QScrollBar::handle:vertical { background-color: #d4c8b0; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #c8b898; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background-color: #f8f4ec; height: 10px; border: none; }
QScrollBar::handle:horizontal { background-color: #d4c8b0; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #c8b898; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
"""
