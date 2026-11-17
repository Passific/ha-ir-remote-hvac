# IR Remote HVAC
[![Marketplace](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg)](https://www.home-assistant.io/integrations/marketplace)
[![Release](https://img.shields.io/github/v/release/Passific/ha-ir-remote-hvac)](https://github.com/Passific/ha-ir-remote-hvac/releases)
[![Downloads](https://img.shields.io/github/downloads/passific/ha-ir-remote-hvac/total)](https://github.com/Passific/ha-ir-remote-hvac/releases)
[![License](https://img.shields.io/github/license/Passific/ha-ir-remote-hvac)](LICENSE)
[![Validate](https://github.com/Passific/ha-ir-remote-hvac/actions/workflows/ci.yml/badge.svg)](https://github.com/Passific/ha-ir-remote-hvac/actions/workflows/ci.yml)

<img src="custom_components/irremote_hvac/brand/dark_icon.svg" alt="IR Remote HVAC icon" width="96" height="96" align="right">

Home Assistant custom integration for IR-controlled HVAC units using the pure-Python `pyhvac` device registry and Home Assistant infrared emitters.

## Features

- Control HVAC power and operating mode.
- Set target temperature, fan mode, swing mode, and presets when supported by the selected brand/model.
- Restore the last known AC state after a Home Assistant restart.
- Optional temperature, humidity, and power sensor inputs for richer state and availability handling.

## Requirements

- Home Assistant 2026.11 or newer
- An IR emitter integration such as ESPHome or Broadlink
- pyhvac 0.2.x (installed automatically by Home Assistant)

## Installation

### Marketplace (recommended)

1. Go to **Settings > Marketplace**.
2. In the upper-right corner, select **Add from link**.
3. Add `https://github.com/Passific/ha-ir-remote-hvac` under **GitHub link**.
4. Select **Add**.
5. Search for **IR Remote HVAC** in the Marketplace and install it.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=Passific&repository=ha-ir-remote-hvac&category=integration)

### Manual

1. Download the latest release from the [releases page](https://github.com/Passific/ha-ir-remote-hvac/releases).
2. Copy `custom_components/irremote_hvac` into your Home Assistant `custom_components` directory.
3. Restart Home Assistant.
4. Add the integration from the Home Assistant UI.

## Configuration

Set up the integration from the Home Assistant UI.

You will need to provide:

- An IR emitter entity
- The HVAC brand and matching model or remote
- Optional temperature, humidity, and power sensors
- Optional timing and temperature range options

You can reconfigure an existing entry from the Home Assistant integration page to change the device name, IR emitter, brand, model, or temperature step. The options flow lets you adjust temperature range, debounce delay, optional sensor entities, and the power threshold. Some legacy protocol/model entries cannot be mapped safely; reconfigure those entries and select the correct brand and remote.

If automatic migration fails, a repair issue explains how to reconfigure the affected entry. It clears after successful migration or reconfiguration.

## Supported Devices And Functions

This integration uses pyhvac 0.2's brand/model registry and sends generated raw IR signals through Home Assistant infrared emitters. The available modes and controls depend on the selected device capabilities.

Supported functions include power, HVAC mode, target temperature, fan mode, swing mode, and presets when supported by the selected device.

## Examples

After setup, add the climate entity to a standard Home Assistant thermostat card. If a power sensor is configured, the entity can report idle versus active heating/cooling based on the configured watt threshold.

## Removal

1. Go to **Settings > Marketplace**.
2. Open the page of the repository.
3. Select the three dots ⋮ menu in the upper-right corner, then select **Uninstall**.
   - If the integration is still configured, select **View integration** to remove the integration setup yourself, or **Delete and uninstall** to remove the setup and uninstall the integration in one step.
4. Confirm.

## Notes

- The integration is designed for local IR control.
- Supported behavior depends on the selected brand/model combination.

## Troubleshooting

- If no emitters are listed during setup, configure an infrared emitter integration first.
- If the HVAC does not respond, verify line of sight, the selected brand/model, and the emitter entity.
- If an optional temperature, humidity, power sensor, or emitter becomes unavailable, Home Assistant will create a repair issue after a grace period.

## Development

Run the tests on Linux with Python 3.14.2 or newer:

```bash
uv venv --python 3.14.2 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

## License

MIT
