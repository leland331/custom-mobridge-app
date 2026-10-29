"""The `widget` Lua library (fdluabind::widget, LUA_SCRIPTING.md Group A §A4 and
addenda #2/#3): twelve factories that build Qt widgets inside the Scripting pane.
Element methods per type as in the exe; signals are bound by name at runtime
through obj:register("<signal>", fn), as the exe's ElementSignalMapperBase does."""
from __future__ import annotations

FACTORIES = ('rootLayout', 'button', 'vlayout', 'hlayout', 'formlayout', 'color', 'tabwidget',
             'stackedwidget', 'progressbar', 'quick', 'objectbuilder', 'getOpenFilename')


class WidgetLib:
    def __init__(self, host, pane_layout):
        self.host = host                # ScriptHost (log, lua)
        self.root = pane_layout         # QBoxLayout of the script pane
        self.created = []

    # --- wrapping ----------------------------------------------------------
    def _t(self, d):
        return self.host.lua.table_from(d)

    def _unwrap(self, o):
        try:
            return o['_q']
        except Exception:
            return o

    def element(self, q):
        from .gui.qt import QtWidgets
        self.created.append(q)
        host = self.host
        d = {'_q': q}

        def register(_s, signal, fn):
            sig = getattr(q, str(signal), None)
            if sig is None or not hasattr(sig, 'connect'):
                return False

            def call(*a):
                try:
                    fn(*a)
                except Exception as ex:
                    host.log('Could not call callback\n(%s)' % ex)
            sig.connect(call)
            return True
        d['register'] = register
        d['className'] = lambda _s: q.metaObject().className()
        d['setName'] = lambda _s, n: q.setObjectName(str(n))
        if isinstance(q, QtWidgets.QLayout):
            d.update(self._layout_methods(q))
            return self._t(d)
        d.update({
            'setText': lambda _s, t: q.setText(str(t)) if hasattr(q, 'setText') else
            q.setFormat(str(t)) if hasattr(q, 'setFormat') else None,
            'getText': lambda _s: q.text() if hasattr(q, 'text') else '',
            'setEnabled': lambda _s, on: q.setEnabled(bool(on)),
            'setVisible': lambda _s, on: q.setVisible(bool(on)),
            'visible': lambda _s: q.isVisible(),
            'setUpdates': lambda _s, on: q.setUpdatesEnabled(bool(on)),
            'updates': lambda _s: q.updatesEnabled(),
            'findChild': lambda _s, n: self._find(q, str(n)),
        })
        if isinstance(q, QtWidgets.QProgressBar):
            d['setValue'] = lambda _s, v: q.setValue(int(v))
            d['setRange'] = lambda _s, a, b: q.setRange(int(a), int(b))
        if isinstance(q, (QtWidgets.QTabWidget, QtWidgets.QStackedWidget)):
            d['addWidget'] = (lambda _s, w, label='': q.addTab(self._unwrap(w), str(label))) \
                if isinstance(q, QtWidgets.QTabWidget) else (lambda _s, w: q.addWidget(self._unwrap(w)))
            d['setCurrentIndex'] = lambda _s, i: q.setCurrentIndex(int(i))
        return self._t(d)

    def _find(self, q, name):
        from .gui.qt import QtCore
        c = q.findChild(QtCore.QObject, name)
        return self.element(c) if c is not None else None

    def _layout_methods(self, lay):
        from .gui.qt import QtWidgets

        def add(_s, obj, *rest):
            x = self._unwrap(obj)
            if isinstance(x, QtWidgets.QLayout):
                lay.addLayout(x)
            else:
                lay.addWidget(x)
            return True

        def insert(_s, i, obj):
            x = self._unwrap(obj)
            (lay.insertLayout if isinstance(x, QtWidgets.QLayout) else lay.insertWidget)(int(i), x)
            return True

        def item_at(_s, i):
            it = lay.itemAt(int(i))
            if it is None:
                return None
            return self.element(it.widget() or it.layout())
        return {'append': add, 'addWidget': add, 'insert': insert, 'itemAt': item_at,
                'addSpacing': lambda _s, n: lay.addSpacing(int(n)),
                'addStretch': lambda _s, n=0: lay.addStretch(int(n or 0)),
                'insertSpacing': lambda _s, i, n: lay.insertSpacing(int(i), int(n)),
                'insertStretch': lambda _s, i, n=0: lay.insertStretch(int(i), int(n or 0))}

    # --- factories -----------------------------------------------------------
    def table(self):
        from .gui.qt import QtGui, QtWidgets

        def color(r=0, g=0, b=0, a=255):
            c = QtGui.QColor(int(r), int(g), int(b), int(a))
            return self._color(c)

        def get_open(caption='', d='', flt=''):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(None, str(caption or ''), str(d or ''), str(flt or ''))
            return path

        def quick(*_a):
            from PySide6 import QtQuickWidgets
            q = QtQuickWidgets.QQuickWidget()
            el = self.element(q)
            el.setSource = lambda _s, url: q.setSource(__import__('PySide6.QtCore', fromlist=['QUrl']).QUrl(str(url)))
            el.setSourceData = lambda _s, data: q.setSource(
                __import__('PySide6.QtCore', fromlist=['QUrl']).QUrl('data:text/plain,' + str(data)))
            el.setContextProperty = lambda _s, k, v: q.rootContext().setContextProperty(str(k), v)
            el.rootObject = lambda _s: q.rootObject()
            el.errors = lambda _s: [str(x.toString()) for x in q.errors()]
            return el
        return self._t({
            'rootLayout': lambda *a: self.element(self.root),
            'button': lambda text='': self.element(QtWidgets.QPushButton(str(text or ''))),
            'vlayout': lambda *a: self.element(QtWidgets.QVBoxLayout()),
            'hlayout': lambda *a: self.element(QtWidgets.QHBoxLayout()),
            'formlayout': lambda *a: self.element(QtWidgets.QFormLayout()),
            'color': color,
            'tabwidget': lambda *a: self.element(QtWidgets.QTabWidget()),
            'stackedwidget': lambda *a: self.element(QtWidgets.QStackedWidget()),
            'progressbar': lambda *a: self.element(QtWidgets.QProgressBar()),
            'quick': quick,
            'objectbuilder': lambda *a: self.element(QtWidgets.QWidget()),
            'getOpenFilename': get_open,
        })

    def _color(self, c):
        return self._t({'_q': c, 'darker': lambda _s, f=200: self._color(c.darker(int(f))),
                        'lighter': lambda _s, f=150: self._color(c.lighter(int(f))),
                        'name': lambda _s: c.name()})

    def clear(self):
        """ScriptWidget::reset: delete what the script created in the pane."""
        from .gui.qt import QtWidgets
        for q in self.created:
            if isinstance(q, QtWidgets.QWidget) and q is not None:
                try:
                    q.deleteLater()
                except RuntimeError:
                    pass
        self.created = []
