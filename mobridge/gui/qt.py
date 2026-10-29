"""Qt binding shim: prefer PySide6, fall back to PyQt6. Import Qt from here."""
try:
    from PySide6 import QtCore, QtGui, QtWidgets          # noqa: F401
    from PySide6.QtCore import Qt, QThread, Signal, Slot   # noqa: F401
    BINDING = 'PySide6'
except ImportError:  # pragma: no cover - environment dependent
    from PyQt6 import QtCore, QtGui, QtWidgets             # noqa: F401
    from PyQt6.QtCore import Qt, QThread                   # noqa: F401
    from PyQt6.QtCore import pyqtSignal as Signal          # noqa: F401
    from PyQt6.QtCore import pyqtSlot as Slot              # noqa: F401
    BINDING = 'PyQt6'
