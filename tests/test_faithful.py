"""Faithfulness pass: the data model, .rax file, DspWrite batching and UI rules
against the exe decompile (re/analysis/*.md). Every assertion names its source."""
import struct

import pytest

from mobridge import model as M
from mobridge import dspwrite as D
from mobridge import rax, uirules as U, wire_apply, parity, filterbins
R = U


def ref_unit():
    """the reference unit: 6 mapped outputs (device-config/DEVICE_CONFIG.md), fw 210."""
    r = M.DspModel(fw=210, product=0x202C)
    r.channel_count = 12
    for role, ch in ((0x100, 0), (0x800, 1), (0x8000, 2), (0x2000, 3), (0x4, 10), (0x20, 11)):
        r.map_speaker(role, ch)
    D.flush(r)                       # the mapping marks are a connect-time write-back, not an edit
    r.modified = False
    return r


# ---------------------------------------------------------------- model
def test_speaker_class_mode_and_defaults():
    # MODEL_REGION §1.1 ctor + FUN_1400f3620 reset, §4 modes
    r = ref_unit()
    sub, woofer, centre = r.speaker(0x2000), r.speaker(0x100), r.speaker(0x8000)
    assert sub.cls == M.CLASS_SUBWOOFER and sub.mode == M.MODE_BIQUAD and sub.filter_design == M.FD_RBJ
    assert woofer.cls == M.CLASS_WOOFER and woofer.mode == M.MODE_BIQUAD
    assert centre.cls == M.CLASS_MIDRANGE and centre.mode == M.MODE_DEVICE_FILTER
    assert centre.filter_design == M.FD_BUTTERWORTH
    for s in (sub, woofer, centre):
        assert (s.filter_type, s.f1, s.f2, s.slope1, s.slope2) == (M.FT_NONE, 150, 2000, 12, 12)
        assert s.gain_db == 0.0 and s.limit_dbfs == 0.0 and s.delay_ms == 0.0 and not s.mute
    assert M.ORDINAL_BY_ROLE[0x2000] == 14 and M.NAME_BY_ROLE[0x8000] == 'CenterMidrange'
    assert M.ROLE_BY_NAME['CenterSpeaker'] == 0x8000            # alias


def test_crossover_clamps_and_band_rules():
    # FUN_1400f4500 / 4120: 20..23080; FUN_1400f4650 / 4270: 6..48; FUN_1400f3e80 Band swap
    s = ref_unit().speaker(0x2000)
    s.set_lop(5); assert s.f1 == 20
    s.set_lop(99999); assert s.f1 == 23080
    s.set_slope1(3); assert s.slope1 == 6
    s.set_slope1(60); assert s.slope1 == 48
    s.set_lop(150); s.set_slope1(12)
    s.set_filter_type(M.FT_BAND)
    assert (s.f1, s.f2) == (2000, 150)                          # Band: LoP is the upper cutoff
    s.set_filter_type(M.FT_LP)
    assert s.f1 == 150                                          # old == Band -> f1 = f2
    assert s.set_design(M.FD_BESSEL) is False                   # FUN_1400f3ad0 refuses in mode 2
    assert s.filter_design == M.FD_RBJ
    c = ref_unit().speaker(0x8000)
    assert c.set_design(M.FD_BESSEL) and c.filter_design == M.FD_BESSEL


def test_stage_count_and_max_points():
    # FUN_1400f3320, FUN_1400f6650
    s = ref_unit().speaker(0x2000)
    assert s.stage_count() == 1 and s.eq.max_points(210) == 16
    s.set_filter_type(M.FT_LP); s.set_slope1(48)
    assert s.stage_count() == 4 and s.eq.max_points(210) == 13 and s.eq.max_points(205) == 5
    c = ref_unit().speaker(0x8000)
    assert c.eq.max_points(210) == 0x40                         # mode != 2
    for i in range(13):
        assert s.eq.add(100.0 * (i + 1), 1.0) is not None
    assert s.eq.add(5000.0, 1.0) is None                        # refused beyond the max
    assert s.eq.points[0].q1 == 3.0 and s.eq.points[0].app_biquad


