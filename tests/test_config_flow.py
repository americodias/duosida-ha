"""Tests for the config and options flows."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from duosida_ev import DiscoveredCharger, DiscoveryError, DuosidaConnectionError
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.duosida_ev.const import (
    CONF_DEVICE_ID,
    CONF_SWITCH_DEBOUNCE,
    DEFAULT_PORT,
    DOMAIN,
    SESSION_RELEASE_DELAY,
)
from custom_components.duosida_ev.coordinator import get_reconnect_gate

from .conftest import DEVICE_ID, HOST, MOCK_INFO, FakeClock, FakeDuosidaClient

OTHER_HOST = "192.168.1.101"


@pytest.fixture
def mock_discover() -> Generator[AsyncMock]:
    """Patch UDP discovery."""
    with patch(
        "custom_components.duosida_ev.config_flow.discover_chargers",
        new_callable=AsyncMock,
        return_value=[DiscoveredCharger(host=HOST)],
    ) as mock:
        yield mock


@pytest.fixture
def mock_identify() -> Generator[AsyncMock]:
    """Patch the short TCP identification."""
    with patch(
        "custom_components.duosida_ev.config_flow.identify_charger",
        new_callable=AsyncMock,
        return_value=MOCK_INFO,
    ) as mock:
        yield mock


@pytest.fixture(autouse=True)
def no_setup(client: FakeDuosidaClient) -> None:
    """Entries created by the flow set up against the fake client."""


async def _start(hass: HomeAssistant, discovery: bool) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"discovery": discovery}
    )


async def test_discovery_flow(
    hass: HomeAssistant, mock_discover: AsyncMock, mock_identify: AsyncMock
) -> None:
    """A discovered charger is identified and added with its own device id."""
    result = await _start(hass, discovery=True)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery"
    mock_discover.assert_awaited_once()
    assert mock_discover.await_args.kwargs["identify"] is False

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_PORT: DEFAULT_PORT,
        CONF_DEVICE_ID: DEVICE_ID,
    }
    assert result["result"].unique_id == DEVICE_ID


async def test_discovery_never_contacts_configured_chargers(
    hass: HomeAssistant,
    mock_discover: AsyncMock,
    mock_identify: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A charger that already has an entry isn't identified over TCP."""
    config_entry.add_to_hass(hass)
    result = await _start(hass, discovery=True)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
    mock_identify.assert_not_awaited()


async def test_discovery_matches_chargers_configured_by_name(
    hass: HomeAssistant, mock_discover: AsyncMock, mock_identify: AsyncMock
) -> None:
    """An entry set up with a host name still protects its charger."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="test",
        data={
            CONF_HOST: "wallbox.lan",
            CONF_PORT: DEFAULT_PORT,
            CONF_DEVICE_ID: "test",
        },
    ).add_to_hass(hass)
    with patch(
        "custom_components.duosida_ev.config_flow.socket.gethostbyname",
        side_effect=lambda host: {"wallbox.lan": HOST}.get(host, host),
    ):
        result = await _start(hass, discovery=True)
    assert result["type"] is FlowResultType.ABORT
    mock_identify.assert_not_awaited()


async def test_manual_flow_matches_chargers_configured_by_name(
    hass: HomeAssistant, mock_identify: AsyncMock
) -> None:
    """Typing the IP of a charger configured by name aborts before connecting."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="test",
        data={
            CONF_HOST: "wallbox.lan",
            CONF_PORT: DEFAULT_PORT,
            CONF_DEVICE_ID: "test",
        },
    ).add_to_hass(hass)
    with patch(
        "custom_components.duosida_ev.config_flow.socket.gethostbyname",
        side_effect=lambda host: {"wallbox.lan": HOST}.get(host, host),
    ):
        result = await _start(hass, discovery=False)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_identify.assert_not_awaited()


@pytest.mark.parametrize(
    "discover",
    [
        pytest.param(AsyncMock(return_value=[]), id="nothing_found"),
        pytest.param(AsyncMock(side_effect=DiscoveryError("no network")), id="error"),
    ],
)
async def test_discovery_finds_nothing(
    hass: HomeAssistant, mock_identify: AsyncMock, discover: AsyncMock
) -> None:
    """No answer to the probe shows an error."""
    with patch("custom_components.duosida_ev.config_flow.discover_chargers", discover):
        result = await _start(hass, discovery=True)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_discovery_skips_unidentifiable_chargers(
    hass: HomeAssistant, mock_discover: AsyncMock, mock_identify: AsyncMock
) -> None:
    """A charger that doesn't answer the TCP identification is left out."""
    mock_identify.side_effect = DuosidaConnectionError("refused")
    result = await _start(hass, discovery=True)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_manual_flow(hass: HomeAssistant, mock_identify: AsyncMock) -> None:
    """Manual setup reads the device id from the charger (never "test")."""
    result = await _start(hass, discovery=False)
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == DEVICE_ID
    assert result["result"].unique_id == DEVICE_ID


