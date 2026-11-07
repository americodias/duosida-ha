"""Tests for the coordinator: push updates, reconnects, settings, commands."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

from duosida_ev import (
    ChargerConfiguration,
    CommandRejectedError,
    DuosidaConnectionError,
    DuosidaTimeoutError,
    ValidationError,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.duosida_ev.const import (
    CONFIGURATION_READ_DELAY,
    CONFIGURATION_READ_TIMEOUT,
    CONFIGURATION_REFRESH_INTERVAL,
    CONFIGURATION_RETRY_BACKOFF,
    COORDINATOR,
    DOMAIN,
    RECONNECT_BACKOFF,
    SESSION_RELEASE_DELAY,
)
from custom_components.duosida_ev.coordinator import (
    DuosidaDataUpdateCoordinator,
    ReconnectGate,
)

from .conftest import (
    FakeClock,
    FakeDuosidaClient,
    entity,
    run_scheduled_configuration_read,
)


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry
) -> DuosidaDataUpdateCoordinator:
    return hass.data[DOMAIN][entry.entry_id][COORDINATOR]


def _configuration_reads(client: FakeDuosidaClient) -> int:
    return sum(1 for call in client.calls if call == ("get_configuration",))


async def _push_and_read(hass: HomeAssistant, client: FakeDuosidaClient) -> None:
    """A status push, then whatever configuration read it scheduled."""
    client.push()
    await run_scheduled_configuration_read(hass)


async def test_data_keeps_the_1_0_keys(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The status is mapped to the keys the entities have always read."""
    data = _coordinator(hass, init_integration).data
    assert data["conn_status"] == 2
    assert data["voltage"] == 230.0
    assert data["temperature_station"] == 35.0
    assert data["total_energy"] == 527.4
    assert data["session_time"] == 90
    assert data["model"] == "SmartChargePI"
    assert data["voltage_l2"] is None
    assert data["current_l3"] is None


