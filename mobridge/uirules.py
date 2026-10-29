"""Every UI rule of the official app that the decompile pinned, as plain data
and tiny functions, so the GUI and the tests read one source:

  * spin-box / slider ranges, steps, decimals, suffixes, defaults (UI_REGION_1 §1,
    UI_REGION_2 §3.1, §4, §9)
  * capability-bit and firmware rules for what is shown / enabled (UI_REGION_1 §2)
  * the connection-state enum and its button texts (UI_REGION_2 §1.1)
  * menu and action table with shortcuts and start-up state (UI_REGION_1 §0.1, §2.1)
  * dialog titles / texts / buttons (UI_REGION_1 §4)
  * firmware validator codes and messages, update status strings (UI_REGION_2 §5,
    UI_REGION_1 §4)
  * graph axis constants (UI_REGION_1 §5.3)

Each entry quotes the function it came from. Where the decompile could not
settle a value it is marked UNRESOLVED and the checklist says "can't-tell".
"""
from __future__ import annotations

from dataclasses import dataclass

APP_NAME = 'mObridge DSP'
APP_VERSION = '25.8.0'
WINDOW_TITLE = 'mObridge DSP - v25.8.0'
ORG_NAME, ORG_DOMAIN = 'mObridge', 'mobridge.us'
SPLASH_MS = 250
ABOUT = {'title': 'About mObridge DSP', 'version': '25.8.0', 'build_date': '2025-08-25T12:00:20Z',
         'build_sha': '9d5c2a08', 'qt': '6.9.1', 'cmake': '3.31.6-msvc6',
         'copyright': 'Copyright 2010-2023 Fiberdyne Research Pty Ltd'}


# --------------------------------------------------------------------------
# value controls
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SpinRule:
    name: str
    lo: float
    hi: float
    step: float
    decimals: int = 1
    suffix: str = ''
    default: float | None = None
    source: str = ''


def gain_step(fw: int) -> float:
    """FUN_1400697e0: 0.1 dB for fw > 201, else 0.5 (legacy u8 gains)."""
    return 0.1 if fw > 0xC9 else 0.5


def gain_slider_range(fw: int) -> tuple[int, int, float]:
    """FUN_140080d10: (min, max, scale) -> value = slider / scale.
    fw >= 202: -270..90 / 10.0; older: -54..18 / 2.0."""
    return (-270, 90, 10.0) if fw >= 0xCA else (-54, 18, 2.0)


GAIN = SpinRule('gainDbDoubleSpinBox', -27.0, 9.0, 0.1, 1, ' dB', 0.0, 'FUN_14006b0c0 (DAT_1401fdd60/dd00/d508)')
EQ_FREQ = SpinRule('frequencySpinBox', 0.0, 24000.0, 10.0, 2, ' Hz', None, 'FUN_14006b0c0 line 7324')
EQ_MAG = SpinRule('magnitudeSpinBox', -24.0, 12.0, 0.5, 2, ' dB', 0.0, 'FUN_14006b0c0 line 7472')
EQ_Q = SpinRule('qFactorLeftSpinBox/Right', 0.05, 14.0, 0.5, 2, '', 3.0, 'FUN_14006b0c0 line 7621')
CUTOFF = SpinRule('loPSpinBox/hiPSpinBox', 20, 23080, 1, 0, ' Hz', 150, 'FilterEditorForm lines 10484/11144')
SLOPE = SpinRule('loPSlopeSpinBox/hiPSlopeSpinBox', 6, 48, 6, 0, ' dB/8ve', 12, 'lines 11652-11698; suffix from the 2026-10-21 video (setSuffix string not in the decompile)')
SLOPE_BIQUAD = SpinRule('slope (woofer/sub, mode 2)', 12, 48, 12, 0, ' dB/8ve', 12, 'lines 11652-11663: min 12 step 12')
DELAY = SpinRule('delayDoubleSpinBox', 0.0, 10.0, 0.1, 1, ' ms', 0.0, 'TimeAlignDelayForm lines 12825-12827')
DELAY_SLIDER = (0, 100, 5, 10)        # min, max, default, tick interval (lines 12783-12787)
TA_DISTANCE = SpinRule('timeAlignDistance', 0.0, 600.0, 0.1, 1, '', 0.0, 'TimeAlignWidget lines 14348-14350')
TA_DELAY = SpinRule('timeAlignDelay', 0.0, 10.0, 0.01, 2, ' ms', 0.0, 'step DAT_1401fe590')
LIMITER = SpinRule('limiterDbfsDoubleSpinBox', -36.0, 0.0, 0.1, 1, ' dBFS', -36.0, 'FUN_1400559c0 / FUN_140054d60')
LIMITER_SLIDER = (-360, 0, 10.0)      # min, max, scale (value = dB * 10)
INPUT_GAIN = SpinRule('InputGainsForm gain', -48.0, 9.0, 0.1, 1, ' dB', 0.0, 'FUN_1400ece60 clamp; step FUN_1400697e0')
INPUT_DELAY = SpinRule('InputGainsForm mono delay', 0.0, 10.0, 0.1, 1, ' ms', 0.0, 'FUN_1400ecce0; slider x DAT_1401fd510=10')
TEST_FREQ = SpinRule('chanOutFreqSpinBox', 1, 22050, 1, 0, ' Hz', None, 'GeneralTestWidget line 64547')
TEST_AMP = SpinRule('chanOutAmpSpinBox', 0, 100, 1, 0, ' %', None, 'line 64588')
RTA_TIMER_MS = 300                    # FUN_140055830

