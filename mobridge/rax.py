"""The .rax configuration file, read and written exactly as the official app does
(re/analysis/MODEL_REGION.md §3: writer FUN_140105530.., reader FUN_1401030b0..).

Return codes of load() follow the exe (UI_REGION_1.md §4 "Open Tune"):
  0 ok, 1/2 parse error ("is not able to be parsed. Error code: N"),
  3 cannot be opened, 4 newer than fw 210 ("not compatible with the current
  version"), 5 settings for a different device (the app asks "apply anyway?",
  then loads again with force=True).

Number formats: QString::number(v, 'f', 2) for gain / timealign / limit / point
attributes ("0.00"), 'g' (default precision 6) for master gain, input gains and
upmix values ("0", "0.08").
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

from . import model as M

MAX_FILE_VERSION = 0xD2          # 210: files newer than this are rejected (code 4)
PROFILE_LAYOUT_VERSION = 0xD1    # 209: from here <upmix>/<timealign> live inside <profile>
LEGACY_OFFSET_VERSION = 0xD0     # 208: files older than this get +LEGACY_GAIN_OFFSET on fw > 207
LEGACY_GAIN_OFFSET = -3.0        # DAT_1401fd828 (0xE700 -> 0xED00 unity change = 0x600/512)
RAX_FILTER = 'Configuration File(*.rax)'
RAX_DIALOG_TITLE = 'Configuration File'


def _f(v: float) -> str:
    return '%.2f' % float(v)


def _g(v: float) -> str:
    """Qt 'g' format, precision 6."""
    s = '%.6g' % float(v)
    return s


def _b(v) -> str:
    return '1' if v else '0'


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def _points(list_, tag: str, point_tag: str) -> list[str]:
    out = ['<%s count="%d">' % (tag, len(list_.points))]
    for p in list_.points:
        out.append('<%s type="peaking" freq=%s mag=%s qleft=%s qright=%s/>'
                   % (point_tag, quoteattr(_f(p.freq)), quoteattr(_f(p.gain)),
                      quoteattr(_f(p.q1)), quoteattr(_f(p.q2))))
    out.append('</%s>' % tag)
    return out


def _vol(master: M.MasterModel) -> list[str]:
    out = ['<vol count="%d">' % len(master.volume_points)]
    for notch, mag in master.volume_points:
        out.append('<volpoint notch="%d" mag=%s/>' % (int(notch), quoteattr(_g(mag))))
    out.append('</vol>')
    return out


def _speaker(s: M.SpeakerModel) -> list[str]:
    out = ['<speaker id=%s gain=%s invertPhase="%s" xoverlink="%s" passthrough="%s" mute="%s" '
           'testtone="%d" timealign=%s limit=%s>'
           % (quoteattr(s.id), quoteattr(_f(s.gain_db)), _b(s.invert_phase),
              _b(s.xover_partner is not None), _b(s.passthrough), _b(s.mute), int(s.testtone),
              quoteattr(_f(s.delay_ms)), quoteattr(_f(s.limit_dbfs)))]
    out += _points(s.eq, 'eq', 'point')
    out.append('<filter type=%s model=%s>' % (quoteattr(M.FILTER_TYPE_NAMES[s.filter_type]),
                                             quoteattr(M.FILTER_DESIGN_NAMES[s.filter_design])))
    out.append('<param name="LoP" value="%d" slope="%d"/>' % (s.f1, s.slope1))
    out.append('<param name="HiP" value="%d" slope="%d"/>' % (s.f2, s.slope2))
    out.append('</filter>')
    out.append('</speaker>')
    return out


def _upmix(u: M.UpmixModel) -> str:
    return ('<upmix enabled="%s" algorithm="%d" lmsrate=%s adprate=%s adpgamma=%s/>'
            % (_b(u.enabled), u.algorithm, quoteattr(_g(u.lmsrate)), quoteattr(_g(u.adprate)),
               quoteattr(_g(u.adpgamma))))


def _timealign(t: M.TimeAlignModel) -> str:
    return ('<timealign units="%d" distanceclosest=%s distancefurthest=%s/>'
            % (t.units, quoteattr(_f(t.closest)), quoteattr(_f(t.furthest))))


def dumps(root: M.DspModel) -> str:
    """Serialise the model to .rax XML text (FUN_140105530)."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<Project version="%d" product="%x" name=%s ampserial=%s profile="%d">'
           % (root.fw, root.product, quoteattr(root.name), quoteattr(root.ampserial),
              root.current_preset)]
    chans = root.channels()
    out.append('<outputs count="%d">' % len(chans))
    for ch in chans:
        out.append('<map speaker=%s channel="%d"/>' % (quoteattr(M.NAME_BY_ROLE[root.channel_role[ch]]), ch))
    enabled = root.enabled_roles()
    for bit, name, _d, _o in M.SPEAKER_TYPES:
        if enabled & bit:
            out.append('<enable speaker=%s value="1"/>' % quoteattr(name))
    out.append('</outputs>')
    out.append('<inputs>')
    for p in range(root.preset_count):
        out.append('<profile id="%d">' % p)
        for i in range(64):
            out.append('<map output="%d" input="%d"/>' % (i, root.input_map.get(i, 0)))
        for k in range(1, 65):
            out.append('<gain input="%d" value=%s/>' % (k, quoteattr(_g(root.input_gains[p][k]))))
        out.append('</profile>')
    out.append('</inputs>')
    for p in range(root.preset_count):
        pr = root.presets[p]
        out.append('<profile id="%d" name=%s>' % (p, quoteattr(pr.name)))
        out.append('<master gain=%s>' % quoteattr(_g(pr.master.gain_db)))
        out += _points(pr.master.eq, 'eq', 'point')
        out += _vol(pr.master)
        out.append('</master>')
        for role in sorted(pr.speakers):
            if role & enabled:                                 # FUN_1400f4cd0(spk) & enabledMask
                out += _speaker(pr.speakers[role])
        out.append(_upmix(pr.upmix))
        out.append(_timealign(pr.timealign))
        out.append('</profile>')
    out.append('<options rearfill="%s" loudness="%s" headunitbassctrlsub="%s"/>'
               % (_b(root.option(5)), _b(root.option(3)), _b(root.option(4))))
    out.append('</Project>')
    return '\n'.join(out) + '\n'


