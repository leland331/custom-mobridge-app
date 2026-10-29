"""Diagnostic Capture: read-only, and GATED OFF until DebugSend is byte-matched."""
import pytest

from mobridge import diagnostics as dg, safety
from mobridge.commands import Access


def test_sequence_is_whitelisted_and_never_destructive():
    dg.assert_sequence_safe()   # raises if any step is off-whitelist or destructive
    for s in dg.STEPS:
        assert dg._token_word(s.token)
        assert safety.classify_console(s.token) is not Access.DESTRUCTIVE


def test_hard_block_examples_can_never_be_emitted():
    # The action emits ONLY whitelisted tokens (see _token_word, the emit gate).
    # Every line on the recipe's never-type list is rejected by that gate, so this
    # action can never issue one -- independent of the global console classifier.
    for line in dg.HARD_BLOCK_EXAMPLES:
        assert dg._token_word(line) is False, line
    # And none of them appear in the fixed sequence.
    seq = {s.token.strip().lower() for s in dg.STEPS}
    for line in dg.HARD_BLOCK_EXAMPLES:
        assert line.strip().lower() not in seq


def test_capture_is_gated_off_today():
    # DebugSend is unmapped (candidate 0x0e00) -> not sendable -> action blocked.
    assert dg.debugsend_sendable() is False

    class _Eng:
        connected = True
    ok, reason = dg.can_run(_Eng())
    assert ok is False
    assert 'DebugSend' in reason and 'fault_capture.py' in reason


def test_run_refuses_while_gated():
    class _Eng:
        connected = True
    with pytest.raises(safety.SafetyError):
        dg.run(_Eng(), '/tmp/should_not_be_written.log')


def test_debugsend_sendable_would_unlock_if_mapped_and_cleared(tmp_path):
    # Simulate a future wiremap where DebugSend maps to a command that IS parity
    # cleared (reuse a read cmd id that already clears, e.g. 0x0710 -> fblock 0x07,
    # fkt 0x10). Proves the gate is tied to real state, not a hardcoded False.
    wm = {'devid': 0x0001, 'jobs': {'DebugSend': {'fblock': 0x07, 'inst': 0, 'fkt': 0x10, 'op': 0}}}
    assert dg.debugsend_sendable(wm) is True
    # ...but a command with no cleared reference (0x0999) stays blocked.
    wm_bad = {'devid': 0x0001, 'jobs': {'DebugSend': {'fblock': 0x09, 'inst': 0, 'fkt': 0x99, 'op': 0}}}
    assert dg.debugsend_sendable(wm_bad) is False
