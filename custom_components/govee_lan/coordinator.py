"""Coordinateur de mise à jour pour Govee LAN."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from ipaddress import IPv4Address

from homeassistant.components import network
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_MANUAL_IPS, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .protocol import GoveeDevice, GoveeLanController, GoveeState

_LOGGER = logging.getLogger(__name__)


class GoveeCoordinator(DataUpdateCoordinator[dict[str, GoveeState | None]]):
    """Redécouvre les appareils et relève leur état à intervalle régulier.

    ``data`` associe l'identifiant de chaque appareil à son dernier état, ou à
    ``None`` s'il n'a pas répondu (appareil éteint, hors réseau, LAN désactivé).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        controller: GoveeLanController,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.controller = controller
        self._manual_ips: list[str] = list(entry.options.get(CONF_MANUAL_IPS, []))

    async def _source_ips(self) -> list[str]:
        """Adresses IPv4 locales utilisables pour émettre le multicast."""
        try:
            addresses = await network.async_get_enabled_source_ips(self.hass)
        except Exception:  # noqa: BLE001 - jamais bloquant pour la découverte
            return []
        return [
            str(addr)
            for addr in addresses
            if isinstance(addr, IPv4Address) and not addr.is_loopback
        ]

    async def _poll(self, device: GoveeDevice) -> GoveeState | None:
        try:
            return await self.controller.get_status(device)
        except (TimeoutError, ConnectionError):
            _LOGGER.debug("%s (%s) ne répond pas", device.sku, device.ip)
            return None

    async def _async_update_data(self) -> dict[str, GoveeState | None]:
        source_ips = await self._source_ips()

        # Le scan (≈2 s) tourne en parallèle de l'interrogation des appareils connus.
        scan_task = self.hass.async_create_task(
            self.controller.scan(source_ips, self._manual_ips)
        )
        known = list(self.controller.devices.values())
        states = await asyncio.gather(*(self._poll(dev) for dev in known))
        results: dict[str, GoveeState | None] = {
            dev.device_id: state for dev, state in zip(known, states, strict=True)
        }
        await scan_task

        # Appareils découverts pendant ce cycle.
        new_devices = [
            dev
            for dev in self.controller.devices.values()
            if dev.device_id not in results
        ]
        if new_devices:
            new_states = await asyncio.gather(*(self._poll(d) for d in new_devices))
            for dev, state in zip(new_devices, new_states, strict=True):
                results[dev.device_id] = state

        return results