FILTER_TYPES = ['None', 'Low Pass', 'High Pass', 'Band Pass']       # filterTypeComboBox index = type id
FILTER_MODELS = ['Butterworth', 'Chebychev', 'Bessel', 'Linkwitz-Riley', 'Biquad']   # filterModelComboBox index = design id
BIQUAD_MODEL_TOOLTIP = 'For Woofers/Subwoofers, \nthis must be set to Biquad'
BIQUAD_SLOPE_TOOLTIP = ('Since Biquad filters are 2nd-order filters, only 2nd, 4th, 6th, and 8th order '
                        'filters may be selected.')
QLR_MIRROR_TOOLTIP = ('Mirror left and right Q-Factors for the selected point. For Woofers and Sub Woofers, '
                      'this is always active.')


def slope_rule(mode: int) -> SpinRule:
    """Woofer/sub (channel mode 2): min 12, step 12, both slopes linked."""
    return SLOPE_BIQUAD if mode == 2 else SLOPE


def clamp(rule: SpinRule, v: float) -> float:
    return max(rule.lo, min(rule.hi, v))


def quantise(rule: SpinRule, v: float) -> float:
    """QDoubleSpinBox rounding to the rule's decimals (the step is applied by
    the arrows, not by typing)."""
    return round(clamp(rule, v), rule.decimals)


def step(rule: SpinRule, v: float, direction: int) -> float:
    return quantise(rule, v + direction * rule.step)


# --------------------------------------------------------------------------
# capability bits (FUN_140097750 product flags) and firmware thresholds
# --------------------------------------------------------------------------
CAP_CHANNEL_EQ = 1 << 0
CAP_MASTER_EQ = 1 << 1          # "CONFIG && MASTER EQ", head-unit options, speaker selector
CAP_TIME_ALIGN = 1 << 2
CAP_INPUT_GAINS = 1 << 3
CAP_INVERT_PHASE = 1 << 6
CAP_FEATURE_SUB2 = 1 << 7
CAP_FEATURE_SUB01 = 1 << 8
CAP_BUNDLED_FW = 1 << 9
CAP_CHANNEL_MAP = 1 << 9        # ChannelMapForm connected panel
CAP_MASTERNODE = 1 << 10
CAP_AUTO_RESET = 1 << 11
CAP_LIMITER = 1 << 15
CAP_CROSSOVER = 1 << 16
CAP_BIQUAD_MODE = 1 << 17
FW_LIMITER_MIN = 0xCE            # 206: limiter tab needs fw >= 206
FW_HEADUNIT_MIN = 0xD2           # 210: head-unit menu needs fw >= 210
FW_BIQUAD_MIN = 0xCB             # 203: app-computed biquads / 0x0708
FW_TIMEALIGN_MIN = 0xD0          # 208: 0x0707 sub3

