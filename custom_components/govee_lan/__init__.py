"""Intégration Govee LAN pour Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import GoveeCoordinator
from .protocol import GoveeLanController

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LIGHT]

type GoveeConfigEntry = ConfigEntry[GoveeCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Démarre le contrôleur UDP et charge les plateformes."""
    controller = GoveeLanController()
    try:
        await controller.start()
    except OSError as err:
        raise ConfigEntryNotReady(
            "Impossible d'écouter sur le port UDP 4002 (déjà utilisé par un autre "
            f"logiciel ?) : {err}"
        ) from err

    coordinator = GoveeCoordinator(hass, entry, controller)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await controller.stop()
        raise

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Décharge les plateformes et libère le port 4002."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.controller.stop()
    return unloaded


async def _async_reload_on_update(hass: HomeAssistant, entry: GoveeConfigEntry) -> None:
    """Recharge l'entrée quand les options changent."""
    await hass.config_entries.async_reload(entry.entry_id)
