"""Tests for the switches."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import PropertyMock, patch

from duosida_ev import CommandRejectedError
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeDuosidaClient, entity


@pytest.fixture(autouse=True)
def enable_all_entities() -> Generator[None]:
    """Enable the entities that are disabled by default."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


async def _switch(hass: HomeAssistant, key: str, service: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity("switch", key)}, blocking=True
    )


async def test_charging_switch_follows_the_charger(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """The charging switch is on while the charger reports Charging."""
    assert hass.states.get(entity("switch", "charging")).state == STATE_ON
    client.push(raw_state=0)
    await hass.async_block_till_done()
    assert hass.states.get(entity("switch", "charging")).state == STATE_OFF


async def test_charging_switch_sends_commands_and_debounces(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Turning the switch off stops charging and holds the state meanwhile."""
    await _switch(hass, "charging", SERVICE_TURN_OFF)
    assert ("stop_charging",) in client.calls
    client.push(raw_state=2)
    await hass.async_block_till_done()
    assert hass.states.get(entity("switch", "charging")).state == STATE_OFF

    await _switch(hass, "charging", SERVICE_TURN_ON)
    assert ("start_charging",) in client.calls


async def test_rejected_stop_raises(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A stop the charger refuses (no known transaction) is reported."""
    client.command_error = CommandRejectedError("stop charging", 1, "Rejected")
    with pytest.raises(HomeAssistantError):
        await _switch(hass, "charging", SERVICE_TURN_OFF)


@pytest.mark.parametrize(
    ("key", "method"),
    [
        pytest.param("direct_mode", "set_direct_work_mode", id="direct_mode"),
        pytest.param(
            "stop_session_on_vehicle_disconnect",
            "set_stop_on_disconnect",
            id="stop_on_disconnect",
        ),
    ],
)
async def test_setting_switches(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    key: str,
    method: str,
) -> None:
    """Setting switches start unknown and then show the last written value."""
    assert hass.states.get(entity("switch", key)).state == STATE_UNKNOWN
    await _switch(hass, key, SERVICE_TURN_ON)
    assert (method, True) in client.calls
    assert hass.states.get(entity("switch", key)).state == STATE_ON
    await _switch(hass, key, SERVICE_TURN_OFF)
    assert (method, False) in client.calls
    assert hass.states.get(entity("switch", key)).state == STATE_OFF