# the reference DA-G2.Pro A2B Ford (product 0x202c): the bits the connected UI showed
# (video 2026-10-21: CONFIG && MASTER EQ, CHANNEL EQ, TIME ALIGN, LIMITER, SCRIPTING).
# The product table itself (PTR_s_Bavsound_142605200) is static data the decompile
# could not read: UNRESOLVED; these bits are what the screen proves.
PRODUCT_CAPS_202C = (CAP_CHANNEL_EQ | CAP_MASTER_EQ | CAP_TIME_ALIGN | CAP_INPUT_GAINS | CAP_INVERT_PHASE
                     | CAP_CHANNEL_MAP | CAP_LIMITER | CAP_CROSSOVER | CAP_BIQUAD_MODE)


def tab_visibility(connected: bool, caps: int, fw: int, channel_count: int, scripting_enabled: bool) -> dict:
    """FUN_140037060 + FUN_1400365c0: which tab buttons are shown and the config
    button's text."""
    v = {'config': True, 'config_text': 'CONFIGURATION', 'channel_eq': False, 'time_align': False,
         'limiter': False, 'anc': False, 'calibration': False, 'volume': False, 'masternode': False,
         'scripting': False, 'device': True}
    if channel_count == 0:
        return v
    if not connected:
        v['channel_eq'] = True
        return v
    v['config_text'] = 'CONFIG && MASTER EQ' if caps & CAP_MASTER_EQ else 'CONFIGURATION'
    v['channel_eq'] = bool(caps & (CAP_CHANNEL_EQ | CAP_CROSSOVER))
    v['time_align'] = bool(caps & CAP_TIME_ALIGN)
    v['limiter'] = bool(caps & CAP_LIMITER) and fw >= FW_LIMITER_MIN
    v['masternode'] = bool(caps & CAP_MASTERNODE)
    v['scripting'] = scripting_enabled
    return v


def menu_state(connected: bool, caps: int, fw: int, state: int) -> dict:
    """FUN_1400365c0: enabled/visible state of the connect-dependent actions."""
    programming = state == 5
    return {
        'mute_all': connected and not programming,
        'unmap_all': connected and not programming,
        'firmware_update': connected and not programming,
        'add_filter_auto_visible': connected and bool(caps & CAP_CROSSOVER),
        'headunit_menu_visible': connected and bool(caps & CAP_MASTER_EQ) and fw >= FW_HEADUNIT_MIN,
        'help_device_links_visible': connected,
        'preset_combo': not programming,
    }


def ui_locked(updater_state: int) -> bool:
    """FUN_1400333b0: everything disabled while the updater is in state 4 or 6."""
    return updater_state in (4, 6)


def speaker_selector_enabled(channel_count: int, state: int, caps: int) -> bool:
    """FUN_140043360: off with no channels, or in states 2/3/9 without CAP_MASTER_EQ."""
    if channel_count == 0:
        return False
    if state in (2, 3, 9):
        return bool(caps & CAP_MASTER_EQ)
    return True


def limiter_controls_enabled(passthrough: bool) -> bool:
    """FUN_1400558d0: limiter slider/spin/bar disabled while the channel is in pass-through."""
    return not passthrough


def crossover_editor_enabled(caps: int, role: int, enabled_mask: int) -> bool:
    """line 11768: disabled unless CAP_CROSSOVER or the role is in the enabled mask."""
    return bool(caps & CAP_CROSSOVER) or bool(role & enabled_mask)


def invert_phase_enabled(caps: int) -> bool:
    return bool(caps & CAP_INVERT_PHASE)         # FUN_140071790


def download_auto_enabled(check_auto: bool) -> bool:
    return check_auto                            # FUN_140037d30 / FUN_14004c890


# --------------------------------------------------------------------------
# connection states (UI_REGION_2 §1.1) and the Connected button text
# --------------------------------------------------------------------------
CONNECT_STATES = {
    0: 'Offline Mode', 1: 'Searching for Devices ...', 2: 'Connecting', 3: 'Connected', 4: 'Connected',
    5: 'Recovery', 6: 'Updating Firmware', 7: 'Restarting', 8: 'Factory Resetting', 9: 'Synchronizing',
    10: 'Transferring Data', 11: 'Transferring Data', 12: 'Tuning', 14: 'Disconnected',
    15: 'Offline Mode', 16: 'Incompatible Device',
}


