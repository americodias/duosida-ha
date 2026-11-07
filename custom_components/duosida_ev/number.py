"""
Number platform for Duosida Local.

Number entities allow the user to set numeric values within a range.
They appear as sliders in the Home Assistant UI.

This integration provides:
1. Max Current - Set maximum charging current (6-32A)
2. LED Brightness - Set screen brightness (0, 1, 3)
3. Max Voltage - Set maximum working voltage (265-290V)
4. Min Voltage - Set minimum working voltage (70-110V)
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential
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
    """Set up Duosida number entities."""
    coordinator: DuosidaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        COORDINATOR
    ]
    device_id = entry.data[CONF_DEVICE_ID]

    async_add_entities(
        [
            DuosidaMaxCurrentNumber(coordinator, device_id),
            DuosidaLedBrightnessNumber(coordinator, device_id),
            DuosidaMaxVoltageNumber(coordinator, device_id),
            DuosidaMinVoltageNumber(coordinator, device_id),
        ]
    )


class DuosidaMaxCurrentNumber(DuosidaEntity, NumberEntity):
    """
    Number entity for setting max charging current.

    This controls how much current the charger will allow during charging.
    Range: 6A to 32A (depending on charger model)

    Use cases:
    - Reduce current during peak electricity hours
    - Match your electrical panel capacity
    - Slow charge overnight to preserve battery health
    """

    # Entity attributes
    _attr_name = "Max Charging Current"
    _attr_icon = "mdi:current-ac"

    # Number range settings
    _attr_native_min_value = 6  # Minimum 6 amps
    _attr_native_max_value = 32  # Maximum 32 amps
    _attr_native_step = 1  # 1 amp increments
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.SLIDER  # Show as slider in UI

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_max_current_control"

    @property
    def native_value(self) -> float | None:
        """
        Return the current max current setting.

        Read back from the charger, or the last value written from Home
        Assistant if that is newer.

        Returns None (unknown) until the charger answers a read or Home
        Assistant sets a value in this run. A value stored by an earlier
        run is not shown, because it may be stale.
        """
        return self.coordinator.get_setting("max_current")

    async def async_set_native_value(self, value: float) -> None:
        """
        Set the max current.

        Called when user moves the slider in the UI.

        Args:
            value: The new current value in amps
        """
        await self.coordinator.async_set_max_current(int(value))


class DuosidaLedBrightnessNumber(DuosidaEntity, NumberEntity):
    """
    Number entity for setting LED/screen brightness.

    The Duosida charger has an LED screen that can be dimmed.
    Valid values: 0 (off), 1 (low), 3 (high)

    Note: Value 2 is not supported by the charger, so we map
    the slider value 2 to 1 (low).

    This entity is disabled by default since it's a less common setting.
    """

    _attr_name = "LED Brightness"
    _attr_icon = "mdi:brightness-6"

    # Slider range (0-3, but 2 will be mapped to 1)
    _attr_native_min_value = 0
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_led_brightness"

    @property
    def native_value(self) -> float | None:
        """
        Return the current brightness level.

        The charger doesn't report this value reliably yet, so this is the
        last value written from Home Assistant.

        Returns None if the user hasn't set this value yet, indicating
        the state is unknown. This prevents the integration from making
        assumptions about the charger's configuration.
        """
        return self.coordinator.get_setting("led_brightness")

    async def async_set_native_value(self, value: float) -> None:
        """
        Set LED brightness.

        Valid charger values are 0, 1, 3 (not 2).
        We map slider value 2 to 1 for a smooth slider experience.
        The setting is persisted to HA storage.

        Args:
            value: Slider value (0, 1, 2, or 3)
        """
        int_value = int(value)

        # Map invalid value 2 to valid value 1
        if int_value == 2:
            int_value = 1

        await self.coordinator.async_set_led_brightness(int_value)


class DuosidaMaxVoltageNumber(DuosidaEntity, NumberEntity):
    """
    Number entity for setting maximum working voltage.

    Sets the upper voltage limit at which the charger will operate.
    If the grid voltage exceeds this value, charging will stop.
    Range: 265V to 290V

    Use cases:
    - Protect vehicle from overvoltage
    - Comply with local grid regulations
    - Prevent charging during voltage spikes

    This entity is disabled by default as most users don't need to adjust it.
    """

    _attr_name = "Max Voltage"
    _attr_icon = "mdi:flash-triangle"

    # Voltage range settings (per duosida-ev library)
    _attr_native_min_value = 265
    _attr_native_max_value = 290
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_mode = NumberMode.SLIDER
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_max_voltage"

    @property
    def native_value(self) -> float | None:
        """
        Return the current max voltage setting.

        The charger doesn't report this value reliably yet, so this is the
        last value written from Home Assistant.

        Returns None if the user hasn't set this value yet, indicating
        the state is unknown.
        """
        return self.coordinator.get_setting("max_voltage")

    async def async_set_native_value(self, value: float) -> None:
        """
        Set maximum voltage.

        Args:
            value: Voltage in volts (265-290V)
        """
        await self.coordinator.async_set_max_voltage(int(value))


class DuosidaMinVoltageNumber(DuosidaEntity, NumberEntity):
    """
    Number entity for setting minimum working voltage.

    Sets the lower voltage limit at which the charger will operate.
    If the grid voltage falls below this value, charging will stop.
    Range: 70V to 110V

    Use cases:
    - Prevent charging during brownouts
    - Protect charger from undervoltage
    - Comply with local grid regulations

    This entity is disabled by default as most users don't need to adjust it.
    """

    _attr_name = "Min Voltage"
    _attr_icon = "mdi:flash-triangle-outline"

    # Voltage range settings (per duosida-ev library)
    _attr_native_min_value = 70
    _attr_native_max_value = 110
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_mode = NumberMode.SLIDER
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_min_voltage"

    @property
    def native_value(self) -> float | None:
        """
        Return the current min voltage setting.

        The charger doesn't report this value reliably yet, so this is the
        last value written from Home Assistant.

        Returns None if the user hasn't set this value yet, indicating
        the state is unknown.
        """
        return self.coordinator.get_setting("min_voltage")

    async def async_set_native_value(self, value: float) -> None:
        """
        Set minimum voltage.

        Args:
            value: Voltage in volts (70-110V)
        """
        await self.coordinator.async_set_min_voltage(int(value))
