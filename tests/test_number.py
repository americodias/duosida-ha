"""Tests for the number entities."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import PropertyMock, patch

from duosida_ev import CommandRejectedError
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID
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


async def _set(hass: HomeAssistant, key: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity("number", key), ATTR_VALUE: value},
        blocking=True,
    )


@pytest.mark.usefixtures("init_integration")
async def test_max_current_shows_the_charger_value(hass: HomeAssistant) -> None:
    """The maximum current is read from the charger."""
    assert hass.states.get(entity("number", "max_charging_current")).state == "16"


async def test_set_max_current(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Setting the current sends it and shows the new value."""
    await _set(hass, "max_charging_current", 10)
    assert ("set_max_current", 10) in client.calls
    assert hass.states.get(entity("number", "max_charging_current")).state == "10"


async def test_rejected_max_current_raises(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A rejected write surfaces as an error and the value stays."""
    client.command_error = CommandRejectedError(
        "set VendorMaxWorkCurrent", 1, "REJECTED"
    )
    with pytest.raises(HomeAssistantError):
        await _set(hass, "max_charging_current", 10)
    assert hass.states.get(entity("number", "max_charging_current")).state == "16"


@pytest.mark.parametrize(
    ("key", "value", "call"),
    [
        pytest.param("led_brightness", 3, ("set_led_brightness", 3), id="led"),
        pytest.param("led_brightness", 2, ("set_led_brightness", 1), id="led_2_is_1"),
        pytest.param("max_voltage", 270, ("set_max_voltage", 270), id="max_voltage"),
        pytest.param("min_voltage", 90, ("set_min_voltage", 90), id="min_voltage"),
    ],
)
async def test_set_stored_numbers(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    key: str,
    value: int,
    call: tuple[str, int],
) -> None:
    """Settings that aren't read back show the last written value."""
    assert hass.states.get(entity("number", key)).state == "unknown"
    await _set(hass, key, value)
    assert call in client.calls
    assert hass.states.get(entity("number", key)).state == str(call[1])
