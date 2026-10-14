"""Plateforme light de Govee LAN."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GoveeConfigEntry
from .const import DOMAIN, MAX_KELVIN, MIN_KELVIN
from .coordinator import GoveeCoordinator
from .protocol import GoveeDevice, GoveeState

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée une lumière par appareil, y compris ceux découverts plus tard."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        new = [
            GoveeLight(coordinator, device)
            for device_id, device in coordinator.controller.devices.items()
            if device_id not in known
        ]
        if new:
            known.update(light.device.device_id for light in new)
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class GoveeLight(CoordinatorEntity[GoveeCoordinator], LightEntity):
    """Lumière Govee pilotée via l'API LAN."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id = device.device_id
        suffix = device.device_id.replace(":", "")[-4:].upper()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="Govee",
            model=device.sku,
            name=f"Govee {device.sku} {suffix}",
            sw_version=device.wifi_version,
        )

    @property
    def _state(self) -> GoveeState | None:
        return (self.coordinator.data or {}).get(self.device.device_id)

    @property
    def available(self) -> bool:
        return self._state is not None

    @property
    def is_on(self) -> bool | None:
        state = self._state
        return None if state is None else state.on

    @property
    def brightness(self) -> int | None:
        state = self._state
        return None if state is None else max(1, round(state.brightness * 255 / 100))

    @property
    def color_mode(self) -> ColorMode:
        state = self._state
        if state is not None and state.kelvin > 0:
            return ColorMode.COLOR_TEMP
        return ColorMode.RGB

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        state = self._state
        return None if state is None else state.rgb

    @property
    def color_temp_kelvin(self) -> int | None:
        state = self._state
        if state is None or state.kelvin <= 0:
            return None
        return max(MIN_KELVIN, min(MAX_KELVIN, state.kelvin))

    # ------------------------------------------------------------------ commandes

    def _apply(self, **changes: Any) -> None:
        """Met à jour l'état local sans attendre le prochain sondage."""
        state = self._state
        if state is None:
            return
        data = dict(self.coordinator.data)
        data[self.device.device_id] = dataclasses.replace(state, **changes)
        self.coordinator.async_set_updated_data(data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        controller = self.coordinator.controller
        device = self.device
        changes: dict[str, Any] = {"on": True}

        await controller.turn(device, True)

        if ATTR_BRIGHTNESS in kwargs:
            percent = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
            await controller.set_brightness(device, percent)
            changes["brightness"] = percent

        if ATTR_RGB_COLOR in kwargs:
            rgb = tuple(int(c) for c in kwargs[ATTR_RGB_COLOR])
            await controller.set_rgb(device, rgb)  # type: ignore[arg-type]
            changes.update(rgb=rgb, kelvin=0)
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            await controller.set_color_temp(device, kelvin)
            changes["kelvin"] = kelvin

        self._apply(**changes)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.controller.turn(self.device, False)
        self._apply(on=False)
