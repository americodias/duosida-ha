"""Tests for the charging binary sensor."""

from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeDuosidaClient, entity


async def test_charging_binary_sensor(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """On while charging, off otherwise."""
    assert hass.states.get(entity("binary_sensor", "charging")).state == STATE_ON
    client.push(raw_state=4)
    await hass.async_block_till_done()
    assert hass.states.get(entity("binary_sensor", "charging")).state == STATE_OFF
