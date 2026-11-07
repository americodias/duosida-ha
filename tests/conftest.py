"""Fixtures for the Duosida EV Charger tests.

The fake client returns the real ``duosida_ev`` model objects, so a change in
the library's models shows up here.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from duosida_ev import (
    ChargerConfiguration,
    ChargerInfo,
    ChargerState,
    ChargerStatus,
    ConfigurationStatus,
    DuosidaConnectionError,
    DuosidaTimeoutError,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.duosida_ev.const import (
    CONF_DEVICE_ID,
    CONFIGURATION_READ_DELAY,
    DEFAULT_PORT,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

HOST = "192.168.1.100"
DEVICE_ID = "0300000000000000000"
ENTITY_PREFIX = "duosida_ev_charger_192_168_1_100"

MOCK_INFO = ChargerInfo(
    device_id=DEVICE_ID,
    model="SmartChargePI",
    manufacturer="Duosida",
    firmware="V2.5",
)


def make_status(**changes: Any) -> ChargerStatus:
    """A status snapshot: charging at 16 A, session started 90 minutes ago."""
    now = datetime.now(UTC)
    raw_state = changes.pop("raw_state", 2)
    values: dict[str, Any] = {
        "state": ChargerState.from_code(raw_state),
        "raw_state": raw_state,
        "voltage": 230.0,
        "current": 16.0,
        "power": 3680.0,
        "total_energy": 527.4,
        "session_energy": 5.5,
        "temperature": 35.0,
        "cp_voltage": 6.0,
        "session_start": now - timedelta(minutes=90),
        "transaction_id": 123,
        "id_tag": "XC_Remote_Tag",
        "received_at": now,
    }
    values.update(changes)
    return ChargerStatus(**values)


class FakeDuosidaClient:
    """Stands in for ``duosida_ev.DuosidaClient``."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, **_: Any) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self.info: ChargerInfo | None = None
        self.status: ChargerStatus | None = None
        self.configuration = ChargerConfiguration(raw={3: 16.0})
        self.connect_error: Exception | None = None
        self.configuration_error: Exception | None = None
        self.command_error: Exception | None = None
        # Whether the charger sends a status push after a (re)connect.
        self.pushes = True
        self.next_push: dict[str, Any] = {}
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.calls: list[tuple[Any, ...]] = []
        self.configuration_timeouts: list[float | None] = []
        self._listeners: list[Callable[[], None]] = []

    @property
    def device_id(self) -> str | None:
        return self.info.device_id if self.info else None

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    async def connect(self) -> ChargerInfo:
        """Like the real client: notifies, and keeps any status from before."""
        self.connect_calls += 1
        if self.connect_error is not None:
            self.notify()
            raise self.connect_error
        self.connected = True
        self.info = MOCK_INFO
        self.notify()
        return MOCK_INFO

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        was_connected = self.connected
        self.connected = False
        if was_connected:
            self.notify()

    def push(self, **changes: Any) -> None:
        """Simulate a status push from the charger."""
        self.status = make_status(**changes)
        self.notify()

    def drop(self) -> None:
        """Simulate the charger closing the session."""
        self.connected = False
        self.notify()

    async def wait_for_status(self, *, timeout: float | None = 30.0) -> ChargerStatus:
        """The next push: a new status object."""
        if not self.connected:
            raise DuosidaConnectionError("not connected")
        if not self.pushes:
            raise DuosidaTimeoutError("no status from the charger")
        # The real client resolves the waiter, then notifies the listeners.
        self.status = make_status(**self.next_push)
        self.notify()
        return self.status

    async def get_status(
        self, *, max_age: float = 30.0, timeout: float = 30.0
    ) -> ChargerStatus:
        if self.status is None or not self.connected:
            return await self.wait_for_status(timeout=timeout)
        return self.status

    async def get_configuration(
        self, *, timeout: float | None = None
    ) -> ChargerConfiguration:
        self.calls.append(("get_configuration",))
        self.configuration_timeouts.append(timeout)
        if self.configuration_error is not None:
            raise self.configuration_error
        return self.configuration

    async def _command(self, *call: Any) -> ConfigurationStatus:
        self.calls.append(call)
        if self.command_error is not None:
            raise self.command_error
        return ConfigurationStatus.ACCEPTED

    async def start_charging(self) -> None:
        await self._command("start_charging")

    async def stop_charging(self) -> None:
        await self._command("stop_charging")

    async def set_max_current(self, amps: int) -> ConfigurationStatus:
        return await self._command("set_max_current", amps)

    async def set_led_brightness(self, level: int) -> ConfigurationStatus:
        return await self._command("set_led_brightness", level)

    async def set_direct_work_mode(self, enabled: bool) -> ConfigurationStatus:
        return await self._command("set_direct_work_mode", enabled)

    async def set_stop_on_disconnect(self, enabled: bool) -> ConfigurationStatus:
        return await self._command("set_stop_on_disconnect", enabled)

    async def set_max_voltage(self, volts: int) -> ConfigurationStatus:
        return await self._command("set_max_voltage", volts)

    async def set_min_voltage(self, volts: int) -> ConfigurationStatus:
        return await self._command("set_min_voltage", volts)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/duosida_ev."""


class FakeClock:
    """A monotonic clock the tests move forward by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def clock() -> Generator[FakeClock]:
    """Control the coordinator's clock (reconnect delays, refresh ages)."""
    fake = FakeClock()
    with patch(
        "custom_components.duosida_ev.coordinator.time",
        SimpleNamespace(monotonic=fake.monotonic),
    ):
        yield fake


@pytest.fixture
def client() -> Generator[FakeDuosidaClient]:
    """The client the integration creates, replaced by a fake."""
    fake = FakeDuosidaClient(HOST)
    with patch("custom_components.duosida_ev.DuosidaClient", return_value=fake):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A config entry as created by version 1.0 (with the old scan interval)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Duosida {HOST}",
        unique_id=DEVICE_ID,
        data={
            CONF_HOST: HOST,
            CONF_PORT: DEFAULT_PORT,
            CONF_DEVICE_ID: DEVICE_ID,
            "scan_interval": 10,
        },
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeDuosidaClient
) -> MockConfigEntry:
    """Set up the integration with the fake client."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def run_scheduled_configuration_read(hass: HomeAssistant) -> None:
    """Let a configuration read scheduled after a status push run."""
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=CONFIGURATION_READ_DELAY + 1)
    )
    await hass.async_block_till_done()


def entity(platform: str, key: str) -> str:
    """Entity id of one of the charger's entities."""
    return f"{platform}.{ENTITY_PREFIX}_{key}"
