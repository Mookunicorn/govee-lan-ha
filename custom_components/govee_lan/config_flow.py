"""Flux de configuration de Govee LAN."""

from __future__ import annotations

from ipaddress import IPv4Address, ip_address
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MANUAL_IPS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .protocol import GoveeLanController


def parse_ips(raw: str) -> list[str]:
    """Convertit "192.168.1.10, 192.168.1.11" en liste d'IPv4 valides.

    Lève ValueError si une entrée n'est pas une adresse IPv4.
    """
    result: list[str] = []
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        address = ip_address(part)
        if not isinstance(address, IPv4Address):
            raise ValueError(part)
        if str(address) not in result:
            result.append(str(address))
    return result


async def _discover(hass: HomeAssistant, manual_ips: list[str]) -> int:
    """Lance un scan temporaire et renvoie le nombre d'appareils trouvés.

    Lève OSError si le port 4002 est indisponible.
    """
    controller = GoveeLanController()
    await controller.start()
    try:
        try:
            addresses = await network.async_get_enabled_source_ips(hass)
        except Exception:  # noqa: BLE001
            addresses = []
        sources = [
            str(a)
            for a in addresses
            if isinstance(a, IPv4Address) and not a.is_loopback
        ]
        await controller.scan(sources, manual_ips, wait=3.0)
        return len(controller.devices)
    finally:
        await controller.stop()


class GoveeLanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configuration initiale : scan du réseau local."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        defaults = {CONF_MANUAL_IPS: ""}

        if user_input is not None:
            defaults = user_input
            try:
                manual_ips = parse_ips(user_input.get(CONF_MANUAL_IPS, ""))
            except ValueError:
                errors[CONF_MANUAL_IPS] = "invalid_ip"
            else:
                try:
                    found = await _discover(self.hass, manual_ips)
                except OSError:
                    errors["base"] = "port_in_use"
                else:
                    if found == 0:
                        errors["base"] = "no_devices_found"
                    else:
                        await self.async_set_unique_id(DOMAIN)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title="Govee LAN",
                            data={},
                            options={
                                CONF_MANUAL_IPS: manual_ips,
                                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                            },
                        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MANUAL_IPS, default=defaults.get(CONF_MANUAL_IPS, "")
                    ): str
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return GoveeLanOptionsFlow()


class GoveeLanOptionsFlow(OptionsFlow):
    """Options : intervalle de sondage et IP manuelles."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self.config_entry.options

        if user_input is not None:
            try:
                manual_ips = parse_ips(user_input.get(CONF_MANUAL_IPS, ""))
            except ValueError:
                errors[CONF_MANUAL_IPS] = "invalid_ip"
            else:
                return self.async_create_entry(
                    data={
                        CONF_MANUAL_IPS: manual_ips,
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    }
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MANUAL_IPS,
                        default=", ".join(current.get(CONF_MANUAL_IPS, [])),
                    ): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            ),
            errors=errors,
        )
