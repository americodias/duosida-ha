"""
Button platform for Duosida Local.

Buttons are entities that trigger an action when pressed.
Unlike switches, they don't have an on/off state - they just execute an action.

This integration provides:
1. Start Charging button - Send start command
2. Stop Charging button - Send stop command

These are alternatives to the charging switch, useful for:
- Automation triggers
- Scripts
- Dashboard buttons
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up Duosida button entities."""
    coordinator: DuosidaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        COORDINATOR
    ]
    device_id = entry.data[CONF_DEVICE_ID]

    async_add_entities(
        [
            DuosidaStartButton(coordinator, device_id),
            DuosidaStopButton(coordinator, device_id),
        ]
    )


class DuosidaStartButton(DuosidaEntity, ButtonEntity):
    """
    Button to start charging.

    When pressed, sends the start charging command to the charger.
    The charger will begin charging if a vehicle is connected.

    This is the primary way to start charging. Use the Charging binary
    sensor to monitor the current charging status.
    """

    _attr_name = "Start Charging"
    _attr_icon = "mdi:play"
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the start button."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_start"

    async def async_press(self) -> None:
        """
        Handle the button press.

        This method is called when the user presses the button
        in the UI or when triggered by an automation.
        """
        await self.coordinator.async_start_charging()


class DuosidaStopButton(DuosidaEntity, ButtonEntity):
    """
    Button to stop charging.

    When pressed, sends the stop charging command to the charger.
    Charging will stop immediately.

    This is the primary way to stop charging. Use the Charging binary
    sensor to monitor the current charging status.
    """

    _attr_name = "Stop Charging"
    _attr_icon = "mdi:stop"
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the stop button."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_stop"

    async def async_press(self) -> None:
        """
        Handle the button press.

        Stops any active charging session.
        """
        await self.coordinator.async_stop_charging()
