"""The most / a2b / can / uds Lua libraries and their wire frames
(LUA_REGION.md §1-§4, LUA_SCRIPTING.md Group A).

CtrlMessage objects are pure Lua (prelude below) with the exe's field maps,
buffer rules and error texts; send() and the bus calls hand a header + payload to
the host, which builds the exe's 0x06xx frame (payload builders FUN_140181xxx /
FUN_140182xxx, all big-endian) and queues it on the live session."""
from __future__ import annotations


def be(v: int, n: int) -> bytes:
    return int(v & ((1 << (8 * n)) - 1)).to_bytes(n, 'big')


def most_send(h: dict, data: bytes) -> tuple[int, bytes]:
    """0x0621 (FUN_140181b40): [u16 dest if nonzero][fblockid][instid][u16 fktid][op][0][u16 len][data]."""
    out = be(h['dest'], 2) if h['dest'] else b''
    out += be(h['fblockid'], 1) + be(h['instid'], 1) + be(h['fktid'], 2) + be(h['operation'], 1)
    return 0x0621, out + b'\x00' + be(len(data), 2) + data


def a2b_send(h: dict, data: bytes) -> tuple[int, bytes]:
    """0x0645 (FUN_1401825f0): [u16 targetnode][u8 fktid][u16 len][data]."""
    return 0x0645, be(h['targetnode'], 2) + be(h['fktid'], 1) + be(len(data), 2) + data


CAN_W = 0      # sess+0x3c, see can_send
CAN_B = 0      # sess+0x30


def can_send(h: dict, data: bytes, ext: int) -> tuple[int, bytes]:
    """0x0610 (FUN_140181660): [u16 w][u8 b][u32 id][<=8 data]; ext sets id bit 31
    (FUN_140124940). w/b are the bytes at Lua-struct +0x14 / +0x08, which no Lua
    field writes, so they go out as 0."""
    cid = h['id'] | (0x80000000 if ext else 0)
    return 0x0610, be(CAN_W, 2) + be(CAN_B, 1) + be(cid, 4) + data[:8]


def uds_send(h: dict, data: bytes) -> tuple[int, bytes]:
    """0x0650 (FUN_1401822e0): [u8 serviceid][data]."""
    return 0x0650, be(h['serviceid'], 1) + data


SENDERS = {'most': most_send, 'a2b': a2b_send, 'uds': uds_send}

A2B_EVENTS = {0: 'commchmsg', 1: 'neton', 2: 'netoff', 3: 'discoverystatus', 4: 'powerfault', 5: 'busdrop'}
CAN_EVENTS = {1: 'CanEventReceive', 2: 'CanEventTransmit'}


