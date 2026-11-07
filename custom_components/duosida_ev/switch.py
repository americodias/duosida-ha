"""
Switch platform for Duosida EV Charger.

Switches are on/off entities that control something on the device.
Unlike sensors which are read-only, switches can be toggled.

This integration provides:
1. Charging Switch - Start/stop charging (with debounce to prevent state bounce)
2. Direct Mode Switch - Enable/disable plug-and-charge mode
3. Stop on Disconnect Switch - Stop session when vehicle disconnects
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_ID, COORDINATOR, DOMAIN
from .coordinator import DuosidaDataUpdateCoordinator
from .entity import DuosidaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up Duosida switches.

    Args:
        hass: Home Assistant instance
        entry: Config entry for this integration
        async_add_entities: Callback to add entities
    """
    coordinator: DuosidaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        COORDINATOR
    ]
    device_id = entry.data[CONF_DEVICE_ID]

    async_add_entities(
        [
            DuosidaChargingSwitch(coordinator, device_id),
            DuosidaDirectModeSwitch(coordinator, device_id),
            DuosidaStopOnDisconnectSwitch(coordinator, device_id),
        ]
    )


class DuosidaChargingSwitch(DuosidaEntity, SwitchEntity):
    """
    Switch to control charging with debounce logic.

    When turned ON: Sends start charging command
    When turned OFF: Sends stop charging command

    The switch uses debounce logic to prevent state bouncing:
    - After sending a command, the switch shows the optimistic state
    - Coordinator updates are ignored for the configured debounce period
    - After the debounce period, the switch syncs with the actual charger state
    - Debounce time is configurable via integration options (default: 30 seconds)

    This prevents the confusing flip-flop that occurs because the charger
    takes time to transition between states (e.g., Available → Preparing → Charging).
    """

    _attr_name = "Charging"
    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the charging switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_charging"
        # Track when we last sent a command for debounce
        self._last_command_time: float | None = None
        # Track the optimistic state we want to show during debounce
        self._optimistic_state: bool | None = None

    @property
    def is_on(self) -> bool:
        """
        Return true if charging is active.

        Uses debounce logic: if we recently sent a command, return the
        optimistic state instead of the actual charger state. This prevents
        the UI from bouncing during state transitions.

        Returns:
            True if currently charging (or optimistically charging), False otherwise
        """
        # Check if we're in the debounce window
        if self._last_command_time is not None and self._optimistic_state is not None:
            elapsed = time.monotonic() - self._last_command_time
            if elapsed < self.coordinator.switch_debounce:
                # Still in debounce window, return optimistic state
                return self._optimistic_state
            else:
                # Debounce window expired, clear the optimistic state
                self._last_command_time = None
                self._optimistic_state = None

        # Return actual state from coordinator
        if not self.coordinator.data:
            return False

        # conn_status == 2 means charging
        return self.coordinator.data.get("conn_status") == 2

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Start charging.

        Sets optimistic state and starts debounce timer before sending command.
        """
        # Set optimistic state before sending command
        self._optimistic_state = True
        self._last_command_time = time.monotonic()

        # Update UI immediately with optimistic state
        self.async_write_ha_state()

        # Send the actual command
        await self.coordinator.async_start_charging()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Stop charging.

        Sets optimistic state and starts debounce timer before sending command.
        """
        # Set optimistic state before sending command
        self._optimistic_state = False
        self._last_command_time = time.monotonic()

        # Update UI immediately with optimistic state
        self.async_write_ha_state()

        # Send the actual command
        await self.coordinator.async_stop_charging()


class DuosidaDirectModeSwitch(DuosidaEntity, SwitchEntity):
    """
    Switch to control direct work mode (plug and charge).

    When enabled: Charging starts automatically when a vehicle is plugged in
    When disabled: User must manually start charging

    This switch is disabled by default as it's a less common setting.
    Users can enable it in the entity settings.
    """

    _attr_name = "Direct Mode"
    _attr_icon = "mdi:lightning-bolt"
    _attr_entity_registry_enabled_default = False  # Hidden by default

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the direct mode switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_direct_mode"

    @property
    def is_on(self) -> bool | None:
        """
        Return true if direct mode is enabled, None if unknown.

        The charger doesn't report this value reliably yet, so this is the
        last value written from Home Assistant.

        Returns None if the user hasn't set this value yet, indicating
        the state is unknown. This prevents the integration from making
        assumptions about the charger's configuration.
        """
        return self.coordinator.get_setting("direct_mode")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Enable direct mode.

        After enabling, charging will start automatically when
        a vehicle is connected. The setting is persisted to HA storage.
        """
        await self.coordinator.async_set_direct_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Disable direct mode.

        After disabling, charging must be started manually.
        The setting is persisted to HA storage.
        """
        await self.coordinator.async_set_direct_mode(False)


class DuosidaStopOnDisconnectSwitch(DuosidaEntity, SwitchEntity):
    """
    Switch to control stop transaction on EV disconnect.

    When enabled: Transaction stops automatically when vehicle disconnects
    When disabled: Transaction remains open after vehicle disconnects

    This is typically enabled by default for most use cases.
    """

    _attr_name = "Stop Session on Vehicle Disconnect"
    _attr_icon = "mdi:connection"
    _attr_entity_registry_enabled_default = False  # Hidden by default

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the stop on disconnect switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_stop_on_disconnect"

    @property
    def is_on(self) -> bool | None:
        """
        Return true if stop on disconnect is enabled, None if unknown.

        The charger doesn't report this value reliably yet, so this is the
        last value written from Home Assistant.

        Returns None if the user hasn't set this value yet, indicating
        the state is unknown. This prevents the integration from making
        assumptions about the charger's configuration.
        """
        return self.coordinator.get_setting("stop_on_disconnect")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Enable stop on disconnect.

        When enabled, the charging transaction will automatically stop
        when the vehicle is disconnected from the cable.
        """
        await self.coordinator.async_set_stop_on_disconnect(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Disable stop on disconnect.

        When disabled, the transaction remains open even after
        the vehicle disconnects.
        """
        await self.coordinator.async_set_stop_on_disconnect(False)
