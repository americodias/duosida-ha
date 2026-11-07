# Duosida EV Charger: Home Assistant custom integration

HACS custom integration, domain `duosida_ev`, for local control of Duosida EV
wall chargers over TCP 9988. It is built on the `duosida-ev` library (PyPI,
async since 0.2.0). This is the full-featured version (sensor, binary_sensor,
switch, number, button). A reduced, sensor-only version is being prepared
separately for Home Assistant core.

## Architecture

- **One persistent session per config entry** (`DuosidaClient`). The charger
  pushes its status about every 10 s. A client listener feeds
  `coordinator.async_set_updated_data`, so entities update without polling.
- **The coordinator refresh (30 s) is only a watchdog.** Pushes reset its timer.
  When the session drops, the refresh reconnects through `ReconnectGate`
  (backoff 60/120/300 s). The gate lives in `hass.data[DOMAIN]` so it survives
  config entry setup retries.
- **The charger accepts ONE TCP session**, stays busy ~30 s after a close, and
  refuses fast reconnects or abrupt (RST) closes, sometimes for minutes.
  Never open a second session:
  - the config flow skips configured hosts before any TCP identify;
  - a failed first refresh closes the session;
  - unload and `homeassistant_stop` disconnect cleanly.
- `coordinator.data` keeps the 1.0 keys (`conn_status`, `temperature_station`,
  `session_time` in minutes, …) so the entities didn't change.
- **Settings:**
  - `max_current` is read back with `get_configuration`, the newer of the
    last read and the last write is shown. Before either happens in this
    run it is unknown: the stored value from an earlier run may be stale.
    It is still saved on writes, because 1.0 reads it from the store.
    The read runs from a timer 3 s after a status push, never inside the
    listener, and waits up to 7 s (`get_configuration(timeout=7)`: 4 sends;
    commands keep the library's 5 s). Reads happen once per session, then every 10 min. A read with
    no answer is retried as a new request (new message id) after
    60/120/300 s, then every 10 min.
  - The charger silently ignores some reads, cause unknown. It TCP-ACKs the
    request and never answers. A capture of the vendor app shows its read
    ignored too, and same-id resends don't always help (the app's or the library's);
  - the rest is the last value written from HA, kept in `Store`
    `duosida_ev.<device_id>`. Unknown keys in that store, such as the 1.0
    energy integration, are preserved, so downgrading loses nothing;
  - settings are **never** written on startup;
  - there is no read-back after a write: load-management automations write
    `max_current` every minute or so, and the command's answer is the
    confirmation.
- Commands raise `HomeAssistantError` when the charger rejects them or the
  session is down.
- Total energy is the charger's own register (`ChargerStatus.total_energy`).

## Compatibility with existing installs

- **Never change** entity `unique_id`s (`{device_id}_{key}`), entity keys or
  names. **Never change** the status names in `const.STATUS_CODES` (including
  Cooling/Finished/Holiday, which differ from the library's OCPP names). User
  automations depend on them.
- Existing entries may carry a placeholder `device_id` from 1.0 manual setups.
  Don't migrate it, because the unique_ids derive from it.
- 1.0 entries still have `scan_interval` in data/options; it is ignored.
- The 1.0 "Reset Total Energy" button is removed from the entity registry at
  setup (`_async_remove_reset_energy_button`).

## Development

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python 'duosida-ev==0.2.2' pytest \
  pytest-homeassistant-custom-component pytest-cov mypy
# unreleased library changes: uv pip install --python .venv/bin/python -e ../duosida-ev

.venv/bin/python -m pytest -q -p no:cacheprovider --cov=custom_components.duosida_ev
uvx ruff@0.14.5 check custom_components tests
uvx black@25.11.0 --check custom_components tests
.venv/bin/mypy custom_components/duosida_ev
```

- `mypy` has 4 known errors: `voluptuous.Schema` vs `probatio.Schema` in
  `config_flow.py`. HA 2026.10 replaced voluptuous with probatio and maps
  `import voluptuous` onto it. This integration keeps voluptuous so it still
  runs on HA 2024.12+.
- The tests use a fake client (`tests/conftest.py`) that returns the real
  `duosida_ev` model objects, so library model changes break the tests.
- One test: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_coordinator.py::test_push_updates_entities`.

## Releasing

1. The required `duosida-ev` version must be on PyPI first. HA and HACS
   install it from there.
2. Bump `manifest.json` `requirements` and `version`, `pyproject.toml`, and
   `CHANGELOG.md`.
3. Tag `vX.Y.Z` and create a GitHub release, which HACS picks up.

## Notes

- LED brightness accepts only 0, 1 and 3. A V2.5 unit accepted level 3 in
  testing, while another project reports rejections on V2.5. A rejection is
  shown to the user as an error.
- The direct mode / plug-and-charge field in the configuration is not
  verified yet. When the library exposes it, read it back instead of storing it.
- **No private data in this repository**: no real IPs, serials/device IDs or
  MACs. Use `192.168.1.100` and `0300000000000000000` in examples and tests.