def test_sync_bits_and_dirty():
    # FUN_1400fa060 bit table; set_* setters; eps compare
    r = ref_unit(); s = r.speaker(0x2000)
    assert s.sync == 0 and not r.modified
    assert s.set_gain(0.0) is False                             # unchanged -> no dirty
    assert s.set_gain(-3.0) and s.sync & M.SYNC_GAIN and r.modified
    s.clear_sync()
    s.set_mute(True); assert s.sync & M.SYNC_MUTE
    s.set_lop(200); assert s.sync & M.SYNC_FILTER and s.sync & M.SYNC_IIR and s.sync & M.SYNC_BINS
    s.clear_sync(); s.eq.add(1000.0, 2.0)
    assert s.sync & M.SYNC_EQ and s.sync & M.SYNC_IIR and not s.sync & M.SYNC_BINS   # [CAP] no 0x0702 on EQ edits
    s.mark_all(); assert s.sync == 0x1FFF


def test_biquad_chain_order():
    s = ref_unit().speaker(0x2000)
    assert s.biquad_chain() == [[0.0] * 5]                      # zero stage when no crossover
    s.set_filter_type(M.FT_LP); s.set_slope1(24)
    s.eq.add(1000.0, 3.0, 2.0)
    chain = s.biquad_chain()
    assert len(chain) == 3 and chain[0] == chain[1]             # 24 dB = cascaded twice, then the EQ point


def test_time_align_conversions():
    # FUN_1400f56d0 / FUN_1400f5330 / FUN_1400f51d0 constants [RDATA]
    r = ref_unit(); ta = r.preset(0).timealign
    a, b = r.speaker(0x100), r.speaker(0x2000)
    for s in r.speakers_in_preset(0):
        s.set_distance(100.0)
    a.set_distance(50.0)
    ta.distances_to_delays()
    assert ta.furthest == 100.0 and ta.closest == 50.0
    assert a.delay_ms == pytest.approx(50.0 / 34.3) and b.delay_ms == 0.0
    ta.set_units(1)
    assert ta.unit_label == 'in' and a.distance == pytest.approx(50.0 * 0.393701)
    assert ta.max_distance == 270.0
    assert ta.delay_for_distance(ta.furthest + 1) == 0.0        # negative -> 0
    ta.reset()
    assert a.delay_ms == 0.0 and a.distance == 0.0 and ta.furthest == 0.0


def test_root_rules():
    r = ref_unit()
    assert r.select_preset(4) is False and r.select_preset(2) and r.current_preset == 2
    r.set_preset_count(0); assert r.preset_count == 4            # FUN_1400ecae0
    r.set_preset_count(2); assert r.preset_count == 2
    r.set_input_gain(3, -60.0); assert r.input_gains[2][3] == -48.0
    r.set_input_gain(99, 20.0); assert r.input_gains[2][1] == 9.0   # k out of range -> 1, clamped
    r.set_input_delay(36.1); assert r.input_delay_ms[2] == 10.0
    assert not r.all_muted(); r.mute_all(True); assert r.all_muted()
    r.set_option(5, True); assert r.option_bits() == 4 and r.root_flags & M.ROOT_OPTIONS
    r.new_tune(connected=False)
    assert r.name == 'Untitled' and r.current_preset == 0 and r.preset_count == 4 and not r.modified
    assert not r.speaker(0x2000).mute                           # offline New Tune resets channels
    r.unmap_all(); assert r.channels() == []


# ---------------------------------------------------------------- dspwrite
def test_flush_matches_the_proven_record_sequence():
    r = ref_unit(); s = r.speaker(0x2000)
    s.set_gain(-3.0)
    f = D.flush(r)
    assert f.cmds() == [0x0700, 0x0702, 0x0708, 0x0704]
    assert f.wire() == wire_apply.record_frames(4, gain_db=-3.0)
    assert f.mask == D.REQ_CHAN_PROPS | D.REQ_CHAN_BINS | D.REQ_CHAN_IIR | D.REQ_CHAN_EQ
    assert f.channel_mask == 1 << 3 and f.preset_mask == 1
    assert s.sync == 0 and D.flush(r).frames == []              # bits cleared, nothing left


def test_flush_master_gain_and_eq_edit():
    r = ref_unit()
    r.preset(0).master.set_gain(1.0)
    assert D.flush(r).wire() == wire_apply.gain_frames(0, 1.0)
    s = r.speaker(0x2000); s.eq.add(1000.0, -0.17965, 3.0)
    f = D.flush(r)
    assert f.cmds() == [0x0708, 0x0704]
    assert f.wire() == wire_apply.eq_band_frames(4, 1000.0, -0.17965, 3.0)
    assert all(parity.frame_cleared(w, c) for w, (c, _) in zip(f.wire(), f.frames))


