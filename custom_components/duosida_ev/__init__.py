"""The Duosida EV Charger integration for Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er

from duosida_ev import DuosidaClient

from .const import (
    CONF_DEVICE_ID,
    CONF_SWITCH_DEBOUNCE,
    COORDINATOR,
    DEFAULT_SWITCH_DEBOUNCE,
    DOMAIN,
    RECONNECT_GATES,
)
from .coordinator import DuosidaDataUpdateCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Duosida charger from a config entry.

    The coordinator's shutdown, which Home Assistant runs on unload and when
    setup fails, closes the session; the charger accepts only one.
    """
    device_id = entry.data[CONF_DEVICE_ID]
    _async_remove_reset_energy_button(hass, device_id)

    coordinator = DuosidaDataUpdateCoordinator(
        hass,
        entry,
        DuosidaClient(entry.data[CONF_HOST], entry.data[CONF_PORT]),
        device_id,
        entry.options.get(CONF_SWITCH_DEBOUNCE, DEFAULT_SWITCH_DEBOUNCE),
    )
    await coordinator.async_load_stored_settings()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {COORDINATOR: coordinator}

    async def _async_stop(_event: Event) -> None:
        await coordinator.async_disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_remove_reset_energy_button(hass: HomeAssistant, device_id: str) -> None:
    """Remove the 1.0 "Reset Total Energy" button.

    Total energy now comes from the charger's own register, which can't be reset.
    """
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id(
        Platform.BUTTON, DOMAIN, f"{device_id}_reset_energy"
    ):
        registry.async_remove(entity_id)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the reconnect state of a removed charger, once it has expired.

    Unloading just closed the session; keeping the gate until it expires stops
    an immediate re-add from connecting while the charger still holds it.
    """
    gates = hass.data.get(DOMAIN, {}).get(RECONNECT_GATES, {})
    if (gate := gates.get(entry.data[CONF_HOST])) and gate.wait_time() == 0:
        del gates[entry.data[CONF_HOST]]
