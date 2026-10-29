"""Catalog of the app's MDI jobs (names from the app binary, NOTES.md 6).

Wire-level ids are NOT known yet. Each entry has `opcode=None` until the
decompile pins it; requesting a job with no opcode raises FramingUnknown.
`access` drives the safety policy (safety.py), so every job is classified
conservatively: when the direction of a job was unclear it is treated as a write.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .modes import DeviceMode


class Access(Enum):
    READ = 'read'               # reads state, changes nothing
    SESSION = 'session'         # changes link/session state only, nothing persistent
    BUS_TX = 'bus-tx'           # puts traffic on the car's CAN/MOST/A2B/UDS buses or plays audio
    WRITE = 'write'             # changes persistent device state
    DESTRUCTIVE = 'destructive'  # erases or resets


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    access: Access
    doc: str = ''
    fw_handler: str = ''         # matching firmware-side handler, where one is known
    opcode: int | None = None    # TODO(decompile)
    modes: tuple = (DeviceMode.MDI,)


R, S, B, W, X = Access.READ, Access.SESSION, Access.BUS_TX, Access.WRITE, Access.DESTRUCTIVE

_TABLE = [
    # general / auth
    ('Ping', 'general', R, 'liveness check'),
    ('SwitchMDI', 'general', S, 'switch the device into MDI mode', '', (DeviceMode.UNKNOWN, DeviceMode.WIZARD, DeviceMode.MDI)),
    ('Authenticate', 'general', S, 'session auth; payload unknown, device does not appear to enforce it', 'mdimod_general_auth'),
    ('EnableDebug', 'general', S, 'turn on device debug output'),
    ('SetBaud', 'general', S, 'change UART speed (19200/115200/230400/460800)'),
    ('SWVersion', 'general', R, 'firmware version'),
    ('SerialNumber', 'general', R, 'serial number'),
    ('DipConfig', 'general', R, 'DIP switch configuration (assumed read)'),
    ('FactoryReset', 'general', X, 'factory reset', 'mdimod_general_reset'),
    ('LocalIDRead', 'general', R, 'local node id'),
    ('LocalIDWrite', 'general', W, 'set local node id'),
    # config (.rax)
    ('ConfigSync', 'config', W, 'sync config (direction unclear, treated as write)'),
    ('ConfigUpload', 'config', W, 'PC -> device config', 'mdimod_config_receive_file'),
    ('ConfigDownload', 'config', R, 'device -> PC config (inferred from downloadNvm being the read path)'),
    ('GetConfigFile', 'config', R, 'fetch the config file'),
    # flash + on-device filesystem
    ('FlashRead', 'flash', R, 'read SPI flash range'),
    ('FlashCrc', 'flash', R, 'CRC of a flash range (CRC32 assumed)'),
    ('FlashFile', 'flash', W, 'send a .fif by ZMODEM and program it', 'mdimod_flashfile_received_cb'),
    ('DirChange', 'fs', S, 'change directory on the device filesystem'),
    ('DirList', 'fs', R, 'list device filesystem'),
    ('DirRm', 'fs', X, 'delete a device file'),
    ('DirFlash', 'fs', W, 'program a file already on the device'),
    # debug console passthrough
    ('DebugEnable', 'debug', S, 'enable the device console over MDI'),
    ('DebugSend', 'debug', B, 'send console text; filtered per command by safety.classify_console'),
    # DSP
    ('DspRead', 'dsp', R, 'read DSP block', 'mdimod_dsp_tx_*'),
    ('DspWrite', 'dsp', W, 'write DSP block', 'mdimod_dsp_rx_*'),
    ('DspParamRead', 'dsp', R, 'read DSP parameter', 'mdimod_dsp_param_tx'),
    ('DspParamWrite', 'dsp', W, 'write DSP parameter', 'mdimod_dsp_param_rx'),
    ('DspTest', 'dsp', B, 'DSP test (may play audio)', 'mdimod_dsp_test'),
    # buses
    ('CanOpen', 'can', S, 'open the PC-to-CAN bridge', 'mdimod_can_open'),
    ('CanBitrate', 'can', S, 'set CAN bitrate'),
    ('CanFrameSend', 'can', B, 'transmit a CAN frame on the vehicle bus', 'mdimod_can_send'),
    ('MostOpen', 'most', S, 'open MOST'),
    ('MostMode', 'most', S, 'MOST mode'),
    ('MostQuery', 'most', R, 'MOST query'),
    ('MostMsgSend', 'most', B, 'transmit a MOST message'),
    ('UdsMsgSend', 'uds', B, 'transmit a UDS diagnostic message'),
    ('A2bOpen', 'a2b', S, 'open A2B'),
    ('A2bQuery', 'a2b', R, 'A2B query'),
    ('A2bDescriptor', 'a2b', R, 'A2B node descriptor'),
    ('A2bMsgSend', 'a2b', B, 'transmit an A2B message'),
    ('A2bI2C', 'a2b', B, 'A2B remote I2C access (can write node registers)'),
    ('A2bPhantom', 'a2b', B, 'A2B phantom power'),
]

COMMANDS: dict[str, Command] = {}
for row in _TABLE:
    name, group, access, doc = row[:4]
    fw = row[4] if len(row) > 4 else ''
    modes = row[5] if len(row) > 5 else (DeviceMode.MDI,)
    COMMANDS[name] = Command(name, group, access, doc, fw, None, modes)


def get(name: str) -> Command:
    try:
        return COMMANDS[name]
    except KeyError:
        raise KeyError('unknown MDI job %r (known: %s)' % (name, ', '.join(COMMANDS)))
