"""Lua bus messages: field maps, buffer rules, and the exe's 0x06xx payloads."""
from mobridge import luabus, scripting as S
from mobridge.message import MdiMessage


class FakeEngine:
    connected, port, conn_state = True, 'x', 3

    def __init__(self):
        self.sent = []

    def bus_request(self, cmd, pl, want=True):
        self.sent.append((cmd, pl.hex()))
        if cmd == 0x0625:
            return MdiMessage(cmd=0x0625, data=b'\x01\x02\x03\x04')
        return MdiMessage(cmd=cmd, data=b'\x01')

    def bus_request_async(self, cmd, pl, cb):
        self.sent.append((cmd, pl.hex()))
        cb(MdiMessage(cmd=0x064A, data=b'\x11\x22'))


def run(tmp_path, src):
    (tmp_path / 'a.lua').write_text(src)
    e, out = FakeEngine(), []
    h = S.ScriptHost(engine=e, log=out.append)
    ok = h.run(str(tmp_path / 'a.lua'))
    h.drain()
    return ok, out, e.sent


def test_payloads(tmp_path):
    ok, out, sent = run(tmp_path, '''
local m = most.message(); m:setFktid(0x123):setOperation(2); m.dest = 0x101; m.fblockid = 0x22
m:encodeNumber(0xabcd, 2):encodeString("hi", 1); print(tostring(m)); m:send()
local c = can.message(); c.id = 0x2a0; c:encodeNumber(5); c:send(1)
local a = a2b.message(); a.targetnode = 3; a.fktid = 7; a:encodeNumber(9); a:send()
local u = uds.message(); u.serviceid = 0x10; u:encodeNumber(1); u:send()
can.open(500000); most.open(); most.mode("master"); a2b.phantom(1); a2b.fetchStatus(3)
print(most.mode(), most.lightlock(), most.npr(), most.mpr())''')
    assert ok, out
    assert out[0] == 'MSG:0101.22.00.123.2.(6).abcd01686900'
    assert out[1] == 'master\t4\t3\t2'
    assert sent[:10] == [(0x0621, '01012200012302000006abcd01686900'), (0x0610, '000000800002a005'),
                         (0x0645, '000307000109'), (0x0650, '1001'), (0x0612, '0007a120'), (0x0620, ''),
                         (0x0624, '01'), (0x064C, '01'), (0x0649, '03'), (0x0625, '')]


def test_buffer_rules(tmp_path):
    ok, out, _ = run(tmp_path, '''
local m = most.message(); m:encodeNumber(0xabcd, 2):encodeString("hi", 1)
m:seek(1); print(m:decodeNumber(2), m:decodeString(), m:pos())
print(pcall(function() m.bogus = 1 end))
print(m[1], m.priority, pcall(function() m:encodeNumber(1, 5) end))
local c = can.message(); for i = 1, 8 do c:encodeNumber(i) end
print(pcall(function() c:encodeNumber(9) end))
print(pcall(function() c:send() end))''')
    assert ok, out
    assert out[0] == '43981\thi\t7'
    assert 'invalid map<K, T> key' in out[1]
    assert out[2].startswith('171\t1\tfalse') and 'Width Out of Range' in out[2]
    assert 'Cannot fit value into max message length' in out[3]
    assert out[4].startswith('false')                     # can send(ext) is mandatory


def test_i2c_future(tmp_path):
    ok, out, sent = run(tmp_path, '''
local f = a2b.i2c(0x68, 1, {5}, 2); print(f:isRunning())
f:after(function(st) print("after", st, select(2, f:value())[2]) end)
local g = a2b.i2c(0x68, 1, nil, 1); g:wait(); print("resumed", (g:value()))''')
    assert ok, out
    assert out == ['true', 'after\t0\t34', 'resumed\t0']
    assert sent == [(0x064A, '0068010205'), (0x064A, '00680101')]


def test_received_message_fires_callbacks(tmp_path):
    from mobridge import busevents
    (tmp_path / 'a.lua').write_text('a2b.register("msg", function(m) print(tostring(m)) end)\n'
                                    'a2b.register("ev", function(n, a, b) print(n, a, b) end)')
    out = []
    h = S.ScriptHost(engine=FakeEngine(), log=out.append)
    assert h.run(str(tmp_path / 'a.lua'))
    h.deliver(busevents.decode(0x0646, bytes.fromhex('00123407 0002aabb'.replace(' ', ''))))
    h.deliver(busevents.decode(0x0646, bytes.fromhex('050102')))
    assert out == ['MSG: 1234.07.02.aabb', 'busdrop\t258\t0']


def test_state_and_stub(tmp_path):
    ok, out, _ = run(tmp_path, 'local c = fddspconnect.instance(); print(c:state(), c:setCurrentVehicleType(1), '
                               'c:flashFile(), c:flashFile("/nope.fif"))')
    assert out == ['Connected\tTODO: Set the vehicle config\tRequires a file to flash\t'
                   'Something wrong with file or path given']


def test_dsptest_fetchstatus_can_rx(tmp_path):
    from mobridge import busevents
    assert luabus.dsp_test_payload(7, 1).hex() == '0701'
    assert luabus.dsp_test_payload(4, 3).hex() == '040000000000000003'
    assert luabus.parse_fetch_status(bytes.fromhex('0502000101')) == {'flag': True, 'count': 0x200}
    (tmp_path / 'a.lua').write_text('can.register("ctrlmsg", function(m) print(string.format("%x", m.id), m[1]) end)\n'
                                    'print(fddspconnect.instance():addDeviceFilter("DA-G2"), fddspconnect.instance():addDeviceFilter(5))')
    out = []
    h = S.ScriptHost(engine=FakeEngine(), log=out.append)
    assert h.run(str(tmp_path / 'a.lua'))
    h.deliver(busevents.decode(0x0615, bytes.fromhex('000002a0' '0000' '00' '00000000' '09')))
    assert out == ['true\tfalse', '2a0\t9']