def test_flush_coalesces_everything_in_exe_order():
    r = ref_unit()
    r.set_name('MyCar'); r.select_preset(1)
    r.preset(1).master.set_gain(-1.0)
    r.speaker(0x100, 1).set_mute(True)
    r.speaker(0x2000, 1).set_delay(1.0)
    r.set_input_delay(5.0, 1)
    r.headunit.set('bass', 3)
    r.preset(1).upmix.set_enabled(True)
    f = D.flush(r, product_flags=0xffffffff)            # a product with every flag bit
    cmds = f.cmds()
    # root first (name, preset), then per-preset master -> inputs -> channels in order, then 0x0705 / 0x0707
    assert cmds[:2] == [0x0704, 0x0716]
    assert cmds.index(0x0705) > cmds.index(0x0700) and cmds[-1] == 0x0707
    i700 = [i for i, c in enumerate(cmds) if c == 0x0700]
    assert f.frames[i700[0]][1][:2] == b'\x01\x00'               # master (preset 1, ch 0) before channels
    assert f.frames[i700[1]][1][1] == 1 and f.frames[i700[2]][1][1] == 4   # ch+1 ascending
    assert f.preset_mask == 0b1111 and f.channel_mask == 0b1001   # input delay is per-preset (root flag 0x20 -> every preset)
    # payload shapes (CMD_TABLE §3)
    assert f.frames[0][1] == b'\x01' + b'MyCar'.ljust(0x40, b'\x00')
    assert f.frames[1][1] == b'\x01\x01'
    delay = [p for c, p in f.frames if c == 0x0704 and p[0] == 8][1]   # preset 1's frame
    assert delay == b'\x08\x01' + (5000).to_bytes(2, 'big')
    hu = [p for c, p in f.frames if c == 0x0705][0]
    assert hu == b'\x00\x03'


def test_channel_record_fields():
    r = ref_unit(); s = r.speaker(0x2000)
    s.set_gain(-27.0); s.set_invert_phase(True); s.set_delay(2.5); s.set_passthrough(True); s.set_mute(True)
    s.set_limit(-20.0)
    p = D.channel_props_payload(r, s, 3)
    assert p[:4] == bytes([0, 4]) + (0xB700).to_bytes(2, 'big')
    assert p[4] == 1 and p[5:7] == (2500).to_bytes(2, 'big') and p[7] == 1 and p[8] == 1
    assert p[9:12] == b'\x00\x00\x00' and p[12:16] == bytes.fromhex('0ccccCd0'.lower())
    s.set_testtone(0x1234)
    assert D.channel_props_payload(r, s, 3)[9:11] == b'\x12\x34'   # [9-10] = +0x84 u16, [11] = +0x88 u8


def test_filter_and_appprops_payloads():
    r = ref_unit(); s = r.speaker(0x2000)
    s.set_filter_type(M.FT_HP); s.set_hip(80); s.set_slope2(24)
    assert D.channel_filter_payload(r, s, 3) == bytes([3, 0, 4, 2, 4]) + struct.pack('>HHBB', 150, 80, 12, 24)
    assert parity.frame_cleared(D._frame(0x0704, D.channel_filter_payload(r, s, 3)), 0x0704)
    assert D.channel_appprops_payload(r, s, 3) == bytes([2, 0, 4, 14, 0])
    assert D.routing_payload(r)[:1] == b'\x00' and len(D.routing_payload(r)) == 1 + 8 * 12


def test_mode1_bins_are_built_from_the_crossover_curve():
    from mobridge import crossover_curve as XC
    r = ref_unit(); c = r.speaker(0x8000)
    c.set_filter_type(M.FT_HP)
    f = D.flush(r)
    assert 0x0702 in f.cmds() and not f.skipped
    tbl = next(p for cmd, p in f.frames if cmd == 0x0702 and p[1] == 3)
    assert len(tbl) == 1026 and tbl[:2] == bytes([0, 3])
    want = XC.crossover_curve(c.filter_type, c.filter_design, c.f1, c.f2, c.slope1, c.slope2)
    # low bins of a 2000 Hz high-pass are cut, top bins are flat
    assert want[0] < -20 and abs(want[1000]) < 0.1
    assert int.from_bytes(tbl[2:4], 'big') < 0xED00 and int.from_bytes(tbl[-2:], 'big') == 0xED00
    for cmd, p in f.frames:
        if cmd == 0x0702:
            assert parity.proven_write(_framed(0x0702, p))
    D.MODE1_UNLOCKED = False
    try:
        f2 = D.flush(ref_unit_with_hp())
        assert 0x0702 not in [c_ for c_ in f2.cmds()] or f2.skipped
    finally:
        D.MODE1_UNLOCKED = True