def connect_button_text(state: int, update_available: bool = False) -> str:
    """FUN_1400b2d00: the Connected button text, with the exe's two trailing spaces."""
    if state == 3 and update_available:
        return 'Firmware Update Available  '
    if state in CONNECT_STATES and state not in (0, 15):
        return CONNECT_STATES[state] + '  '
    return 'Offline Mode  '


def connection_where(port: str | None) -> str:
    """'over USB (COM3)' / 'over the network (pi:4000)' / '' for the status line."""
    if not port:
        return ''
    if '://' in port:
        return 'over the network (%s)' % port.split('://', 1)[1]
    return 'over USB (%s)' % port


def connection_status(state: int, port: str | None = None, *, searching: bool = False,
                      reconnecting: bool = False, product: str | None = None,
                      error: str | None = None) -> str:
    """The Connection screen's one status line, in plain words, from the engine's
    connection state (CONNECT_STATES) plus what the screen itself is doing."""
    where = connection_where(port)
    sp = (' ' + where) if where else ''
    if reconnecting:
        return 'Connection lost. Reconnecting%s…' % sp
    if searching:
        return 'Looking for your Mobridge…'
    if state == 2:
        return 'Connecting%s…' % sp
    if state in (3, 4):
        return 'Connected to %s%s.' % (product or 'the Mobridge', sp)
    if state == 16:
        return 'Incompatible device%s.' % sp
    if state in CONNECT_STATES and state not in (0, 1, 14, 15):
        return '%s%s…' % (CONNECT_STATES[state], sp)      # Updating Firmware, Restarting, ...
    return 'Not connected.' + (' ' + error if error else '')


def product_image(product_id) -> str | None:
    """FUN_140097870: the product's image (':/images/dag2.a2b.ford.pro.png' ->
    our asset 'dag2_a2b_ford_pro.png'); None when we do not carry that image."""
    import os
    from . import products
    path = products.image_path(product_id)
    if not path:
        return None
    stem, ext = os.path.splitext(path.rsplit('/', 1)[-1])
    name = stem.replace('.', '_') + ext
    here = os.path.join(os.path.dirname(__file__), 'gui', 'official', 'assets', name)
    return name if os.path.exists(here) else None


STATE_CONNECTED, STATE_CONNECTED_UNKNOWN, STATE_RECOVERY, STATE_UPDATING = 3, 4, 5, 6
STATE_OFFLINE, STATE_INCOMPATIBLE, STATE_CONNECTING, STATE_MULTIPLE = 15, 16, 2, 15
CONNECT_TIMING = {'post_open_ms': 100, 'deque_poll_ms': 10, 'deque_wait_ms': 100, 'post_write_ms': 250,
                  'product_poll': (10, 100), 'reconnect_ms': 100, 'fdsm_tick_ms': 100,
                  'flash_timeout_s': 240, 'job_timeout_s': 15, 'reset_timeout_s': 20,
                  'baud_open': 115200, 'baud_fast': 230400}
SYNC_RESULTS = {0: 'ok', 1: 'read failed', 2: 'decode failed', 3: 'unknown product', 4: 'more than 64 channels'}


def state_after_sync(result: int, allow_unknown: bool, reconnect_after_reset: bool) -> int:
    """FUN_1400b1020: 0 -> Connected (3), 3 with the "allow unknown" option ->
    4, after a reset -> Recovery (5); anything else drops the link."""
    if result == 0 or (result == 3 and allow_unknown):
        if reconnect_after_reset:
            return STATE_RECOVERY
        return STATE_CONNECTED_UNKNOWN if result == 3 else STATE_CONNECTED
    return 14


