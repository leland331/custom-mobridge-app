"""the reference device as the official app showed it (connected video, 2026-10-21,
DA-G2.Pro A2B Ford, fw 02.11.08, Preset 1), cross-checked with
device-config/DEVICE_CONFIG.md. Only values seen on screen or byte-proven in
the NVM dump are here; anything else is marked as inferred or left out.

proof tags: 'video' = read off the official app's screen, 'nvm' = decoded from
the settings dump with a proven scale, 'inferred' = not seen, filled by pattern.
"""

PRODUCT = {
    'name': 'DA-G2.Pro A2B Ford',
    'subtitle': '12 Channel Professional DSP',
    'firmware': '02.11.08',
    'vehicle': 'Ford A2B G1',
    'preset': 'Preset 1',
}

# Output Mapping list (t06.0). CH10/CH11 scrolled off in the video.
OUTPUTS = (['No Mapping'] + ['CH%d [Output Channel]' % n for n in range(1, 9)]
           + ['CH9 [RCA 1]', 'CH10 [RCA 2]', 'CH11 [TOSLINK Left]', 'CH12 [TOSLINK Right]'])
OUTPUTS_INFERRED = {'CH10 [RCA 2]'}   # CH11 is confirmed by the Front Left Woofer row

INPUTS = ['Left', 'Right', 'Prompt', 'Beeps', 'Phone', 'Voice']

# Speaker slots in the SpeakerSelector. size: 'big' woofer, 'mid', 'tiny' tweeter, 'sub'.
# side: which way the cone faces (always toward the cabin).
# pos = icon centre in reference pixels (frame coords of the capture).
SLOTS = [
    # objectName,          display name,           size,   facing,  centre
    ('centertweet',        'Center Tweeter',        'tiny', 'down',  (272.0, 267.5)),
    ('centermid',          'Center Midrange',       'mid',  'down',  (326.5, 267.0)),
    ('frontleftwoofer',    'Front Left Woofer',     'big',  'right', (90.5, 335.0)),
    ('frontleftmid',       'Front Left Midrange',   'mid',  'right', (143.0, 313.0)),
    ('frontlefttweeter',   'Front Left Tweeter',    'tiny', 'right', (147.5, 363.0)),
    ('rearleftwoofer',     'Rear Left Woofer',      'big',  'right', (90.5, 501.0)),
    ('rearleftmid',        'Rear Left Midrange',    'mid',  'right', (143.0, 485.0)),
    ('rearlefttweeter',    'Rear Left Tweeter',     'tiny', 'right', (147.5, 534.0)),
    ('frontrightwoofer',   'Front Right Woofer',    'big',  'left',  (522.5, 335.0)),
    ('frontrightmid',      'Front Right Midrange',  'mid',  'left',  (470.5, 313.0)),
    ('frontrighttweeter',  'Front Right Tweeter',   'tiny', 'left',  (467.5, 363.0)),
    ('rearrightwoofer',    'Rear Right Woofer',     'big',  'left',  (522.5, 501.0)),
    ('rearrightmid',       'Rear Right Midrange',   'mid',  'left',  (470.5, 485.0)),
    ('rearrighttweeter',   'Rear Right Tweeter',    'tiny', 'left',  (467.5, 534.0)),
    ('leftsubwoofer',      'Left Sub Woofer',       'sub',  'up',    (258.0, 571.0)),
    ('rightsubwoofer',     'Right Sub Woofer',      'sub',  'up',    (346.5, 569.5)),
]

# Mapped outputs (video + DEVICE_CONFIG). channel -> per-speaker state.
CHANNELS = {
    'rearleftwoofer':   {'ch': 1,  'output': 'CH1 [Output Channel]',  'inputs': {'Left'}},
    'rearrightwoofer':  {'ch': 2,  'output': 'CH2 [Output Channel]',  'inputs': None},
    'centermid':        {'ch': 3,  'output': 'CH3 [Output Channel]',  'inputs': set(),
                         'upmix': 'Summing'},
    'rightsubwoofer':   {'ch': 4,  'output': 'CH4 [Output Channel]',  'inputs': {'Left', 'Right'},
                         'gain_db': -3.0},
    'frontleftwoofer':  {'ch': 11, 'output': 'CH11 [TOSLINK Left]',   'inputs': None},
    'frontrightwoofer': {'ch': 12, 'output': 'CH12 [TOSLINK Right]',
                         'inputs': {'Right', 'Prompt', 'Beeps', 'Phone', 'Voice'}},
}
# 'inputs': None = not seen in the video, rendered all-off with a "not read yet" tooltip.