def ref_unit_with_hp():
    r = ref_unit(); r.speaker(0x8000).set_filter_type(M.FT_HP)
    return r
    assert c2.bins_curve() == filterbins.eq_curve([(1000.0, 1.0, 3.0, 3.0)])   # no filter -> EQ only


# ---------------------------------------------------------------- rax
def test_rax_round_trip_and_schema():
    r = ref_unit(); s = r.speaker(0x2000)
    s.set_gain(-3.0); s.eq.add(1000.0, 2.5, 1.5, 2.5); s.set_filter_type(M.FT_LP); s.set_limit(-6.0)
    r.preset(0).master.set_gain(0.5); r.preset(0).upmix.set_enabled(True)
    r.set_input_gain(4, -2.0, 0); r.set_input_map(10, 61); r.set_option(5, True)
    r.select_preset(0)
    txt = rax.dumps(r)
    assert txt.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<Project version="210" product="202c" name="Untitled" ampserial="" profile="0">')
    assert '<map speaker="RightSubWoofer" channel="3"/>' in txt
    assert '<speaker id="RightSubWoofer" gain="-3.00" invertPhase="0" xoverlink="0" passthrough="0" mute="0" testtone="0" timealign="0.00" limit="-6.00">' in txt
    assert '<point type="peaking" freq="1000.00" mag="2.50" qleft="1.50" qright="2.50"/>' in txt
    assert '<filter type="LowPass" model="RBJ">' in txt and '<param name="LoP" value="150" slope="12"/>' in txt
    assert '<master gain="0.5">' in txt and 'lmsrate="0.08"' in txt
    assert '<map output="10" input="61"/>' in txt and '<gain input="4" value="-2"/>' in txt
    assert '<options rearfill="1" loudness="0" headunitbassctrlsub="0"/>' in txt
    r2 = ref_unit()
    assert rax.loads(r2, txt) == 0
    assert rax.dumps(r2) == txt
    assert r2.speaker(0x2000).limit_dbfs == -6.0 and r2.input_map[10] == 61 and r2.option(5)


def test_rax_version_product_and_legacy_rules():
    r = ref_unit()
    with pytest.raises(rax.RaxError) as e:
        rax.loads(r, '<Project version="211" product="202c"/>')
    assert e.value.code == 4
    with pytest.raises(rax.RaxError) as e:
        rax.loads(r, '<Project version="210" product="1234"/>')
    assert e.value.code == 5
    assert rax.loads(r, '<Project version="210" product="1234"/>', force=True) == 0   # "apply anyway"
    with pytest.raises(rax.RaxError) as e:
        rax.loads(r, 'not xml')
    assert e.value.code == 1
    # file < 208 on fw > 207: gains and EQ mags get -3.0 (DAT_1401fd828); pre-209 upmix at Project level
    old = ('<Project version="207" product="202c" profile="1">'
           '<outputs><map speaker="RightSubWoofer" channel="3"/></outputs>'
           '<profile id="1"><master gain="1"><eq count="1"><point type="peaking" freq="100" mag="1" qleft="3" qright="3"/></eq></master>'
           '<speaker id="RightSubWoofer" gain="0.00"/></profile>'
           '<upmix enabled="1" algorithm="2" lmsrate="0.08" adprate="0" adpgamma="0"/></Project>')
    r = ref_unit()
    assert rax.loads(r, old) == 0
    assert r.current_preset == 1 and r.speaker(0x2000, 1).gain_db == -3.0
    assert r.presets[1].master.gain_db == -2.0 and r.presets[1].master.eq.points[0].gain == -2.0
    assert r.presets[1].upmix.enabled and r.presets[1].upmix.algorithm == 2
    off = M.DspModel(fw=0, product=0)
    assert rax.loads(off, old) == 0 and off.fw == 207 and off.product == 0x202C   # offline adopts the file


