"""Tests for the sensors."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeDuosidaClient, entity


@pytest.mark.usefixtures("init_integration")
@pytest.mark.parametrize(
    ("key", "expected"),
    [
        pytest.param("status", "Charging", id="status"),
        pytest.param("voltage", "230.0", id="voltage"),
        pytest.param("current", "16.0", id="current"),
        pytest.param("power", "3680.0", id="power"),
        pytest.param("cp_voltage", "6.0", id="cp_voltage"),
        pytest.param("temperature", "35.0", id="temperature"),
        pytest.param("session_energy", "5.5", id="session_energy"),
        pytest.param("session_time", "1.5", id="session_time_hours"),
        pytest.param("total_energy", "527.4", id="total_energy_register"),
    ],
)
async def test_sensor_values(hass: HomeAssistant, key: str, expected: str) -> None:
    """Each sensor shows the value from the charger's status."""
    assert hass.states.get(entity("sensor", key)).state == expected


@pytest.mark.parametrize(
    ("raw_state", "expected"),
    [
        pytest.param(0, "Available", id="available"),
        pytest.param(1, "Preparing", id="preparing"),
        pytest.param(3, "Cooling", id="code_3_keeps_its_name"),
        pytest.param(4, "SuspendedEV", id="suspended_ev"),
        pytest.param(5, "Finished", id="code_5_keeps_its_name"),
        pytest.param(6, "Holiday", id="holiday"),
        pytest.param(8, "Unknown", id="unknown_code"),
    ],
)
async def test_status_names_are_stable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    raw_state: int,
    expected: str,
) -> None:
    """Status names don't change with the library's state enum (automations)."""
    client.push(raw_state=raw_state)
    await hass.async_block_till_done()
    assert hass.states.get(entity("sensor", "status")).state == expected


@pytest.mark.usefixtures("init_integration")
async def test_per_phase_sensors_are_disabled(hass: HomeAssistant) -> None:
    """L2/L3 sensors stay disabled by default."""
    assert hass.states.get(entity("sensor", "voltage_l2")) is None
    assert hass.states.get(entity("sensor", "current_l3")) is None