# --------------------------------------------------------------------------
# menus (UI_REGION_1 §0.1 / retranslateUi) : (text, shortcut, checkable, start enabled, start visible)
# --------------------------------------------------------------------------
MENU_FILE = [
    ('New Tune', 'Ctrl+N', False, True, True),
    ('Save Tune', 'Ctrl+S', False, True, True),
    ('Save Tune As...', 'Ctrl+Alt+S', False, True, True),
    ('Open Tune...', 'Ctrl+O', False, True, True),
    None,
    ('Import Script Module...', None, False, True, False),    # visible only with the scripting option
    None,
    ('Fullscreen', 'F11', True, True, True),
    ('Exit', 'Ctrl+Q', False, True, True),
]
MENU_OPTIONS = [       # menuOptions is hidden at start (menuAction()->setVisible(false)) until a menu is non-empty
    ('Add Filter Automatically', None, True, False, False),
    ('Ignore Load Warnings', None, True, True, True),
]   # exe order: Add Filter Automatically, Firmware Update >, Ignore Load Warnings, Head Unit >, Effects >
OPTIONS_TOOLTIPS = {   # AppSettings tooltip strings (NVM_RESET_SETTINGS item 11)
    'Add Filter Automatically': 'Automatically add filters to speakers when reset/mapped',
    'Check for Updates Automatically': 'Check for updates automatically on device connection',
    'Download Updates Automatically': 'Automatically download online updates when available',
    'Ignore Load Warnings': 'Ignore warnings when loading .rax files',
}
MENU_OPTIONS_UPDATES = [
    ('Check for Updates Automatically', None, True, True, True),
    ('Download Updates Automatically', None, True, True, True),   # Device-screen LED keeps "Online"
]
MENU_OPTIONS_HEADUNIT = [('Bass Control Sets Sub Gain Offset', None, True, True, False)]
MENU_OPTIONS_EFFECTS = [('Rear Fill', None, True, True, False)]
MENU_TOOLS = [
    ('Select USB Device...', None, False, True, False),        # only with mobridgedebug
    ('Debug Device...', None, False, True, False),         # only with mobridgedebug
    ('Mute All', 'Ctrl+M', True, False, True),
    ('Unmap all', 'Ctrl+U', False, False, True),
    ('Configure DRC...', None, False, True, True),             # not wired in the exe (dead item)
    ('Configure Time Alignment...', None, False, True, True),  # not wired in the exe (dead item)
    ('Firmware Update...', None, False, False, True),
]
MENU_HELP = [
    ('UI User Manual', None, False, True, True),
    ('Device User Manual', None, False, False, False),        # shown + enabled once the device sends its URL
    ('Device Install Guide', None, False, False, False),
    None,
    ('About', None, False, True, True),
]
MENU_ICONS = {'New Tune': 'icon_new_12x12.png', 'Save Tune': 'icon_save_12x12.png',
              'Open Tune...': 'icon_open_12x12.png', 'Fullscreen': 'icon_fullscreen_12x12.png',
              'About': 'icon_info_12x12.png'}   # :/images/... on actionNew/Save/Open/Fullscreen/AboutVersion
TAB_SHORTCUTS = {k: 'Ctrl+T, Ctrl+' + c for k, c in (
    ('config', 'C'), ('channel_eq', 'E'), ('time_align', 'T'), ('limiter', 'L'), ('anc', 'A'),
    ('calibration', 'B'), ('volume', 'V'), ('masternode', 'M'), ('scripting', 'S'), ('device', 'D'))}   # "tabs Ctrl+T,Ctrl+{C,E,T,L,A,B,V,M,S,D}" (retranslateUi)
HEADUNIT_TOOLTIPS = {
    'bass': 'If enabled, the in-vehicle head unit bass control sets any sub woofer assigned channels\' gain offsets.',
    'rearfill': 'If enabled, the rear-assigned channels will mix left minus right, and ignore all other input mappings.',
}
HELP_URLS = {'logo': 'https://mobridge.us', 'ui_manual': 'www.mobridge.us/mobridge-dsp/'}
OFFLINE_HELP_TOOLTIP = 'Unavailable offline'
PRESET_ITEMS = ['Preset 1', 'Preset 2', 'Preset 3', 'Preset 4']


# --------------------------------------------------------------------------
# dialogs (UI_REGION_1 §4)
# --------------------------------------------------------------------------
def dirty_prompt(connected: bool) -> dict:
    """FUN_140036320: Save | Discard | Cancel."""
    text = 'The current DSP settings have been modified.<br>Do you want to save your changes'
    text += (" locally?<br><br><span style='font-size:9pt;'>(The connected device will retain all DSP settings)</span>"
             if connected else '?')
    return {'title': APP_NAME, 'text': text, 'buttons': ('Save', 'Discard', 'Cancel'), 'icon': 'warning'}