# ---------------------------------------------------------------- uirules
def test_control_rules():
    assert (U.GAIN.lo, U.GAIN.hi, U.GAIN.step) == (-27.0, 9.0, 0.1) and U.gain_step(201) == 0.5 and U.gain_step(210) == 0.1
    assert U.gain_slider_range(210) == (-270, 90, 10.0) and U.gain_slider_range(200) == (-54, 18, 2.0)
    assert (U.EQ_MAG.lo, U.EQ_MAG.hi, U.EQ_MAG.step) == (-24.0, 12.0, 0.5)
    assert (U.EQ_Q.lo, U.EQ_Q.hi, U.EQ_Q.default) == (0.05, 14.0, 3.0)
    assert (U.LIMITER.lo, U.LIMITER.hi, U.LIMITER.suffix) == (-36.0, 0.0, ' dBFS')
    assert U.slope_rule(2).lo == 12 and U.slope_rule(2).step == 12 and U.slope_rule(1).lo == 6
    assert U.step(U.GAIN, 8.95, +1) == 9.0 and U.quantise(U.LIMITER, -40) == -36.0
    assert U.quantise(U.CUTOFF, 23090) == 23080


def test_visibility_rules():
    caps = U.PRODUCT_CAPS_202C
    v = U.tab_visibility(True, caps, 210, 6, scripting_enabled=True)
    assert v['config_text'] == 'CONFIG && MASTER EQ' and v['channel_eq'] and v['time_align'] and v['limiter'] and v['scripting']
    assert not U.tab_visibility(True, caps, 205, 6, True)['limiter']          # limiter needs fw >= 206
    assert U.tab_visibility(True, caps & ~U.CAP_MASTER_EQ, 210, 6, True)['config_text'] == 'CONFIGURATION'
    off = U.tab_visibility(False, 0, 0, 6, True)
    assert off['channel_eq'] and not off['time_align'] and not off['limiter']
    assert not any(U.tab_visibility(True, caps, 210, 0, True)[k] for k in ('channel_eq', 'time_align', 'limiter'))
    m = U.menu_state(True, caps, 210, 3)
    assert m['mute_all'] and m['unmap_all'] and m['firmware_update'] and m['headunit_menu_visible']
    assert not U.menu_state(True, caps, 209, 3)['headunit_menu_visible']
    assert not any(U.menu_state(False, caps, 210, 15)[k] for k in ('mute_all', 'unmap_all', 'firmware_update'))
    assert U.ui_locked(4) and U.ui_locked(6) and not U.ui_locked(3)
    assert not U.speaker_selector_enabled(6, 9, 0) and U.speaker_selector_enabled(6, 9, U.CAP_MASTER_EQ)
    assert not U.limiter_controls_enabled(True)


def test_texts_and_validator():
    assert U.dirty_prompt(False)['text'].endswith('changes?')
    assert 'locally?' in U.dirty_prompt(True)['text'] and U.dirty_prompt(True)['buttons'] == ('Save', 'Discard', 'Cancel')
    assert U.FILE_DIALOG['filter'] == 'Configuration File(*.rax)'
    assert U.CONNECT_STATES[3] == 'Connected' and U.CONNECT_STATES[16] == 'Incompatible Device'
    assert U.firmware_version_text(2, 11, 9) == '02.11.09'
    assert U.freq_label(500) == '500 Hz' and U.freq_label(1000) == '1 kHz' and U.freq_label(10) == ''
    assert U.channel_caption(3, 'Right Sub Woofer') == '4: Right Sub Woofer'
    v = U.validate_firmware
    assert v(0x202C, 0x202C, 210, (2, 11, 0), (2, 11, 9), (2, 11, 8), False, 3) == 1    # older or equal
    assert v(0x202C, 0x202C, 210, (2, 11, 0), (2, 11, 9), (2, 11, 8), False, 5) == 0    # not in Recovery
    assert v(0x1234, 0x202C, 210, (2, 11, 0), (2, 11, 9), (2, 12, 0), False, 3) == 4
    assert v(0x202C, 0x202C, 211, (2, 11, 0), (2, 11, 9), (2, 12, 0), False, 3) == 2
    assert v(0x202C, 0x202C, 210, (2, 11, 0), (2, 11, 9), (2, 12, 0), True, 3) == 5
    assert v(0x202C, 0x202C, 210, (2, 12, 0), (2, 11, 9), (2, 12, 1), False, 3) == 3
    assert v(0, 0x202C, 210, (2, 11, 0), (2, 11, 9), (2, 12, 0), False, 3) == 0
    assert U.backup_file_name(0x202C, 'DA-G2.Pro A2B Ford', '22-10-26_01-02-03') == 'backup_202c_da-g2.pro_a2b_ford_22-10-26_01-02-03.rax'


