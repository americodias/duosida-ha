"""Tests for setting up and unloading the integration."""

from __future__ import annotations

from duosida_ev import DuosidaConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.duosida_ev.const import (
    DOMAIN,
    RECONNECT_BACKOFF,
    RECONNECT_GATES,
    SESSION_RELEASE_DELAY,
)

from .conftest import DEVICE_ID, HOST, FakeClock, FakeDuosidaClient, entity


async def test_setup_connects_once_and_creates_entities(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """One session is opened and the entities get its data."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert client.connect_calls == 1
    assert client.connected
    assert hass.states.get(entity("sensor", "status")).state == "Charging"


async def test_unload_closes_the_session(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Unloading disconnects from the charger (coordinator shutdown)."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert not client.connected
    assert client.disconnect_calls == 1


async def test_home_assistant_stop_closes_the_session(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Stopping Home Assistant closes the session cleanly."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert not client.connected
    assert client.disconnect_calls >= 1


async def test_reload_waits_for_the_charger_to_release_the_session(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """A reload waits for the old session, and HA's retries don't extend that."""
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert client.connect_calls == 1
    assert init_integration.state is ConfigEntryState.SETUP_RETRY

    # Home Assistant retries after 5 and 10 more seconds; neither connects
    # nor pushes the gate further away.
    for seconds in (5, 10):
        clock.advance(seconds)
        await hass.config_entries.async_reload(init_integration.entry_id)
        await hass.async_block_till_done()
        assert client.connect_calls == 1

    clock.advance(SESSION_RELEASE_DELAY - 15 + 1)
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert client.connect_calls == 2
    assert init_integration.state is ConfigEntryState.LOADED


async def test_setup_retries_when_the_charger_is_unreachable(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A failed connection puts the entry in retry and arms the reconnect gate."""
    client.connect_error = DuosidaConnectionError("refused")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert client.connect_calls == 1
    gate = hass.data[DOMAIN][RECONNECT_GATES][HOST]
    assert gate.failures == 1
    assert gate.wait_time() > 0


async def test_quick_setup_retry_does_not_reconnect(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A setup retry inside the backoff window doesn't touch the charger."""
    client.connect_error = DuosidaConnectionError("refused")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    client.connect_error = None
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert client.connect_calls == 1
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_failed_first_refresh_closes_the_session(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Connected but no status: the session is closed and counts as a failure."""
    client.pushes = False
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not client.connected
    assert client.disconnect_calls == 1
    assert hass.data[DOMAIN][RECONNECT_GATES][HOST].failures == 1


async def test_backoff_grows_while_connections_bring_no_status(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """Connected-but-silent attempts count as failures, so the delay grows."""
    client.pushes = False
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    gate = hass.data[DOMAIN][RECONNECT_GATES][HOST]
    assert gate.failures == 1

    clock.advance(RECONNECT_BACKOFF[0] + 1)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert client.connect_calls == 2
    assert gate.failures == 2
    assert gate.wait_time() > RECONNECT_BACKOFF[0]


async def test_reset_energy_button_is_removed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: FakeDuosidaClient,
    entity_registry: er.EntityRegistry,
) -> None:
    """The 1.0 reset button leaves the entity registry."""
    config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        Platform.BUTTON,
        DOMAIN,
        f"{DEVICE_ID}_reset_energy",
        config_entry=config_entry,
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        entity_registry.async_get_entity_id(
            Platform.BUTTON, DOMAIN, f"{DEVICE_ID}_reset_energy"
        )
        is None
    )


async def test_removing_the_entry_keeps_an_armed_gate(
    hass: HomeAssistant, init_integration: MockConfigEntry, clock: FakeClock
) -> None:
    """Right after removal the gate stays, so a re-add can't connect at once."""
    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][RECONNECT_GATES][HOST].wait_time() > 0


async def test_removing_the_entry_forgets_an_expired_gate(
    hass: HomeAssistant, init_integration: MockConfigEntry, clock: FakeClock
) -> None:
    """Once expired, the gate of a removed charger is forgotten."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    clock.advance(SESSION_RELEASE_DELAY + 1)
    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()
    assert HOST not in hass.data[DOMAIN][RECONNECT_GATES]
