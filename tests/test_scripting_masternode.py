"""Lua host, bus events, Reset/FactoryReset verdicts, Masternode screen."""
import io
import os
import zipfile
import zlib

from mobridge import busevents, scripting, parity, products
from mobridge.framing import MdiMessage


def _mod(d, body):
    with open(os.path.join(d, 'a.lua'), 'w') as f:
        f.write(body)
    with open(os.path.join(d, 'mod.yaml'), 'w') as f:
        f.write('Name: Test\nDescription: one\nScript: a.lua\nProduct: x\n')


def test_modules_and_run(tmp_path):
    _mod(tmp_path, 'print("hi", 1+1, nil)\nprint(require("m").x)\nprint(bit.band(6,3), io ~= nil, os ~= nil)')
    (tmp_path / 'm.lua').write_text('return {x=5}')
    mods = scripting.find_modules([str(tmp_path)])
    assert [m.selector_text for m in mods] == ['Test\none']
    out = []
    h = scripting.ScriptHost(log=out.append)
    assert h.run(mods[0].script)
    assert out == ['hi\t2\tnil', '5', '2\ttrue\ttrue']
    out.clear()
    assert h.restart() and out[0] == 'hi\t2\tnil'


def test_libs_in_g_and_send_needs_device(tmp_path):
    _mod(tmp_path, 'for _,n in ipairs({"fddspconnect","fddspmodel","timer","most","a2b","can","uds"}) do\n'
                   ' assert(_G[n], n) end\nlocal ok = pcall(function() can.message():send(0) end)\nprint(ok)\n'
                   'print(fddspconnect.instance():connected())')
    out = []
    h = scripting.ScriptHost(log=out.append)
    assert h.run(scripting.find_modules([str(tmp_path)])[0].script)
    assert out == ['false', 'false']


def test_script_error_is_logged_not_raised(tmp_path):
    _mod(tmp_path, 'error("boom")')
    out = []
    assert not scripting.ScriptHost(log=out.append).run(str(tmp_path / 'a.lua'))
    assert 'boom' in out[0]


