"""Config flow for the Duosida EV Charger integration."""

from __future__ import annotations

import logging
import math
import socket
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
import voluptuous as vol

from duosida_ev import ChargerInfo, DuosidaError, discover_chargers, identify_charger

from .const import (
    CONF_DEVICE_ID,
    CONF_SWITCH_DEBOUNCE,
    DEFAULT_PORT,
    DEFAULT_SWITCH_DEBOUNCE,
    DOMAIN,
)
from .coordinator import get_reconnect_gate

_LOGGER = logging.getLogger(__name__)

DISCOVERY_TIMEOUT = 5.0
IDENTIFY_TIMEOUT = 5.0


class ChargerBusy(Exception):
    """The charger may still hold a session that was just closed."""

    def __init__(self, wait: float) -> None:
        """Store how long to wait."""
        super().__init__(f"try again in {wait:.0f} s")
        self.placeholders = {"seconds": str(math.ceil(wait))}


class DuosidaLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Duosida charger, found by discovery or entered by hand.

    The charger accepts a single TCP session. A charger that already has an
    entry is never contacted (compared by host and by resolved address). The
    flow respects the reconnect gate, and each short identification arms it,
    so neither the flow nor the setup that follows connects while the charger
    may still hold a session.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered: dict[str, ChargerInfo] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether to discover chargers or enter one by hand."""
        if user_input is not None:
            if user_input["discovery"]:
                return await self.async_step_discovery()
            return await self.async_step_manual()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("discovery", default=True): bool}),
        )

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find chargers on the network and let the user pick one."""
        if user_input is not None:
            host = user_input["device"]
            info = self._discovered[host]
            await self.async_set_unique_id(info.device_id)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
            return self._async_create(host, DEFAULT_PORT, info)

        try:
            self._discovered = await self._async_discover()
        except ChargerBusy as err:
            return self.async_abort(
                reason="busy", description_placeholders=err.placeholders
            )
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(
                        {
                            host: f"{info.model or 'Duosida'} ({host})"
                            for host, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def _async_discover(self) -> dict[str, ChargerInfo]:
        """Discover chargers and identify the ones not configured yet.

        Raises ChargerBusy if nothing could be identified because a charger
        may still hold a recent session.
        """
        try:
            found = await discover_chargers(DISCOVERY_TIMEOUT, identify=False)
        except DuosidaError as err:
            _LOGGER.debug("Discovery failed: %s", err)
            return {}
        configured = await self._async_configured_addresses()
        discovered: dict[str, ChargerInfo] = {}
        busy: ChargerBusy | None = None
        for charger in found:
            if charger.host in configured:
                continue
            try:
                info = await self._async_identify(charger.host, DEFAULT_PORT)
            except ChargerBusy as err:
                busy = err
                continue
            if info is not None:
                discovered[charger.host] = info
        if not discovered and busy is not None:
            raise busy
        return discovered

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a charger from its address."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            configured = await self._async_configured_addresses()
            if host in configured or await self._async_resolve(host) in configured:
                return self.async_abort(reason="already_configured")
            try:
                info = await self._async_identify(host, port)
            except ChargerBusy as err:
                errors["base"] = "busy"
                placeholders = err.placeholders
            else:
                if info is None:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(info.device_id)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    return self._async_create(host, port, info)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_identify(self, host: str, port: int) -> ChargerInfo | None:
        """Read the charger's identity over a short session.

        Returns None if the charger doesn't answer; raises ChargerBusy instead
        of connecting while the reconnect gate is closed.
        """
        gate = get_reconnect_gate(self.hass, host)
        if (wait := gate.wait_time()) > 0:
            raise ChargerBusy(wait)
        try:
            return await identify_charger(host, port, timeout=IDENTIFY_TIMEOUT)
        except DuosidaError as err:
            _LOGGER.debug("Cannot identify the charger at %s:%s: %s", host, port, err)
            return None
        finally:
            gate.closed()

    async def _async_configured_addresses(self) -> set[str]:
        """Hosts of the existing entries, plus the addresses they resolve to."""
        hosts = {entry.data[CONF_HOST] for entry in self._async_current_entries()}
        addresses = {await self._async_resolve(host) for host in hosts}
        return hosts | {address for address in addresses if address is not None}

    async def _async_resolve(self, host: str) -> str | None:
        try:
            return await self.hass.async_add_executor_job(socket.gethostbyname, host)
        except OSError:
            return None

    @callback
    def _async_create(
        self, host: str, port: int, info: ChargerInfo
    ) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"Duosida {host}",
            data={CONF_HOST: host, CONF_PORT: port, CONF_DEVICE_ID: info.device_id},
        )


class OptionsFlowHandler(OptionsFlow):
    """Options: the charging switch debounce."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(
            CONF_SWITCH_DEBOUNCE, DEFAULT_SWITCH_DEBOUNCE
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SWITCH_DEBOUNCE, default=current): vol.All(
                        int, vol.Range(min=5, max=120)
                    )
                }
            ),
        )
