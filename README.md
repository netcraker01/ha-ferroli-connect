<p align="center">
  <img src="custom_components/ferroli_connect/brand/logo.png" alt="Ferroli Connect" width="300">
</p>

<p align="center">Connect Home Assistant to your Ferroli Connect cloud account for live telemetry and carefully gated thermostat and boiler water setpoint controls.</p>

<p align="center">
  <img src="https://img.shields.io/github/v/release/netcraker01/ha-ferroli-connect?label=release" alt="GitHub release">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <a href="https://github.com/netcraker01/ha-ferroli-connect/actions/workflows/hacs.yml"><img src="https://github.com/netcraker01/ha-ferroli-connect/actions/workflows/hacs.yml/badge.svg" alt="HACS validation"></a>
  <a href="https://github.com/netcraker01/ha-ferroli-connect/actions/workflows/hassfest.yml"><img src="https://github.com/netcraker01/ha-ferroli-connect/actions/workflows/hassfest.yml/badge.svg" alt="Hassfest validation"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.10-blue" alt="Tested with Home Assistant 2026.10">
</p>

This is an unofficial community integration; it is not an official Ferroli product. The protocol was reverse-engineered from the Ferroli Connect Android app, and only a limited set of operations has been exercised on real hardware. Use the integration cautiously and confirm behaviour on your installation.

## Why Ferroli Connect

- ☁️ Connects directly to the Ferroli Connect cloud account—no DNS workarounds or local broker setup to manage.
- 🧭 Guided configuration flow with account login and a device picker.
- 🌡️ Room thermostat and boiler water target controls are separately opt-in and confirmed by readback telemetry.
- 📡 Live MQTT boiler telemetry plus thermostat temperatures and target values polled over HTTPS.
- 🌍 Home Assistant UI strings are available in English and Spanish.
- 👐 MIT-licensed, unofficial community project.

## Features and entities

The integration provides `climate`, `binary_sensor`, and `sensor` platforms. The `number` platform is added only when boiler setpoint control is explicitly enabled. Entity names below are their Home Assistant names; entity IDs depend on your naming settings.

| Entity | Platform | Behaviour |
| --- | --- | --- |
| Room thermostat | Climate | Reports room temperature, target and thermostat state. Verified Heat/Off mode and temperature-target controls are gated by the control-safety option. In automatic mode, target reporting is limited to an active saved override; scheduled bands are not decoded. |
| Heating active | Binary sensor | Read-only boiler heating-active status from MQTT telemetry. |
| Heating modulation | Sensor | Read-only boiler heating modulation percentage from MQTT telemetry (`0–100%`). |
| Heating water temperature | Sensor | Read-only current heating-water temperature from MQTT telemetry. |
| Heating water target | Sensor / Number (opt-in) | Target telemetry; a gated Number control writes integer °C and requires matching MQTT telemetry readback. |
| Domestic hot water temperature | Sensor | Read-only current DHW temperature from MQTT telemetry. |
| Domestic hot water target | Sensor / Number (opt-in) | DHW target telemetry; a gated Number control writes integer °C and requires matching MQTT telemetry readback. |
| Room temperature | Sensor | Read-only thermostat room temperature. |
| Room target | Sensor | Read-only effective room target; may be empty if no target applies. |
| Room base target | Sensor | Read-only thermostat base target point. |
| Room saved override target | Sensor | Read-only saved override target; it is not necessarily the active target. |

Room thermostat values are polled over HTTPS. Boiler telemetry is received over MQTT using TLS. The integration does not expose battery entities in the current implementation.

## Requirements

- Home Assistant with support for config flows and custom integrations. The integration has been run against Home Assistant Core 2026.10.x; it does not declare a minimum version.
- A Ferroli Connect cloud account with at least one device registered in the app.
- Network access from Home Assistant to the Ferroli cloud (HTTPS and MQTT over TLS).

## Installation

### Option 1: HACS

1. Open **HACS** in Home Assistant.
2. Search for **Ferroli Connect**.
3. Select **Download** and restart Home Assistant.

The repository is included in the HACS default list.

### Option 2: Custom repository (fallback)

Use this fallback if the default listing is not available.

1. In Home Assistant, open **HACS**.
2. Open the menu (⋮) and select **Custom repositories**.
3. Add the repository URL `https://github.com/netcraker01/ha-ferroli-connect` and choose category **Integration**.
4. Find **Ferroli Connect** in HACS, select **Download**, and restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → Add integration** and search for **Ferroli Connect**.
2. Enter your Ferroli Connect username and password.
3. Select the device from the list.

The password is used for login and re-authentication and is not stored. Home Assistant's config entry stores the username, access token, user slug, and selected device metadata. Protect Home Assistant configuration files and backups because they contain the access token.

### Re-authentication

If the cloud session expires or rejects the token, Home Assistant prompts you to re-authenticate. Enter the username and password again; the integration stores the renewed token and reloads.

### Control safety option

Controls are disabled by default. In **Configure**, enable thermostat temperature/Heat-Off control and, separately, **I understand and enable boiler water setpoint controls** to expose heating-water and DHW Number entities. All controls require readback confirmation; disabling the boiler option removes the Number platform. The integration reloads after options change.

## Safety & trust

- Telemetry is read-only by default; control requires an explicit opt-in.
- Verified writes are the room thermostat target, Heat/Off thermostat modes, heating-water setpoint, and DHW setpoint; all were confirmed with readback on real hardware. Master-relay control is not a writable feature.
- A command requires fresh, known thermostat state and a valid saved manual target. After publishing, the integration reads values back over HTTPS (up to three attempts); it updates state only if the result matches, with no optimistic update.
- The implementation validates the thermostat mode and target before sending. Heat/Off mode commands are limited to known manual or off states. Unknown state or an unconfirmed readback blocks/reports failure rather than claiming success.
- Boiler heating modulation is decoded from the source-observed `0x2023` one-byte percentage field; values above 100 or malformed lengths are rejected.

