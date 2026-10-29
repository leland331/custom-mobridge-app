"""Dark/red theme matching the official mObridge DSP v25.8.0 app, reconstructed
from the connected-screen video + the carved QSS palette. Near-black brushed
panels, mObridge red accent, red-bordered value fields, Segoe UI."""

RED = '#d1322c'          # mObridge red (tab underline, selection, borders)
RED_HILITE = '#e0453f'
WINDOW_BG = '#1b1b1b'
PANEL_BG = '#232323'
FIELD_BG = '#141414'
TEXT = '#e9e9e9'
TEXT_DIM = '#9a9a9a'
ORANGE = '#ff7a1a'       # EQ curve

QSS = f"""
* {{ font-family: "Droid Sans", "Segoe UI"; color: {TEXT}; font-size: 14px; }}
QMainWindow, QWidget#central {{ background: {WINDOW_BG}; }}

QMenuBar {{ background: #262626; color: {TEXT}; padding: 2px 6px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; }}
QMenuBar::item:selected {{ background: {RED}; color: #fff; border-radius: 3px; }}
QMenu {{ background: #2a2a2a; border: 1px solid #3a3a3a; }}
QMenu::item:selected {{ background: {RED}; color: #fff; }}

QWidget#header {{ background: {WINDOW_BG}; }}
QLabel#wordmarkM {{ color: {RED}; font-size: 34px; font-weight: bold; }}
QLabel#wordmarkRest {{ color: #cfcfcf; font-size: 34px; }}

QWidget#tabstrip {{ background: {WINDOW_BG}; }}
QToolButton#tab {{ background: transparent; border: none; color: {TEXT_DIM};
    font-size: 16px; padding: 10px 18px; }}
QToolButton#tab:hover {{ color: #d8d8d8; }}
QToolButton#tab:checked {{ color: #ffffff; border-bottom: 3px solid {RED}; }}
QToolButton#tabExtra:checked {{ color: {RED}; border-bottom: 3px solid #6a6a6a; }}
QLabel#connected {{ color: #cfcfcf; font-size: 15px; }}
QToolButton#hdrBtn {{ background: #2b2b2b; border: 1px solid #3c3c3c; border-radius: 4px; padding: 4px 8px; }}
QToolButton#hdrBtn:hover {{ background: {RED}; }}

QWidget#stage {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #262626, stop:0.5 #1e1e1e, stop:1 #171717);
    border: 1px solid #3a3a3a; border-radius: 10px; }}

QLabel#speakerName {{ font-size: 26px; color: #f2f2f2; }}
QLabel#sectionTitle {{ font-size: 24px; color: #f2f2f2; }}
QLabel#groupLabel {{ color: {TEXT_DIM}; font-size: 13px; }}
QLabel#eqAxis {{ color: #8f8f8f; font-size: 12px; }}

QGroupBox {{ border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 12px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {TEXT_DIM}; }}

QLineEdit, QComboBox, QPlainTextEdit, QTreeWidget {{
    background: {FIELD_BG}; border: 1px solid #454545; border-radius: 4px; padding: 4px; }}
QDoubleSpinBox, QSpinBox {{ background: {FIELD_BG}; border: 1.5px solid {RED};
    border-radius: 3px; padding: 3px 4px; color: #fff; }}
QComboBox#mapCombo {{ border: 1.5px solid {RED}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: #202020; selection-background-color: {RED}; }}

QPushButton {{ background: #333333; border: 1px solid #4a4a4a; border-radius: 14px;
    padding: 7px 18px; color: {TEXT}; }}
QPushButton:hover {{ background: #404040; }}
QPushButton:disabled {{ color: #6a6a6a; border-color: #333; }}
QPushButton#destructiveButton {{ color: #fff; background: #7a1f1f; border-color: #9a2a2a; }}
QPushButton#pill {{ border-radius: 14px; background: #2e2e2e; }}
QPushButton#pill:hover {{ background: {RED}; }}

QPlainTextEdit#consoleLog, QPlainTextEdit#fifReport {{ font-family: "Consolas", monospace; font-size: 12px; }}
QLabel#pendingNote {{ color: #c9a227; font-style: italic; font-size: 12px; }}
QLabel#blockedNote {{ color: #e06c6c; font-weight: bold; font-size: 12px; }}
QStatusBar {{ background: #161616; color: {TEXT_DIM}; }}
QStatusBar QLabel {{ font-size: 12px; }}
QLabel#liveChip {{ color: #7ddc7d; font-size: 12px; }}
QLabel#blockChip {{ color: #e06c6c; font-size: 12px; }}
"""


def apply(app):
    app.setStyleSheet(QSS)