# --------------------------------------------------------------------------
# main window: menus, shortcuts, dialogs, close flow (UI_MAINWINDOW.md §A-§C)
# --------------------------------------------------------------------------
def _window(root=None):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    from mobridge.gui.official.window import OfficialMainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = OfficialMainWindow([], root=root)
    return app, w, QtWidgets


def test_window_title_and_menu_tables():
    app, w, QtWidgets = _window()
    assert w.windowTitle() == R.WINDOW_TITLE == 'mObridge DSP - v25.8.0'
    titles = [m.title() for m in w.menuBar().findChildren(QtWidgets.QMenu) if m.parent() is w.menuBar()]
    assert titles[:4] == ['File', 'Options', 'Tools', 'Help']
    assert w.actions['Save Tune'].shortcut().toString() == 'Ctrl+S'
    assert w.actions['Save Tune As...'].shortcut().toString() == 'Ctrl+Alt+S'
    assert w.actions['Exit'].shortcut().toString() == 'Ctrl+Q'
    assert w.actions['Fullscreen'].isCheckable()
    assert w.actions['Import Script Module...'].isVisible()     # scripting is on in this app
    assert 'Show Time Alignment as Popup' not in w.actions
    assert w.actions['Mute All'].isCheckable() and w.actions['Mute All'].shortcut().toString() == 'Ctrl+M'
    assert w.actions['Unmap all'].shortcut().toString() == 'Ctrl+U'
    assert not w.actions['Configure DRC...'].isVisible()
    assert w.actions['Add Filter Automatically'].isChecked()
    # [CUSTOM] auto-update toggles start off
    assert not w.actions['Check for Updates Automatically'].isChecked()
    assert w.actions['Bass Control Sets Sub Gain Offset'].toolTip() == R.HEADUNIT_TOOLTIPS['bass']


def test_menu_state_offline_vs_connected():
    app, w, QtWidgets = _window()
    # offline (start-up): connect-only items disabled, head-unit menus hidden
    assert not w.actions['Mute All'].isEnabled()
    assert not w.actions['Unmap all'].isEnabled()
    assert not w.actions['Firmware Update...'].isEnabled()
    assert not w.menu_headunit.menuAction().isVisible()
    assert not w.actions['Device User Manual'].isVisible()
    w.apply_menu_state(True, R.PRODUCT_CAPS_202C, 210, 4)
    assert w.actions['Mute All'].isEnabled() and w.actions['Unmap all'].isEnabled()
    assert w.menu_headunit.menuAction().isVisible() and w.menu_effects.menuAction().isVisible()
    assert w.actions['Add Filter Automatically'].isVisible()
    assert w.actions['Device User Manual'].isVisible()
    w.apply_menu_state(True, R.PRODUCT_CAPS_202C, 209, 4)       # head-unit menu needs fw >= 210
    assert not w.menu_headunit.menuAction().isVisible()
    w.apply_menu_state(True, R.PRODUCT_CAPS_202C, 210, 5)       # programming: nothing destructive
    assert not w.actions['Mute All'].isEnabled() and not w.chrome.preset.enabled


def test_close_prompt_save_discard_cancel(tmp_path):
    from mobridge import rax
    root = M.DspModel(fw=210)
    app, w, QtWidgets = _window(root)
    assert w._confirm_discard()                      # clean: no prompt
    root.map_speaker(0x2000, 3)
    root.speaker(0x2000).set_gain(-3.0)
    assert root.is_modified()
    w._auto_answer = QtWidgets.QMessageBox.Cancel
    assert not w._confirm_discard()
    w._auto_answer = QtWidgets.QMessageBox.Discard
    assert w._confirm_discard()
    # Save with a path set writes the .rax and clears modified
    root.path = str(tmp_path / 't.rax')
    w._auto_answer = QtWidgets.QMessageBox.Save
    assert w._confirm_discard()
    assert (tmp_path / 't.rax').exists() and not root.is_modified()
    txt = (tmp_path / 't.rax').read_text()
    assert '<Project' in txt and 'RightSubWoofer' in txt
    # Open the file back into a fresh model (offline: no warning)
    root2 = M.DspModel(fw=210)
    app, w2, _ = _window(root2)
    w2._auto_answer = QtWidgets.QMessageBox.Yes
    assert w2.load_rax(root.path)
    assert abs(root2.speaker(0x2000).gain_db + 3.0) < 1e-9
    # dirty prompt text matches the exe for both link states
    assert R.dirty_prompt(False)['text'].endswith('changes?')
    assert 'retain all DSP settings' in R.dirty_prompt(True)['text']


