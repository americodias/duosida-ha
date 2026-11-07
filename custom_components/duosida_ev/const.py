"""Constants for the Duosida EV Charger integration."""

from typing import Final

DOMAIN: Final = "duosida_ev"
NAME: Final = "Duosida EV Charger"

# Keys in hass.data[DOMAIN]
COORDINATOR: Final = "coordinator"
RECONNECT_GATES: Final = "reconnect_gates"  # per charger host

# Config entry keys
CONF_DEVICE_ID: Final = "device_id"
CONF_SWITCH_DEBOUNCE: Final = "switch_debounce"

DEFAULT_PORT: Final = 9988

# After a start/stop command the charging switch keeps its optimistic state for
# this many seconds, so it doesn't bounce while the charger changes state.
DEFAULT_SWITCH_DEBOUNCE: Final = 30

# The charger pushes its status about every 10 s over one persistent session.
# The coordinator's periodic refresh is only a watchdog: it runs when no push
# arrived for this many seconds, and reconnects if the session dropped.
WATCHDOG_INTERVAL: Final = 30

# Longest wait for a status push after (re)connecting.
STATUS_TIMEOUT: Final = 25

# Seconds before each reconnect attempt after a failure. The charger has a
# single TCP session and refuses fast reconnects (for minutes, sometimes
# longer), so attempts are spaced out.
RECONNECT_BACKOFF: Final = (60, 120, 300)

# The charger keeps its session busy for about 30 s after any close, so no new
# connection is opened sooner than this after one ends (reload, config flow).
SESSION_RELEASE_DELAY: Final = 35

# Close a session that stays connected without sending status for this many
# watchdog refreshes in a row, so the next refresh can reconnect.
SILENT_REFRESHES_BEFORE_RECONNECT: Final = 2

# Re-read an answered configuration after this many seconds (it only changes
# when written).
CONFIGURATION_REFRESH_INTERVAL: Final = 600

# The configuration is read this many seconds after a status push, from a
# timer, never straight from the push. The charger ignores some reads without
# answering (cause unknown; a capture shows the vendor app's read ignored too).
CONFIGURATION_READ_DELAY: Final = 3

# After a read gets no answer, the next one is a new request (new message id)
# after these many seconds, then every CONFIGURATION_REFRESH_INTERVAL.
CONFIGURATION_RETRY_BACKOFF: Final = (60, 120, 300)

# Connection status codes as this integration has always shown them. The raw
# code comes from the charger; the names are kept stable for automations.
STATUS_CODES: Final = {
    0: "Available",
    1: "Preparing",
    2: "Charging",
    3: "Cooling",
    4: "SuspendedEV",
    5: "Finished",
    6: "Holiday",
}