LOAD_WARNING = ("Loading from the file: <br><br><b>'%1'</b><br><br>will overwrite all current settings on the "
                "device.<br><br>Would you like to continue?")          # Yes | No | Cancel, only when connected
LOAD_OTHER_DEVICE = ('This file has settings created for a different device than the one you are connected '
                     'to.<br><br><b>%1</b><br><br>Would you like to apply these settings anyway?')   # Yes | No
LOAD_ERRORS = {
    1: "The file '%1' is not able to be parsed. Error code: %2.",
    2: "The file '%1' is not able to be parsed. Error code: %2.",
    3: "The file '%1' cannot be opened.",
    4: "The file '%1' is not compatible with the current version of %2.",
}
LOAD_ERROR_SUFFIX = "Please check <a href='https://mobridge.us/'>mObridge.us</a> for support and the latest software."
FILE_DIALOG = {'title': 'Configuration File', 'filter': 'Configuration File(*.rax)'}
NVM_DIALOGS = {'nvm': ('Exported NVM File', 'Raw Data (*.bin)'), 'fixed': ('Exported Fixed NVM File', 'Raw Data (*.bin)'),
               'app': ('Exported NVM App File', 'Raw Data (*.bin)')}
SCRIPT_ARCHIVE_DIALOG = ('Script Module Archive', 'Script Module Archive(*.sma)')
FIRMWARE_FILE_DIALOG = ('Firmware Update File', 'Firmware Update (*.fif)')
EXIT_DURING_UPDATE = 'A firmware update is currently running.\n\nAre you sure you want to exit ?mObridge DSP?'   # Yes | Cancel
EXIT_DURING_PROCESS = 'A process is being performed on the device.'
UNMAP_ALL_PROMPT = 'Are you sure you would like to unmap all speakers on the device?'         # Yes | No
FACTORY_RESET_PROMPT = ('Are you sure you would like to perform a factory reset on the device?\n\n'
                        'This will reset all stored settings on the device.')                    # Yes | No
FACTORY_RESET_FAILED = 'Device reset failed to complete successfully.'
FACTORY_RESET_BUSY = 'Unable to reset!\n\nDevice cannot reset while being programmed.'
FACTORY_RESET_FAILED = 'Device reset failed to complete successfully.'
REBOOT_BUSY = 'Unable to reboot!\n\nDevice cannot reboot while being programmed.'
DEVICE_SELECTOR = {'none': 'There are no mObridge devices connected!',
                   'one': 'There is only one mObridge device connected!',
                   'title': 'Device Selector',
                   'label': '\nMultiple devices have been detected.\n\nPlease choose a device to connect to:\n',
                   'ok': 'Select', 'size': (350, 300)}
IMPORT_SCRIPT_FAILED = ('Failure', 'Failed to import the script archive.')
STATUS_TEXTS = {'checking': 'Checking for Firmware Update...'}
UPDATE_AVAILABLE = {2: ('Firmware v%1 is available for download.', 'DOWNLOAD UPDATE'),
                    3: ('Firmware v%1 has been downloaded and is ready to be programmed.', 'FIRMWARE UPDATE')}
DEVICE_SCREEN_TEXTS = {'update': 'FIRMWARE UPDATE', 'recovery': 'FIRMWARE RECOVERY', 'factory_reset': '  FACTORY RESET',
                       'nvm': 'EXPORT NVM', 'nvm_fixed': 'EXPORT NVM FIXED', 'nvm_app': 'EXPORT NVM APP',
                       'options_group': 'Firmware Update Options',
                       'check_auto': 'Check for Updates Automatically ',
                       'download_auto': 'Download Online Updates Automatically',
                       'tt_update': 'Start firmware update process.', 'tt_reset': 'Reset device to default settings',
                       'tt_restore': 'Restore the device using an earlier backup file.', 'tt_restart': 'Restart the device'}

