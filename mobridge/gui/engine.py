"""Bridge between the GUI and the protocol engine.

All device I/O runs on a worker QThread so the UI never blocks (mirrors the app's
job queue). Screens call engine methods and connect to its signals; they never
touch a transport or framer directly. With no device connected, the engine stays
in DISCONNECTED and every screen renders from static/spec data.
"""
from __future__ import annotations

from ..modes import DeviceMode
from .. import model as M
from .qt import QtCore, Signal


_THREADS: list = []          # connect threads, joined at exit so none is destroyed running


def _join_threads() -> None:
    for t in _THREADS:
        try:
            t.quit(); t.wait(3000)
        except RuntimeError:
            pass


import atexit as _atexit
import threading
import os
_atexit.register(_join_threads)


class Engine(QtCore.QObject):
    mode_changed = Signal(object)        # DeviceMode
    model_changed = Signal(object)       # the DspModel after a device read or an edit
    log = Signal(str)                    # a device log / status line
    device_log = Signal(str)             # one line of the device's own debug log (0x0401 text)
    device_info = Signal(dict)           # {name, version, product, serial}
    job_done = Signal(str, object)       # (job name, MdiMessage | None)
    error = Signal(str)
    control_applied = Signal(object)     # live.ApplyResult
    dirty_changed = Signal(bool)         # unsaved (volatile) changes present
    save_result = Signal(object)         # live.ApplyResult of a commit attempt
    device_values_changed = Signal(dict) # live values read from the device on connect
    meter_levels_changed = Signal(object)  # {channel: metering.LevelReading} (object: preserves int keys/objects)
    rta_level = Signal(int, float)         # (output channel index, dBFS) from DspTest type 7
    flash_status = Signal(str, int, int)     # ('phase', FLASH_STATUS key) / ('position'|'upload', done, total)
    bus_event = Signal(object)             # busevents.BusEvent (0x0646 A2B / 0x0615 CAN)
    state_changed = Signal(int)            # connection state 1..16 (uirules.CONNECT_STATES)
    _session_idle = Signal()               # session thread -> UI thread
    _session_preset = Signal(int)
    _session_lost = Signal(str)
    _net_opened = Signal(object, object, str)  # (port, transport | None, error) from the open thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = DeviceMode.DISCONNECTED
        self.client = None               # set on connect; None = offline
        self.port = None
        self._meter = None               # metering.MeterState while live metering is on (None = off)
        # The ONE output channel (1-based) the meter follows: the config tab's selection.
        self._meter_channel = None
        self._console_buf = ''           # 0x0401 text not yet ended by a newline
        from .. import metering as _metering
        self._meter_timer = QtCore.QTimer(self)
        self._meter_timer.setInterval(_metering.POLL_MS)
        self._meter_timer.timeout.connect(self._meter_poll)
        self.rta_level.connect(self._on_meter_level)
        self.require_parity = True       # frames go through parity.frame_cleared (flash family stays shut)
        self.capabilities = 0            # session capability bits of the connected product
        # The tune being edited (fddsp::model::DspModel). Offline it starts as
        # the reference device as the official app showed it (so the preview is
        # faithful); on connect the device's own tune replaces it.
        from .official import data as _data
        self.root: M.DspModel = _data.demo_model()
        self._pending: dict[str, float] = {}   # edits live on the device but not yet backed up to a .rax
        # Values read FROM the device on connect. When connected these are the
        # single source of truth the UI shows -- never a cached/offline value.
        self.device_values: dict = {}
        # Edits made while DISCONNECTED. A local working copy ONLY: it is never
        # queued and never sent to the device. On the next connect the device's
        # values win and this is discarded (project rule). See sync_from_device.
        self._offline_draft: dict[str, float] = {}
        self.session = None              # session.Session: the live service loop
        self.conn_state = 0              # 0 = never connected ("Offline Mode")
        self._push_waiting = False       # an edit arrived while a write job was running
        self._user_disconnected = False
        self._reconnecting = False
        self._session_idle.connect(self._on_session_idle)
        self._session_preset.connect(self._on_device_preset)
        self._session_lost.connect(self._on_session_lost)
        self._net_opened.connect(self._on_net_opened)
        self._net_opening = False
        self.features: frozenset[str] = frozenset()   # custom firmware features found (features.py)
        self.compensation = None         # features.Compensation for the current tune (None = not computed)

    @property
    def rear_chimes(self) -> bool:
        return 'rear_chimes' in self.features

    @rear_chimes.setter
    def rear_chimes(self, on: bool) -> None:
        self.features = (self.features | {'rear_chimes'}) if on else (self.features - {'rear_chimes'})

    @property
    def meters12(self) -> bool:
        """Firmware meters outputs 9-12 (DspTest type 8, LB-RC<n>)."""
        return 'meters12' in self.features

    @meters12.setter
    def meters12(self, on: bool) -> None:
        self.features = (self.features | {'meters12'}) if on else (self.features - {'meters12'})

    @property
    def connecting(self) -> bool:
        """A connect is in flight (network open or connect worker running)."""
        return self._net_opening or getattr(self, '_conn_transport', None) is not None

    @property
    def reconnecting(self) -> bool:
        """The link was lost and the app is retrying on its own."""
        return self._reconnecting

    @property
    def mode(self) -> DeviceMode:
        return self._mode

    @property
    def vehicle_name(self) -> str | None:
        """*(FUN_1400971a0(dev)+0x10): the connected product's vehicle string from the
        runtime product map (model+0x1d8). Only the value seen on the official app is
        carried (products.VEHICLE); any other product shows no row, as with a null object."""
        from .. import products
        r = getattr(self, 'root', None)
        return products.VEHICLE.get(getattr(r, 'product', None)) if self.connected else None

    @property
    def connected(self) -> bool:
        return self.client is not None

    @property
    def dirty(self) -> bool:
        """True when live edits have been made that are not yet backed up to a
        .rax file on the PC. (Live edits already PERSIST on the device; "Save" is a
        file backup, not a device commit.) Drives the file-backup prompt."""
        return bool(self._pending)

    @property
    def pending_changes(self) -> dict:
        return dict(self._pending)

    def set_state(self, state: int) -> None:
        self.conn_state = state
        self.state_changed.emit(state)

    def set_mode(self, mode: DeviceMode) -> None:
        self._mode = mode
        self.mode_changed.emit(mode)

    # Live read-only connect. Opens the transport, runs the proven connect
    # sequence (mobridge.connect.run_connect) on a worker thread, then applies
    # what the DEVICE reported.
    #
    # SAFETY CONTRACT (project rule, enforced in connect.run_connect + tested):
    #   * connect transmits ONLY parity-cleared read/handshake frames (a write
    #     frame raises before a byte leaves).
    #   * it NEVER sends self._offline_draft or self._pending to the device.
    #   * the device's values WIN: sync_from_device() replaces the UI values and
    #     discards the offline draft. Offline edits only ever leave this app
    #     through an explicit .rax file the user chooses to load.
    # A network port (rfc2217://) can take seconds to fail when the Pi is off, so
    # it is opened on a helper thread; the UI never waits on it. While the Pi is
    # unreachable nothing is sent, and retries are spaced out (NET_RETRY_MS).
    NET_RETRY_MS = 3000

    def connect_device(self, port: str, baud: int | None = None) -> None:
        from ..transport import open_transport, is_network_port
        from .. import connect as _connect
        if self.connected or self.connecting:
            if port == self.port:
                return                             # already on it (e.g. Connect clicked twice)
            if self.connecting:
                return                             # one connect at a time; the running one decides
            self._drop_session()                   # switching device: close the old link first
            self.set_mode(DeviceMode.DISCONNECTED)
        self.port = port
        self._user_disconnected = False
        if is_network_port(port) or self._reconnecting:
            # network opens can take seconds; USB reconnect retries every 100 ms:
            # both open on a helper thread so the UI never waits
            if self._net_opening:
                return
            self._net_opening = True
            self.set_state(2)                      # "Connecting"

            def _open():
                try:
                    t = open_transport(port, baud=_connect.POWERON_BAUD)
                    self._net_opened.emit(port, t, '')
                except Exception as e:             # unreachable, refused, timed out
                    self._net_opened.emit(port, None, str(e))
            threading.Thread(target=_open, name='net-open', daemon=True).start()
            return
        try:
            transport = open_transport(port, baud=_connect.POWERON_BAUD)
        except Exception as e:
            if self._reconnecting:
                self._schedule_reconnect()
            else:
                self.error.emit('Could not open %s: %s' % (port, e))
            return
        self._start_connect(transport)

    def _on_net_opened(self, port, transport, err: str) -> None:
        self._net_opening = False
        if transport is not None and (self._user_disconnected or port != self.port):
            try:                                   # user moved on while it was opening
                transport.close()
            except Exception:
                pass
            return
        if transport is None:
            self.set_state(14)                     # "Disconnected"
            if self._reconnecting:
                self._schedule_reconnect()
            elif not self._user_disconnected:
                self.error.emit('Could not open %s: %s' % (port, err))
            return
        self._start_connect(transport)

    def _start_connect(self, transport) -> None:
        """Spin up the connect worker on its own QThread. The protocol + the
        device-wins/no-write contract live in connect.run_connect; this is just
        the thread plumbing."""
        from .device_worker import ConnectWorker
        self.set_state(2)                          # "Connecting"
        self._conn_transport = transport
        self._conn_thread = QtCore.QThread()
        _THREADS.append(self._conn_thread)
        self._conn_partial = bool(self.post_flash)     # this connect reads the version only
        self._conn_worker = ConnectWorker(transport, read_mask=0x8000000 if self.post_flash else None,
                                          recovery=self._recovery_next)
        self._conn_worker.moveToThread(self._conn_thread)
        self._conn_thread.started.connect(self._conn_worker.run)
        self._conn_worker.log.connect(self.log)
        self._conn_worker.finished.connect(self._on_connected)
        self._conn_worker.failed.connect(self._on_connect_failed)
        self._conn_thread.start()

    def _on_connected(self, result) -> None:
        self._conn_thread.quit()
        transport, self._conn_transport = self._conn_transport, None
        if self._user_disconnected:                # Disconnect pressed while connecting
            try:
                transport.close()
            except Exception:
                pass
            self.set_state(14)
            return
        self._apply_connect_result(result, transport)

    def _on_connect_failed(self, msg: str) -> None:
        self._conn_thread.quit()
        try:
            self._conn_transport.close()
        except Exception:
            pass
        self._conn_transport = None
        self.set_state(14)                         # "Disconnected"
        if self._reconnecting:
            self._schedule_reconnect()
            return
        self.error.emit('Connect failed (nothing was written to the device): %s' % msg)

    def _apply_connect_result(self, result, transport) -> None:
        """Establish the live session from a ConnectResult. Headless-testable (no
        Qt thread needed): builds the client, publishes device info, and lets the
        device's values win via sync_from_device. Transmits nothing itself."""
        from ..client import MobridgeClient
        from ..framing import MdiFramer
        from ..session import Session
        partial, self._conn_partial = self._conn_partial, False
        if self._flash_pending(result):
            # after a flash the unit is still booting: no product/version yet. Not back
            # yet, not a mismatch: close this link and keep retrying (state unchanged).
            self.log.emit('Firmware check: the unit has not reported its version yet')
            try:
                transport.close()
            except Exception:
                pass
            self._reconnecting = True
            self._schedule_reconnect()
            return
        self._reconnecting = False
        self._recovery_next = False            # P+0xdfcb9 holds for one connection only
        self.product_id = result.product_id
        mode = getattr(result, 'mode', None) or DeviceMode.MDI
        recovery = bool(getattr(result, 'recovery', False))
        # the client only holds the port (closed on disconnect); it sends nothing itself
        self.client = MobridgeClient(transport, MdiFramer(), on_frame=self._on_device_frame)
        if mode is DeviceMode.MDI:
            self.session = Session(transport, MdiFramer(), on_frame=self._on_session_frame,
                                   on_preset=self._session_preset.emit,
                                   on_idle=self._session_idle.emit,
                                   on_lost=lambda e: self._session_lost.emit(str(e)),
                                   log=self.log.emit)
            self.session.start()
        else:
            # BOOTS / LW / WIZARD link: no MDI session, no keep-alive, no meter polls
            self.session = None
        if result.product_id is not None:
            self.device_name = 'product 0x%04x' % result.product_id
        self.capabilities = getattr(result, 'capabilities', 0)
        self.app_version = result.app_version
        flash_ok = self._check_flashed(result)     # None: no flash to check on this connect
        if flash_ok is False and getattr(result, 'model', None) is None:
            self.post_flash = False            # verdict on a link that read no tune
            self.write_back_model = None
        if (self.post_flash or partial) and getattr(result, 'model', None) is not None:
            # (partial: a version-only read never replaces the app's tune, even when
            # the post-flash flag changed while it ran)
            # after a firmware update only the version was read (+0x1bf97): keep the
            # model; FUN_140099030 writes it back whole when P+0xdfcba is set
            from .. import flashflow
            self.post_flash = False
            wb, self.write_back_model = self.write_back_model, None
            kept = wb if wb is not None else self.root
            if flash_ok is not False:
                kept.fw = result.model.fw
            result.model = kept
            if wb is not None and flash_ok is not False:   # old firmware still runs: no write-back
                flashflow.mark_full_write(kept)
                QtCore.QTimer.singleShot(0, lambda: self.push('write-back'))
        if getattr(result, 'model', None) is not None:
            # The device's tune replaces whatever was being edited (device wins).
            self.root = result.model
            self.root.connected = True
            self.model_changed.emit(self.root)
        elif recovery or mode is not DeviceMode.MDI:
            # Recovery / bootloader link: no tune was read. The old one is not live
            # any more: drop its pending bits so nothing stale is written later.
            self.root.connected = False
            self.root.clear_dirty()
            self.model_changed.emit(self.root)
        self.device_info.emit({'product': result.product_id, 'version': result.app_version,
                               'info': dict(result.info), 'local_id': getattr(result, 'local_id', None),
                               'serial': getattr(result, 'serial', None), 'port': self.port})
        self._console_buf = ''
        for chunk in getattr(result, 'log_lines', None) or []:   # log text read during connect
            self._console_text(chunk)
        # Device values win; discards any offline draft; arms the meter parser
        # (no meter on a recovery or bootloader link: it would poll a DspTest).
        self.sync_from_device(result.as_values(), meter=mode is DeviceMode.MDI and not recovery)
        self.set_mode(mode)
        self._detect_features(result)
        from .. import products
        if mode in (DeviceMode.BOOTS, DeviceMode.LW):
            self.set_state(5)          # bootloader link: the exe's Recovery state (detection < 0 -> BOOTS)
        elif recovery:
            self.set_state(5)          # startRecovery reconnect (P+0xdfcb9): "Recovery", FIRMWARE RECOVERY
        elif mode is DeviceMode.WIZARD:
            self.set_state(6)          # WIZARDM: the update transition (Init/Update/Idle/FlashFile/SwitchMDI)
        else:
            self.set_state(3 if result.product_id in products.BY_ID else 16)   # "Connected" / "Incompatible Device"
        self.log.emit('Connected: product 0x%04x, app %s, %d channel value(s) read'
                      % (result.product_id or 0, result.app_version, len(result.channel_gain)))
        if flash_ok is not None:
            shown, self._flash_reported = self._flash_reported, False
            if flash_ok or not shown:              # "Error!" already shown: not twice
                self._flash_result(flash_ok)

    def _on_device_frame(self, frame) -> None:
        """Route a device-initiated frame (debug log text, A2B / CAN bus events)."""
        msg = getattr(frame, 'message', None)
        if getattr(frame, 'kind', None) == 'mdi' and msg is not None and msg.cmd == 0x0401:
            self._console_text(bytes(msg.data).decode('latin-1').replace('\0', ''))
            return
        if getattr(frame, 'kind', None) == 'mdi' and msg is not None and msg.cmd in (0x0646, 0x0615):
            from .. import busevents
            ev = busevents.decode(msg.cmd, msg.data)
            if ev is not None:
                self.bus_event.emit(ev)
            return

    def _console_text(self, text: str) -> None:
        """The device's debug log arrives as 0x0401 text in chunks of up to 128
        bytes that do not follow line ends (the official app shows it in its debug
        console window). Emit one device_log per complete line."""
        buf = self._console_buf + text.replace('\r', '\n')
        *lines, self._console_buf = buf.split('\n')
        if len(self._console_buf) > 4096:                # a line that never ends
            lines.append(self._console_buf)
            self._console_buf = ''
        for ln in lines:
            if ln.strip():
                self.device_log.emit(ln)
                if 'Build Time' in ln:          # the unit rebooted while connected: re-check
                    self._features_from_banner(ln)

    def _on_session_frame(self, msg) -> None:
        class _F:
            kind, message = 'mdi', msg
        self._on_device_frame(_F)

    def _on_session_idle(self) -> None:
        if self._push_waiting:
            self._push_waiting = False
            self.push('edit')

    def _on_device_preset(self, preset: int) -> None:
        """The device changed preset itself (0x0716 [03][p]): adopt it, no write."""
        if 0 <= preset < self.root.preset_count and preset != self.root.current_preset:
            self.root.current_preset = preset
            self.model_changed.emit(self.root)

    def _on_session_lost(self, why: str) -> None:
        self.log.emit('Device connection lost: %s' % why)
        self._drop_session()
        self.set_mode(DeviceMode.DISCONNECTED)
        self.set_state(14)
        if not self._user_disconnected and self.port:
            self._reconnecting = True
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._user_disconnected or not self.port:
            self._reconnecting = False
            return
        from ..transport import is_network_port
        delay = self.NET_RETRY_MS if is_network_port(self.port) else 100
        QtCore.QTimer.singleShot(delay, lambda: self.connect_device(self.port)
                                 if self._reconnecting and not self._user_disconnected else None)

    def _drop_session(self) -> None:
        if self._meter is not None:
            self.enable_metering(False)
        if self.session is not None:
            self.session.stop()
            self.session = None
        if self.client is not None:
            try:
                self.client.t.close()
            except Exception:
                pass
        self.client = None

    def sync_from_device(self, values: dict, meter: bool = True) -> None:
        """Apply the live values read FROM the device on connect. The device is the
        single source of truth: these populate the UI, and any edits made while
        offline are DISCARDED here (the device always wins). This transmits
        nothing -- it only consumes what the read-only connect already read."""
        self.device_values = dict(values)
        discarded = len(self._offline_draft)
        self._offline_draft.clear()
        self.device_values_changed.emit(self.device_values)
        # Live meter is always on while connected (project call), selected channel only.
        if meter:
            self.enable_metering(True)
        if discarded:
            self.log.emit('Loaded %d live value(s) from the device; %d offline edit(s) discarded '
                          '(device wins, nothing was sent)' % (len(values), discarded))
        else:
            self.log.emit('Loaded %d live value(s) from the device' % len(values))

    @property
    def offline_draft(self) -> dict:
        return dict(self._offline_draft)

    # --- live output-level metering (reads only; easy to disable) ----------
    @property
    def metering(self) -> bool:
        return self._meter is not None

    def enable_metering(self, on: bool, channel=None) -> None:
        """Turn the selected-channel level meter on/off. While on it polls the
        DspTest type-7 level read (the official Limiter RTA's read; no setting
        changes) for the selected output, if that output is 1-8."""
        from .. import metering
        if not on:
            self._meter = None
            self._meter_timer.stop()
            self.meter_levels_changed.emit({})
            return
        if channel is not None:
            self._meter_channel = channel
        if self._meter_channel is None:
            self._meter_channel = 1
        self._meter = metering.MeterState()
        self._meter_timer.start()

    def set_meter_channel(self, channel: int) -> None:
        """Follow a new selection; the bar resets so no stale level lingers."""
        self._meter_channel = channel
        if self._meter is not None:
            self._meter.clear()
            self.meter_levels_changed.emit({})

    def _meter_poll(self) -> None:
        from .. import metering
        ch = self._meter_channel
        if self._meter is not None and ch is not None and 1 <= ch <= metering.max_channel(self.meters12):
            self.rta_request(ch - 1)

    def _on_meter_level(self, ch: int, db: float) -> None:
        if self._meter is None or ch + 1 != self._meter_channel:
            return
        r = self._meter.update(ch + 1, db)
        self.meter_levels_changed.emit({ch + 1: r})

    def apply_dspnv_version(self, version: int) -> None:
        """Set the gain/EQ-magnitude unity reference from the device's DSPNV settings
        version, read at connect. version > 207 -> 0xED00 zero point, else 0xE700
        (confirmed in the native decoder). Affects DISPLAY only; sending stays gated.
        Call this once the connect read (or a loaded NVM dump) yields the version;
        the reference unit reads 210."""
        from .. import encoders
        encoders.set_gain_unity_for_fw(version)
        self.dspnv_version = version
        unity = '0xED00' if version > encoders.GAIN_UNITY_VERSION_THRESHOLD else '0xE700'
        self.log.emit('DSPNV settings version %d -> gain unity %s' % (version, unity))

    def load_nvm_version(self, data: bytes, base: int = 0) -> dict | None:
        """Read the DSPNV version from a settings-partition dump, verify its CRC, and
        (only if the CRC is valid) apply it to the gain-unity gate. Returns the parse
        result ({'version','crc_ok',...}) or None if the buffer is too short. A bad
        CRC is logged and NOT applied -- the version is untrustworthy then."""
        from .. import stored_config
        info = stored_config.dspnv_version(data, base=base)
        if info is None:
            self.log.emit('NVM dump too short to read the DSPNV version')
            return None
        if info['crc_ok']:
            self.apply_dspnv_version(info['version'])
        else:
            self.log.emit('DSPNV version %d IGNORED: settings CRC mismatch '
                          '(stored 0x%08x != computed 0x%08x)'
                          % (info['version'], info['stored_crc'], info['computed_crc']))
        return info

    def disconnect_device(self) -> None:
        self._user_disconnected = True
        self._reconnecting = False
        self._drop_session()
        self.set_mode(DeviceMode.DISCONNECTED)
        self.set_state(14)

    # --- Live update ------------------------------------------------------
    # Mirror the official app: a control edits the model (mobridge.model) and the
    # dirty bits are flushed to the device at once, in the exe's write order
    # (dspwrite.flush = FUN_140099a80 -> DspWrite). Connected: every frame goes
    # out through the client (the device persists it: write-through NVM).
    # Offline: the model keeps the edit (it is the tune being edited, saved to a
    # .rax by File > Save); nothing is queued for a later connect - on connect the
    # device's tune replaces the model (device wins).
    def push(self, what: str = ''):
        """Flush the model's pending bits. Returns the dspwrite.Flush (frames sent,
        or that would have been sent offline)."""
        from .. import dspwrite
        if not self.connected or not self.writes_live:
            # offline, Recovery (state 5) or a bootloader link: nothing is sent
            f = dspwrite.flush(self.root, clear=False)
            self.model_changed.emit(self.root)
            return f
        if self.session is not None and not self.session.idle:
            # one DspWrite job at a time: the bits stay dirty and go out in the
            # next job, when the running one (and its 250 ms sleep) is over
            self._push_waiting = True
            self.model_changed.emit(self.root)
            return dspwrite.flush(self.root, clear=False)
        f = dspwrite.flush(self.root, hold_routing=not self.root.routing_read)
        if self.session is not None:
            if f.frames:
                from ..session import Job
                self.session.submit(Job('DspWrite', list(f.frames), write=True))
        else:
            for frame in f.wire():
                self.client.send_frame(frame)
        self.root.modified = True            # live on the device, not yet backed up to a .rax
        if f.frames:
            self._pending[what or 'edit'] = len(f.frames)
            self.dirty_changed.emit(True)
            self.log.emit('%s: %d frame(s) sent (%s)' % (what or 'edit', len(f.frames),
                          ' '.join('%04x' % c for c in f.cmds())))
        for s in f.skipped:
            self.log.emit('skipped: %s' % s)
        if self.features:
            self.update_compensation()
        self.model_changed.emit(self.root)
        return f

    # --- custom firmware features (features.py) ---------------------------
    def _detect_features(self, result) -> None:
        """Which custom firmware features the unit has (decides extra UI rows).
        Sends nothing and changes no model value."""
        from .. import features, rearchimes
        tag = rearchimes.build_tag(getattr(result, 'log_lines', None))
        self._set_features(features.active(tag, self.capabilities), tag or '(not seen)')

    _rear_chimes_on_connect = _detect_features         # old name, kept for callers/tests

    def _features_from_banner(self, line: str) -> None:
        from .. import features, rearchimes
        tag = rearchimes.build_tag([line])
        if tag and self.connected:
            self._set_features(features.active(tag, self.capabilities), tag)

    def _set_features(self, found: frozenset, tag: str) -> None:
        from .. import features
        changed = found != self.features
        self.features = found
        names = [f.name for f in features.FEATURES if f.key in found]
        self.log.emit('firmware build tag %s, custom features: %s' % (tag, ', '.join(names) or 'none'))
        self.update_compensation()
        if changed:
            self.model_changed.emit(self.root)

    def update_compensation(self):
        """Recompute the delay/phase compensation for the current tune and features
        (the project's 2026-10-28 rule). Called on detection and after every edit. No
        feature adds latency yet, so the plan is all zeros and nothing is applied;
        applying non-zero extra delay arrives with the first feature that needs it."""
        from .. import features
        root = self.root
        from .. import products
        outputs = range(products.channel_count(getattr(root, 'product', None)))   # 0 = unknown product
        try:
            comp = features.plan(self.features, root, outputs)
        except Exception as e:                       # a feature's latency report is broken
            comp = features.Compensation({}, False, str(e))
        if not comp.ok and (self.compensation is None or self.compensation.ok):
            self.log.emit('Delay compensation not possible: %s' % comp.reason)
        self.compensation = comp
        return comp

    def _device_job(self, name: str, cmd: int, state: int) -> bool:
        """Queue a device job (only when idle, like FUN_1400ac9c0 / FUN_1400b0a90)."""
        from .. import parity
        from ..session import Job
        if self.session is None or cmd in parity.LOCKED_COMMANDS or self.conn_state in (5, 6, 7, 8):
            return False
        self.set_state(state)
        self._submit_device_job(name, cmd, 0)
        return True

    RESET_RETRIES = 10

    def _submit_device_job(self, name: str, cmd: int, tries: int) -> None:
        from ..session import Job

        def done(job):
            rep = job.replies[0] if job.replies else None
            QtCore.QMetaObject.invokeMethod(self, '_device_job_done', QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, name), QtCore.Q_ARG(int, cmd),
                                            QtCore.Q_ARG(int, tries), QtCore.Q_ARG(object, rep))
        if self.session is not None:
            self.session.submit(Job(name, [(cmd, b'')], done=done))

    @staticmethod
    def device_job_ok(cmd: int, rep) -> bool | None:
        """Reply verdict as the exe judges it: True ok, False failed, None retry.
        Reset: advance on the 0x0010 echo, retry on 0x0fff. FactoryReset: ok on
        0x0011{u8==1} or 0x0fff{u16==2}, anything else failed."""
        if rep is None:
            return None if cmd == 0x0010 else False
        if cmd == 0x0010:
            return True if rep.cmd == 0x0010 else None
        d = rep.data or b''
        if rep.cmd == 0x0011 and d[:1] == b'\x01':
            return True
        if rep.cmd == 0x0fff and len(d) >= 2 and int.from_bytes(d[:2], 'big') == 2:
            return True
        return False

    @QtCore.Slot(str, int, int, object)
    def _device_job_done(self, name, cmd, tries, rep):
        ok = self.device_job_ok(cmd, rep)
        self.job_done.emit(name, rep)
        if ok is None and tries < self.RESET_RETRIES:
            self._submit_device_job(name, cmd, tries + 1)
            return
        if not ok:
            if cmd == 0x0010 and self._flash_expect is not None:
                # Reset never answered: show "Error!", but keep post_flash, the
                # write-back and the expected version, so a later return of the unit
                # with the new firmware still gets its settings written back (as main).
                self.set_state(5 if getattr(self, '_state_before_flash', 3) == 5 else 3)
                if not self._flash_reported:
                    self._flash_reported = True
                    self._flash_result(False)
                return
            if cmd == 0x0011:
                from .. import uirules
                self.error.emit(uirules.FACTORY_RESET_FAILED)
            self.set_state(3)
            return
        # success: the unit reboots; reconnect after 3 s like the exe
        self.set_state(7)
        self._reconnect_after_reboot()

    def _reconnect_after_reboot(self) -> None:
        self._drop_session()
        self._reconnecting = True
        QtCore.QTimer.singleShot(3000, lambda: self.connect_device(self.port)
                                 if self._reconnecting and not self._user_disconnected and self.port else None)

    # DspTest 0x0719 type 7 = DBFS_RMS_OUT, a level read (firmware 0x518D48): the
    # id byte is an output BITMASK (bit n = output n from 0, CH1-CH8 only) and the
    # reply is one int8 dB per set bit.
    def rta_request(self, ch: int) -> bool:
        """One RTA poll for output channel index `ch` (FUN_1400ac860(dm, 7, ch))."""
        from .. import cmdspec, metering
        from ..session import Job
        if self.session is None or not 0 <= ch < metering.max_channel(self.meters12) or self.session._q:
            return False
        typ, ident = metering.request(ch)

        def done(job, ch=ch, ident=ident, typ=typ):
            rep = job.replies[0] if job.replies else None
            if rep is None or rep.cmd != 0x0719:
                return
            try:
                v = cmdspec.parse_dsp_test(rep.data, typ, ident)
            except ValueError:
                return
            db = metering.rta_dbfs(v['db'][0]) if v['db'] else None
            if db is not None:
                self.rta_level.emit(ch, db)
        self.session.submit(Job('DspTest', [(0x0719, cmdspec.dsp_test(typ, ident))], done=done))
        return True

    def factory_reset(self) -> bool:
        """Job 0xc -> FactoryReset 0x0011 (NV re-init + reboot); state 8."""
        return self._device_job('FactoryReset', 0x0011, 8)

    def restart(self) -> bool:
        """Job 0xb -> Reset 0x0010 (reboot); state 7."""
        return self._device_job('Reset', 0x0010, 7)

    # --- firmware update (FlashFile job) -----------------------------------
    write_back_model = None           # P+0xdfcba: the model to write back after the post-flash read

    _recovery_next = False            # P+0xdfcb9: the next connect is a recovery connect
    product_id = None                 # product the unit identified as (0x0001), also in recovery

    def start_recovery(self) -> bool:
        """fddspconnect:startRecovery (FUN_1401d92e0 -> connect vtable+0x68): reconnect
        with the recovery flag, so the link identifies the unit and stops in state 5
        ("Recovery"). Sends nothing beyond the read-only connect frames."""
        if not self.port:
            return False
        if self.connecting or self.conn_state in (6, 7, 10):
            return False                       # never mid-flash: no session drop
        self._recovery_next = True
        if self.connected:
            self._drop_session()
            self.set_mode(DeviceMode.DISCONNECTED)
        self._user_disconnected = False
        self.connect_device(self.port)
        return True

    @property
    def writes_live(self) -> bool:
        """Whether push() may send DspWrite frames: only on an MDI application link,
        never in Recovery (state 5) or on a BOOTS/LW/WIZARD link."""
        return self._mode not in (DeviceMode.BOOTS, DeviceMode.LW, DeviceMode.WIZARD) \
            and self.conn_state != 5

    def flash_file(self, plan) -> bool:
        """Queue command 0 -> FlashFile. Sets the write-back flag when the file's
        DSPNV_VERS differs from the device's settings version. On a BOOTS console
        link the BOOTS flash job runs instead (boots.BootsFlasher), never MDI 0x0201;
        the LW flash job is not decoded and WIZARD jobs are not in the dump, so those
        links do not flash."""
        from .. import parity, flashflow
        if self._mode is DeviceMode.BOOTS and self.client is not None \
                and 0x0201 not in parity.LOCKED_COMMANDS:
            return self._start_boots_flash(plan)
        if self._mode in (DeviceMode.BOOTS, DeviceMode.LW, DeviceMode.WIZARD):
            self.log.emit('Firmware update not started.')
            return False
        if 0x0201 in parity.LOCKED_COMMANDS or self.session is None:
            self.log.emit('Firmware update not started.')
            return False
        if self.conn_state != 5 and flashflow.write_back_needed(plan, getattr(self.root, 'fw', 0) or 0):
            self.write_back_model = self.root      # in Recovery no model was read: nothing to keep
        return self._start_flash(plan)

    post_flash = False                # +0x1bf97: next connect reads the version only

    def _start_flash(self, plan) -> bool:
        """State 6 "Updating Firmware" (10 for an NVM_FIXED file), then the FlashFile
        job; on success Reset 0x0010 and the version-only reconnect."""
        from .. import flashflow, uirules
        from ..session import Job
        try:
            with open(plan.path, 'rb') as f:
                data = f.read()
        except OSError as e:
            self.error.emit(str(e))
            return False
        fixed = bool((plan.info.preamble_json or {}).get('NVM_FIXED', False))
        self._state_before_flash = self.conn_state
        self._flash_expect = self._expected(plan)
        self._flash_reported = False
        self.set_state(10 if fixed else 6)

        def status(kind, a=0, b=0):
            if (kind, a) == ('phase', 10):
                return     # sub 8 = finished, not succeeded: "Complete!" waits for the version check
            self.flash_status.emit(kind, int(a), int(b))

        def body(session, job):
            job.replies.append(flashflow.run_flash(session, data, os.path.basename(plan.path), status))

        def done(job):
            QtCore.QMetaObject.invokeMethod(self, '_flash_done', QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(bool, bool(job.replies and job.replies[0])))
        self.session.submit(Job('FlashFile', [], run=body, done=done))
        return True

    def _start_boots_flash(self, plan) -> bool:
        """The BOOTS flash job (boots.BootsFlasher: identify, recovery-state checks,
        flash -uart, raw .fif) on its own thread, for a unit on the BOOTS console."""
        from .. import boots
        from ..commands import Access
        from ..safety import Policy
        transport = self.client.t
        self._state_before_flash = self.conn_state
        self._flash_expect = self._expected(plan)
        self._flash_reported = False
        self.set_state(6)

        def run():
            ok = False
            try:
                st = boots.BootsFlasher(transport, Policy.allow(Access.WRITE), log=self.log.emit) \
                    .flash(plan.path, execute=True, expect_product=self.product_id)
                ok = st.state == 'complete'
            except Exception as e:                  # refused or timed out: report, never crash
                self.log.emit('%s: %s' % (type(e).__name__, e))
            QtCore.QMetaObject.invokeMethod(self, '_flash_done', QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(bool, ok))
        self._boots_thread = threading.Thread(target=run, name='boots-flash', daemon=True)
        self._boots_thread.start()
        return True

    # --- post-flash version check (our addition: the RE notes show the exe only
    # re-reads the version, FLASH_GROUP_C.md C2; no compare is documented) ---------
    POST_FLASH_TIMEOUT_S = 60.0       # Reset + 3 s wait + boot + connect, with margin
    _flash_expect = None              # (product, 'a.b.c') of the .fif being flashed
    _flash_token = 0
    _flash_reported = False           # "Error!" already shown for this flash (timeout / no Reset)
    _conn_partial = False             # the running connect reads the version only

    def _flash_pending(self, result) -> bool:
        """A post-flash reconnect whose MDI identify gave no product/version: the
        unit is still booting. Only a reported product/version can mismatch."""
        if self._flash_expect is None:
            return False
        mode = getattr(result, 'mode', None) or DeviceMode.MDI
        return mode is DeviceMode.MDI and (result.product_id is None or result.app_version is None)

    @staticmethod
    def _expected(plan):
        info = getattr(plan, 'info', None)
        if info is None:
            return None
        return (info.product, info.version_str)

    def _check_flashed(self, result):
        """After a flash: True if the reconnected unit reports the .fif's version (and
        product, unless the .fif is the product-0 wildcard), False if not, None when
        no flash is waiting for its check."""
        exp, self._flash_expect = self._flash_expect, None
        if exp is None:
            return None
        product, version = exp
        ok = result.app_version == version and (not product or result.product_id == product)
        self.log.emit('Firmware check: unit reports product 0x%04x, app %s; file 0x%04x, %s -> %s'
                      % (result.product_id or 0, result.app_version, product, version,
                         'match' if ok else 'MISMATCH'))
        return ok

    def _flash_result(self, ok: bool) -> None:
        """The existing end-of-flash texts: "Complete!" or the failed-flash "Error!"."""
        from .. import uirules
        self.flash_status.emit('phase', 10 if ok else 11, 0)
        if not ok:
            self.error.emit(uirules.FLASH_STATUS[11])

    def _post_flash_timeout(self, tok: int) -> None:
        if tok != self._flash_token or self._flash_expect is None:
            return
        if self._flash_reported:
            return
        self.log.emit('Firmware check: the unit did not come back after the update')
        # keep _flash_expect, post_flash and write_back_model: if the unit comes back
        # later with the new version its settings are still written back (as main)
        self._flash_reported = True
        if not self.connected:
            self.set_state(14)                 # "Disconnected"; the reconnect loop keeps going
        self._flash_result(False)

    @QtCore.Slot(bool)
    def _flash_done(self, ok: bool):
        recovered = getattr(self, '_state_before_flash', 3) == 5
        if not ok:
            self.write_back_model = None
            self._flash_expect = None
            self.set_state(5 if recovered else 3)
            from .. import uirules
            self.error.emit(uirules.FLASH_STATUS[11])
            return
        # Sub 8 also ends a product mismatch or a verify failure (BOOTLOADER_DISASM §5b):
        # only the version the unit reports after the restart tells. Give it a deadline.
        self._flash_token += 1
        tok = self._flash_token
        QtCore.QTimer.singleShot(int(self.POST_FLASH_TIMEOUT_S * 1000),
                                 lambda: self._post_flash_timeout(tok))
        # After a recovery flash no tune was read before it: the reconnect reads the
        # unit's own tune and nothing is written back (write_back_model stays None).
        self.post_flash = not recovered
        self.set_state(7)
        if self._mode is DeviceMode.BOOTS:
            self._reconnect_after_reboot()          # the BOOTS job ends with its own reboot
            return
        self._submit_device_job('Reset', 0x0010, 0)

    def bus_request_async(self, cmd: int, payload: bytes, cb) -> None:
        from ..session import Job
        if self.session is None:
            cb(None)
            return
        self.session.submit(Job('Bus%04X' % cmd, [(cmd, bytes(payload))],
                                done=lambda job: cb(job.replies[0] if job.replies else None)))

    def bus_request(self, cmd: int, payload: bytes, want: bool = True, timeout: float = 2.0):
        """One 0x06xx frame from a script or the Masternode page, queued on the
        session like any job; returns the reply (or None)."""
        import threading
        from ..session import Job
        if self.session is None:
            return None
        ev, box = threading.Event(), []

        def done(job):
            box.extend(job.replies)
            ev.set()
        self.session.submit(Job('Bus%04X' % cmd, [(cmd, bytes(payload))], done=done))
        if not want:
            return None
        ev.wait(timeout)
        return box[0] if box else None

    def write_local_id(self, node_id: int) -> bool:
        """LocalIDWrite for MOST units, exactly as the exe gates it (capability bit
        0x2). Returns False and sends nothing on a product without the bit."""
        from .. import connect as _connect
        if not self.connected:
            return False
        return _connect.write_local_id(self.client, node_id, self.capabilities)

    # --- Save = .rax file backup to the PC (NOT a device commit) ----------
    # Live edits already persist on the amp. "Save" mirrors the official app: it
    # exports a settings backup FILE to the PC. Writing a file transmits nothing to
    # the device, so it is not parity-gated and actually succeeds.
    def export_backup(self, path: str):
        from .. import stored_config
        meta = {'device': self.device_name if hasattr(self, "device_name") else None,
                'connected': self.connected}
        written = stored_config.export_backup(path, dict(self._pending), meta=meta)
        self._pending.clear()
        self.dirty_changed.emit(False)
        self.log.emit('settings backed up to %s' % written)
        self.save_result.emit(written)
        return written

    def discard(self):
        """Close without backing up. The device keeps the live edits (they already
        persisted); we only clear the 'needs backup' state."""
        self._pending.clear()
        self.dirty_changed.emit(False)
