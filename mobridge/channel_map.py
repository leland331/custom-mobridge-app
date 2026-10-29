"""Speaker map + labelling, identical to the official app so the user isn't
confused switching between them.

- Display names and objectNames are taken word-for-word from the app's
  SpeakerSelector / ChannelMapForm vocabulary (UI_SPEC §3.1 + the laptop's
  decompiled label set, consolidated in answers_for_deviceconfig.md). We do NOT
  paraphrase: the strings read exactly like the real app.
- The slot assignment (speakermap) is per-preset: speaker position P drives amp
  output slot N when `<objectName>_channel = N`. the reference CURRENT assignment is
  seeded below so the screen looks familiar the moment he opens it.

This is UI/display data. It does not transmit anything; writing a new map to the
device is a DSP write and stays behind the parity send-gate like every other
control.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeakerPosition:
    display: str        # word-for-word app label, e.g. "Front Left Woofer"
    object_name: str    # app objectName used for `<name>_channel`
    group: str          # selector cluster, for layout
    confirmed: bool = True   # label confirmed against the app vocabulary


# Full position vocabulary the app supports, in the app's selector order. Casing
# of object_name is preserved exactly as the app uses it (tweeter/mid/woofer are
# lowercase; FullRange is CamelCase; TOSLINK is upper) — see answers file.
SPEAKERS: list[SpeakerPosition] = [
    SpeakerPosition('Front Left Tweeter',    'frontlefttweeter',    'Front Left'),
    SpeakerPosition('Front Left Midrange',   'frontleftmid',        'Front Left'),
    SpeakerPosition('Front Left Woofer',     'frontleftwoofer',     'Front Left'),
    SpeakerPosition('Front Left Full Range', 'FrontLeftFullRange',  'Front Left'),
    SpeakerPosition('Front Right Tweeter',   'frontrighttweeter',   'Front Right'),
    SpeakerPosition('Front Right Midrange',  'frontrightmid',       'Front Right'),
    SpeakerPosition('Front Right Woofer',    'frontrightwoofer',    'Front Right'),
    SpeakerPosition('Front Right Full Range','FrontRightFullRange', 'Front Right'),
    SpeakerPosition('Rear Left Tweeter',     'rearlefttweeter',     'Rear Left'),
    SpeakerPosition('Rear Left Midrange',    'rearleftmid',         'Rear Left'),
    SpeakerPosition('Rear Left Woofer',      'rearleftwoofer',      'Rear Left'),
    SpeakerPosition('Rear Left Full Range',  'RearLeftFullRange',   'Rear Left'),
    SpeakerPosition('Rear Right Tweeter',    'rearrighttweeter',    'Rear Right'),
    SpeakerPosition('Rear Right Midrange',   'rearrightmid',        'Rear Right'),
    SpeakerPosition('Rear Right Woofer',     'rearrightwoofer',     'Rear Right'),
    SpeakerPosition('Rear Right Full Range', 'RearRightFullRange',  'Rear Right'),
    SpeakerPosition('Front Center Midrange', 'centermid',           'Front Center'),
    SpeakerPosition('Front Center Tweeter',  'centertweet',         'Front Center'),
    SpeakerPosition('Left Sub Woofer',       'leftsubwoofer',       'Subs'),
    SpeakerPosition('Right Sub Woofer',      'rightsubwoofer',      'Subs'),
    SpeakerPosition('Headliner Left Tweeter','headlinerlefttweet',  'Headliner'),
    SpeakerPosition('Headliner Right Tweeter','headlinerrighttweet','Headliner'),
    SpeakerPosition('Headrest Left Tweeter', 'headrestlefttweet',   'Headrest'),
    SpeakerPosition('Headrest Right Tweeter','headrestrighttweet',  'Headrest'),
    SpeakerPosition('TOSLINK Left',          'TOSLINK Left',        'Digital Inputs'),
    SpeakerPosition('TOSLINK Right',         'TOSLINK Right',       'Digital Inputs'),
]

BY_OBJECT = {s.object_name: s for s in SPEAKERS}

# "Output Channel 1…N" is the app's combo-box vocabulary for a slot assignment.
def output_channel_label(slot: int) -> str:
    return 'Output Channel %d' % slot

UNASSIGNED = 'Not Assigned'   # app's empty-slot label


# the reference CURRENT assignment (object_name -> output slot), captured live from the
# app's SpeakerSelector `_channel` labels (answers_for_deviceconfig.md), cross-
# checked against his decoded NVM speakermap. Seed this so the screen matches
# what he sees in the official app today.
CURRENT_ASSIGNMENT: dict[str, int] = {
    'frontleftwoofer':  11,
    'frontrightwoofer': 12,
    'rearleftwoofer':   1,
    'rearrightwoofer':  2,
    'centermid':        3,
    'rightsubwoofer':   4,
}


def assignment_table(assignment: dict[str, int] | None = None) -> list[tuple[str, str]]:
    """(display label, output-channel label) rows for the ChannelMap screen, in
    the app's selector order. Unassigned positions read 'Not Assigned'."""
    assignment = CURRENT_ASSIGNMENT if assignment is None else assignment
    rows = []
    for s in SPEAKERS:
        slot = assignment.get(s.object_name)
        rows.append((s.display, output_channel_label(slot) if slot else UNASSIGNED))
    return rows


def assigned_positions(assignment: dict[str, int] | None = None) -> list[tuple[str, int]]:
    assignment = CURRENT_ASSIGNMENT if assignment is None else assignment
    return [(BY_OBJECT[o].display, n) for o, n in assignment.items() if o in BY_OBJECT]
