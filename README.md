# Ferroli Connect for Home Assistant

A custom Home Assistant integration for Ferroli boilers that are managed through the Ferroli Connect cloud account. It exposes two logical devices, **Calefacción** (central heating) and **Agua caliente sanitaria (ACS)** (domestic hot water), with read-only telemetry and gated control.

This is an unofficial community integration. It is not an official Ferroli product.

> **Status: early release (0.1.0).** The protocol was reverse-engineered from the Android app, and live behaviour has been verified only for a limited set of operations (see [Verification status](#verification-status)). Operate the boiler cautiously and confirm behaviour on your own installation.

## Features

- Sign in with a Ferroli Connect account and select a device during setup.
- Heating device: room thermostat (climate entity), heating water temperature and target, room temperature and targets, heating-active indicator, and a gated heating-water setpoint number.
- Domestic hot water (ACS) device: hot-water temperature and target, and a gated hot-water setpoint number.
- Re-authentication when the cloud session expires.
- Real-time boiler telemetry over MQTT (TLS), and room thermostat values polled over HTTPS.
- All operating commands are disabled until you explicitly enable control in the integration options.

## Requirements

- Home Assistant with support for config flows and custom integrations.
- A Ferroli Connect account with at least one device registered in the app.
- Network access from Home Assistant to the Ferroli cloud (HTTPS and MQTT over TLS).

The integration does not declare a minimum Home Assistant version yet. It has been run against Home Assistant Core 2026.10.x during development.

## Installation

### Option 1: HACS custom repository

1. In Home Assistant, open **HACS**.
2. Open the menu (⋮) and select **Custom repositories**.
3. Add the repository URL `https://github.com/netcraker01/ha-ferroli-connect` and choose category **Integration**.
4. Find **Ferroli Connect** in HACS, select **Download**, and restart Home Assistant.

### Option 2: Manual installation

1. Download or clone this repository.
2. Copy the folder `custom_components/ferroli_connect` into your Home Assistant configuration directory so that the result is `<config>/custom_components/ferroli_connect/`.
3. Restart Home Assistant.

Do not copy the whole repository into `custom_components`. Only the `ferroli_connect` folder belongs there.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for **Ferroli Connect**.
2. Enter your Ferroli Connect username and password.
3. Select the boiler device from the list.

The password is used only for login and re-authentication and is not stored. The config entry keeps the username, the access token, the user slug, and the selected device metadata.

### Re-authentication

If the session expires or the cloud rejects the token, Home Assistant shows a re-authentication prompt for the integration. Enter the username and password again. The integration then stores the new token and reloads.

### Enabling control

Control is off by default. To allow commands:

1. Open the **Ferroli Connect** integration and select **Configure**.
2. Enable **I understand and enable boiler control** and submit.

The integration reloads. Only then do the water setpoint number entities appear, and the room thermostat accepts commands. Disabling the option again makes the integration refuse commands.

## Devices and entities

Entity names are shown as Home Assistant displays them. Exact entity IDs depend on your naming.

### Calefacción (central heating)

| Entity | Type | Notes |
| --- | --- | --- |
| Room thermostat | Climate | Room thermostat in manual or off mode. See [Room thermostat](#room-thermostat). |
| Heating active | Binary sensor (running) | Boiler warming-up status flag. |
| Heating water temperature | Sensor (°C) | Current heating-water temperature from MQTT telemetry. |
| Heating water target | Sensor (°C) | Heating-water target reported by the boiler. |
| Heating water setpoint | Number (°C) | Only with control enabled. Changes the heating-water target. |
| Room temperature | Sensor (°C) | Current room temperature from the thermostat. |
| Room target | Sensor (°C) | Effective room target. Empty when no target applies (see below). |
| Room base target | Sensor (°C) | Thermostat base target point. |
| Room saved override target | Sensor (°C) | Saved override target, which is not necessarily active. |

### Agua caliente sanitaria (ACS, domestic hot water)

| Entity | Type | Notes |
| --- | --- | --- |
| Domestic hot water temperature | Sensor (°C) | Current hot-water temperature from MQTT telemetry. |
| Domestic hot water target | Sensor (°C) | Hot-water target reported by the boiler. |
| Domestic hot water setpoint | Number (°C) | Only with control enabled. Changes the hot-water target. |

Water setpoint limits come from the boiler's live minimum and maximum values. They are not hard-coded. A number entity is unavailable until valid limits have been received.

### Water temperatures are separate from room temperature

- **Heating water** and **domestic hot water** are the boiler's water circuits. Their temperatures and targets are reported by the boiler over MQTT.
- **Room temperature** and **room targets** come from the room thermostat, which is polled over HTTPS.
- A heating-water target is **not** a room target. Changing the heating-water setpoint does not change the room thermostat, and changing the room thermostat does not change the water setpoint.

## Room thermostat

The climate entity reflects the room thermostat's mode:

- **Off** (`HVACMode.OFF`) maps to thermostat mode 4.
- **Heat** (`HVACMode.HEAT`) maps to thermostat manual mode 1.
- Automatic (schedule) mode is shown without an HVAC mode, and its target is only the active saved override, if one is active. Scheduled program bands are not decoded.

Commands are gated:

- Setting a temperature requires control enabled, a known Off or manual mode, and a saved manual target. It writes the saved manual target while the thermostat stays in its current Off or manual mode. Accepted values are 5.0 to 35.0 °C in 0.1 °C steps.
- Setting **Heat** requires control enabled, a known Off or manual mode, and a saved manual target. It switches the thermostat to manual mode.
- Setting **Off** requires control enabled and a known Off or manual mode. It switches the thermostat to Off mode.
- Any other HVAC mode is rejected.

After each thermostat command, the integration reads the thermostat values back over HTTPS (up to three attempts). The entity state changes only if the readback matches the command. There is no optimistic update.

## Boiler water setpoints

Heating-water and hot-water setpoint numbers:

- Only exist and accept changes when control is enabled.
- Accept whole degrees Celsius within the live min/max limits reported by the boiler.
- Send one write, then a query. The write counts as confirmed only when a fresh boiler telemetry response (received after the query) reports the requested value. The wait times out after 8 seconds and the entity reports an error.

## Safety model

- **Control is opt-in.** Every write path checks the integration option first.
- **Relay control is not exposed.** The codec contains the boiler master relay encoding, but no Home Assistant entity sends it. Only the room thermostat and water setpoints can be changed.
- **No optimistic state.** Writes change the displayed state only after the device or cloud confirms the value.
- **Values are validated before sending**: finite numbers, whole degrees for water setpoints, 0.1 °C steps and 5–35 °C for room targets, and live limits for water setpoints.
- **Unknown state blocks writes.** If the room mode, manual target, or water limits are unknown, the corresponding write is refused.
- **Failures are explicit.** A command that is published but not confirmed raises an error instead of being reported as successful.

## Protocol evidence and limitations

The API and packet contract were derived by static inspection of the Ferroli Connect Android app, version 1.5.2 (`com.szacs.ferroliconnect`), and its decompiled source. The app's code was not executed, and the decompiled export is not part of this repository.

**Cloud API (HTTPS, base `https://eu-api.topband-cloud.com/`)**

- `POST api/token` for form login, `GET api/current_user`, and `GET api/users/{user_slug}/devices` for device listing.
- `GET api/mqtt/token` and `GET api/mqtt/app` for MQTT credentials and broker configuration.
- `POST api/token/refashion` to refresh an expired token.
- `GET apps/topiot_ferroli_temperature_controller/thermostat_points/{device}/{sdid}` to read room thermostat points.

**MQTT**

- Broker host, port, and topic templates come from the cloud configuration at setup. Topics are rendered only from the returned templates; unresolved placeholders fail setup.
- Connects over TLS. Subscribes to status and message topics at QoS 2 and publishes commands at QoS 1.
- Reconnects with exponential backoff (up to 60 seconds). Refreshes the MQTT password on reconnect.

**Packet format**

- Frame and TLV layout follow the app's SDK classes. Relay encoding, the heating-water target flag, the boiler ID, and the room thermostat flags are covered by offline tests that use fixed wire vectors.
- Decoded telemetry: heating-water and hot-water current and target temperatures, heating-active flag, and min/max limits. The boiler master relay state is decoded internally but not exposed as an entity.

**Limitations**

- The Ferroli weekly program (7×48 packed schedule) is **not implemented**. The encoding is not fully verified.
- Room schedule bands are not derived. In automatic mode, the room target is only shown when an override is active.
- DHW and summer mode are not modelled as heating.
- Boiler relay (on/off of the whole boiler) is not exposed. Do not treat the Heat/Off room thermostat modes as boiler on/off control.
- Only the operations listed under [Verification status](#verification-status) have been exercised on a real installation.

## Verification status

What the project's records and tests support:

- **Offline tests** cover codec vectors, malformed-packet handling, thermostat point parsing, room control encoding, gating and readback logic, entity contracts, and the legacy device cleanup guard.
- **Live, reported by the maintainer:** heating-water and hot-water setpoints were visible in Home Assistant. One room thermostat write was confirmed in Off mode: the saved manual target changed from 21.0 °C to 21.5 °C, and an independent REST read confirmed it while the mode stayed Off.

Not verified on a real installation yet:

- Setting the room thermostat to **Heat** (manual mode).
- Writing the **heating-water setpoint** and the **hot-water setpoint**, including the MQTT readback path.
- Heating-active and water telemetry changes over a full heating cycle.
- Behaviour during cloud outages, token expiry, and re-authentication.

Test on your installation before relying on any of these operations.

## Tests

The tests run without Home Assistant or a network connection.

```bash
python3 -m compileall -q custom_components tests
python3 tests/test_ferroli_codec.py
python3 tests/test_ferroli_runtime_control.py
```

`tests/test_ferroli_connect.py` contains contract tests written as pytest-style functions. They inspect the manifest, the API and config flow sources, and the entity definitions, and they include stubbed behaviour checks. Run them with pytest where it is available:

```bash
python3 -m pytest tests/test_ferroli_connect.py
```

The test files do not contact the cloud or MQTT.

## Troubleshooting

- **"Invalid username or password"**: check the credentials in the Ferroli Connect app, then re-authenticate.
- **"No devices found"**: the account has no devices visible through the cloud API.
- **"Unable to sign in or retrieve devices"**: check network access to `eu-api.topband-cloud.com`.
- **Entities unavailable**: the MQTT connection or the thermostat poll is failing. Enable debug logs (below) and look for `Ferroli MQTT connection failed` or `Ferroli thermostat poll failed`.
- **"Enable thermostat control in integration options first"** or similar: control is disabled. Enable it under **Configure**.
- **"Temperature changes require known OFF or manual mode"** or **"A valid saved manual target is required"**: the room thermostat is in automatic mode or has no saved manual target. Set manual mode on the thermostat itself first.
- **"Thermostat command was published but readback did not confirm it"**: the command may or may not have applied. Check the room thermostat values on the device before retrying.
- **"Boiler target write was not confirmed by fresh MQTT readback"**: the boiler did not report the new value within 8 seconds. Check the water setpoint on the device.
- **"Selected device lacks product_code/device_code"** or **"MQTT config is missing required dynamic topic templates"**: the cloud returned incomplete device or MQTT data. The integration does not guess topics; setup fails until the data is complete.
- **Legacy "Termostato" device**: an older version created a separate device. After a successful setup the integration removes it, but only when it is unique, owned by this entry, and has no linked entities. Otherwise it is left in place.

To enable debug logs, add the following to `configuration.yaml` and restart or reload logging:

```yaml
logger:
  logs:
    custom_components.ferroli_connect: debug
```

Do not post passwords, access tokens, or MQTT credentials in issue reports. Remove them from logs before sharing.

## Security

- The password is not stored. Only the access token is stored in the config entry.
- The access token is kept in Home Assistant's configuration storage. Anyone with access to your Home Assistant files or backups can use it, so protect your backups.
- Tokens are refreshed through the cloud's refresh endpoint when the server rejects them.
- No credentials, tokens, or device identifiers are included in this repository.
- The Ferroli Connect app package (APK) is retained only as a local analysis artifact, is not redistributed, and is excluded from version control.

## Contributing and issues

Please report issues with the Home Assistant version, integration version, relevant debug logs with secrets removed, and the boiler model where known.

## License

No license has been selected yet.
