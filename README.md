# Duosida EV Charger - Home Assistant Integration (Local Control)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Home Assistant integration for **local control** of Duosida EV wall chargers. This integration communicates directly with your charger over your local network, without requiring cloud services.

## Features

- **Local Control**: Direct TCP communication with your charger (no cloud dependency)
- **Auto-Discovery**: Automatically finds chargers on your network
- **Real-time Monitoring**: Voltage, current, power, temperature, energy consumption
- **Charging Control**: Start/stop charging, set max current (6-32A)
- **Configuration**: LED brightness, direct mode (plug and charge)
- **Per-phase sensors (L2, L3)**: present but not decoded yet, so they show unknown (disabled by default)

## Sensors

The charger pushes its status about every 10 seconds; all sensors update with each push.

| Sensor | Unit | Device Class | State Class | Description |
|--------|------|--------------|-------------|-------------|
| Status | - | - | - | Current charger state (Available, Preparing, Charging, Cooling, SuspendedEV, Finished, Holiday) |
| CP Voltage | V | voltage | measurement | Control Pilot voltage (indicates connection state) |
| Voltage (L1) | V | voltage | measurement | Line 1 voltage |
| Voltage L2* | V | voltage | measurement | Line 2 voltage (3-phase chargers) |
| Voltage L3* | V | voltage | measurement | Line 3 voltage (3-phase chargers) |
| Current (L1) | A | current | measurement | Line 1 charging current |
| Current L2* | A | current | measurement | Line 2 charging current (3-phase chargers) |
| Current L3* | A | current | measurement | Line 3 charging current (3-phase chargers) |
| Power | W | power | measurement | Instantaneous power consumption |
| Temperature | °C | temperature | measurement | Charger station temperature |
| Session Energy | kWh | energy | total_increasing | Energy consumed in current charging session |
| Session Time | h | duration | measurement | Duration of current charging session |
| Total Energy | kWh | energy | total_increasing | The charger's own energy register (survives sessions and restarts) |
| Model* | - | - | - | Charger model name |
| Manufacturer* | - | - | - | Manufacturer name |
| Firmware* | - | - | - | Firmware version |

*Disabled by default (enable in entity settings if needed)

## Controls

### Switches

| Entity | Description | Default State |
|--------|-------------|---------------|
| Charging | Start/stop charging (reads actual charging state) | Off |
| Direct Mode | Plug-and-charge mode (auto-start when vehicle connected) | Unknown until set |
| Stop on Disconnect | Automatically stop session when vehicle disconnects | Unknown until set |

### Numbers (Sliders)

| Entity | Range | Step | Description | Default |
|--------|-------|------|-------------|---------|
| Max Charging Current | 6-32 A | 1 A | Maximum allowed charging current | Read from the charger |
| LED Brightness* | 0, 1, 3 | - | Screen/LED brightness (0=off, 1=low, 3=high) | Unknown until set |
| Max Voltage* | 265-290 V | 1 V | Maximum voltage protection threshold | Unknown until set |
| Min Voltage* | 70-110 V | 1 V | Minimum voltage protection threshold | Unknown until set |

*Disabled by default (enable in entity settings if needed)

### Buttons

| Entity | Action | Description |
|--------|--------|-------------|
| Start Charging* | Sends start command | Start charging (alternative to switch) |
| Stop Charging* | Sends stop command | Stop charging (alternative to switch) |

*Disabled by default (enable in entity settings if needed)

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right corner
3. Select "Custom repositories"
4. Add this repository URL: `https://github.com/americodias/duosida-ha`
5. Select "Integration" as the category
6. Click "Add"
7. Search for "Duosida" and install
8. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/duosida_ev` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Duosida"
4. Choose auto-discovery or manual configuration
5. If manual, enter your charger's IP address

## Requirements

- Duosida EV wall charger on your local network
- Network access to TCP port 9988 on the charger
- Home Assistant 2024.12 or newer

## Tested On

- Duosida SmartChargePI single-phase 7.2 kW

## Comparison with Cloud Integration

| Feature | This Integration | Cloud Integration |
|---------|-----------------|-------------------|
| Connection | Local TCP | Cloud API |
| Latency | Low (~10ms) | High (1-2s) |
| Internet Required | No | Yes |
| Privacy | Full control | Data in cloud |
| Reliability | High | Depends on cloud |

## Example Automations

### Solar-Powered Charging

Automatically adjust charging current based on solar production:

```yaml
automation:
  - alias: "EV - Adjust charging to solar production"
    trigger:
      - platform: state
        entity_id: sensor.solar_power
    condition:
      - condition: state
        entity_id: switch.duosida_ev_charger_charging
        state: "on"
    action:
      - service: number.set_value
        target:
          entity_id: number.duosida_ev_charger_max_charging_current
        data:
          value: >
            {% set solar = states('sensor.solar_power') | float(0) %}
            {% set current = (solar / 230) | round(0, 'floor') %}
            {{ [6, [current, 32] | min] | max }}