def save(root: M.DspModel, path: str) -> None:
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(dumps(root))
    root.path = path
    root.modified = False


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
class RaxError(Exception):
    def __init__(self, code: int, msg: str = ''):
        super().__init__(msg or 'rax error %d' % code)
        self.code = code


def precheck(text: str) -> tuple[int, int]:
    """FUN_1400ff810: (version, product) from the <Project> element only."""
    try:
        el = ET.fromstring(text)
    except ET.ParseError:
        raise RaxError(1, 'not able to be parsed')
    if el.tag != 'Project':
        raise RaxError(2, 'no Project element')
    return int(el.get('version', '0')), int(el.get('product', '0') or '0', 16)


def _apply_points(list_: M.EqPointList, el, offset: float, fw: int) -> None:
    list_.points.clear()
    for pt in el.findall('point'):
        if any(pt.get(a) is None for a in ('type', 'freq', 'mag', 'qleft', 'qright')):
            continue                                            # reader requires all five
        list_.add(float(pt.get('freq')), float(pt.get('mag')) + offset,
                  float(pt.get('qleft')), float(pt.get('qright')), fw=fw)


def _apply_speaker(root: M.DspModel, el, profile: int, offset: float) -> None:
    role = M.ROLE_BY_NAME.get(el.get('id', ''))
    if not role or role not in root.role_channel:              # FUN_1400ea7b0(root, role, -1)
        return
    s = root.speaker(role, profile)
    if el.get('gain') is not None:
        s.set_gain(float(el.get('gain')) + offset)
    if el.get('invertPhase') is not None:
        s.set_invert_phase(int(el.get('invertPhase')) != 0)
    if el.get('xoverlink') is not None:
        if int(el.get('xoverlink')) != 0:
            root.link_xover_partner(s)
        else:
            s.link_xover(None)
    if el.get('passthrough') is not None:
        s.set_passthrough(int(el.get('passthrough')) != 0)
    if el.get('mute') is not None:
        s.set_mute(int(el.get('mute')) != 0)
    if el.get('testtone') is not None:
        s.set_testtone(1 if int(el.get('testtone')) != 0 else 0)   # written as int, read as bool
    if el.get('timealign') is not None:
        s.set_delay(float(el.get('timealign')))
    if el.get('limit') is not None:
        s.set_limit(float(el.get('limit')))
    f = el.find('filter')
    if f is not None:
        t = {v: k for k, v in M.FILTER_TYPE_NAMES.items()}.get(f.get('type', ''), None)
        if t is not None:
            s.set_filter_type(t)
        d = {v: k for k, v in M.FILTER_DESIGN_NAMES.items()}.get(f.get('model', ''), None)
        if d is not None:
            s.set_design(d)                                     # refused for mode 2
        for prm in f.findall('param'):
            if prm.get('name') == 'LoP':
                if prm.get('value') is not None: s.set_lop(int(float(prm.get('value'))))
                if prm.get('slope') is not None: s.set_slope1(int(float(prm.get('slope'))))
            elif prm.get('name') == 'HiP':
                if prm.get('value') is not None: s.set_hip(int(float(prm.get('value'))))
                if prm.get('slope') is not None: s.set_slope2(int(float(prm.get('slope'))))
    eq = el.find('eq')
    if eq is not None:
        _apply_points(s.eq, eq, offset, root.fw)


def _apply_upmix(u: M.UpmixModel, el) -> None:
    if el.get('enabled') is not None: u.set_enabled(int(el.get('enabled')) & 0xFF)
    if el.get('algorithm') is not None: u.set_algorithm(int(el.get('algorithm')))
    if el.get('lmsrate') is not None: u.set_lmsrate(float(el.get('lmsrate')))
    if el.get('adprate') is not None: u.set_adprate(float(el.get('adprate')))
    if el.get('adpgamma') is not None: u.set_adpgamma(float(el.get('adpgamma')))


