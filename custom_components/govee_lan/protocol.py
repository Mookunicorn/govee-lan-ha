"""Client asyncio pour l'API LAN de Govee (JSON sur UDP).

Ce module ne dépend pas de Home Assistant afin de pouvoir être testé seul.

Protocole (https://app-h5.govee.com/user-manual/wlan-guide) :
  * découverte : multicast vers 239.255.255.250:4001, les appareils répondent
    en unicast sur le port 4002 du client ;
  * contrôle   : commandes UDP envoyées à l'IP de l'appareil, port 4003 ;
  * statut     : la commande ``devStatus`` est envoyée sur 4003, la réponse
    arrive sur le port 4002 du client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

MULTICAST_ADDR = "239.255.255.250"
SCAN_PORT = 4001
LISTEN_PORT = 4002
COMMAND_PORT = 4003

SCAN_MESSAGE = {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}


@dataclass
class GoveeDevice:
    """Appareil Govee découvert sur le réseau local."""

    device_id: str
    ip: str
    sku: str
    wifi_version: str | None = None
    ble_version: str | None = None


@dataclass
class GoveeState:
    """État d'un appareil tel que renvoyé par ``devStatus``."""

    on: bool
    brightness: int  # 1-100 (%)
    rgb: tuple[int, int, int]
    kelvin: int  # 0 si l'appareil est en mode couleur


DiscoveryCallback = Callable[[GoveeDevice, bool], None]


class _Protocol(asyncio.DatagramProtocol):
    """Pont entre asyncio et le contrôleur."""

    def __init__(self, controller: GoveeLanController) -> None:
        self._controller = controller

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._controller.handle_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Erreur UDP : %s", exc)