# firmware validator (FUN_1400ab740) codes and the messages of FUN_14009d7c0
FW_VALIDATE = {
    0: 'ok',
    1: 'The firmware is older than or equal to the firmware on the connected device.',
    2: '<br><br>The firmware is incompatible with the current version of mObridge DSP.',
    3: "The firmware file '%1' requires that a minimum version of %2 be installed prior to update. "
       'Do you want to install this update now?',
    4: 'The firmware is incompatible with the connected device.',
    5: 'The fixed NVM on the connected device is about to be flashed.',
    6: 'The firmware is invalid or corrupted.',
}
FW_MAX_DSPNV_VERS = 0xD2


def validate_firmware(product_id: int, device_product: int, dspnv_vers: int, min_prev: tuple, device_ver: tuple,
                      fif_ver: tuple, nvm_fixed: bool, state: int, parse_ok: bool = True) -> int:
    """FUN_1400ab740 return code (0..6). Pure; the flash itself stays locked."""
    if not parse_ok:
        return 6
    if not (product_id == device_product or product_id == 0):
        return 4
    if nvm_fixed:
        return 5
    if dspnv_vers > FW_MAX_DSPNV_VERS:
        return 2
    if tuple(device_ver) < tuple(min_prev):
        return 3
    if state != STATE_RECOVERY and tuple(fif_ver) <= tuple(device_ver):
        return 1
    return 0


FW_CONFIRM = {'update': 'The firmware on the connected device is about to be updated from version <b>%1</b> to <b>%2</b>.',
              'recovery': 'The firmware on the connected device is about to be recovered to version <b>%1</b>.',
              'tail': 'A backup will automatically be made. Do not power off the device or close this application until finished!',
              'title': 'Firmware Update', 'buttons': ('Ok', 'Cancel'),
              'older': '…is older than or equal to the firmware on the connected device. Do you want to proceed?'}
BACKUP_NAME = 'backup_%1_%2_%3.%4'      # productId, device name, dd-MM-yy_hh-mm-ss, rax; spaces -> '_', lowercased
DOWNLOAD_STATUS = {2: 'Fetching Update Information...', 3: 'Starting Download', 4: 'Downloading...',
                   5: 'Verifying Download...', 6: 'Download Complete!', 7: 'Network Timeout Error!',
                   8: 'Network Reply Error!', 9: 'Host JSON Error!', 10: 'Download File Size Error!',
                   11: 'Download Verification Error!', 12: 'Error Reaching Host!'}
FLASH_STATUS = {1: 'Upload Starting', 2: 'Uploading...', 3: 'Upload Error', 4: 'Flash Starting', 5: 'Copying File...',
                6: 'Checking File...', 7: 'Deleting File...', 8: 'Programming...', 9: 'Verifying...', 10: 'Complete!',
                11: 'Error!'}
TRANSFER_STATUS = {0: 'Idle', 1: 'Transferring...', 2: 'Complete', 3: 'Transfer Error'}
UPDATE_URL = 'https://mobridge.us/api/v3/firmware_update_info/?product_id=%d'
UPDATE_TIMEOUT_S, REACHABILITY_URL, REACHABILITY_TIMEOUT_S = 15, 'http://www.google.com', 2


def firmware_version_text(major: int, minor: int, patch: int) -> str:
    """FUN_140053040: three bytes as %02d.%02d.%02d."""
    return '%02d.%02d.%02d' % (major, minor, patch)


def backup_file_name(product_id: int, device_name: str, stamp: str) -> str:
    name = 'backup_%x_%s_%s.rax' % (product_id, device_name, stamp)
    return name.replace(' ', '_').lower()


# --------------------------------------------------------------------------
# graph axis (UI_REGION_1 §5.3)
# --------------------------------------------------------------------------
GRAPH_DB_MIN, GRAPH_DB_MAX = -27.0, 9.0
GRAPH_F_MIN, GRAPH_F_MAX = 20.0, 24000.0
GRAPH_F_GRID = (50, 100, 500, 1000, 5000, 10000, 20000)
GRAPH_DB_GRID_STEP = 3.0
GRAPH_FONT = 'Droid Sans'
GRAPH_BINS = 1024


