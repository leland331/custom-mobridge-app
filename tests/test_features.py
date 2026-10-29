"""Feature registry, delay/phase compensation maths and the connection status line."""
import re

import pytest

from mobridge import features as FX
from mobridge import rearchimes as RC
from mobridge import uirules as UR


# --- registry ----------------------------------------------------------------
@pytest.mark.parametrize('tag, want', [
    ('LB-RC1', {'rear_chimes', 'meters12'}), ('LB-RC12', {'rear_chimes', 'meters12'}), (' LB-RC2 ', {'rear_chimes', 'meters12'}),
    ('LB-03', set()), ('LB-06', set()), ('LB-PAC02', set()), ('12:57:20', set()), (None, set()), ('', set()),
])
def test_detection_by_build_tag(tag, want):
    assert FX.active(tag) == want
    assert RC.detect(tag) == ('rear_chimes' in want)          # old helper agrees with the registry


def test_capability_bits_must_all_be_set():
    f = FX.Feature('x', 'X', re.compile(r'LB-X\d*'), caps=0b101)
    assert not f.detected('LB-X1', 0b001)
    assert f.detected('LB-X1', 0b111)
    assert not f.detected('LB-03', 0b111)                     # right bits, wrong firmware


def test_registry_keys_unique_and_no_latency_today():
    keys = [f.key for f in FX.FEATURES]
    assert len(keys) == len(set(keys))
    comp = FX.plan({f.key for f in FX.FEATURES}, None, range(12))
    assert comp.ok and set(comp.extra.values()) == {0}


# --- compensation maths ------------------------------------------------------
def _reg(*latencies):
    return tuple(FX.Feature('f%d' % i, 'F%d' % i, re.compile('LB-F'), latency=lambda _r, m=m: m)
                 for i, m in enumerate(latencies))


def test_other_outputs_get_the_missing_delay():
    reg = _reg({2: 480, 3: 480})                             # e.g. an upmix delaying centre + one more
    comp = FX.plan({'f0'}, None, range(6), registry=reg)
    assert comp.ok
    assert comp.extra == {0: 480, 1: 480, 2: 0, 3: 0, 4: 480, 5: 480}
    assert comp.extra_ms()[0] == pytest.approx(10.0)          # 480 samples at 48 kHz


def test_features_in_series_add_up():
    reg = _reg({0: 100}, {0: 50, 1: 30})
    assert FX.path_latency({'f0', 'f1'}, None, range(3), registry=reg) == {0: 150, 1: 30, 2: 0}
    assert FX.plan({'f0', 'f1'}, None, range(3), registry=reg).extra == {0: 0, 1: 120, 2: 150}
    assert FX.plan({'f1'}, None, range(3), registry=reg).extra == {0: 0, 1: 20, 2: 50}   # only active ones


def test_latency_may_depend_on_the_tune():
    reg = (FX.Feature('up', 'Upmix', re.compile('x'), latency=lambda r: {2: 256} if r['on'] else {}),)
    assert FX.plan({'up'}, {'on': False}, range(3), registry=reg).extra == {0: 0, 1: 0, 2: 0}
    assert FX.plan({'up'}, {'on': True}, range(3), registry=reg).extra == {0: 256, 1: 256, 2: 0}


def test_outputs_outside_the_product_are_ignored():
    reg = _reg({0: 10, 40: 999})
    assert FX.plan({'f0'}, None, range(2), registry=reg).extra == {0: 0, 1: 10}


def test_not_enough_delay_range_is_not_ok():
    comp = FX.compensate({0: 0, 1: 300, 2: 300}, headroom={0: 200, 1: 0, 2: 0})
    assert not comp.ok and 'output(s) 1' in comp.reason
    assert FX.compensate({0: 0, 1: 300}, headroom={0: 300, 1: 0}).ok


def test_negative_latency_is_refused():
    with pytest.raises(ValueError):
        FX.plan({'f0'}, None, range(2), registry=_reg({0: -1}))


def test_only_the_music_path_is_ever_delayed():
    from mobridge import model as M
    assert set(FX.MUSIC_INPUTS) == {'Left', 'Right'}
    assert set(FX.MUSIC_INPUTS) | set(FX.UNDELAYED_INPUTS) == set(M.INPUT_BITS)
    assert RC.NAME not in FX.MUSIC_INPUTS                                              # Rear Chimes undelayed


def test_empty():
    assert FX.compensate({}) == FX.Compensation({}, True)


# --- connection status line ----------------------------------------------------
@pytest.mark.parametrize('kw, want', [
    (dict(state=0), 'Not connected.'),
    (dict(state=14, error='Could not open COM3: busy'), 'Not connected. Could not open COM3: busy'),
    (dict(state=0, searching=True), 'Looking for your Mobridge…'),
    (dict(state=2, port='COM3'), 'Connecting over USB (COM3)…'),
    (dict(state=3, port='rfc2217://pi.local:4000', product='DA-G2.Pro'),
     'Connected to DA-G2.Pro over the network (pi.local:4000).'),
    (dict(state=14, port='COM3', reconnecting=True), 'Connection lost. Reconnecting over USB (COM3)…'),
    (dict(state=16, port='COM3'), 'Incompatible device over USB (COM3).'),
    (dict(state=6, port='COM3'), 'Updating Firmware over USB (COM3)…'),
])
def test_connection_status(kw, want):
    state = kw.pop('state')
    assert UR.connection_status(state, **kw) == want
