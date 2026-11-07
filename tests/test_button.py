"""Tests for the buttons."""

from __future__ import annotations

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeDuosidaClient, entity


@pytest.mark.parametrize(
    ("key", "call"),
    [
        pytest.param("start_charging", ("start_charging",), id="start"),
        pytest.param("stop_charging", ("stop_charging",), id="stop"),
    ],
)
async def test_buttons_send_commands(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    key: str,
    call: tuple[str],
) -> None:
    """Pressing a button sends its command."""
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: entity("button", key)},
        blocking=True,
    )
    assert call in client.calls


@pytest.mark.usefixtures("init_integration")
async def test_reset_energy_button_is_gone(hass: HomeAssistant) -> None:
    """Total energy comes from the charger; there is nothing to reset."""
    assert hass.states.get(entity("button", "reset_total_energy")) is None
