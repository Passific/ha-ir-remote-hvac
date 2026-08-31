# IR Remote HVAC
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/Passific/ha-ir-remote-hvac)](https://github.com/Passific/ha-ir-remote-hvac/releases)
[![License](https://img.shields.io/github/license/Passific/ha-ir-remote-hvac)](LICENSE)
[![Validate](https://github.com/Passific/ha-ir-remote-hvac/actions/workflows/ci.yml/badge.svg)](https://github.com/Passific/ha-ir-remote-hvac/actions/workflows/ci.yml)

<img src="custom_components/irremote_hvac/brand/dark_logo.svg" alt="Firefly III icon" height="64" align="right">

Home Assistant custom integration for IR-controlled HVAC units using `pyhvac` and IRremoteESP8266-compatible protocol definitions.

## Features

- Control HVAC power and operating mode.
- Set target temperature, fan mode, swing mode, and presets when supported by the selected protocol/model.
- Restore the last known AC state after a Home Assistant restart.
- Optional temperature, humidity, and power sensor inputs for richer state and availability handling.

## Requirements

- Home Assistant
- `pyhvac>=0.1.6`
- An IR emitter integration such as ESPHome or Broadlink

## Installation

### HACS

1. Make sure [HACS](https://hacs.xyz/) is installed.
2. Go to **HACS > Integrations > ⋮ > Custom repositories**.
3. Add `https://github.com/Passific/ha-ir-remote-hvac` as an **Integration** repository.
4. Search for **IR Remote HVAC** in HACS and install it.
5. Restart Home Assistant.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Passific&repository=ha-ir-remote-hvac&category=integration)

### Manual

1. Copy `custom_components/irremote_hvac` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from the Home Assistant UI.

## Configuration

Set up the integration from the Home Assistant UI.

You will need to provide:

- An IR emitter entity
- The HVAC protocol
- The optional model variant, if your device needs one
- Optional temperature, humidity, and power sensors
- Optional timing and temperature range options

## Notes

- The integration is designed for local IR control.
- Supported behavior depends on the selected protocol/model combination.
- The repository includes a vendored copy of IRremoteESP8266 source material for reference and protocol alignment.

## Development

Run the test suite with the Home Assistant test harness. Home Assistant 2026.3 and later require Python 3.14.2 or newer.

```bash
uv venv --python 3.14.2 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

## License

MIT
