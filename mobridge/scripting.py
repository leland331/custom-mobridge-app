"""Lua scripting host, as the exe's ScriptScreen/ScriptWidget (LUA_SCRIPTING.md).

* Modules are YAML descriptors (.yaml / .module) with Name / Description / Script /
  Product / Test / Hardware, found by a recursive walk of the script dirs.
* The VM is LuaJIT (Lua 5.1 ABI) with every standard library open (mask 0x1ff, no
  sandbox); `print` goes to the script log; package.path gets ?.lua, ?.luo,
  lib/?.lua, lib/?.luo under each script root and the script's own dir.
* The script body runs on a coroutine. Libraries land in _G in the exe's order:
  fddspconnect, fddspmodel, timer, most, a2b, can, uds.
* A .sma (Script Module Archive) is inflated into the script dir.

Bus sends from scripts (most/a2b/can/uds) raise a Lua error instead of sending:
the payload layout of those frames is not pinned (parity safety rule), and a CAN
send would transmit on the vehicle bus.
"""
from __future__ import annotations

import io
import os
import zipfile
import zlib
from dataclasses import dataclass
from typing import Callable

from . import luabus

MODULE_EXTS = ('.yaml', '.module')
PACKAGE_TEMPLATES = ('?.lua', '?.luo', 'lib/?.lua', 'lib/?.luo')
LIB_ORDER = ('fddspconnect', 'fddspmodel', 'timer', 'most', 'a2b', 'can', 'uds')

NO_MODULES_TITLE = 'No script modules found!'
NO_MODULES_TEXT = 'Please place scripts and script modules at this directory: %s'
SELECTOR_TITLE = 'Script Selector'
LOG_FILE_FILTER = 'Choose Log File(*.txt)'
LOGGING_TO = 'Logging to %1'
NO_SCRIPT = 'No script selected'
SMA_FILTER = 'Script Module Archive(*.sma)'
SMA_FAILED_TITLE = 'Failure'
SMA_FAILED = 'Failed to import the script archive.'


@dataclass
class ScriptModule:
    name: str
    description: str
    script: str          # absolute path of the Lua entry file
    product: str = ''
    test: str = ''
    hardware: str = ''
    path: str = ''       # the descriptor

    @property
    def selector_text(self) -> str:
        return '%s\n%s' % (self.name, self.description)


def script_dirs(app_data: str, app_dir: str) -> list[str]:
    """<AppLocalData>/script and <app dir>/script (ScriptScreen ctor)."""
    return [os.path.join(app_data, 'script'), os.path.join(app_dir, 'script')]


def load_module(path: str) -> ScriptModule | None:
    import yaml
    try:
        with open(path, encoding='utf-8') as f:
            d = yaml.safe_load(f)
    except Exception:
        return None
    if not isinstance(d, dict) or not d.get('Script'):
        return None
    script = str(d['Script'])
    if not os.path.isabs(script):
        script = os.path.join(os.path.dirname(path), script)
    return ScriptModule(str(d.get('Name', '')), str(d.get('Description', '')),
                        os.path.abspath(script), str(d.get('Product', '')), str(d.get('Test', '')),
                        str(d.get('Hardware', '')), path)


def find_modules(dirs) -> list[ScriptModule]:
    out = []
    for root in dirs:
        if not os.path.isdir(root):
            continue
        for dp, _dn, fn in sorted(os.walk(root)):
            for n in sorted(fn):
                if n.lower().endswith(MODULE_EXTS):
                    m = load_module(os.path.join(dp, n))
                    if m is not None:
                        out.append(m)
    return out


def import_sma(path: str, dest: str) -> bool:
    """Inflate a .sma into `dest` (a zip, or a zlib stream holding one)."""
    try:
        with open(path, 'rb') as f:
            blob = f.read()
        if not zipfile.is_zipfile(io.BytesIO(blob)):
            blob = zlib.decompress(blob)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            base = os.path.realpath(dest)
            for n in z.namelist():
                tgt = os.path.realpath(os.path.join(base, n))
                if not tgt.startswith(base + os.sep) and tgt != base:
                    return False
            os.makedirs(base, exist_ok=True)
            z.extractall(base)
        return True
    except Exception:
        return False