SUBS = {'leftsubwoofer', 'rightsubwoofer'}       # subs show only Left/Right inputs
CENTERS = {'centermid', 'centertweet'}           # centres show the Upmix Algorithm row

# Digital Inputs (Config tab). Values proven: 1/512 dB scale, Beeps 0xe900 = -2.0 dB.
INPUT_GAINS = {'Stereo': 0.0, 'Prompt': 0.0, 'Beeps': -2.0, 'Phone': 0.0, 'Voice': 0.0}
MONO_DELAY_MS = 10.0       # shown 10.0 ms; firmware caps the stored 36.1 ms to 10 ms

MASTER_GAIN_DB = 0.0

# Channel EQ filter block as shown for the Right Sub Woofer (t00.5).
FILTER = {'type': 'None', 'model': 'Biquad', 'lp_hz': 150, 'hp_hz': 2000,
          'lp_slope': 12, 'hp_slope': 12}

# Per-channel delay and limiter as shown (Time Align 0.00 ms, Limiter 0.0 dBFS on every row).
# Wire: delay u16 us -> ms; limiter i32, dBFS = 20*log10(raw/2^31), 0x7FFFFFFF = 0 dBFS = OFF
# (decoders in mobridge/encoders.py). Distance is derived from the delay, not stored.
DELAY_MS = {obj: 0.0 for obj in CHANNELS}
LIMIT_DBFS = {obj: 0.0 for obj in CHANNELS}
SPEED_OF_SOUND_CM_PER_MS = 34.3


def distance_cm(obj):
    return DELAY_MS[obj] * SPEED_OF_SOUND_CM_PER_MS


def role_for(obj: str) -> int:
    """SpeakerSelector objectName -> model role bit (exact or prefix of the xml id)."""
    from ... import model as M
    names = {name.lower(): bit for bit, name, _d, _o in M.SPEAKER_TYPES}
    if obj in names:
        return names[obj]
    for n, bit in names.items():
        if n.startswith(obj):
            return bit
    raise KeyError(obj)


def obj_for(role: int) -> str | None:
    from ... import model as M
    name = M.NAME_BY_ROLE.get(role, '').lower()
    for s in SLOTS:
        if name == s[0] or name.startswith(s[0]):
            return s[0]
    return None


def demo_model():
    """the reference device as a DspModel (the offline preview; replaced by the device's
    own tune on connect). Values above, nothing invented: unseen input maps are 0."""
    from ... import model as M
    root = M.DspModel(fw=210, product=0x202c)
    root.channel_count = 12
    root.name = PRODUCT['name']
    for obj, c in CHANNELS.items():
        ch = c['ch'] - 1
        root.map_speaker(role_for(obj), ch)
        s = root.speaker(role_for(obj), 0)
        s.gain_db = c.get('gain_db', 0.0)
        mask = 0
        for n in (c['inputs'] or ()):
            mask |= 1 << M.INPUT_BITS[n]
        root.input_map[ch] = mask
    for p in range(M.MAX_PRESETS):
        for n, k in M.INPUT_GAIN_INDEX.items():
            root.input_gains[p][k] = INPUT_GAINS[n] if p == 0 else 0.0
        root.input_delay_ms[p] = MONO_DELAY_MS if p == 0 else 0.0
    root.clear_dirty()
    return root


def mapped_slots():
    return [s for s in SLOTS if s[0] in CHANNELS]


def slot(obj):
    for s in SLOTS:
        if s[0] == obj:
            return s
    raise KeyError(obj)