```

### Charge During Off-Peak Hours

Start charging automatically during cheap electricity hours:

```yaml
automation:
  - alias: "EV - Start charging at off-peak"
    trigger:
      - platform: time
        at: "23:00:00"
    condition:
      - condition: state
        entity_id: binary_sensor.car_connected
        state: "on"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.duosida_ev_charger_charging
      - service: number.set_value
        target:
          entity_id: number.duosida_ev_charger_max_charging_current
        data:
          value: 32

  - alias: "EV - Stop charging at peak hours"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.duosida_ev_charger_charging
```

### Temperature-Based Current Limiting

Reduce charging current if charger gets too hot:

```yaml
automation:
  - alias: "EV - Reduce current on high temperature"
    trigger:
      - platform: numeric_state
        entity_id: sensor.duosida_ev_charger_temperature
        above: 60
    action:
      - service: number.set_value
        target:
          entity_id: number.duosida_ev_charger_max_charging_current
        data:
          value: 16
      - service: notify.mobile_app
        data:
          title: "EV Charger Temperature Warning"
          message: "Charging current reduced due to high temperature"

  - alias: "EV - Restore current on normal temperature"
    trigger:
      - platform: numeric_state
        entity_id: sensor.duosida_ev_charger_temperature
        below: 50
    action:
      - service: number.set_value
        target:
          entity_id: number.duosida_ev_charger_max_charging_current
        data:
          value: 32
