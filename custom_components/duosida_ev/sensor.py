"""
Sensor platform for Duosida Local.

Sensors are read-only entities that display values from the charger.
This file defines all the sensors and how to get their values.

How sensors work in Home Assistant:
1. async_setup_entry() is called when the integration loads
2. We create sensor entities for each sensor type
3. The coordinator fetches data periodically
4. Each sensor reads its value from coordinator.data
5. Home Assistant displays the values in the UI

Sensor types:
- Measurement: Instantaneous values (voltage, current, temperature)
- Total Increasing: Cumulative values that only go up (energy register)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_ID, COORDINATOR, DOMAIN, STATUS_CODES
from .coordinator import DuosidaDataUpdateCoordinator
from .entity import DuosidaEntity


@dataclass(frozen=True)
class DuosidaSensorEntityDescription(SensorEntityDescription):
    """
    Describes a Duosida sensor entity.

    Extends SensorEntityDescription with a value_fn that extracts
    the sensor's value from the coordinator data.

    Attributes:
        value_fn: Function that takes coordinator data dict and returns the sensor value
    """

    value_fn: Callable[[dict[str, Any]], Any] | None = None


# Define all sensors
# Each tuple entry creates one sensor entity
# Order: User preference for logical display flow
SENSORS: tuple[DuosidaSensorEntityDescription, ...] = (
    # Status sensor - shows the current charger state as text (FIRST - most important)
    DuosidaSensorEntityDescription(
        key="state",
        name="Status",
        icon="mdi:ev-station",
        # Convert numeric status to readable text using STATUS_CODES dict
        value_fn=lambda data: STATUS_CODES.get(data.get("conn_status", 0), "Unknown"),
    ),
    # CP voltage - Control Pilot voltage level (actual reading from Field 9)
    # Used to determine vehicle state (12V=no vehicle, 9V=connected, 6V=charging)
    DuosidaSensorEntityDescription(
        key="cp_voltage",
        name="CP Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:ev-plug-type2",
        value_fn=lambda data: data.get("cp_voltage"),
    ),
    # Voltage sensor - Line voltage (L1)
    DuosidaSensorEntityDescription(
        key="voltage",
        name="Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("voltage"),
    ),
    # Voltage L2 - Second phase voltage (disabled by default for single-phase)
    DuosidaSensorEntityDescription(
        key="voltage_l2",
        name="Voltage L2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("voltage_l2"),
        entity_registry_enabled_default=False,
    ),
    # Voltage L3 - Third phase voltage
    DuosidaSensorEntityDescription(
        key="voltage_l3",
        name="Voltage L3",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("voltage_l3"),
        entity_registry_enabled_default=False,
    ),
    # Current sensor - Charging current (L1)
    DuosidaSensorEntityDescription(
        key="current",
        name="Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("current"),
    ),
    # Current L2 - Second phase current
    DuosidaSensorEntityDescription(
        key="current_l2",
        name="Current L2",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("current_l2"),
        entity_registry_enabled_default=False,
    ),
    # Current L3 - Third phase current
    DuosidaSensorEntityDescription(
        key="current_l3",
        name="Current L3",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("current_l3"),
        entity_registry_enabled_default=False,
    ),
    # Power sensor - Instantaneous power (voltage * current)
    DuosidaSensorEntityDescription(
        key="power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("power"),
    ),
    # Session energy - Energy used in current charging session (reported by charger)
    DuosidaSensorEntityDescription(
        key="session_energy",
        name="Session Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.get("session_energy"),
    ),
    # Session time - Duration of the current session in hours (from the
    # session start on the charger's clock, which the library keeps right)
    DuosidaSensorEntityDescription(
        key="session_time",
        name="Session Time",
        icon="mdi:timer",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            round(data.get("session_time", 0) / 60.0, 2)
            if data.get("session_time")
            else 0
        ),
    ),
    # Total energy - The charger's own energy register (always increasing,
    # survives sessions and Home Assistant restarts)
    DuosidaSensorEntityDescription(
        key="total_energy",
        name="Total Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
        value_fn=lambda data: data.get("total_energy"),
    ),
    # Temperature sensor - Charger station temperature
    DuosidaSensorEntityDescription(
        key="temperature",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("temperature_station"),
    ),
    # Device info sensors - diagnostic information
    DuosidaSensorEntityDescription(
        key="model",
        name="Model",
        icon="mdi:information",
        value_fn=lambda data: data.get("model"),
        entity_registry_enabled_default=False,
    ),
    DuosidaSensorEntityDescription(
        key="manufacturer",
        name="Manufacturer",
        icon="mdi:factory",
        value_fn=lambda data: data.get("manufacturer"),
        entity_registry_enabled_default=False,
    ),
    DuosidaSensorEntityDescription(
        key="firmware",
        name="Firmware",
        icon="mdi:chip",
        value_fn=lambda data: data.get("firmware"),
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up Duosida sensors.

    Called by Home Assistant when loading the sensor platform.

    Args:
        hass: Home Assistant instance
        entry: The config entry for this integration
        async_add_entities: Callback to add entities to HA
    """
    # Get the coordinator from storage
    coordinator: DuosidaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        COORDINATOR
    ]
    device_id = entry.data[CONF_DEVICE_ID]

    # Create all sensor entities
    # This is a generator expression that creates one DuosidaSensor
    # for each description in SENSORS
    async_add_entities(
        DuosidaSensor(coordinator, device_id, description) for description in SENSORS
    )


class DuosidaSensor(DuosidaEntity, SensorEntity):
    """
    Representation of a Duosida sensor.

    Inherits from:
    - DuosidaEntity: Provides device_info and coordinator connection
    - SensorEntity: Provides sensor-specific functionality
    """

    # Type hint for the entity description
    entity_description: DuosidaSensorEntityDescription

    def __init__(
        self,
        coordinator: DuosidaDataUpdateCoordinator,
        device_id: str,
        description: DuosidaSensorEntityDescription,
    ) -> None:
        """
        Initialize the sensor.

        Args:
            coordinator: The data update coordinator
            device_id: Device ID for unique entity ID generation
            description: Sensor description with metadata and value function
        """
        super().__init__(coordinator, device_id)

        # Store the description (contains name, icon, units, etc.)
        self.entity_description = description

        # Generate unique ID for this entity
        # This must be unique across all entities in HA
        # Format: {device_id}_{sensor_key}
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """
        Return the state of the sensor.

        This property is called by Home Assistant to get the current value.
        We use the value_fn from the description to extract the value
        from the coordinator's data.

        Returns:
            The sensor value, or None if no data available
        """
        if not self.coordinator.data:
            return None

        # Call the value function with the coordinator data
        # value_fn is guaranteed to be set for all our sensor descriptions
        if self.entity_description.value_fn is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