def freq_label(f: float) -> str:
    """FUN_1400587d0: '<n> Hz' below 1000, '<n> kHz' from 1000 (labels only 20..24000)."""
    if f < 20.0 or f > 24000.0:
        return ''
    if f < 1000.0:
        return '%g Hz' % f
    return '%g kHz' % (f / 1000.0)


def gain_label(db: float) -> str:
    return '%g dB' % db


def channel_caption(channel: int, speaker_name: str) -> str:
    """FUN_1400490d0 / FUN_140045ef0: "<channel+1>: <speaker name>"."""
    return '%d: %s' % (channel + 1, speaker_name)


# --------------------------------------------------------------------------
# EqualizerScene geometry (UI_MAINWINDOW.md §D): value <-> scene axis, doubles,
# no snapping. W/H = plot size = viewport - (45, 40); margins L40 R5 T20 B20.
# --------------------------------------------------------------------------
import math as _math

SCENE_DEFAULT_W, SCENE_DEFAULT_H = 1024, 256
SCENE_MARGIN_L, SCENE_MARGIN_R, SCENE_MARGIN_T, SCENE_MARGIN_B = 40, 5, 20, 20
SCENE_POINT_DIAMETER, SCENE_FILTER_HANDLE_DIAMETER = 30, 19
SCENE_NEW_POINT_Q = 3.0
SCENE_FILTER_DEFAULT_F = (150.0, 2000.0)
SCENE_CURVE_PEN = ('#ff672f', 3)        # EQ response path
SCENE_BINS_PEN = ((253, 253, 125), 3)   # device filter-bins plot
_LOG_F_MIN = _math.log10(GRAPH_F_MIN)
_LOG_SPAN = _math.log10(GRAPH_F_MAX) - _LOG_F_MIN      # log10(1200); UNRESOLVED initialiser, implied by round trip


def scene_size(viewport_w: int, viewport_h: int) -> tuple[int, int]:
    """FUN_140057270: W = width - 45, H = height - 40."""
    return int(viewport_w) - 45, int(viewport_h) - 40


def scene_x(f: float, W: int = SCENE_DEFAULT_W) -> float:
    """FUN_140058dd0: freq -> x; outside 20..24000 -> 0."""
    if GRAPH_F_MIN <= f <= GRAPH_F_MAX:
        return (_math.log10(f) - _LOG_F_MIN) / _LOG_SPAN * W
    return 0.0


def scene_freq(x: float, W: int = SCENE_DEFAULT_W) -> float:
    """FUN_14005a740: x -> freq, 0 outside the plot; capped at 24000."""
    if 0.0 <= x <= W:
        return min(GRAPH_F_MAX, 10.0 ** ((x / W) * _LOG_SPAN + _LOG_F_MIN))
    return 0.0


def scene_y(db: float, H: int = SCENE_DEFAULT_H, gain_offset: float = 0.0) -> float:
    """FUN_140059680 (mode 1): axis dB = band dB + channel gain; clamp to -27..+9."""
    db = db + gain_offset
    if db > GRAPH_DB_MAX:
        return -(H // 2)
    if db < GRAPH_DB_MIN:
        return float(H // 2)
    return (H // 2) - H * (db - GRAPH_DB_MIN) / (GRAPH_DB_MAX - GRAPH_DB_MIN)


def scene_db(y: float, H: int = SCENE_DEFAULT_H, gain_offset: float = 0.0) -> float:
    """FUN_14005a7a0: y -> band dB (gain offset removed)."""
    half = H // 2
    if y > half:
        a = GRAPH_DB_MIN
    elif y < -half:
        a = GRAPH_DB_MAX
    else:
        a = (1.0 - (y + half) / H) * (GRAPH_DB_MAX - GRAPH_DB_MIN) + GRAPH_DB_MIN
    return a - gain_offset


def clamp_drag(x: float, y: float, W: int = SCENE_DEFAULT_W, H: int = SCENE_DEFAULT_H) -> tuple[float, float]:
    """EqualizerScenePoint::itemChange clamp: x in [0, W-1], y in [-H/2, H/2]."""
    half = H / 2.0
    if 0.0 <= x <= W and -half <= y <= half:
        return x, y
    x = min(max(0.0, x), W - 1.0)
    y = min(max(-half, y), half)
    return x, y