Room target range is 5.0–35.0 °C in 0.1 °C steps and requires known Off or manual mode and saved manual target. Boiler heating-water range is 25–85 °C; DHW is 30–65 °C, both in 1 °C steps. Each write updates state only after matching readback.

## Cloud and local-broker approaches

This comparison describes this integration and a typical local-MQTT Ferroli approach (for example, [josesblas/ferroli-connect](https://github.com/josesblas/ferroli-connect)); details can vary by project and setup.

| Topic | Ferroli Connect (this) | Local-broker approach |
| --- | --- | --- |
| Setup effort | Guided cloud login and device selection in Home Assistant. | Typically requires a reachable local MQTT broker and integration-specific broker/device configuration. |
| Cloud dependency | Requires Ferroli Connect cloud HTTPS and MQTT over TLS. | Typically communicates through a local broker; cloud dependence varies by implementation and device provisioning. |
| Privacy model | Account authentication and telemetry/control traffic use Ferroli's cloud services; Home Assistant stores an access token. | Can keep device communication local, depending on the implementation and any cloud-dependent provisioning. |
| Verified control writes | Room thermostat target, Heat/Off modes, heating-water and DHW targets; writes were confirmed by live readback. | Control capabilities and verification depend on the specific project and hardware; check its documentation. |
| Translations | English and Spanish UI strings. | Translation availability depends on the specific project. |

## Protocol evidence and limitations

The API and packet contract were derived by static inspection of Ferroli Connect Android app version 1.5.2 (`com.szacs.ferroliconnect`) and its decompiled source. The app was not executed, and the decompiled export is not part of this repository.

**Cloud API:** The integration uses `https://eu-api.topband-cloud.com/` for form login (`POST api/token`), current-user and device lookup, MQTT credentials/configuration, token refresh, and room thermostat points.

**MQTT:** Broker configuration and topic templates come from the cloud. The integration connects over TLS, subscribes to status/message topics at QoS 2, publishes commands at QoS 1, reconnects with exponential backoff up to 60 seconds, and refreshes the MQTT password on reconnect. Incomplete device metadata or unresolved topic templates fail setup instead of being guessed.

**Limitations:** The weekly 7×48 Ferroli program and room schedule bands are not implemented; in automatic mode the room target is shown only when an override is active. DHW and summer modes are not modelled as heating. Boiler relay on/off is not controllable. Behaviour through cloud outages, token expiry, and re-authentication has not been live-verified. Offline tests cover codec vectors, malformed packets, thermostat point parsing, control encoding/gating/readback, entity contracts, and the legacy device cleanup guard; tests do not contact the cloud or MQTT.

## FAQ

### Where do my credentials go, and what is stored?

The password is used to authenticate and re-authenticate but is not stored. The username, access token, user slug, and selected device metadata are stored in Home Assistant's config entry. Protect your Home Assistant files and backups.

### How do I enable boiler water setpoint controls?

Enable **I understand and enable boiler water setpoint controls** separately in integration options. Heating-water and DHW writes have been readback-tested on a real boiler; the controls remain disabled by default and require matching MQTT telemetry before success is reported.

### Which Home Assistant version has been tested?

Home Assistant Core 2026.10.x. No minimum version is declared yet.

### Will it work with my boiler?

It is intended for devices registered to supported Ferroli Connect cloud accounts/receivers and visible through the account's device list. Compatibility depends on the account, receiver, and device data returned by the cloud; not every model has been verified. The integration requires cloud access from Home Assistant.

### How do I report an issue?

Open a GitHub issue with your Home Assistant and integration versions, relevant debug logs with secrets removed, and the boiler model if known. Never share passwords, access tokens, or MQTT credentials.

## Troubleshooting

- **`invalid_auth` / “Invalid username or password”**: check the credentials in the Ferroli Connect app, then retry setup or re-authenticate.
- **`no_devices` / “No devices found”**: the account has no devices visible through the cloud API.
- **`cannot_connect` / “Unable to sign in or retrieve devices”**: sign-in or device retrieval failed; check Home Assistant network access to `eu-api.topband-cloud.com`.
- **Entities unavailable**: the MQTT connection or thermostat poll may be failing. Enable debug logs and look for `Ferroli MQTT connection failed` or `Ferroli thermostat poll failed`.
- **“Enable thermostat control in integration options first”**: enable the control-safety option under **Configure**.
- **“Temperature changes require known OFF or manual mode”** or **“A valid saved manual target is required”**: the thermostat mode or saved manual target is not suitable/known. Set manual mode on the thermostat itself first.
- **“Thermostat command was published but readback did not confirm it”**: the command may or may not have applied. Check the thermostat values on the device before retrying.
- **“Selected device lacks product_code/device_code”** or missing MQTT topic templates: the cloud returned incomplete device/MQTT data. Setup stops rather than guessing topics.
- **Legacy “Termostato” device**: an older version may have created a separate device. After successful setup, the integration removes it only when it is unique, owned by this entry, and has no linked entities; otherwise it is left in place.

To enable debug logging, add this to `configuration.yaml` and restart Home Assistant or reload logging:

```yaml
logger:
  logs:
    custom_components.ferroli_connect: debug
```

Remove secrets from logs before sharing them.

## Tests

The tests run without Home Assistant or a network connection:

```bash
python3 -m compileall -q custom_components tests
python3 tests/test_ferroli_codec.py
python3 -m pytest tests/test_ferroli_connect.py
```

## License

Licensed under the [MIT License](LICENSE).
