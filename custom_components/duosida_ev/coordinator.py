"""Data update coordinator for the Duosida EV Charger integration.

The coordinator keeps one persistent session with the charger. The charger
pushes its status about every 10 s; each new status updates all entities.

The periodic refresh is a watchdog. While pushes arrive it never runs (each
push resets its timer). When the session is down it reconnects, and when the
session stays silent it closes it so the next refresh can reconnect.

The charger accepts a single session, keeps it busy for a while after any
close and refuses fast reconnects. ``ReconnectGate`` (one per host, shared
with the config flow) decides when the next connection may be opened.

Settings are written to the charger, which confirms or rejects each write.
The maximum current is also read back from the charger (at connect and every
few minutes). The other settings are not read back reliably yet, so the
integration remembers the last value written from Home Assistant. It never
writes settings on its own (for example on startup).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from duosida_ev import (
    ChargerConfiguration,
    ChargerStatus,
    CommandRejectedError,
    DuosidaClient,
    DuosidaError,
)

from .const import (
    CONFIGURATION_REFRESH_INTERVAL,
    DOMAIN,
    RECONNECT_BACKOFF,
    RECONNECT_GATES,
    SESSION_RELEASE_DELAY,
    SILENT_REFRESHES_BEFORE_RECONNECT,
    STATUS_TIMEOUT,
    WATCHDOG_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass
class ReconnectGate:
    """When the next connection to one charger may be opened."""

    failures: int = 0
    next_attempt: float = 0.0

    def wait_time(self) -> float:
        """Seconds until the next attempt is allowed."""
        return max(0.0, self.next_attempt - time.monotonic())

    def failed(self) -> int:
        """Record a failed attempt and return the delay before the next one."""
        delay = RECONNECT_BACKOFF[min(self.failures, len(RECONNECT_BACKOFF) - 1)]
        self.failures += 1
        self.next_attempt = max(self.next_attempt, time.monotonic() + delay)
        return delay

    def closed(self) -> None:
        """A session just ended; the charger keeps it busy for a while."""
        self.next_attempt = max(
            self.next_attempt, time.monotonic() + SESSION_RELEASE_DELAY
        )

    def succeeded(self) -> None:
        """A session delivered a status: reset the backoff."""
        self.failures = 0
        self.next_attempt = 0.0


def get_reconnect_gate(hass: HomeAssistant, host: str) -> ReconnectGate:
    """The gate for a charger, kept across setups, reloads and config flows."""
    gates: dict[str, ReconnectGate] = hass.data.setdefault(DOMAIN, {}).setdefault(
        RECONNECT_GATES, {}
    )
    return gates.setdefault(host, ReconnectGate())


class DuosidaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keep one session with the charger and share its pushed status."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: DuosidaClient,
        device_id: str,
        switch_debounce: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=WATCHDOG_INTERVAL),
        )
        self.client = client
        self.switch_debounce = switch_debounce
        self._gate = get_reconnect_gate(hass, client.host)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{device_id}"
        )
        # Unknown keys (such as the 1.0 energy integration state) are kept as
        # they are, so going back to an older version loses nothing.
        self._stored_settings: dict[str, Any] = {}
        self._configuration: ChargerConfiguration | None = None
        self._configuration_read_at: float | None = None
        self._configuration_attempted_at: float | None = None
        self._written_at: dict[str, float] = {}
        # The client keeps its last status after a disconnect and notifies on
        # every change; only a status object not seen before is published.
        self._last_status: ChargerStatus | None = None
        self._silent_refreshes = 0
        self._remove_listener = client.add_listener(self._handle_client_update)

    # ------------------------------------------------------------ lifecycle

    async def async_load_stored_settings(self) -> None:
        """Load the settings remembered from earlier writes."""
        self._stored_settings = await self._store.async_load() or {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Watchdog: reconnect if needed and return a current status."""
        reconnected = False
        if not self.client.connected:
            await self._async_connect()
            reconnected = True
        try:
            if reconnected:
                # A status from before the reconnect would look current.
                status = await self.client.wait_for_status(timeout=STATUS_TIMEOUT)
            else:
                status = await self.client.get_status(
                    max_age=WATCHDOG_INTERVAL, timeout=STATUS_TIMEOUT
                )
        except DuosidaError as err:
            if reconnected:
                self._gate.failed()
            await self._async_handle_silence()
            raise UpdateFailed(f"No status from the charger: {err}") from err
        self._gate.succeeded()
        self._silent_refreshes = 0
        self._last_status = status
        if self._configuration_due():
            await self._async_refresh_configuration()
        return self._build_data(status)

    async def _async_connect(self) -> None:
        if (wait := self._gate.wait_time()) > 0:
            raise UpdateFailed(
                f"Waiting {wait:.0f} s before reconnecting to the charger"
            )
        try:
            await self.client.connect()
        except DuosidaError as err:
            delay = self._gate.failed()
            raise UpdateFailed(
                f"Cannot connect to the charger: {err} (next attempt in {delay} s)"
            ) from err
        self._configuration_attempted_at = None

    async def _async_handle_silence(self) -> None:
        """Close a session that stays connected but sends no status.

        Keepalives can hold a session open after the charger stopped pushing
        status; closing it lets the next refresh reconnect.
        """
        if not self.client.connected:
            return
        self._silent_refreshes += 1
        if self._silent_refreshes >= SILENT_REFRESHES_BEFORE_RECONNECT:
            self._silent_refreshes = 0
            await self.client.disconnect()

    @callback
    def _handle_client_update(self) -> None:
        """Called by the client on every status push or connection change."""
        if not self.client.connected:
            self._gate.closed()
            if self.data is not None and self.last_update_success:
                self.async_set_update_error(
                    UpdateFailed("Connection to the charger lost")
                )
            return
        status = self.client.status
        if status is None or status is self._last_status:
            return
        self._last_status = status
        self._silent_refreshes = 0
        self.async_set_updated_data(self._build_data(status))
        if self._configuration_due():
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_refresh_configuration_in_background(),
                f"{DOMAIN} configuration refresh",
            )

    async def async_disconnect(self) -> None:
        """Close the session cleanly. Safe to call more than once."""
        self._remove_listener()
        was_connected = self.client.connected
        await self.client.disconnect()
        # Only a session that was open keeps the charger busy; a setup retry
        # that never connected must not push the next attempt further away.
        if was_connected:
            self._gate.closed()

    async def async_shutdown(self) -> None:
        """Stop the watchdog and close the session (unload, failed setup)."""
        await super().async_shutdown()
        await self.async_disconnect()

    # ------------------------------------------------------------ data

    def _build_data(self, status: ChargerStatus) -> dict[str, Any]:
        """Map a status to the keys the entities have always used."""
        info = self.client.info
        duration = status.session_duration
        return {
            "conn_status": status.raw_state,
            "voltage": status.voltage,
            "current": status.current,
            "power": status.power,
            "total_energy": status.total_energy,
            "session_energy": status.session_energy,
            "session_time": int(duration.total_seconds() // 60) if duration else 0,
            "temperature_station": status.temperature,
            "cp_voltage": status.cp_voltage,
            # Not decoded by the library.
            "voltage_l2": None,
            "voltage_l3": None,
            "current_l2": None,
            "current_l3": None,
            "model": info.model if info else None,
            "manufacturer": info.manufacturer if info else None,
            "firmware": info.firmware if info else None,
        }

    def get_setting(self, key: str) -> Any:
        """Value of a charger setting: the newer of the last read and last write.

        Only the maximum current is read back (the other configuration fields
        are not verified yet); the rest is the last value written from HA.
        """
        if key == "max_current" and self._configuration is not None:
            read = self._configuration.max_current
            read_at = self._configuration_read_at or 0.0
            if read is not None and read_at > self._written_at.get(key, 0.0):
                return int(read)
        return self._stored_settings.get(key)

    def _configuration_due(self) -> bool:
        return (
            self._configuration_attempted_at is None
            or time.monotonic() - self._configuration_attempted_at
            > CONFIGURATION_REFRESH_INTERVAL
        )

    async def _async_refresh_configuration(self) -> None:
        self._configuration_attempted_at = time.monotonic()
        try:
            configuration = await self.client.get_configuration()
        except DuosidaError as err:
            _LOGGER.debug("Cannot read the charger configuration: %s", err)
            return
        self._configuration = configuration
        self._configuration_read_at = time.monotonic()

    async def _async_refresh_configuration_in_background(self) -> None:
        await self._async_refresh_configuration()
        self.async_update_listeners()

    # ------------------------------------------------------------ commands

    async def _async_command(
        self, description: str, command: Callable[[], Awaitable[Any]]
    ) -> None:
        if not self.client.connected:
            raise HomeAssistantError(
                f"Cannot {description}: the charger is not connected"
            )
        try:
            await command()
        except CommandRejectedError as err:
            raise HomeAssistantError(
                f"The charger rejected the request to {description} ({err.status_name})"
            ) from err
        except DuosidaError as err:
            raise HomeAssistantError(f"Cannot {description}: {err}") from err

    async def _async_set(
        self,
        key: str,
        value: Any,
        description: str,
        command: Callable[[], Awaitable[Any]],
    ) -> None:
        await self._async_command(description, command)
        # The charger's answer to the command is the confirmation; no read-back
        # (load-management automations write the current every minute or so).
        self._stored_settings[key] = value
        self._written_at[key] = time.monotonic()
        await self._store.async_save(self._stored_settings)
        self.async_update_listeners()

    async def async_start_charging(self) -> None:
        """Ask the charger to start charging; the state follows in a push."""
        await self._async_command("start charging", self.client.start_charging)

    async def async_stop_charging(self) -> None:
        """Ask the charger to stop charging; the state follows in a push."""
        await self._async_command("stop charging", self.client.stop_charging)

    async def async_set_max_current(self, current: int) -> None:
        """Set the maximum charging current (6-32 A)."""
        await self._async_set(
            "max_current",
            current,
            f"set the maximum current to {current} A",
            lambda: self.client.set_max_current(current),
        )

    async def async_set_led_brightness(self, level: int) -> None:
        """Set the LED brightness (0 off, 1 low, 3 high)."""
        await self._async_set(
            "led_brightness",
            level,
            f"set the LED brightness to {level}",
            lambda: self.client.set_led_brightness(level),
        )

    async def async_set_direct_mode(self, enabled: bool) -> None:
        """Turn plug-and-charge (direct work mode) on or off."""
        await self._async_set(
            "direct_mode",
            enabled,
            f"turn direct mode {'on' if enabled else 'off'}",
            lambda: self.client.set_direct_work_mode(enabled),
        )

    async def async_set_stop_on_disconnect(self, enabled: bool) -> None:
        """Choose whether the session ends when the vehicle unplugs."""
        await self._async_set(
            "stop_on_disconnect",
            enabled,
            f"turn stop on disconnect {'on' if enabled else 'off'}",
            lambda: self.client.set_stop_on_disconnect(enabled),
        )

    async def async_set_max_voltage(self, voltage: int) -> None:
        """Set the over-voltage limit (265-290 V)."""
        await self._async_set(
            "max_voltage",
            voltage,
            f"set the maximum voltage to {voltage} V",
            lambda: self.client.set_max_voltage(voltage),
        )

    async def async_set_min_voltage(self, voltage: int) -> None:
        """Set the under-voltage limit (70-110 V)."""
        await self._async_set(
            "min_voltage",
            voltage,
            f"set the minimum voltage to {voltage} V",
            lambda: self.client.set_min_voltage(voltage),
        )