class GoveeLanController:
    """Découvre et pilote les appareils Govee via l'API LAN."""

    def __init__(
        self,
        *,
        listen_port: int = LISTEN_PORT,
        scan_port: int = SCAN_PORT,
        command_port: int = COMMAND_PORT,
        multicast_addr: str = MULTICAST_ADDR,
    ) -> None:
        self._listen_port = listen_port
        self._scan_port = scan_port
        self._command_port = command_port
        self._multicast_addr = multicast_addr
        self._transport: asyncio.DatagramTransport | None = None
        self._waiters: dict[str, list[asyncio.Future[GoveeState]]] = {}
        self._listeners: list[DiscoveryCallback] = []
        self.devices: dict[str, GoveeDevice] = {}

    # ------------------------------------------------------------------ cycle de vie

    async def start(self) -> None:
        """Ouvre le socket d'écoute (port 4002). Lève OSError si occupé."""
        if self._transport is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Volontairement sans SO_REUSEADDR : si un autre logiciel écoute
            # déjà sur 4002, mieux vaut une erreur claire qu'une réception
            # aléatoire des réponses.
            sock.bind(("0.0.0.0", self._listen_port))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), sock=sock
        )

    async def stop(self) -> None:
        """Ferme le socket et annule les requêtes en attente."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        for futures in self._waiters.values():
            for fut in futures:
                if not fut.done():
                    fut.cancel()
        self._waiters.clear()

    def add_discovery_listener(self, callback: DiscoveryCallback) -> Callable[[], None]:
        """Appelle ``callback(device, is_new)`` à chaque réponse de scan."""
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    # ------------------------------------------------------------------ réception

    def handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Traite un datagramme reçu sur le port d'écoute."""
        try:
            payload = json.loads(data.decode("utf-8"))
            msg = payload["msg"]
            cmd = msg["cmd"]
            body = msg.get("data") or {}
        except (ValueError, KeyError, TypeError):
            _LOGGER.debug("Datagramme ignoré de %s : %r", addr[0], data[:200])
            return

        if cmd == "scan":
            self._handle_scan(body, addr[0])
        elif cmd == "devStatus":
            self._handle_status(body, addr[0])

    def _handle_scan(self, body: dict[str, Any], source_ip: str) -> None:
        device_id = body.get("device")
        if not device_id:
            return
        ip = body.get("ip") or source_ip
        existing = self.devices.get(device_id)
        is_new = existing is None
        if existing is None:
            device = GoveeDevice(
                device_id=device_id,
                ip=ip,
                sku=str(body.get("sku", "unknown")),
                wifi_version=body.get("wifiVersionSoft"),
                ble_version=body.get("bleVersionSoft"),
            )
            self.devices[device_id] = device
        else:
            existing.ip = ip  # l'IP peut changer (DHCP)
            device = existing
        for callback in list(self._listeners):
            callback(device, is_new)

    def _handle_status(self, body: dict[str, Any], source_ip: str) -> None:
        waiters = self._waiters.get(source_ip)
        if not waiters:
            return
        try:
            color = body.get("color") or {}
            state = GoveeState(
                on=bool(body.get("onOff", 0)),
                brightness=max(1, min(100, int(body.get("brightness", 100)))),
                rgb=(
                    int(color.get("r", 255)),
                    int(color.get("g", 255)),
                    int(color.get("b", 255)),
                ),
                kelvin=int(body.get("colorTemInKelvin", 0) or 0),
            )
        except (TypeError, ValueError):
            return
        for fut in waiters:
            if not fut.done():
                fut.set_result(state)

    # ------------------------------------------------------------------ envoi

    def _send(self, ip: str, port: int, message: dict[str, Any] | bytes) -> None:
        if self._transport is None:
            raise ConnectionError("Le contrôleur Govee n'est pas démarré")
        payload = message if isinstance(message, bytes) else json.dumps(message).encode()
        self._transport.sendto(payload, (ip, port))

    def _send_multicast(self, source_ip: str | None, payload: bytes) -> None:
        """Envoie la requête de scan en multicast depuis une interface donnée."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if source_ip:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(source_ip),
                )
                sock.bind((source_ip, 0))
            sock.sendto(payload, (self._multicast_addr, self._scan_port))
        except OSError as err:
            _LOGGER.debug("Scan multicast impossible depuis %s : %s", source_ip, err)
        finally:
            sock.close()

    async def scan(
        self,
        source_ips: Iterable[str] = (),
        extra_ips: Iterable[str] = (),
        wait: float = 2.0,
    ) -> None:
        """Lance une découverte puis attend ``wait`` secondes les réponses.

        ``source_ips`` : adresses locales depuis lesquelles émettre le multicast
        (une par interface). ``extra_ips`` : IP d'appareils interrogés en unicast
        (utile quand le multicast est filtré ou entre sous-réseaux).
        """
        payload = json.dumps(SCAN_MESSAGE).encode()
        sources = list(source_ips)
        if sources:
            for source_ip in sources:
                self._send_multicast(source_ip, payload)
        else:
            self._send_multicast(None, payload)
        for ip in extra_ips:
            self._send(ip, self._scan_port, payload)
        await asyncio.sleep(wait)

    async def get_status(
        self, device: GoveeDevice, timeout: float = 1.5, retries: int = 2
    ) -> GoveeState:
        """Interroge l'état de l'appareil. Lève TimeoutError sans réponse."""
        loop = asyncio.get_running_loop()
        request = {"msg": {"cmd": "devStatus", "data": {}}}
        for _ in range(retries):
            future: asyncio.Future[GoveeState] = loop.create_future()
            self._waiters.setdefault(device.ip, []).append(future)
            try:
                self._send(device.ip, self._command_port, request)
                return await asyncio.wait_for(future, timeout)
            except TimeoutError:
                continue
            finally:
                waiters = self._waiters.get(device.ip, [])
                if future in waiters:
                    waiters.remove(future)
        raise TimeoutError(f"Pas de réponse de {device.ip}")

    # ------------------------------------------------------------------ commandes

    def _command(self, device: GoveeDevice, cmd: str, data: dict[str, Any]) -> None:
        self._send(device.ip, self._command_port, {"msg": {"cmd": cmd, "data": data}})

    async def turn(self, device: GoveeDevice, on: bool) -> None:
        self._command(device, "turn", {"value": 1 if on else 0})

    async def set_brightness(self, device: GoveeDevice, percent: int) -> None:
        self._command(device, "brightness", {"value": max(1, min(100, int(percent)))})

    async def set_rgb(self, device: GoveeDevice, rgb: tuple[int, int, int]) -> None:
        r, g, b = (max(0, min(255, int(c))) for c in rgb)
        self._command(
            device,
            "colorwc",
            {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0},
        )

    async def set_color_temp(self, device: GoveeDevice, kelvin: int) -> None:
        self._command(
            device,
            "colorwc",
            {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": int(kelvin)},
        )
