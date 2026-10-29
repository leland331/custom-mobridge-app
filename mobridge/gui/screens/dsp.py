from ..qt import QtWidgets
from .base import Screen


class DspScreen(Screen):
    """UI_SPEC §3: DSP tuning. Sub-tabs are stubbed from the spec; exact controls,
    ranges and the vehicle/EQ graphics come from the UI export (marked PENDING)."""
    title = 'DSP / EQ'

    TABS = [
        ('Speaker Map', 'Assign each speaker to an output channel (speakermap/speakerprops).'),
        ('Channel EQ', 'Per-channel parametric EQ; filter type/model, Lo-P/Hi-P + slope.'),
        ('Master EQ', 'Global EQ across all channels.'),
        ('Gain / Delay / Phase', 'Per-channel gain, time alignment, phase, mute.'),
        ('Inputs / Source', 'Input gain/delay, source switch, head-unit control.'),
        ('Presets', 'Preset / appdata management.'),
        ('DSP Test', 'DspTest (may play audio — bus-tx safety class).'),
    ]

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'DSP / EQ', 'Tuning is on the CONFIGURATION, CHANNEL EQ, TIME ALIGN and LIMITER tabs.')
        tabs = QtWidgets.QTabWidget()
        for name, desc in self.TABS:
            w = QtWidgets.QWidget(); wl = QtWidgets.QVBoxLayout(w)
            wl.addWidget(QtWidgets.QLabel(desc))
            wl.addWidget(self.pending('Use the %s controls on the tuning tabs.' % name))
            wl.addStretch(1)
            tabs.addTab(w, name)
        lay.addWidget(tabs, 1)
