"""Smoke test for the official-skin window: builds offscreen, every official tab
renders at the reference size, and nothing in the skin is editable."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
QtWidgets = pytest.importorskip('PySide6.QtWidgets')


def test_official_window_renders():
    from mobridge.gui.official.window import OfficialMainWindow
    from mobridge.gui.official.screens import OFFICIAL_SCREENS
    from mobridge.gui.official import data
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = OfficialMainWindow([])
    w.show(); app.processEvents()
    # Scripting now lives in Tools ▸ Scripting (project call), so the tab bar shows
    # the other four official screens; its page still exists and is reachable.
    # offline (FUN_140037060): CONFIGURATION + CHANNEL EQ only; connected adds the rest
    assert [t.label for t in w.chrome.tabs if t.visible] == ['CONFIGURATION', 'CHANNEL EQ']
    w.apply_menu_state(True, 0x180cf, 210)
    assert [t.label for t in w.chrome.tabs if t.visible] == [c.title for c in OFFICIAL_SCREENS if c.title != 'SCRIPTING']
    w.open_scripting(); app.processEvents()
    assert w.stack.currentIndex() == w.pages[4]
    for k in list(range(5)) + ['device']:
        w.select(k); app.processEvents()
        img = w.grab().toImage()
        assert img.width() >= 1300 and not img.isNull()
    assert data.INPUT_GAINS['Beeps'] == -2.0
    assert data.CHANNELS['rightsubwoofer']['gain_db'] == -3.0


def test_popup_pick_edits_the_model_and_rebuilds():
    """A combo pick changes the value AND the model (official: live apply)."""
    from mobridge.gui.official.window import OfficialMainWindow
    from mobridge.gui.official.canvas import Combo, Spin
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = OfficialMainWindow([])
    w.show(); app.processEvents()
    cfg = w.stack.widget(w.pages[0])
    root = cfg.root
    combo = next(i for i in cfg.items if isinstance(i, Combo) and i.active)
    spin = next(i for i in cfg.items if isinstance(i, Spin) and i.active)
    assert combo.current == 'CH4 [Output Channel]'          # Right Sub Woofer on CH4
    cfg.set_hot(spin, 'up'); spin.pressed = True
    assert spin.state('up') == 'pressed' and spin.state('down') is None
    pop = cfg.open_popup(combo)
    pop.itemClicked.emit(pop.item(0))                        # "No Mapping"
    app.processEvents()
    assert root.channel_for_role(0x2000) == -1               # unmapped in the model
    combo = next(i for i in cfg.items if isinstance(i, Combo))
    assert combo.current == 'No Mapping'                     # screen rebuilt from the model
    w.close()