PRELUDE = r'''
local host = ...
local bit = bit
local M = {}

local FIELDS = {
  most = { {"priority", 0, 0}, {"source", 1, 2}, {"dest", 1, 2}, {"fblockid", 3, 1}, {"instid", 4, 1},
           {"fktid", 5, 2}, {"operation", 6, 1} },
  a2b  = { {"priority", 0, 0}, {"targetnode", 1, 2}, {"fktid", 2, 1} },
  can  = { {"priority", 0, 0}, {"id", 1, 4} },
  uds  = { {"serviceid", 0, 1}, {"length", 1, 2} },
}
local MAXLEN = { most = 0xff, a2b = 0x40, can = 8, uds = 0xff }
local TYPE = { most = "fdluabind::most::CtrlMessageBind", a2b = "fdluabind::a2b::CtrlMessageBind",
               can = "fdluabind::can::CtrlMessageBind", uds = "fdluabind::uds::CtrlMessageBind" }

local function slot(bus, name)
  for _, f in ipairs(FIELDS[bus]) do if f[1] == name then return f end end
end

local Msg = {}
local mt = {}

local function new(bus, sender)
  local m = { _bus = bus, _h = {}, _d = {}, _len = 0, _pos = 0, _n = 0, _max = MAXLEN[bus], _sender = sender }
  for _, f in ipairs(FIELDS[bus]) do m._h[f[2]] = 0 end
  return setmetatable(m, mt)
end
M.new = new

local function hdr(m)
  local h = {}
  for _, f in ipairs(FIELDS[m._bus]) do h[f[1]] = m._h[f[2]] end
  return h
end

local function bytes(m)
  local t = {}
  for i = 1, m._len do t[i] = m._d[i] or 0 end
  return t
end
M.bytes = bytes
M.hdr = hdr

function Msg.clear(m) m._len, m._pos, m._n = 0, 0, 0 return m end
function Msg.crop(m) if m._pos < m._len then m._len = m._pos end m._n = 0 return m end
function Msg.seek(m, n) n = (n or 1) - 1 if n < 0 then n = 0 end if n > m._len then n = m._len end m._pos = n return m end
function Msg.pos(m) return m._pos + 1 end
function Msg.clone(m)
  local c = new(m._bus, m._sender)
  for k, v in pairs(m._h) do c._h[k] = v end
  for i = 1, m._len do c._d[i] = m._d[i] end
  c._len, c._pos, c._n = m._len, m._pos, m._n
  return c
end
function Msg.copyHeader(m, o)
  if getmetatable(o) ~= mt or o._bus ~= m._bus then error("bad argument #2 (" .. TYPE[m._bus] .. " expected)") end
  for k, v in pairs(o._h) do m._h[k] = v end
  return m
end
function Msg.encodeNumber(m, v, w)
  if type(v) == "boolean" then v = v and 1 or 0 else v = tonumber(v) or error("bad argument #2 (number expected)") end
  w = w or 1
  if w - 1 > 3 or w < 1 then error("bad argument #3 (Width Out of Range (1-8)") end
  if m._max < m._pos + w then error("Cannot fit value into max message length") end
  for i = w, 1, -1 do
    m._d[m._pos + 1] = bit.band(bit.rshift(v, 8 * (i - 1)), 0xff)
    m._pos = m._pos + 1
  end
  if m._len < m._pos then m._len = m._pos end
  m._n = m._n + 1
  return m
end
function Msg.decodeNumber(m, w)
  w = w or 1
  if w - 1 > 3 or w < 1 then error("bad argument #2 (Width Out of Range (1-8)") end
  if m._pos + w > m._len then return end
  local v = 0
  for i = w, 1, -1 do
    v = v + m._d[m._pos + 1] * 2 ^ (8 * (i - 1))
    m._pos = m._pos + 1
  end
  m._n = m._n + 1
  return v
end
local function tostr(v)
  local t = type(v)
  if t == "nil" then return "nil" elseif t == "boolean" then return v and "true" or "false"
  elseif t == "number" or t == "string" or t == "userdata" then return tostring(v)
  elseif t == "table" then return "table" elseif t == "function" then return "function"
  elseif t == "thread" then return "thread" end
  error("Unknown type found")
end
function Msg.encodeString(m, v, ty)
  local s = tostr(v)
  ty = ty or -1
  if ty == 1 or ty == 2 then
    if m._max < m._pos + 2 + #s then error("Cannot fit string into max message length") end
  elseif ty ~= -1 then error("String type not supported") end
  if m._bus ~= "uds" and ty >= 0 then m._pos = m._pos + 1 m._d[m._pos] = ty end
  for i = 1, #s do m._pos = m._pos + 1 m._d[m._pos] = s:byte(i) end
  m._pos = m._pos + 1 m._d[m._pos] = 0
  if m._len < m._pos then m._len = m._pos end
  m._n = m._n + 1
  return m
end
function Msg.decodeString(m)
  if m._bus == "uds" then
    if m._len < m._pos + 1 then error("Length of remaining data too short (%d)") end
  else
    if m._len < m._pos + 2 then error("Length of remaining data too short (%d)") end
    local ty = m._d[m._pos + 1]
    m._pos = m._pos + 1
    if ty ~= 1 and ty ~= 0xff then error(string.format("Unsupported string type found (%d)", ty)) end
  end
  local e = m._pos + 1
  while e <= m._len and m._d[e] ~= 0 do e = e + 1 end
  if e <= m._len and m._d[e] == 0 then
    local t = {}
    for i = m._pos + 1, e - 1 do t[#t + 1] = string.char(m._d[i]) end
    m._pos = e
    m._n = m._n + 1
    return table.concat(t)
  end
end
function Msg.send(m, ext)
  if m._bus == "can" then
    ext = tonumber(ext) or error("bad argument #2 (number expected, got no value)")
  end
  host.send(m._bus, hdr(m), bytes(m), ext or 0)
  return m
end

mt.__index = function(m, k)
  if type(k) == "number" then
    if k >= 1 and k <= m._len then return m._d[k] end
    return
  end
  if Msg[k] then return Msg[k] end
  if type(k) == "string" and k:sub(1, 3) == "set" and #k > 3 then
    local name = k:sub(4, 4):lower() .. k:sub(5)
    return function(self, v) self[name] = v return self end
  end
  local f = slot(m._bus, k)
  if not f then return end
  if f[3] == 0 then return 1.0 end
  return m._h[f[2]]
end
mt.__newindex = function(m, k, v)
  local f = slot(m._bus, k)
  if not f then error("invalid map<K, T> key") end
  if f[3] == 0 then return end
  m._h[f[2]] = bit.band(tonumber(v) or 0, f[3] == 4 and 0xffffffff or (2 ^ (8 * f[3]) - 1))
end
mt.__call = function() error("Not callable") end
mt.__tostring = function(m)
  local p = {}
  for i = 1, m._len do p[#p + 1] = string.format("%02x", m._d[i]) end
  local h, s = m._h
  if m._bus == "most" then s = string.format("MSG:%04x.%02x.%02x.%03x.%1x.(%d)", h[1], h[3], h[4], h[5], h[6], m._len)
  elseif m._bus == "a2b" then s = string.format("MSG: %04x.%02x.%02x", h[1], h[2], m._len)
  elseif m._bus == "can" then s = string.format("MSG:%04x.%01x.%02x", h[1], m._len, m._len)
  else s = string.format("MSG:%04x.%02x", h[0], m._len) end
  return s .. "." .. table.concat(p)
end

-- callbacks: registry[lib]["_cb"][event] = {fn...}
local function lib(name)
  local L = { _cb = {} }
  function L.register(ev, fn)
    ev = tostr(ev)
    L._cb[ev] = L._cb[ev] or {}
    table.insert(L._cb[ev], fn)
    return #L._cb[ev]
  end
  function L.help() end
  function L.message() return new(name, name) end
  L._emit = function(ev, ...)
    for _, fn in ipairs(L._cb[ev] or {}) do
      local ok, err = pcall(fn, ...)
      if not ok then host.log("Could not call callback\n(" .. tostring(err) .. ")") end
    end
  end
  return L
end

local most, a2b, can, uds = lib("most"), lib("a2b"), lib("can"), lib("uds")
function most.open() host.call("most.open") end
function most.mode(v)
  if v == nil then return host.call("most.mode") == 1 and "master" or "slave" end
  if v == "master" then host.call("most.mode", 1) elseif v == "slave" then host.call("most.mode", 0) end
end
function most.lightlock() return host.call("most.status", 3) end
function most.npr() return host.call("most.status", 2) end
function most.mpr() return host.call("most.status", 1) end
function can.open(baud) host.call("can.open", baud) end
function a2b.phantom(v) host.call("a2b.phantom", tonumber(v) or error("bad argument #1 (number expected)")) end
function a2b.fetchStatus(t) host.call("a2b.fetchStatus", tonumber(t) or error("bad argument #1 (number expected)")) end
function a2b.getNetStatus() return host.call("a2b.get", "net") end
function a2b.getDiscoveryStatus() return host.call("a2b.get", "discovery") end
function a2b.getPowerFaultStatus() return host.call("a2b.get", "powerfault") end
function a2b.getBusDropStatus() return host.call("a2b.get", "busdrop") end

-- Future<tuple<int, vector<u8>>> (a2b.i2c)
local Future = {}
Future.__index = Future
function Future:after(fn)
  if type(fn) ~= "function" then error("bad argument #1 (Expected a function)") end
  if self._running then self._th[fn] = fn end
  return self
end
function Future:wait()
  if not self._running then return self end
  local co, main = coroutine.running()
  if co == nil or main then error("Cannot wait from main thread - use coroutine") end
  self._th[co] = co
  coroutine.yield()
  return self
end
function Future:isRunning() return self._running end
function Future:value() return self._status, self._bytes end
function Future:result() return self._status, self._bytes end
local function settle(f, status, data)
  f._running, f._status = false, status
  if data and #data > 0 then f._bytes = data else f._bytes = nil end
  for k, v in pairs(f._th) do
    if type(v) == "thread" then coroutine.resume(v) else
      local ok, err = pcall(v, status)
      if not ok then host.log("Could not call callback\n(" .. tostring(err) .. ")") end
    end
  end
  f._th = {}
end
M.settle = settle
function a2b.i2c(addr, reg, tbl, flag)
  addr = bit.band(tonumber(addr) or error("bad argument #1 (number expected)"), 0xffff)
  reg = bit.band(tonumber(reg) or error("bad argument #2 (number expected)"), 0xff)
  local w = {}
  if type(tbl) == "table" then for i = 1, #tbl do w[i] = bit.band(tonumber(tbl[i]) or 0, 0xff) end end
  local f = setmetatable({ _running = true, _th = {} }, Future)
  host.i2c(f, addr, reg, w, tonumber(flag) or 0)
  return f
end

-- incoming (§3): wrap and fire "ctrlmsg", "msg", "all"
function M.receive(name, h, data)
  local L = ({ most = most, a2b = a2b, can = can, uds = uds })[name]
  local m = new(name, name)
  for _, f in ipairs(FIELDS[name]) do if h[f[1]] ~= nil and f[3] > 0 then m._h[f[2]] = h[f[1]] end end
  for i = 1, #data do m._d[i] = data[i] end
  m._len = #data
  L._emit("ctrlmsg", m) L._emit("msg", m) L._emit("all", m)
end
function M.event(name, ev, a, b)
  local L = ({ most = most, a2b = a2b, can = can, uds = uds })[name]
  L._emit("ev", ev, a, b) L._emit("all", ev, a, b)
end
M.libs = { most = most, a2b = a2b, can = can, uds = uds }
return M
'''