def test_sma_import(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('mod.yaml', 'Name: N\nDescription: D\nScript: a.lua\n')
        z.writestr('a.lua', 'print(1)')
    (tmp_path / 'z.sma').write_bytes(zlib.compress(buf.getvalue()))
    dest = tmp_path / 'script' / 'z'
    assert scripting.import_sma(str(tmp_path / 'z.sma'), str(dest))
    assert scripting.find_modules([str(tmp_path / 'script')])[0].name == 'N'
    (tmp_path / 'bad.sma').write_bytes(b'nope')
    assert not scripting.import_sma(str(tmp_path / 'bad.sma'), str(dest))


def test_bus_events_routed_like_exe():
    ev = busevents.decode(0x0646, bytes.fromhex('00' '1234' '07' '0002' 'aabb'))
    assert ev.bus == 'a2b' and ev.fields == {'id': 0x1234, 'flags': 7, 'data': b'\xaa\xbb'}
    ev = busevents.decode(0x0615, bytes.fromhex('000002a0' '0000' '00' '00000000' '0102'))
    assert ev.bus == 'can' and ev.fields == {'id': 0x2a0, 'data': b'\x01\x02'}
    assert busevents.decode(0x0615, b'\x00\x00') is None          # shorter than the header
    for cmd in (0x0651, 0x070e, 0x0720):
        assert busevents.decode(cmd, b'\x00\x01') is None


def test_reset_verdicts_and_unlock():
    from mobridge.gui.engine import Engine as E
    assert 0x0010 not in parity.LOCKED_COMMANDS and 0x0011 not in parity.LOCKED_COMMANDS
    assert not ({0x0201, 0x0280, 0x0281} & set(parity.LOCKED_COMMANDS))   # flashing unlocked 23:18Z
    ok = E.device_job_ok
    assert ok(0x0010, MdiMessage(cmd=0x0010, data=b'')) is True
    assert ok(0x0010, MdiMessage(cmd=0x0fff, data=b'\x00\x01')) is None     # retry
    assert ok(0x0011, MdiMessage(cmd=0x0011, data=b'\x01')) is True
    assert ok(0x0011, MdiMessage(cmd=0x0fff, data=b'\x00\x02')) is True
    assert ok(0x0011, MdiMessage(cmd=0x0011, data=b'\x00')) is False
    assert ok(0x0011, None) is False


def test_masternode_tab_only_for_bit10(app):
    from mobridge import uirules as R
    assert not R.tab_visibility(True, products.flags(0x202c), 210, 12, True)['masternode']
    assert R.tab_visibility(True, products.flags(0x201c), 210, 8, True)['masternode']
    from mobridge.gui.official.masternode import MasternodeScreen
    s = MasternodeScreen()
    assert [b.text() for b in s.buttons] == ['Controls', 'Logging', 'Levels']
    assert s.buttons[0].isChecked()
    c = s.pages[0]
    assert [c.tabs.isTabEnabled(i) for i in range(5)] == [True, False, False, False, False]
    log = s.pages[1]
    log.add_event(busevents.decode(0x0646, bytes.fromhex('0012340700 02aabb'.replace(' ', ''))))
    assert log.tables['a2b'].rowCount() == 1 and log.tables['a2b'].item(0, 3).text() == 'AA BB'


def test_window_masternode_hidden_and_scripting_runs(app, tmp_path):
    from mobridge.gui.official.window import OfficialMainWindow as OfficialWindow
    w = OfficialWindow()
    w._auto_answer = 0
    w.apply_menu_state(True)
    mn = [t for t in w.chrome.tabs if t.index == 'masternode'][0]
    assert mn.visible is False                         # 0x202c: no bit 10
    w.apply_menu_state(True, caps=products.flags(0x201c))
    assert mn.visible is True
    _mod(tmp_path, 'print("from lua")')
    sc = w.stack.widget(w.pages[w.tab_index['SCRIPTING']])
    m = scripting.find_modules([str(tmp_path)])[0]
    assert sc.run_script(m)
    assert 'from lua' in sc.console.toPlainText()
    assert sc.ran and sc.title_text.endswith('a.lua')


def test_masternode_uds_routines():
    from mobridge.gui.official import masternode as MN
    st = {'chan': 0x0A, 'freq': 1000, 'amp': 0x40, 'gpio_mask': 0x300, 'gpio_out': 0x100, 'src': 0x1B}
    assert MN.uds_diag_session().hex() == '1002'
    assert MN.uds_chan_start(st).hex() == '3101f0010a03e840'
    assert MN.uds_stop(1).hex() == '3102f001' and MN.uds_stop(2).hex() == '3102f002'
    assert MN.uds_gpio_start(st).hex() == '3101f00203000100'
    assert MN.uds_freq(1, 0x1B).hex() == '3101f0031b' and MN.uds_freq(3, 0x0A).hex() == '3103f0030a'


def test_widget_lib(app, tmp_path):
    from mobridge.gui.qt import QtWidgets
    pane = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(pane)
    (tmp_path / 'a.lua').write_text('''
local root = widget.rootLayout()
local b = widget.button("Go"); root:append(b)
b:register("clicked", function() print("clicked", b:getText()) end)
local h = widget.hlayout(); root:append(h); h:append(widget.progressbar())
print(b:className(), widget.color(10, 20, 30):darker():name())
_G.btn = b''')
    out = []
    h = scripting.ScriptHost(log=out.append, pane_layout=lay)
    assert h.run(str(tmp_path / 'a.lua'))
    h.lua.globals().btn['_q'].click()
    assert out == ['QPushButton\t#050a0f', 'clicked\tGo']
    h.reset()


def test_dspparam_and_model_binds(tmp_path):
    from mobridge import model as M
    sent = []

    class Eng:
        connected = True
        root = M.DspModel(fw=210)

        def bus_request_async(self, cmd, pl, cb):
            sent.append((cmd, pl))

        def push(self, what=''):
            sent.append(('push', what))
    eng = Eng()
    eng.root.headunit.set('volume', 20)
    out = []
    p = tmp_path / 'b.lua'
    p.write_text("local c = fddspconnect.instance() c:dspParam('tone-frequency', 1000) "
                 "local m = fddspmodel.instance() local h = m:headunitModel() "
                 "print(h:volume()) h:subgain(5) print(m:profile())")
    h = scripting.ScriptHost(engine=eng, log=out.append)
    assert h.run(str(p)), out
    assert sent[0] == (0x0717, bytes.fromhex('000a000003e8'))
    assert out[:2] == ['20', '0'] and eng.root.headunit.values['subgain'] == 5


def test_masternode_custom_sends(app):
    from mobridge.gui.official.masternode import ControlsPage
    p = ControlsPage()
    for cb, t in zip(p.most_fields, ('01', '22', '01', '0400', '02', '0a0b')):
        cb.setEditText(t)
    assert p.most_custom_send() == (0x0621, bytes.fromhex('0001220104000200' '0002' '0a0b'))
    for cb, t in zip(p.a2b_fields, ('03', '05', 'ff')):
        cb.setEditText(t)
    assert p.a2b_custom_send() == (0x0645, bytes.fromhex('0003' '05' '0001' 'ff'))


def test_levels_slave_frames(app):
    from mobridge.gui.official import masternode as MN
    assert MN.level_net_b(0x0102, 1, 7) == (0x0645, bytes.fromhex('0102220005010101' '0007'))
    assert MN.level_net_a(0x0202, 7) == (0x0621, bytes.fromhex('0401220102020200' '0001' '07'))
    p = MN.LevelsPage()
    assert not p.ms_switch.isEnabled()
    p.set_networks([('A2B', 3)])
    assert p.ms_switch.isEnabled() and p.network == 1


def test_can_tab_send(app):
    from mobridge.gui.official.masternode import ControlsPage
    p = ControlsPage()
    assert p.can_custom_send() == (0x0610, bytes.fromhex('0000' '00' '000003c3'))
    p.can_iface.setEditText('01'); p.can_id.setEditText('0123'); p.can_data.setEditText('aabb')
    assert p.can_custom_send() == (0x0610, bytes.fromhex('0000' '01' '00000123' 'aabb'))