def test_load_error_messages(tmp_path):
    root = M.DspModel(fw=210)
    app, w, QtWidgets = _window(root)
    w._auto_answer = QtWidgets.QMessageBox.Yes
    bad = tmp_path / 'bad.rax'; bad.write_text('<nope>')
    assert not w.load_rax(str(bad))
    assert 'not able to be parsed' in w.last_message[1] and 'bad.rax' in w.last_message[1]
    assert not w.load_rax(str(tmp_path / 'missing.rax'))
    assert 'cannot be opened' in w.last_message[1]


def test_unmap_all_prompt():
    root = M.DspModel(fw=210)
    app, w, QtWidgets = _window(root)
    root.map_speaker(0x2000, 3)
    w._auto_answer = QtWidgets.QMessageBox.No
    assert not w.unmap_all() and len(root.channel_role) > 0
    w._auto_answer = QtWidgets.QMessageBox.Yes
    assert w.unmap_all() and len(root.channel_role) == 0


# --------------------------------------------------------------------------
# EqualizerScene geometry (UI_MAINWINDOW.md §D)
# --------------------------------------------------------------------------
def test_scene_axis_round_trip():
    W, H = R.scene_size(1069, 296)
    assert (W, H) == (1024, 256)
    for f in (20, 50, 1000, 12345.6, 24000):
        assert abs(R.scene_freq(R.scene_x(f, W), W) - f) < 1e-6 * f
    assert R.scene_x(10, W) == 0.0 and R.scene_x(30000, W) == 0.0
    assert R.scene_freq(-1, W) == 0.0 and R.scene_freq(W + 1, W) == 0.0
    assert R.scene_y(9, H) == -128 and R.scene_y(-27, H) == 128 and R.scene_y(9.1, H) == -128
    assert abs(R.scene_y(0, H) + 64.0) < 1e-9              # 0 dB line at H/2 - H*27/36 (y up is negative)
    for db in (-27, -3.3, 0, 8.9):
        assert abs(R.scene_db(R.scene_y(db, H), H) - db) < 1e-9
    # channel gain offset: axis shows gain + band
    assert abs(R.scene_db(R.scene_y(2.0, H, -3.0), H, -3.0) - 2.0) < 1e-9
    assert R.scene_db(H, H) == -27 and R.scene_db(-H, H) == 9
    assert R.clamp_drag(-5, 300, W, H) == (0.0, 128.0)
    assert R.clamp_drag(2000, -300, W, H) == (1023.0, -128.0)
    assert R.clamp_drag(10.5, 3.25, W, H) == (10.5, 3.25)
    # max draggable frequency is one scene unit short of 24 kHz
    assert 23800 < R.scene_freq(W - 1, W) < 23900


def test_spin_rule_quantises_like_qdoublespinbox():
    pytest.importorskip('PySide6.QtWidgets')
    from mobridge.gui.official.canvas import Spin
    from PySide6 import QtCore
    s = Spin(QtCore.QRectF(0, 0, 10, 10), '', True, rule=R.GAIN, number=0.0)
    assert s.value == '0.0 dB'
    assert s.set_number(12.34) == 9.0 and s.value == '9.0 dB'
    assert s.set_number(-27.04) == -27.0
    s.set_number(8.95); assert s.step_number(+1) == 9.0
    c = Spin(QtCore.QRectF(0, 0, 10, 10), '', True, rule=R.CUTOFF, number=10)
    assert c.value == '20 Hz' and c.set_number(99999) == 23080
    sl = Spin(QtCore.QRectF(0, 0, 10, 10), '', True, rule=R.SLOPE, number=12)
    assert sl.value == '12 dB/8ve' and sl.step_number(+1) == 18 and sl.set_number(60) == 48


# --------------------------------------------------------------------------
# gates opened on the decompile alone (2026-10-22 14:16Z) vs still locked
# --------------------------------------------------------------------------
def _framed(cmd, payload):
    from mobridge.framing import MdiFramer
    from mobridge.message import MdiMessage
    return MdiFramer().encode(MdiMessage(cmd=cmd, data=payload))