async def test_manual_flow_matches_a_name_for_a_charger_configured_by_ip(
    hass: HomeAssistant, mock_identify: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Typing a host name that resolves to a configured charger aborts."""
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.duosida_ev.config_flow.socket.gethostbyname",
        side_effect=lambda host: {"wallbox.lan": HOST}.get(host, host),
    ):
        result = await _start(hass, discovery=False)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "wallbox.lan", CONF_PORT: DEFAULT_PORT}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_identify.assert_not_awaited()


async def test_manual_flow_with_an_unresolvable_name_still_tries(
    hass: HomeAssistant, mock_identify: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A name that doesn't resolve isn't a duplicate; identification decides."""
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.duosida_ev.config_flow.socket.gethostbyname",
        side_effect=OSError("no such host"),
    ):
        result = await _start(hass, discovery=False)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "unknown.lan", CONF_PORT: DEFAULT_PORT}
        )
    mock_identify.assert_awaited_once()


async def test_manual_flow_waits_for_a_busy_charger(
    hass: HomeAssistant, mock_identify: AsyncMock
) -> None:
    """While the gate is closed the flow says so instead of connecting."""
    get_reconnect_gate(hass, HOST).closed()
    result = await _start(hass, discovery=False)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["errors"] == {"base": "busy"}
    assert int(result["description_placeholders"]["seconds"]) > 0
    mock_identify.assert_not_awaited()


async def test_retry_right_after_cannot_connect_waits(
    hass: HomeAssistant, mock_identify: AsyncMock
) -> None:
    """A failed identification also closes a session; resubmitting waits."""
    mock_identify.side_effect = DuosidaConnectionError("refused")
    result = await _start(hass, discovery=False)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["errors"] == {"base": "busy"}
    assert mock_identify.await_count == 1


async def test_discovery_waits_for_a_busy_charger(
    hass: HomeAssistant, mock_discover: AsyncMock, mock_identify: AsyncMock
) -> None:
    """Discovery of a charger that just closed a session says to wait."""
    get_reconnect_gate(hass, HOST).closed()
    result = await _start(hass, discovery=True)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "busy"
    mock_identify.assert_not_awaited()


async def test_identification_delays_the_first_connection(
    hass: HomeAssistant, mock_identify: AsyncMock
) -> None:
    """The setup after the flow waits until the identify session is released."""
    result = await _start(hass, discovery=False)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    await hass.async_block_till_done()
    assert get_reconnect_gate(hass, HOST).wait_time() > 0
    assert result["result"].state is ConfigEntryState.SETUP_RETRY


async def test_manual_flow_recovers_from_cannot_connect(
    hass: HomeAssistant, mock_identify: AsyncMock, clock: FakeClock
) -> None:
    """An unreachable charger shows an error; fixing it completes the flow."""
    mock_identify.side_effect = DuosidaConnectionError("refused")
    result = await _start(hass, discovery=False)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    clock.advance(SESSION_RELEASE_DELAY + 1)
    mock_identify.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_manual_flow_aborts_on_a_configured_host(
    hass: HomeAssistant, mock_identify: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A configured host aborts before any connection."""
    config_entry.add_to_hass(hass)
    result = await _start(hass, discovery=False)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_identify.assert_not_awaited()


async def test_manual_flow_updates_the_host_of_a_known_charger(
    hass: HomeAssistant, mock_identify: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The same charger at a new address updates the existing entry."""
    config_entry.add_to_hass(hass)
    result = await _start(hass, discovery=False)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST, CONF_PORT: DEFAULT_PORT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == OTHER_HOST


async def test_options_flow(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The options only hold the switch debounce."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert list(result["data_schema"].schema) == [CONF_SWITCH_DEBOUNCE]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SWITCH_DEBOUNCE: 45}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {CONF_SWITCH_DEBOUNCE: 45}
