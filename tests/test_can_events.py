"""CAN event decoder for Diagnostics & Health: decode only what the device sees."""
from mobridge.can_events import (EventFeed, EventType, ACCEPTED_RX_IDS, DEVICE_TX_IDS)


def _texts(feed, cid, data, ts=0.0):
    return [e.text for e in feed.feed(cid, bytes(data), ts)]


def test_volume_detents_up_and_down():
    f = EventFeed()
    assert _texts(f, 0x2A0, [0, 0, 0, 0, 0, 0, 0x20, 0]) == ['Volume up 2 (—, knob)']
    assert _texts(f, 0x2A0, [0, 0, 0, 0, 0, 0, 0x1E, 0]) == []        # no change
    assert _texts(f, 0x1DF, [0x1C, 0, 0, 0, 0, 0, 0, 0]) == ['Volume down 2 (—, steering)']


def test_amp_enable_edges_and_status():
    f = EventFeed()
    assert _texts(f, 0x223, [0, 0, 0, 0xC0, 0, 0, 0, 0]) == ['Amplifier ENABLED']
    assert f.status.amp_enabled is True
    assert _texts(f, 0x223, [0, 0, 0, 0xC0, 0, 0, 0, 0]) == []        # no re-fire
    assert _texts(f, 0x223, [0, 0, 0, 0x00, 0, 0, 0, 0]) == ['Amplifier disabled']
    assert f.status.amp_enabled is False


def test_audio_on_off():
    f = EventFeed()
    assert _texts(f, 0x2E3, [0x02, 0, 0, 0, 0, 0, 0, 0]) == ['Audio turned ON']
    assert f.status.audio_on is True
    assert _texts(f, 0x2E3, [0x01, 0, 0, 0, 0, 0, 0, 0]) == ['Audio turned off']


def test_tone_and_preset_from_0x1dd():
    f = EventFeed()
    # first sight seeds the status tiles silently (no flood of flat values)...
    out = _texts(f, 0x1DD, [0xB7, 0x77, 0x00])   # bass11 treble7 bal7 fade7 svc0->Preset1
    assert out == []
    assert f.status.bass == 11 and f.status.preset == 0
    # ...a later change is a feed event.
    out2 = _texts(f, 0x1DD, [0xC7, 0x77, 0x00])  # bass 11 -> 12
    assert 'Bass -> 12 (7=flat)' in out2


def test_chime_shows_id_not_guess_and_suppresses_cancel_flood():
    f = EventFeed()
    assert _texts(f, 0x220, [0, 0x11, 0, 0, 0, 0, 0, 0]) == ['Repeating chime: id 0x11']
    assert _texts(f, 0x220, [0, 0x00, 0, 0, 0, 0, 0, 0]) == ['Chimes cleared']
    assert _texts(f, 0x220, [0, 0x00, 0, 0, 0, 0, 0, 0]) == []        # flood suppressed
    # the event is tagged uncertain because which Ford warning the id is is [U]
    ev = f.feed(0x2F4, bytes([0, 0x1C, 0, 0, 0, 0, 0, 0]), 0.0)[0]
    assert ev.tag == 'U' and ev.type is EventType.CHIME


def test_offfilter_ids_produce_nothing():
    f = EventFeed()
    # a 'door' style id the MCP2515 mask never accepts -> no event, no placeholder
    assert f.feed(0x3B3, bytes(8), 0.0) == []
    assert f.feed(0x100, bytes(8), 0.0) == []


def test_stereo_volume_echo_seeds_then_reports():
    f = EventFeed()
    assert _texts(f, 0x221, [0x4A, 48, 8, 0, 9, 0, 0, 0]) == []       # first seeds status
    assert f.status.stereo_volume == 48
    assert _texts(f, 0x221, [0x4A, 56, 8, 0, 9, 0, 0, 0]) == ['Stereo volume -> 56 (up)']


def test_accepted_id_sets_are_disjoint_and_complete():
    assert ACCEPTED_RX_IDS.isdisjoint(DEVICE_TX_IDS)
    assert 0x223 in ACCEPTED_RX_IDS and 0x5C6 in DEVICE_TX_IDS   # wake-amp relevant


# --- GUI screen: tiles update and the type filter actually filters ---------------
def test_diagnostics_screen_tiles_and_filter(app=None):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    import pytest
    pytest.importorskip('PySide6')
    from mobridge.gui.ui import build_app
    from mobridge.gui.engine import Engine
    from mobridge.gui.screens.diagnostics_health import DiagnosticsHealthScreen
    build_app([])
    s = DiagnosticsHealthScreen(Engine())
    for cid, d in [(0x223, [0, 0, 0, 0xC0, 0, 0, 0, 0]),   # amp enable
                   (0x2E3, [0x02, 0, 0, 0, 0, 0, 0, 0]),    # audio on
                   (0x2A0, [0, 0, 0, 0, 0, 0, 0x20, 0]),    # volume up
                   (0x220, [0, 0x11, 0, 0, 0, 0, 0, 0])]:   # chime
        s.on_frame(cid, bytes(d), 0.0)
    assert s.table.rowCount() == 4
    assert s._tiles['amp'].text() == 'ENABLED'
    assert s._tiles['audio'].text() == 'ON'
    assert s._tiles['chime'].text() == 'id 0x11'
    # filter to Amplifier -> exactly the one amp row visible
    labels = [s.filter.itemText(i) for i in range(s.filter.count())]
    s.filter.setCurrentIndex(labels.index('Amplifier'))
    visible = [r for r in range(s.table.rowCount()) if not s.table.isRowHidden(r)]
    assert len(visible) == 1
    assert s.table.item(visible[0], 1).text() == 'Amplifier'


