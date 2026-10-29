from ..qt import QtWidgets, Qt


class Screen(QtWidgets.QWidget):
    """A navigation page. `title` is the sidebar label; `flag` (if set) is the
    cfg.ini [Options] key that must be true for the page to be shown."""
    title = 'Screen'
    flag = None

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

    @staticmethod
    def pending(text):
        """A short note in plain words (e.g. 'Coming soon')."""
        lbl = QtWidgets.QLabel(text)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lbl.setObjectName('pendingNote')
        return lbl

    @staticmethod
    def blocked_note(text):
        """A note in plain words on why a control is unavailable."""
        lbl = QtWidgets.QLabel(text)
        lbl.setWordWrap(True)
        lbl.setObjectName('blockedNote')
        return lbl

    def header(self, layout, title, subtitle=''):
        h = QtWidgets.QLabel(title)
        h.setObjectName('screenTitle')
        f = h.font(); f.setPointSize(f.pointSize() + 4); f.setBold(True); h.setFont(f)
        layout.addWidget(h)
        if subtitle:
            s = QtWidgets.QLabel(subtitle)
            s.setObjectName('screenSubtitle')
            s.setWordWrap(True)
            layout.addWidget(s)
