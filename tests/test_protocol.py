"""Test du client LAN contre un faux appareil Govee (loopback, sans HA)."""

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path

# Charge protocol.py sans importer le paquet (qui dépend de Home Assistant).
_spec = importlib.util.spec_from_file_location(
    "govee_protocol",
    Path(__file__).parent.parent / "custom_components" / "govee_lan" / "protocol.py",
)
protocol = importlib.util.module_from_spec(_spec)
sys.modules["govee_protocol"] = protocol
_spec.loader.exec_module(protocol)

# Ports de test (pas ceux de production, pour ne pas gêner un vrai appareil).
SCAN, LISTEN, COMMAND = 14001, 14002, 14003


class FakeDevice(asyncio.DatagramProtocol):
    """Imite un appareil : répond au scan et à devStatus, mémorise le reste."""

    def __init__(self):
        self.state = {
            "onOff": 0,
            "brightness": 100,
            "color": {"r": 255, "g": 0, "b": 0},
            "colorTemInKelvin": 0,
        }
        self.received = []
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        msg = json.loads(data)["msg"]
        cmd, body = msg["cmd"], msg["data"]
        self.received.append((cmd, body))
        reply_to = (addr[0], LISTEN)
        if cmd == "scan":
            reply = {
                "msg": {
                    "cmd": "scan",
                    "data": {
                        "ip": "127.0.0.1",
                        "device": "1F:80:C5:C4:AA:BB:CC:DD",
                        "sku": "H61BE",
                        "wifiVersionSoft": "1.02.11",
                    },
                }
            }
        elif cmd == "devStatus":
            reply = {"msg": {"cmd": "devStatus", "data": self.state}}
        else:
            if cmd == "turn":
                self.state["onOff"] = body["value"]
            elif cmd == "brightness":
                self.state["brightness"] = body["value"]
            elif cmd == "colorwc":
                self.state["color"] = body["color"]
                self.state["colorTemInKelvin"] = body["colorTemInKelvin"]
            return
        self.transport.sendto(json.dumps(reply).encode(), reply_to)


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        loop = asyncio.get_running_loop()
        self.fake = FakeDevice()
        self.scan_t, _ = await loop.create_datagram_endpoint(
            lambda: self.fake, local_addr=("127.0.0.1", SCAN)
        )
        self.cmd_t, _ = await loop.create_datagram_endpoint(
            lambda: self.fake, local_addr=("127.0.0.1", COMMAND)
        )
        self.ctrl = protocol.GoveeLanController(
            listen_port=LISTEN, scan_port=SCAN, command_port=COMMAND
        )
        await self.ctrl.start()

    async def asyncTearDown(self):
        await self.ctrl.stop()
        self.scan_t.close()
        self.cmd_t.close()

    async def _discover(self):
        events = []
        self.ctrl.add_discovery_listener(lambda d, new: events.append((d, new)))
        await self.ctrl.scan(extra_ips=["127.0.0.1"], wait=0.3)
        return events

    async def test_discovery(self):
        events = await self._discover()
        self.assertEqual(len(events), 1)
        device, is_new = events[0]
        self.assertTrue(is_new)
        self.assertEqual(device.sku, "H61BE")
        self.assertEqual(device.device_id, "1F:80:C5:C4:AA:BB:CC:DD")
        self.assertEqual(device.ip, "127.0.0.1")
        # Un second scan ne recrée pas l'appareil.
        await self.ctrl.scan(extra_ips=["127.0.0.1"], wait=0.3)
        self.assertEqual(len(self.ctrl.devices), 1)

    async def test_status(self):
        await self._discover()
        device = next(iter(self.ctrl.devices.values()))
        state = await self.ctrl.get_status(device)
        self.assertFalse(state.on)
        self.assertEqual(state.brightness, 100)
        self.assertEqual(state.rgb, (255, 0, 0))
        self.assertEqual(state.kelvin, 0)

    async def test_commands(self):
        await self._discover()
        device = next(iter(self.ctrl.devices.values()))
        await self.ctrl.turn(device, True)
        await self.ctrl.set_brightness(device, 42)
        await self.ctrl.set_rgb(device, (10, 20, 30))
        await asyncio.sleep(0.2)
        state = await self.ctrl.get_status(device)
        self.assertTrue(state.on)
        self.assertEqual(state.brightness, 42)
        self.assertEqual(state.rgb, (10, 20, 30))

        await self.ctrl.set_color_temp(device, 4000)
        await asyncio.sleep(0.2)
        state = await self.ctrl.get_status(device)
        self.assertEqual(state.kelvin, 4000)

    async def test_status_timeout(self):
        await self._discover()
        device = next(iter(self.ctrl.devices.values()))
        self.cmd_t.close()  # l'appareil ne répond plus
        with self.assertRaises(TimeoutError):
            await self.ctrl.get_status(device, timeout=0.2, retries=2)

    async def test_garbage_ignored(self):
        self.ctrl.handle_datagram(b"not json", ("127.0.0.1", 1))
        self.ctrl.handle_datagram(b'{"msg": 3}', ("127.0.0.1", 1))
        self.assertEqual(self.ctrl.devices, {})


if __name__ == "__main__":
    unittest.main()