# fddspconnect:state() (FUN_1401d9300)
STATE_NAMES = {0: 'Disconnected', 1: 'Searching', 2: 'Connecting', 3: 'Connected', 4: 'ConnectedUnknown',
               5: 'Recovery', 6: 'Updating', 7: 'Restarting', 8: 'FactoryResetting', 9: 'Synchronizing',
               10: 'Uploading'}


class ScriptError(Exception):
    pass


class ScriptHost:
    """One Lua VM. reset() = fresh VM without running; run(path) = reset + run;
    restart() = reset + run the same script again."""

    def __init__(self, *, engine=None, log: Callable[[str], None] | None = None,
                 lib_dirs: list[str] | None = None, timer_factory=None, pane_layout=None):
        self.engine = engine
        self._log = log or (lambda s: None)
        self.lib_dirs = list(lib_dirs or [])
        self.timer_factory = timer_factory
        self.pane_layout = pane_layout
        self.widgets = None
        self.path = ''
        self.arg = ''
        self.log_file = None
        self.lua = None
        self.timers = []
        self._handlers = {}
        self._settled = []

    # --- output -------------------------------------------------------------
    def log(self, text: str) -> None:
        self._log(text)
        if self.log_file:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(text + '\n')
            except OSError:
                pass

    # --- lifecycle ----------------------------------------------------------
    def reset(self) -> None:
        for t in self.timers:
            try:
                t.stop()
            except Exception:
                pass
        self.timers = []
        self._handlers = {}
        from lupa import luajit21 as lj
        self.lua = lj.LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.print = self._print
        roots = list(self.lib_dirs)
        self._set_path(roots)
        self._most_mode = 0
        self._most_status = b'\0\0\0\0'
        self._a2b = {}
        self.bus = self.lua.execute(luabus.PRELUDE, self._host_table())
        if self.widgets is not None:
            self.widgets.clear()
        if self.pane_layout is not None:
            from .luawidget import WidgetLib
            self.widgets = WidgetLib(self, self.pane_layout)
            g.widget = self.widgets.table()
        for name in LIB_ORDER:
            g[name] = self._make_lib(name)

    def _set_path(self, roots) -> None:
        pkg = self.lua.globals().package
        parts = [os.path.join(r, t).replace('\\', '/') for r in roots for t in PACKAGE_TEMPLATES]
        pkg.path = ';'.join(parts + [str(pkg.path)])

    def run(self, path: str, arg: str = '') -> bool:
        self.reset()
        self.path, self.arg = path, arg
        if not path:
            return True
        self._set_path([os.path.dirname(os.path.abspath(path))])
        try:
            with open(path, 'rb') as f:
                src = f.read()
        except OSError as e:
            self.log(str(e))
            return False
        try:
            chunk = self.lua.eval('function(src, name, arg) local f, e = loadstring(src, name) '
                                  'if not f then error(e, 0) end '
                                  'local co = coroutine.create(function() return f(arg) end) '
                                  'local ok, err = coroutine.resume(co) '
                                  'if not ok then error(debug.traceback(co, tostring(err)), 0) end end')
            chunk(src.decode('utf-8', 'replace'), '=' + os.path.basename(path), arg)
            return True
        except Exception as e:
            self.log(str(e))
            return False

    def restart(self) -> bool:
        return self.run(self.path, self.arg)

    # --- libs -----------------------------------------------------------------
    def _print(self, *args):
        self.log('\t'.join(self._tostr(a) for a in args))

    def _tostr(self, v) -> str:
        if v is None:
            return 'nil'
        if v is True or v is False:
            return 'true' if v else 'false'
        return str(self.lua.globals().tostring(v)) if self.lua is not None else str(v)

    def _table(self, d: dict):
        return self.lua.table_from(d)

    def _unavailable(self, what: str):
        def f(*_a):
            raise ScriptError('%s is not supported by this application' % what)
        return f

    def _make_lib(self, name: str):
        if name == 'fddspconnect':
            return self._table({'instance': lambda *a: self._connect_bind()})
        if name == 'fddspmodel':
            return self._table({'instance': lambda *a: self._model_bind()})
        if name == 'timer':
            return self._table({'create': lambda *a: self._timer()})
        return self.bus.libs[name]

    # --- the bus host (luabus) -------------------------------------------------
    def _request(self, cmd: int, payload: bytes, want: bool = True):
        e = self.engine
        if e is None or not getattr(e, 'connected', False):
            raise ScriptError('Device not connected')
        return e.bus_request(cmd, payload, want)

    def _host_table(self):
        def send(bus, h, data, ext):
            hd = {k: int(v) for k, v in dict(h).items()}
            raw = bytes(int(data[i]) & 0xFF for i in range(1, len(data) + 1))
            if bus == 'can':
                cmd, pl = luabus.can_send(hd, raw, int(ext))
            else:
                cmd, pl = luabus.SENDERS[bus](hd, raw)
            self._request(cmd, pl)

        def call(name, arg=None):
            if name == 'most.open':
                self._request(0x0620, b'')
            elif name == 'can.open':
                self._request(0x0612, luabus.be(int(arg or 0), 4))
            elif name == 'most.mode':
                if arg is None:
                    return self._most_mode
                rep = self._request(0x0624, bytes([int(arg)]))
                if rep is not None and rep.cmd == 0x0624 and rep.data:
                    self._most_mode = rep.data[0]
            elif name == 'most.status':
                rep = self._request(0x0625, b'')
                if rep is not None and rep.cmd == 0x0625 and len(rep.data) > 2:
                    self._most_status = (bytes(rep.data) + b'\0' * 4)[:4]
                return self._most_status[int(arg)]
            elif name == 'a2b.phantom':
                self._request(0x064C, bytes([int(arg) & 0xFF]))
            elif name == 'a2b.fetchStatus':
                rep = self._request(0x0649, bytes([int(arg) & 0xFF]))
                if rep is not None and rep.cmd == 0x0649 and len(rep.data) >= 2:
                    self._a2b[int(arg)] = luabus.parse_fetch_status(bytes(rep.data))
            elif name == 'a2b.get':
                return self._a2b_status(str(arg))
            return None

        def i2c(fut, addr, reg, w, flag):
            # A2bI2C 0x064a (FUN_1401829b0): [u16 addr][u8 reg][u8 read count][write bytes];
            # the Future settles when the reply comes back (FUN_14011a550)
            wr = bytes(int(w[i]) & 0xFF for i in range(1, len(w) + 1))
            pl = luabus.be(int(addr), 2) + luabus.be(int(reg), 1) + luabus.be(int(flag), 1) + wr
            e = self.engine
            if e is None or not getattr(e, 'connected', False):
                raise ScriptError('Device not connected')

            def done(rep, fut=fut, lua=self.lua):
                ok = rep is not None and rep.cmd == 0x064A
                self._settled.append((lua, fut, 0 if ok else -1, list(rep.data) if ok and rep.data else None))
                self._wake()
            e.bus_request_async(0x064A, pl, done)

        return self._table({'send': send, 'call': call, 'i2c': i2c, 'log': self.log})

    def _wake(self) -> None:
        if self.timer_factory is not None:
            from .gui.qt import QtCore
            QtCore.QTimer.singleShot(0, self.drain)

    def drain(self) -> None:
        """Settle the Futures whose replies arrived (resumes f:wait(), runs f:after())."""
        while self._settled:
            lua, fut, st, data = self._settled.pop(0)
            if lua is not self.lua:
                continue                       # the VM was reset meanwhile
            try:
                self.bus.settle(fut, st, self.lua.table_from(data) if data else None)
            except Exception as ex:
                self.log(str(ex))

    def _a2b_status(self, kind: str):
        return luabus.a2b_status(self._a2b, kind, self._table)

    def deliver(self, ev) -> None:
        """A received bus event (busevents.BusEvent) -> the lib's callbacks."""
        if self.lua is None:
            return
        try:
            if ev.bus == 'a2b' and ev.sub == 0 and ev.fields:
                f = ev.fields
                self.bus.receive('a2b', self._table({'targetnode': f['id'], 'fktid': f['flags']}),
                                 self._table(dict(enumerate(f['data'], 1))))
            elif ev.bus == 'a2b':
                vals = list(ev.fields.values())
                self.bus.event('a2b', luabus.A2B_EVENTS.get(ev.sub, 'unknown'),
                               vals[0] if vals else 0, vals[1] if len(vals) > 1 else 0)
            elif ev.bus == 'can':
                f = ev.fields
                self.bus.receive('can', self._table({'id': f['id']}), self._table(dict(enumerate(f['data'], 1))))
        except Exception as e:
            self.log(str(e))

    def _connect_bind(self):
        e = self.engine
        from . import products
        pid = (lambda: (getattr(e, 'product_id', None) if getattr(e, 'conn_state', 0) == 5 else None)
               or getattr(getattr(e, 'root', None), 'product', None))

        def m(fn):
            return lambda _self=None, *a: fn(*a)
        filters = []

        def add_filter(v=None, idx=None):
            # FUN_1401d6180: a device-name filter, optional index; one boolean back
            if not isinstance(v, str):
                return False
            if isinstance(idx, int) and 0 <= idx <= len(filters):
                filters.insert(idx, v)
            else:
                filters.append(v)
            if e is not None:
                e.device_filters = list(filters)
            return True

        def clear_filter(n=None):
            if isinstance(n, int) and 0 <= n < len(filters):
                del filters[n]
            else:
                filters.clear()
            if e is not None:
                e.device_filters = list(filters)

        def flash_file(path=None):
            # FUN_1401d7cb0
            if not isinstance(path, str):
                return 'Requires a file to flash'
            if not os.path.isfile(path):
                return 'Something wrong with file or path given'
            from . import flashflow
            plan = flashflow.validate(path, pid() or 0, flashflow._ver3(getattr(e, 'app_version', '') or ''),
                                      getattr(e, 'conn_state', 3))
            if e is None or not e.connected:
                return 'device is not connected'
            started = bool(not plan.guard and plan.code in (0, 1) and e.flash_file(plan))
            return 'Flash has started' if started else flashflow.message(plan)

        t = {
            'connected': m(lambda: bool(e is not None and e.connected)),
            'available': m(lambda: bool(e is not None and getattr(e, 'port', None))),
            'state': m(lambda: STATE_NAMES.get(getattr(e, 'conn_state', 0), 'Disconnected')),
            'start': m(lambda: e.connect_device(e.port) if e is not None and getattr(e, 'port', None) else None),
            'stop': m(lambda: e.disconnect_device() if e is not None else None),
            'name': m(lambda: products.name(pid()) if pid() in products.BY_ID else ''),
            'description': m(lambda: products.description(pid()) if pid() in products.BY_ID else ''),
            'imagePath': m(lambda: products.image_path(pid()) if pid() in products.BY_ID else ''),
            'softwareVersion': m(lambda: getattr(e, 'app_version', '') or ''),
            'serialNumber': m(lambda: getattr(e, 'serial', '') or ''),
            'productId': m(lambda: pid() or 0),
            'isShuttingDown': m(lambda: False),
            'addDeviceFilter': m(add_filter),
            'clearDeviceFilter': m(clear_filter),
            'setCurrentVehicleType': m(lambda *a: 'TODO: Set the vehicle config'),   # stub in the exe
            'flashFile': m(flash_file),
            'reset': m(lambda: bool(e is not None and e.restart())),
            'factoryReset': m(lambda: bool(e is not None and e.factory_reset())),
        }
        t['headunitRead'] = m(lambda: (e.headunit_read() if e is not None and hasattr(e, 'headunit_read') else None) and None)
        def dsp_test(name=None, chan=0, arg=None):
            # FUN_1401d6e80 -> DspTest 0x0719 [u8 kind] + u8 (kinds 6/7) or u64 mask
            kind = luabus.DSPTEST_KINDS.get(name)
            if kind is None:
                raise ScriptError('Expected valid test type')   # FUN_1401d6e80 luaL_error
            rep = self._request(0x0719, luabus.dsp_test_payload(kind, int(chan)))
            return rep.data[1] if rep is not None and rep.cmd == 0x0719 and len(rep.data) > 1 else 0
        t['dspTest'] = m(dsp_test)
        def dsp_param(name=None, value=0):
            # FUN_1401d6be0 -> FUN_1400b1960: queued {paramId, value} DSP-param write, nothing back
            pid_ = luabus.DSPPARAM_IDS.get(name)
            if pid_ is None:
                raise ScriptError('bad argument #2 to \'dspParam\'')
            if e is None or not getattr(e, 'connected', False):
                return None
            e.bus_request_async(0x0717, luabus.be(pid_, 2) + luabus.be(int(value) & 0xFFFFFFFF, 4), lambda *_a: None)
            return None
        t['dspParam'] = m(dsp_param)
        # FUN_1401d92e0: connect vtable+0x68, returns nothing (engine.start_recovery)
        t['startRecovery'] = m(lambda: (e.start_recovery() if e is not None else None) and None)
        return self._table(t)

    def _model_bind(self):
        e = self.engine
        root = (lambda: getattr(e, 'root', None))
        host = self

        def speakers():
            r = root()
            return r.speakers_in_preset(r.current_preset) if r is not None else []

        def current_speaker(name=None):
            # FUN_1401d9df0: optional name selects, the index comes back
            sp = speakers()
            if isinstance(name, str):
                for i, s in enumerate(sp):
                    if s.id == name or s.name == name:
                        host.current_speaker = i
            return host.current_speaker

        def speaker_bind():
            # FUN_1401d9dc0 -> SpeakerModelBind of the selected speaker
            sp = speakers()
            if not sp:
                return None
            return host._speaker_bind(sp[min(host.current_speaker, len(sp) - 1)])

        def reset():
            r = root()
            if r is not None:
                r.new_tune(bool(getattr(r, 'connected', False)))
            return 1

        from . import model as M
        types = [bit for bit, *_r in M.SPEAKER_TYPES]

        def index_of(bit):
            # QMetaEnum("SpeakerType") enumerator index = wire ordinal (0 = none)
            return M.ORDINAL_BY_ROLE.get(bit, 0)

        def get_speakers(mask=None):
            sp = speakers()
            if mask is not None:
                sp = [x for x in sp if x.role & int(mask)]
            return host.lua.table_from([host._speaker_bind(x) for x in sp])

        def mapped_speakers():
            r = root()
            out = {}
            if r is not None:
                mask = r.enabled_roles()
                for bit in types:
                    if bit & mask == bit:
                        out[index_of(bit)] = host._speaker_bind(r.speaker(bit))
            return host.lua.table_from(out)

        def channel_mapping(ch=-1):
            r = root()
            role = r.channel_role.get(int(ch)) if r is not None else None
            return index_of(role) if role else None

        def mute_all(*_a):
            r = root()
            if r is None:
                return 0
            for x in speakers():
                host.log('Muting %s' % x.name)
            r.mute_all(True)
            if e is not None and hasattr(e, 'push'):
                e.push('script')
            return r.name

        t = {
            'name': lambda *_a: getattr(root(), 'name', '') or '',
            'headunitModel': lambda *_a: host._headunit_bind(),
            'currentSpeaker': lambda *_a: speaker_bind(),
            'currentSpeakerType': lambda *_a: host.current_speaker,
            'enabledSpeakers': lambda *_a: root().enabled_roles() if root() is not None else 0,
            'getSpeakers': lambda _s=None, m=None: get_speakers(m),
            'getMappedSpeakers': lambda *_a: mapped_speakers(),
            'channelMapping': lambda _s=None, ch=-1: channel_mapping(ch),
            'muteAllSpeakers': mute_all,
            'profile': lambda *_a: getattr(root(), 'current_preset', 0),
            'profileName': lambda *_a: root().preset().name if root() is not None else '',
            'reset': lambda *_a: reset(),
        }
        def load_configuration(path=None):
            # FUN_1401d9e20 -> FUN_1400e7fb0 XML <Project> path: 0 ok, 3 open fail,
            # 4 version, 5 product (rax.load, no "apply anyway")
            from . import rax
            r = root()
            if not isinstance(path, str) or not path or r is None:
                return 3
            try:
                rc = rax.load(r, path)
            except rax.RaxError as ex:
                return ex.code if ex.code in (4, 5) else 3
            except Exception:
                return 3
            if e is not None and hasattr(e, 'push'):
                e.push('script')
            return rc

        t['masterModel'] = lambda *_a: host._master_bind()
        t['loadConfiguration'] = lambda _s=None, p=None: load_configuration(p)
        return self._table(t)

    current_speaker = 0

    def _prop_bind(self, get: dict, set_: dict, label: str):
        """get/set property userdata: obj:prop() reads, obj:prop(v) writes."""
        def prop(name):
            def f(*a):
                if a and self.lua is not None and self.lua.globals().type(a[0]) == 'table':
                    a = a[1:]
                v = a[0] if a else None
                if v is None:
                    return get[name]()
                set_[name](v)
                if self.engine is not None and hasattr(self.engine, 'push'):
                    self.engine.push('script')
                return None
            return f
        t = {n: prop(n) for n in get}
        t['__label'] = label
        return self._table(t)

    def _master_bind(self):
        """MasterModelBind: one property gainDb (FUN_1400f6cf0 / FUN_1400f6ef0), int dB."""
        r = getattr(self.engine, 'root', None)
        if r is None:
            return None
        m = r.preset().master
        return self._prop_bind({'gainDb': lambda: int(m.gain_db)},
                               {'gainDb': lambda v: m.set_gain(float(int(v)))}, 'MasterModel')

    def _headunit_bind(self):
        r = getattr(self.engine, 'root', None)
        if r is None:
            return None
        hu = r.headunit
        get = {f: (lambda f=f: hu.values[f]) for f in hu.FIELDS}
        set_ = {f: (lambda v, f=f: hu.set(f, int(v))) for f in hu.FIELDS}
        return self._prop_bind(get, set_, 'HeadunitModel')

    def _speaker_bind(self, s):
        from . import model as M
        ftname = {M.FT_LP: 'LowPass', M.FT_HP: 'HighPass'}
        get = {
            'id': lambda: s.role, 'name': lambda: s.name,
            'filterType': lambda: ftname.get(s.filter_type, ''),
            'filterDesign': lambda: s.filter_design,
            'loP': lambda: int(s.f1), 'loPSlope': lambda: int(s.slope1),
            'hiP': lambda: int(s.f2), 'hiPSlope': lambda: int(s.slope2),
            'gainDb': lambda: int(s.gain_db),
            'mute': lambda: 'Muted' if s.mute else 'Unmuted',
        }
        set_ = {
            'id': lambda v: None, 'name': lambda v: None,
            'filterType': lambda v: s.set_filter_type(int(v)),
            'filterDesign': lambda v: s.set_design(int(v)),
            'loP': lambda v: s.set_lop(int(v)), 'loPSlope': lambda v: s.set_slope1(int(v)),
            'hiP': lambda v: s.set_hip(int(v)), 'hiPSlope': lambda v: s.set_slope2(int(v)),
            'gainDb': lambda v: s.set_gain(float(int(v))),
            'mute': lambda v: s.set_mute(bool(int(v))),
        }
        return self._prop_bind(get, set_, 'SpeakerModel[%s]' % s.id)

    def _timer(self):
        host = self
        holder = {'cb': None, 'left': None}
        qt = self.timer_factory() if self.timer_factory else None
        if qt is not None:
            self.timers.append(qt)

            def fire():
                if holder['left'] is not None:
                    holder['left'] -= 1
                    if holder['left'] <= 0:
                        qt.stop()
                if holder['cb'] is not None:
                    try:
                        holder['cb']()
                    except Exception as ex:
                        host.log(str(ex))
            qt.timeout.connect(fire)

        def start(_self, interval, count=None):
            holder['left'] = int(count) if count else None
            if qt is not None:
                qt.start(int(interval))
            return True

        def stop(_self=None):
            if qt is not None:
                qt.stop()
            return True

        def register(_self, event, fn):
            if str(event) == 'timeout':
                holder['cb'] = fn
            return True
        return self._table({'start': start, 'stop': stop, 'register': register})