def test_decompile_only_gates_open_and_locked():
    r = ref_unit()
    r.set_name('My F-150')
    assert parity.proven_write(_framed(0x0704, D.name_payload(r.name)))
    assert not parity.proven_write(_framed(0x0704, D.name_payload('Zoë')))        # non-ASCII stays gated
    assert not parity.proven_write(_framed(0x0704, b'\x01' + b'A' * 0x40))        # no NUL
    assert parity.proven_write(_framed(0x0716, D.select_preset_payload(2)))
    assert not parity.proven_write(_framed(0x0716, D.select_preset_payload(4)))
    r.set_input_delay(5.0, 0)
    assert parity.proven_write(_framed(0x0704, D.input_delay_payload(r, 0)))
    r.set_input_delay(10.0, 1)
    assert D.input_delay_payload(r, 1)[2:] == b'\x27\x10'                         # 10000 cap
    assert parity.proven_write(_framed(0x0704, D.input_delay_payload(r, 1)))
    sub = r.speaker(0x2000)
    assert parity.proven_write(_framed(0x0704, D.channel_appprops_payload(r, sub, 3)))
    assert parity.proven_write(_framed(0x0704, bytes.fromhex('0200010901')))     # captured frame
    r.channel_count = 12
    assert parity.proven_write(_framed(0x0701, D.routing_payload(r)))
    p = r.presets[0]
    assert parity.proven_write(_framed(0x0707, D.upmix_payload(r, p.upmix)))
    # opened after the .rdata reads (DAT_1401fdd28 = 100, DAT_140201480 = -48)
    r.set_input_gain(6, -48.0, 3)
    ig = D.input_gains_payload(r, 3)
    assert len(ig) == 130 and ig[2 + 5 * 2:2 + 6 * 2] == b'\x8d\x00'                    # preset-4 input 6 as read
    assert parity.proven_write(_framed(0x0704, ig))
    assert not parity.proven_write(_framed(0x0704, ig[:2] + b'\x8c\xff' + ig[4:]))     # below -48 dB
    assert not parity.proven_write(_framed(0x0704, b'\x07\x04' + ig[2:]))              # preset 4 (firmware hazard)
    assert parity.proven_write(_framed(0x0707, D.timealign_payload(r, p.timealign)))
    assert not parity.proven_write(_framed(0x0707, b'\x03\x00\x02\x00\x00\x00\x00'))   # units byte
    # still shut by design: 0x0101 has no firmware handler
    assert not parity.proven_write(_framed(0x0101, b'\x02\x00\x00\x00\x01\x00\x00\x00'))
    # pinned by exe + firmware disasm (FW_LOCKED_WRITES_OCT22.md)
    assert parity.proven_write(_framed(0x0705, b'\x00\x18'))                  # bass 24
    assert parity.proven_write(_framed(0x0705, b'\x05\xb4'))                  # volume 180
    assert not parity.proven_write(_framed(0x0705, b'\x06\x00'))              # index 6 never sent by the exe
    assert parity.proven_write(_framed(0x0703, D.sourcesel_payload(r)))
    assert not parity.proven_write(_framed(0x0703, b'\x02'))
    assert M.HeadunitModel.FIELDS[3] == 'balance' and M.HeadunitModel.FIELDS[4] == 'fade'
    rec = bytes.fromhex('0004ED0000000000000001007FFFFFFF')                   # test tone 1 (inert in firmware)
    assert parity.proven_write(_framed(0x0700, rec))
    assert not parity.proven_write(_framed(0x0700, rec[:11] + b'\x01' + rec[12:]))   # [11] never set
    assert not parity.proven_write(_framed(0x0700, rec[:9] + b'\x01' + rec[10:]))    # [9] never set


def test_flush_is_filtered_by_product_flags_like_the_exe():
    """FUN_1400aed20 ANDs the request mask by FUN_1400ac190: 0x202c has no flag
    bit 10, so the head-unit 0x0705 write is dropped; 0x0707 sub 2 (bit 7) stays."""
    r = ref_unit()
    r.headunit.set('bass', 3)
    r.preset(1).upmix.set_enabled(True)
    r.product = 0x202c
    f = D.flush(r)
    assert 0x0705 not in f.cmds() and 0x0707 in f.cmds()
    assert not f.mask & D.REQ_HEADUNIT


def test_effects_go_out_as_dsp_param_write_0717():
    r = ref_unit()
    r.product = 0x202c
    r.set_option(5, True)                        # Rear Fill
    f = D.flush(r)
    assert f.frames[-1] == (0x0717, b'\x00\x0c\x00\x00\x00\x04')
    r.set_option(5, False)
    assert D.flush(r, product_flags=0x1).frames == []   # no flag bit 1: FUN_1400ac230 drops it