def _apply_timealign(t: M.TimeAlignModel, el) -> None:
    if el.get('units') is not None: t.set_units(int(el.get('units')))
    if el.get('distanceclosest') is not None: t.set_closest(float(el.get('distanceclosest')))
    if el.get('distancefurthest') is not None: t.set_furthest(float(el.get('distancefurthest')))


def loads(root: M.DspModel, text: str, force: bool = False) -> int:
    """FUN_1400e7fb0 / FUN_1401030b0: parse into `root` (which is first reset like
    New Tune). Returns 0 on success; raises RaxError(code) otherwise. With
    force=True a product mismatch is applied anyway (the "apply anyway" path)."""
    version, product = precheck(text)
    if version > MAX_FILE_VERSION:
        raise RaxError(4, 'not compatible with the current version')
    device_fw = root.fw
    if root.product != 0 and product != 0 and product != root.product and not force:
        raise RaxError(5, 'settings created for a different device')
    el = ET.fromstring(text)
    root.new_tune(root.connected)                                # FUN_1400ebdd0 before parsing
    if device_fw == 0:
        root.fw = version                                        # offline: adopt the file version
    if root.product == 0:
        root.product = product & 0xFFFF
    if el.get('name') is not None:
        root.set_name(el.get('name'))
    if el.get('ampserial') is not None:
        root.ampserial = el.get('ampserial')
    profile = max(0, int(el.get('profile', '0') or 0))
    offset = LEGACY_GAIN_OFFSET if (root.fw > 0xCF and version < LEGACY_OFFSET_VERSION) else 0.0

    outs = el.find('outputs')
    if outs is not None:
        if outs.get('ampserial') is not None:
            root.ampserial = outs.get('ampserial')
        for m in outs.findall('map'):
            role = M.ROLE_BY_NAME.get(m.get('speaker', ''))
            if role and m.get('channel') is not None:
                root.map_speaker(role, int(m.get('channel')))
        for e in outs.findall('enable'):
            role = M.ROLE_BY_NAME.get(e.get('speaker', ''))
            if role:
                root.enable_speaker(role, int(e.get('value', '0')) == 1)
    ins = el.find('inputs')
    if ins is not None:
        for pr in ins.findall('profile'):
            pid = int(pr.get('id', '0'))
            for m in pr.findall('map'):
                o = int(m.get('output', '-1'))
                if 0 <= o < 64:
                    root.set_input_map(o, int(m.get('input', '0')))
            for g in pr.findall('gain'):
                k = int(g.get('input', '0'))
                if 0 <= k - 1 < 64 and pid < M.MAX_PRESETS:
                    root.set_input_gain(k, float(g.get('value', '0')), pid)
    for pr in el.findall('profile'):
        pid = int(pr.get('id', '0'))
        if pid >= M.MAX_PRESETS:
            continue
        preset = root.presets[pid]
        if pr.get('name') is not None:
            preset.name = pr.get('name')
        ma = pr.find('master')
        if ma is not None:
            if ma.get('gain') is not None:
                preset.master.set_gain(float(ma.get('gain')) + offset)
            eq = ma.find('eq')
            if eq is not None:
                _apply_points(preset.master.eq, eq, offset, root.fw)
            vol = ma.find('vol')
            if vol is not None:
                preset.master.volume_points = [(int(v.get('notch', '0')), float(v.get('mag', '0')))
                                               for v in vol.findall('volpoint')]
        for sp in pr.findall('speaker'):
            _apply_speaker(root, sp, pid, offset)
        if version > PROFILE_LAYOUT_VERSION - 1:                  # >= 209: inside <profile>
            u = pr.find('upmix')
            if u is not None:
                _apply_upmix(preset.upmix, u)
            t = pr.find('timealign')
            if t is not None:
                _apply_timealign(preset.timealign, t)
        preset.timealign.delays_to_distances()                    # FUN_1400f5890 after </profile>
    if version < PROFILE_LAYOUT_VERSION:                          # pre-209: at Project level
        u = el.find('upmix')
        if u is not None:
            _apply_upmix(root.presets[profile].upmix, u)
        t = el.find('timealign')
        if t is not None:
            _apply_timealign(root.presets[profile].timealign, t)
    opts = el.find('options')
    if opts is not None:
        for key, attr in ((5, 'rearfill'), (3, 'loudness'), (4, 'headunitbassctrlsub')):
            if opts.get(attr) is not None:
                root.set_option(key, int(opts.get(attr)) != 0)
    root.select_preset(profile)                                   # on </Project>
    return 0


def load(root: M.DspModel, path: str, force: bool = False) -> int:
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        raise RaxError(3, 'cannot be opened')
    rc = loads(root, text, force)
    root.path = path
    root.modified = False
    return rc