async def test_no_session_means_zero_session_time(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Without a session start the session time is 0."""
    client.push(raw_state=0, session_start=None)
    await hass.async_block_till_done()
    assert _coordinator(hass, init_integration).data["session_time"] == 0


async def test_push_updates_entities(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A status push reaches the entities without polling."""
    client.push(raw_state=4, current=0.0)
    await hass.async_block_till_done()
    assert hass.states.get(entity("sensor", "status")).state == "SuspendedEV"
    assert hass.states.get(entity("sensor", "current")).state == "0.0"


async def test_repeated_notification_is_not_republished(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A notification without a new status (e.g. StatusNotification) is ignored."""
    coordinator = _coordinator(hass, init_integration)
    with patch.object(
        coordinator, "async_set_updated_data", wraps=coordinator.async_set_updated_data
    ) as published:
        client.notify()
        client.push()
    assert published.call_count == 1


async def test_lost_session_makes_entities_unavailable(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """When the charger drops the session, entities go unavailable."""
    client.drop()
    await hass.async_block_till_done()
    assert hass.states.get(entity("sensor", "status")).state == STATE_UNAVAILABLE


async def test_watchdog_waits_before_reconnecting_after_a_drop(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """After a drop, the charger gets time to release the session first."""
    coordinator = _coordinator(hass, init_integration)
    client.drop()
    await coordinator.async_refresh()
    assert client.connect_calls == 1

    clock.advance(SESSION_RELEASE_DELAY + 1)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert client.connect_calls == 2
    assert hass.states.get(entity("sensor", "status")).state == "Charging"


async def test_reconnect_never_publishes_the_old_status(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """Entities stay unavailable until a status arrives on the new session."""
    coordinator = _coordinator(hass, init_integration)
    client.drop()
    clock.advance(SESSION_RELEASE_DELAY + 1)
    client.pushes = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert client.connected
    assert not coordinator.last_update_success
    assert hass.states.get(entity("sensor", "status")).state == STATE_UNAVAILABLE


async def test_watchdog_backs_off_after_a_failed_reconnect(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """A refused reconnect delays the next attempt by the backoff."""
    coordinator = _coordinator(hass, init_integration)
    client.drop()
    clock.advance(SESSION_RELEASE_DELAY + 1)
    client.connect_error = DuosidaConnectionError("busy")
    await coordinator.async_refresh()
    assert client.connect_calls == 2

    clock.advance(RECONNECT_BACKOFF[0] - 1)
    await coordinator.async_refresh()
    assert client.connect_calls == 2

    clock.advance(2)
    await coordinator.async_refresh()
    assert client.connect_calls == 3


async def test_silent_session_is_closed(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Connected but no status for two refreshes: the session is closed."""
    coordinator = _coordinator(hass, init_integration)
    with patch.object(
        client, "get_status", side_effect=DuosidaTimeoutError("no status")
    ):
        await coordinator.async_refresh()
        assert client.connected
        await coordinator.async_refresh()
    assert not client.connected
    assert not coordinator.last_update_success


async def test_failed_setup_handshake_logs_no_error(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: FakeDuosidaClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A charger unreachable at startup doesn't log "connection lost"."""
    client.connect_error = DuosidaConnectionError("refused")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert "Connection to the charger lost" not in caplog.text


def test_gate_backoff_sequence() -> None:
    """The gate spaces attempts by the configured delays, capped at the last."""
    gate = ReconnectGate()
    assert gate.wait_time() == 0
    delays = [gate.failed() for _ in range(len(RECONNECT_BACKOFF) + 2)]
    assert delays == [*RECONNECT_BACKOFF, RECONNECT_BACKOFF[-1], RECONNECT_BACKOFF[-1]]
    gate.succeeded()
    assert gate.wait_time() == 0
    gate.closed()
    assert gate.wait_time() > 0


async def test_configuration_is_read_shortly_after_the_first_push(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """One read per connection, a few seconds after a status push."""
    assert _configuration_reads(client) == 0
    await run_scheduled_configuration_read(hass)
    assert _configuration_reads(client) == 1


async def test_configuration_read_waits_longer_than_commands(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """The charger answers some reads only 5-6 s after the first send."""
    await run_scheduled_configuration_read(hass)
    assert client.configuration_timeouts == [CONFIGURATION_READ_TIMEOUT]


async def test_max_current_is_read_from_the_charger(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """The configuration read gives the maximum current, also to the entity."""
    await run_scheduled_configuration_read(hass)
    assert _coordinator(hass, init_integration).get_setting("max_current") == 16
    assert hass.states.get(entity("number", "max_charging_current")).state == "16"


async def test_configuration_is_never_read_from_the_status_listener(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """A due read waits for its timer instead of following the push at once."""
    await run_scheduled_configuration_read(hass)
    clock.advance(CONFIGURATION_REFRESH_INTERVAL)
    client.push()
    await hass.async_block_till_done()
    assert _configuration_reads(client) == 1
    await run_scheduled_configuration_read(hass)
    assert _configuration_reads(client) == 2


async def test_unanswered_configuration_read_is_retried_with_backoff(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """Each retry is a new request, sooner than the normal interval at first."""
    client.configuration_error = DuosidaTimeoutError("no answer")
    await run_scheduled_configuration_read(hass)
    reads = 1
    for delay in (*CONFIGURATION_RETRY_BACKOFF, CONFIGURATION_REFRESH_INTERVAL):
        clock.advance(delay - 1)
        await _push_and_read(hass, client)
        assert _configuration_reads(client) == reads
        clock.advance(1)
        await _push_and_read(hass, client)
        reads += 1
        assert _configuration_reads(client) == reads


async def test_answered_read_resets_the_backoff(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """After an answer, reads go back to the normal interval and a new backoff."""
    client.configuration_error = DuosidaTimeoutError("no answer")
    await run_scheduled_configuration_read(hass)
    client.configuration_error = None
    clock.advance(CONFIGURATION_RETRY_BACKOFF[0])
    await _push_and_read(hass, client)
    assert _configuration_reads(client) == 2
    assert _coordinator(hass, init_integration).get_setting("max_current") == 16

    clock.advance(CONFIGURATION_REFRESH_INTERVAL - 1)
    await _push_and_read(hass, client)
    assert _configuration_reads(client) == 2
    client.configuration_error = DuosidaTimeoutError("no answer")
    clock.advance(1)
    await _push_and_read(hass, client)
    assert _configuration_reads(client) == 3

    clock.advance(CONFIGURATION_RETRY_BACKOFF[0])
    await _push_and_read(hass, client)
    assert _configuration_reads(client) == 4


async def test_configuration_read_logs_attempt_and_push_offset(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The debug log tells which attempt got an answer, and when it was sent."""
    caplog.set_level(logging.DEBUG, logger="custom_components.duosida_ev")
    client.configuration_error = DuosidaTimeoutError("no answer")
    client.push()
    clock.advance(CONFIGURATION_READ_DELAY)
    await run_scheduled_configuration_read(hass)
    assert "attempt 1, 3.0 s after a status push" in caplog.text
    assert "next attempt in 60 s" in caplog.text

    client.configuration_error = None
    clock.advance(CONFIGURATION_RETRY_BACKOFF[0])
    await _push_and_read(hass, client)
    assert "Read the charger configuration (attempt 2," in caplog.text


async def test_unexpected_read_error_still_waits_for_the_next_interval(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An error outside the library's own is logged, not repeated on every push."""
    client.configuration_error = RuntimeError("library bug")
    await run_scheduled_configuration_read(hass)
    assert _configuration_reads(client) == 1
    assert "Unexpected error reading the charger configuration" in caplog.text
    clock.advance(CONFIGURATION_RETRY_BACKOFF[-1])
    await _push_and_read(hass, client)
    assert _configuration_reads(client) == 1
    clock.advance(CONFIGURATION_REFRESH_INTERVAL)
    client.configuration_error = None
    await _push_and_read(hass, client)
    assert _configuration_reads(client) == 2


async def test_one_configuration_read_at_a_time(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A push while a read waits for its answer doesn't start another read."""
    answer = asyncio.Event()
    started = 0

    async def slow_read(*, timeout: float | None = None) -> ChargerConfiguration:
        nonlocal started
        started += 1
        await answer.wait()
        return client.configuration

    with patch.object(client, "get_configuration", slow_read):
        await run_scheduled_configuration_read(hass)
        assert started == 1
        await _push_and_read(hass, client)
        assert started == 1
        answer.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert _coordinator(hass, init_integration).get_setting("max_current") == 16


async def test_new_session_reads_the_configuration_without_backoff(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """A reconnect reads again at once, and its backoff starts over."""
    coordinator = _coordinator(hass, init_integration)
    client.configuration_error = DuosidaTimeoutError("no answer")
    await run_scheduled_configuration_read(hass)
    client.drop()
    clock.advance(SESSION_RELEASE_DELAY + 1)
    assert SESSION_RELEASE_DELAY + 1 < CONFIGURATION_RETRY_BACKOFF[0]
    await coordinator.async_refresh()
    await run_scheduled_configuration_read(hass)
    assert _configuration_reads(client) == 2

    clock.advance(CONFIGURATION_RETRY_BACKOFF[0])
    await _push_and_read(hass, client)
    assert _configuration_reads(client) == 3


async def test_no_configuration_read_after_the_session_dropped(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """A read scheduled before a drop is not sent."""
    client.drop()
    await run_scheduled_configuration_read(hass)
    assert _configuration_reads(client) == 0


async def test_scheduled_configuration_read_is_cancelled_on_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Unloading cancels a read that was waiting for its timer."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await run_scheduled_configuration_read(hass)
    assert _configuration_reads(client) == 0


async def test_written_value_wins_over_an_older_read(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """After a write, the written value is shown until a newer read."""
    coordinator = _coordinator(hass, init_integration)
    await run_scheduled_configuration_read(hass)
    assert coordinator.get_setting("max_current") == 16
    reads = _configuration_reads(client)
    clock.advance(1)
    await coordinator.async_set_max_current(10)
    assert coordinator.get_setting("max_current") == 10
    # No read-back after the write (load management writes often).
    await run_scheduled_configuration_read(hass)
    assert _configuration_reads(client) == reads

    clock.advance(1)
    client.configuration = ChargerConfiguration(raw={3: 12.0})
    await coordinator._async_refresh_configuration()
    assert coordinator.get_setting("max_current") == 12


async def test_failed_configuration_read_keeps_the_written_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    clock: FakeClock,
) -> None:
    """A failed read doesn't bring back an older value."""
    coordinator = _coordinator(hass, init_integration)
    await run_scheduled_configuration_read(hass)
    assert coordinator.get_setting("max_current") == 16
    clock.advance(1)
    await coordinator.async_set_max_current(10)
    clock.advance(1)
    client.configuration_error = DuosidaTimeoutError("no answer")
    await coordinator._async_refresh_configuration()
    assert coordinator.get_setting("max_current") == 10


async def test_max_current_from_an_earlier_run_is_not_shown(
    hass: HomeAssistant,
    hass_storage: dict,
    config_entry: MockConfigEntry,
    client: FakeDuosidaClient,
) -> None:
    """The maximum current is unknown until read or written in this run.

    The stored value may be stale: the vendor app or the CLI can change it
    while Home Assistant is not connected.
    """
    hass_storage[f"{DOMAIN}.{config_entry.unique_id}"] = {
        "version": 1,
        "data": {"max_current": 18},
    }
    client.configuration_error = DuosidaTimeoutError("no answer")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await run_scheduled_configuration_read(hass)

    coordinator = _coordinator(hass, config_entry)
    assert _configuration_reads(client) == 1
    assert coordinator.get_setting("max_current") is None
    state = hass.states.get(entity("number", "max_charging_current"))
    assert state.state == STATE_UNKNOWN

    # A write in this run is shown, and still saved for 1.0 to read.
    await coordinator.async_set_max_current(10)
    await hass.async_block_till_done()
    assert coordinator.get_setting("max_current") == 10
    key = f"{DOMAIN}.{config_entry.unique_id}"
    assert hass_storage[key]["data"]["max_current"] == 10


async def test_setting_without_read_back_comes_from_the_last_write(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Settings the charger doesn't report are the last written value."""
    coordinator = _coordinator(hass, init_integration)
    assert coordinator.get_setting("direct_mode") is None
    await coordinator.async_set_direct_mode(True)
    assert coordinator.get_setting("direct_mode") is True


async def test_settings_are_never_written_on_startup(
    hass: HomeAssistant,
    hass_storage: dict,
    config_entry: MockConfigEntry,
    client: FakeDuosidaClient,
) -> None:
    """Stored settings are not sent to the charger on startup."""
    hass_storage[f"{DOMAIN}.{config_entry.unique_id}"] = {
        "version": 1,
        "data": {"max_current": 20, "led_brightness": 1, "total_energy": 400.0},
    }
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert [call for call in client.calls if call[0] != "get_configuration"] == []
    assert _coordinator(hass, config_entry).get_setting("led_brightness") == 1


async def test_unknown_stored_keys_are_kept(
    hass: HomeAssistant,
    hass_storage: dict,
    config_entry: MockConfigEntry,
    client: FakeDuosidaClient,
) -> None:
    """The 1.0 energy state stays in storage, so downgrading loses nothing."""
    key = f"{DOMAIN}.{config_entry.unique_id}"
    hass_storage[key] = {"version": 1, "data": {"total_energy": 400.0}}
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await _coordinator(hass, config_entry).async_set_max_voltage(270)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"] == {"total_energy": 400.0, "max_voltage": 270}


@pytest.mark.parametrize(
    ("error", "message"),
    [
        pytest.param(
            CommandRejectedError("set VendorMaxWorkCurrent", 1, "REJECTED"),
            "rejected",
            id="rejected",
        ),
        pytest.param(ValidationError("amps must be 6-32"), "6-32", id="invalid"),
        pytest.param(DuosidaTimeoutError("no answer"), "no answer", id="timeout"),
    ],
)
async def test_command_errors_raise(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeDuosidaClient,
    error: Exception,
    message: str,
) -> None:
    """A refused or failed command is reported to the caller."""
    coordinator = _coordinator(hass, init_integration)
    await run_scheduled_configuration_read(hass)
    client.command_error = error
    with pytest.raises(HomeAssistantError, match=message):
        await coordinator.async_set_max_current(10)
    assert coordinator.get_setting("max_current") == 16


async def test_command_without_session_raises(
    hass: HomeAssistant, init_integration: MockConfigEntry, client: FakeDuosidaClient
) -> None:
    """Commands fail clearly while the session is down."""
    client.drop()
    with pytest.raises(HomeAssistantError, match="not connected"):
        await _coordinator(hass, init_integration).async_start_charging()
    assert ("start_charging",) not in client.calls