def a2b_status(cache: dict, kind: str, table):
    """a2b.get*Status() from the last fetchStatus of each type (FUN_14011a110)."""
    if kind == 'net':
        c = cache.get(1) or cache.get(2) or {}
        return bool(c.get('flag'))
    if kind == 'discovery':
        c = cache.get(3, {})
        return table({1: bool(c.get('flag')), 2: bool(c.get('b2')), 3: c.get('count', 0)})
    if kind == 'powerfault':
        c = cache.get(4, {})
        return table({1: bool(c.get('flag')), 2: c.get('a', 0), 3: c.get('b', 0)})
    c = cache.get(5, {})
    return table({1: bool(c.get('flag')), 2: c.get('count', 0)})


def parse_fetch_status(d: bytes) -> dict:
    """0x0649 reply (FUN_1401827c0): [type] + per-type fields + trailing status u8.
    -> {'u16': sess+0x36 or +0x38 word, 'u8': ..., 'status': trailing byte}."""
    t = d[0]
    if t == 3 and len(d) >= 4:        # discovery: u8 0x36, u8 0x37
        return {'flag': d[3] != 0, 'b2': d[2] != 0, 'count': d[1]}
    if t == 4 and len(d) >= 5:        # power fault: u8 0x36, u16 0x38
        return {'flag': d[4] != 0, 'a': d[1], 'b': int.from_bytes(d[2:4], 'big')}
    if t == 5 and len(d) >= 4:        # bus drop: u16 0x36
        return {'flag': d[3] != 0, 'count': int.from_bytes(d[1:3], 'big')}
    return {'flag': d[-1] != 0 if len(d) >= 2 else False}


# dspTest names -> DspTest kind (FUN_1401d6e80, exe asm): fft/rms/gpio via FUN_1400ac860 (0/1/2);
# thd/pinknoise/tone/volmin/volmax via FUN_1401d7200 (R8D 3/4/5/8/9); 6/7 unnamed; both paths emit 0x0719
DSPTEST_KINDS = {'fft': 0, 'rms': 1, 'gpio': 2, 'thd': 3, 'pinknoise': 4, 'tone': 5, 'volmin': 8, 'volmax': 9}


def dsp_test_payload(kind: int, chan: int) -> bytes:
    """0x0719 (FUN_140181350): [u8 kind] then u8 for kinds 6/7, else u64 mask."""
    return bytes([kind]) + (be(chan, 1) if kind in (6, 7) else be(chan, 8))


# fddspconnect:dspParam names -> param id (FUN_1401d6be0)
DSPPARAM_IDS = {'hilo': 6, 'frequency': 7, 'amplitude': 8, 'pinknoise-amplitude': 9,
                'tone-frequency': 10, 'tone-amplitude': 11}