def test_device_log_text_frames_reach_the_diagnostics_tiles():
    """0x0401 debug-log chunks (not line-aligned) from the live session are split
    into lines and fill the tiles; the engine used to drop them."""
    import pytest
    pytest.importorskip('PySide6')
    from mobridge.gui.ui import build_app
    from mobridge.gui.engine import Engine
    from mobridge.gui.screens.diagnostics_health import DiagnosticsHealthScreen
    from mobridge.framing import MdiMessage
    build_app([])
    e = Engine()
    s = DiagnosticsHealthScreen(e)
    for chunk in (b'[INFO] AMP_STATE_EN', b'ABLED\r\n[INFO] Audio tur', b'ned on\r\n\0\0'):
        e._on_session_frame(MdiMessage(cmd=0x0401, data=chunk))
    assert s._tiles['amp'].text() == 'ENABLED'
    assert s._tiles['audio'].text() == 'ON'
    e.log.emit('Connected: product 0x202c')          # the app's own status lines are not device log
    assert s.table.rowCount() == 2


# --- passive debug-console LOG STREAM parser (live-now source, no send) ---------
def test_log_stream_parses_amp_audio_power_chime():
    f = EventFeed()

    def logln(s):
        return [e.text for e in f.feed_log_line(s, 0.0)]
    assert logln('[INFO] AMP_STATE_ENABLED') == ['Amplifier ENABLED']
    assert f.status.amp_enabled is True
    assert logln('AMP_STATE_DISABLED now') == ['Amplifier disabled']
    assert logln('Audio turned on') == ['Audio turned ON']
    assert f.status.audio_on is True
    assert logln('pwrcntrl_set_power: 1') == ['Power control -> 1']
    assert logln('Set amp remote: 0') == ['Amp remote -> 0']
    assert logln('A2B Network ON') == ['A2B network ON']
    assert logln('DSP stopped') == ['DSP stopped']
    # the ~1 Hz chime-cancel flood collapses to one 'cleared' then goes quiet
    f._chime_cleared = False
    assert logln('WSNDMGR_CancelWarning: 255') == ['Chimes cleared']
    assert logln('WSNDMGR_CancelWarning: 255') == []       # suppressed
    # an unrelated line produces nothing
    assert logln('some other firmware chatter') == []


# --- chime catalog: show id now, build id->name map by annotation ---------------
def test_chime_catalog_label_and_persistence(tmp_path):
    from mobridge.can_events import ChimeCatalog, EventFeed
    cat = ChimeCatalog()
    # unknown id -> raw id only, never a guessed name
    assert cat.label(0x11) == 'id 0x11'
    assert cat.name(0x11) is None
    # feed records observations and uses the catalog
    f = EventFeed(chimes=cat)
    [e] = f.feed(0x220, bytes([0, 0x11, 0, 0, 0, 0, 0, 0]), 1.0)
    assert e.text == 'Repeating chime: id 0x11' and e.tag == 'U'
    assert cat.observed[0x11].count == 1
    # annotate -> name shows, event tag becomes confirmed
    cat.set_name(0x11, 'Seatbelt')
    assert cat.label(0x11) == 'Seatbelt (id 0x11)'
    f._chime_cleared = True
    [e2] = f.feed(0x2F4, bytes([0, 0x11, 0, 0, 0, 0, 0, 0]), 2.0)
    assert e2.text == 'Repeating chime: Seatbelt (id 0x11)' and e2.tag == 'C'
    # persist + reload round-trips, keyed by hex id
    p = str(tmp_path / 'chime_names.json')
    cat.save(p)
    cat2 = ChimeCatalog(); cat2.load(p)
    assert cat2.name(0x11) == 'Seatbelt'
    # blank label clears it
    cat2.set_name(0x11, '  ')
    assert cat2.name(0x11) is None


def test_chime_catalog_load_tolerates_missing_and_corrupt(tmp_path):
    from mobridge.can_events import ChimeCatalog
    cat = ChimeCatalog()
    cat.load(str(tmp_path / 'nope.json'))          # missing -> no error, empty
    assert cat.names == {}
    bad = tmp_path / 'bad.json'; bad.write_text('{not json')
    cat.load(str(bad))                              # corrupt -> swallowed
    assert cat.names == {}
