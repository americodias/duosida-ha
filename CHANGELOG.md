# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - Unreleased

Requires `duosida-ev` 0.2.0 and Home Assistant 2024.12 or newer.

### Changed

- **One persistent connection.** Built on the async `duosida-ev` 0.2.0. The
  charger pushes its status about every 10 seconds, instead of Home Assistant
  opening a new connection on every poll. The charger accepts only one
  connection and refuses fast reconnects, which caused bursts of
  "unavailable". After a connection loss, reconnect attempts are now spaced
  1 to 5 minutes apart, also across setup retries.
- **Session Time is correct.** Version 0.1 of the library set the charger's
  clock to a fixed date on every connection, so the session time was wrong by
  about a year. A session that started before the upgrade can still show a
  wrong value until it ends.
- **Total Energy comes from the charger's own energy register** instead of
  integrating power in Home Assistant. Its history has a one-time jump at the
  upgrade.
- **Max Charging Current is read from the charger**, so changes made outside
  Home Assistant show up. The charger sometimes ignores this read; then the
  last value set from Home Assistant, if any, is shown, and the read is retried after
  1, 2 and 5 minutes, then every 10 minutes.
- **Commands report failures.** If the charger rejects a command, or it can't
  be sent, the action fails with an error instead of failing silently.
  Automations that change settings often may want `continue_on_error`.
- **The charger's single local connection stays taken.** In 1.0 it was free
  between polls; now the mobile app may not connect locally while the
  integration is running. Disable the integration when you need the app.
- **Reload and options changes take about half a minute** before the entities
  are back: the charger keeps a closed connection busy for about 30 seconds,
  and the integration waits for it.
- **Energy Dashboard:** if you use Total Energy as a consumption source, the
  switch to the charger's register counts the difference as consumption in
  the hour of the upgrade. Fix it in Developer tools → Statistics → adjust
  sum.
- The L2/L3 voltage and current sensors (disabled by default) show unknown
  instead of a fixed 0.0; the library doesn't decode them yet.
- Settings are no longer sent to the charger when Home Assistant starts.
- Manual setup reads the device ID from the charger. Manual setups used to get
  a placeholder ID. Existing entries keep their ID, so entity IDs don't change.
- Discovery and manual setup never connect to a charger that is already
  configured (compared by host name and resolved address), so they don't take
  the connection from the running integration.
- The connection is closed cleanly on unload and when Home Assistant stops.
  A connection that stays open but stops sending status is closed and
  reopened.
- `iot_class` is now `local_push`.

### Removed

- The scan interval option, because the status is pushed. The switch debounce
  option stays.
- The Reset Total Energy button: the charger's register can't be reset. The
  entity is removed from the registry on upgrade.
- The device ID and scan interval fields of the manual setup form. The device
  ID is read from the charger.

### Fixed

- The options dialog (Configure) works again on current Home Assistant
  versions.
- The coordinator passes its config entry explicitly, as current Home
  Assistant requires.
- Discovery that finds nothing ends the flow with a message instead of an
  empty form that failed on submit.

## [1.0.0] - 2025-01-21

### Added

- **Local Control**: Direct TCP communication with Duosida EV wall chargers (port 9988), no cloud dependency
- **28 Entities**:
  - 17 sensors (status, voltage, current, power, temperature, energy, etc.)
  - 1 binary sensor (charging status)
  - 3 switches (charging, direct mode, stop on disconnect)
  - 4 number controls (max current, LED brightness, voltage thresholds)
  - 3 buttons (start charging, stop charging, reset total energy)
- **Settings Persistence**: Unknown-first approach - settings persist across restarts without assuming defaults
- **Total Energy Integration**: Calculates lifetime energy consumption using trapezoidal integration
- **Auto-Recovery**: Exponential backoff for connection failures
- **Charging Switch Debounce**: Configurable debounce (5-120s) prevents UI bounce during state transitions
- **Options Flow**: Reconfigure scan interval and switch debounce without removing the integration
- **Config Flow**: Setup wizard with automatic device discovery
- **20 Language Translations**: Full localization support
- **Home Assistant Energy Dashboard**: Compatible total energy sensor

### Technical

- 68 tests with 96% code coverage
- Strict typing with mypy
- Code quality with ruff and black
- Uses `duosida-ev` library (v0.1.3) for charger communication

[1.0.0]: https://github.com/americodias/duosida-ha/releases/tag/v1.0.0
