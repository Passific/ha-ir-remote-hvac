# IR Remote HVAC

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

1. Add this repository as a custom repository in HACS.
2. Install the integration.
3. Restart Home Assistant.
4. Add the integration from the Home Assistant UI and select your IR emitter entity.

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