```

### Energy Dashboard Integration

The Total Energy sensor is automatically compatible with Home Assistant's Energy Dashboard. To add it:

1. Go to **Settings** → **Dashboards** → **Energy**
2. Click **Add Consumption**
3. Select **Duosida EV Charger Total Energy**
4. Save

## FAQ

### Why do some settings show "Unknown"?

The maximum charging current is read from the charger. Some chargers occasionally ignore that read; the integration then shows the last value set from Home Assistant, if any, and retries after 1, 2 and 5 minutes, then every 10 minutes. The other settings (LED brightness, direct mode, voltage thresholds, stop on disconnect) are not read back reliably yet, so they start as "Unknown" until you set them from Home Assistant. Once set, the value is remembered across restarts. The integration never writes a setting on its own, for example at startup.

### What's the difference between Session Energy and Total Energy?

- **Session Energy**: Reported by the charger, resets when you start a new charging session
- **Total Energy**: The charger's own energy register, which only goes up (like an odometer)

Use Total Energy for the Energy Dashboard and lifetime tracking. Use Session Energy to see how much a single charge consumed.

### Why can't I set LED Brightness to 2?

The charger hardware only supports brightness values of 0 (off), 1 (low), and 3 (high). Value 2 is not accepted by the charger's firmware.

### How do I find my charger's IP address?

1. Use the auto-discovery feature when adding the integration
2. Check your router's DHCP client list for "SmartChargePI"
3. Use a network scanner app on your phone

Tip: Set a DHCP reservation in your router to keep the IP address stable.

### Does this work offline?

Yes! This integration communicates directly with your charger over your local network. No internet connection is required.

### Can I use this alongside the mobile app?

The charger accepts only one local connection at a time, and Home Assistant keeps that connection open. The app may therefore fail to connect locally while the integration is running. If you need the app, disable the integration temporarily. Changes made in the app to the maximum current show up in Home Assistant; other settings don't, because the charger doesn't report them back.

### What's "Direct Mode"?

Direct mode (also called plug-and-charge) automatically starts charging when you plug in your vehicle, without needing to start it manually via the app or Home Assistant.

### The charger supports Stop on Disconnect, but I can't see the current state?

Like other configuration settings, the charger doesn't report the current state. Once you set it via Home Assistant, the integration will remember and display the value you set.

## Troubleshooting

### Charger not discovered

**Symptoms:** Auto-discovery doesn't find your charger

**Solutions:**
- Ensure your charger and Home Assistant are on the same network (same subnet)
- Check that UDP ports 48890 and 48899 are not blocked by firewall
- Some routers block UDP broadcast - check router settings
- Try manual configuration with the charger's IP address instead
- Restart the charger and try discovery again

### Cannot connect to charger

**Symptoms:** "Failed to connect" error during setup

**Solutions:**
- Verify the charger is powered on and connected to network
- Check that TCP port 9988 is accessible from Home Assistant
- Try pinging the charger IP from Home Assistant host: `ping <charger_ip>`
- Check firewall rules on your network
- Try restarting the charger by power cycling it

### Entities show "Unavailable"

**Symptoms:** All entities become unavailable

**Solutions:**
- Check the Home Assistant logs: **Settings** → **System** → **Logs**, filter for "duosida"
- Make sure nothing else holds the charger's single local connection (the mobile app, another integration)
- After losing the connection, the integration waits before reconnecting (about 35 seconds after a close, then 1 to 5 minutes between failed attempts), because the charger refuses fast reconnects. A reload or a change of options therefore leaves the entities unavailable for about half a minute.
- Keep **Enable polling for updates** on in the integration's system options: the integration uses that periodic check to reconnect
- Verify network connectivity to charger
- Check if charger is responding: `telnet <charger_ip> 9988`
- Restart the integration: **Settings** → **Devices & Services** → **Duosida** → **⋮** → **Reload**
- If problem persists, restart Home Assistant

### Values not updating

**Symptoms:** Sensor values frozen or not changing

**Solutions:**
- The charger pushes its status about every 10 seconds. If the connection drops, or stays open without sending status, the integration closes it and reconnects; that can take 1 to 3 minutes
- Enable debug logging to see communication details:
  ```yaml
  logger:
    logs:
      custom_components.duosida_ev: debug
  ```
- Check Home Assistant logs for connection errors
- Verify charger is actually changing state (e.g., actually charging)

### Settings don't persist

**Symptoms:** LED brightness, direct mode, etc. reset after HA restart

**Solutions:**
- Check that `.storage/duosida_ev.*` file exists in your HA config directory
- Ensure Home Assistant has write permissions to the `.storage/` directory
- Check logs for storage-related errors
- Try setting the value again and verify it persists

### Integration won't load after update

**Symptoms:** Integration fails to load after updating

**Solutions:**
1. Clear browser cache (Ctrl+F5 or Cmd+Shift+R)
2. Restart Home Assistant completely
3. Check logs for errors
4. Avoid removing and re-adding the integration: a new entry gets the charger's real device ID, which may differ from the one stored in an entry created by version 1.0. That would change every entity ID and break automations and dashboards that use them.

### High temperature warnings

**Symptoms:** Temperature sensor shows >60°C

**Solutions:**
- Ensure adequate ventilation around the charger
- Check if charger is in direct sunlight
- Reduce max charging current temporarily
- Consider using the temperature-based automation example above
- If temperature exceeds 80°C, stop charging and contact support

### Debug logging

To enable detailed debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.duosida_ev: debug
    custom_components.duosida_ev.coordinator: debug
```

This will log all communication with the charger, helpful for troubleshooting connection issues.

### Getting help

If you can't resolve the issue:

1. Enable debug logging (see above)
2. Reproduce the problem
3. Check logs: **Settings** → **System** → **Logs**
4. Open an issue on [GitHub](https://github.com/americodias/duosida-ha/issues) with:
   - Home Assistant version
   - Integration version
   - Charger model
   - Relevant log entries
   - Steps to reproduce

## Credits

- Based on the [duosida-ev](https://github.com/americodias/duosida-ev) Python library
- Inspired by [jello1974/duosidaEV-home-assistant](https://github.com/jello1974/duosidaEV-home-assistant)

## License

MIT License - see [LICENSE](LICENSE) file
