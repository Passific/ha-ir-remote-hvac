"""Config flow for IR Remote HVAC integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import (
    infrared,  # type: ignore[reportAttributeAccessIssue]
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from pyhvac import irhvac  # type: ignore[reportMissingImports]

from .const import (
    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID,
    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID,
    CONF_DEBOUNCE_DELAY,
    CONF_EMITTER_ENTITY_ID,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_MODEL,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_POWER_THRESHOLD,
    CONF_PROTOCOL,
    CONF_TEMP_STEP,
    DEFAULT_DEBOUNCE_DELAY,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_MODEL,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_TEMP_STEP,
    DOMAIN,
    SUPPORTED_PROTOCOLS,
)

_LOGGER = logging.getLogger(__name__)

PROTOCOL_LABELS = sorted(SUPPORTED_PROTOCOLS.keys())
CONF_SHOW_ADVANCED = "show_advanced"
TEMP_STEP_HALF = "0_5"
TEMP_STEP_WHOLE = "1_0"
DEFAULT_TEMP_STEP_SELECTOR_VALUE = TEMP_STEP_WHOLE
TEMP_STEP_SELECTOR_VALUES = {
    TEMP_STEP_HALF: 0.5,
    TEMP_STEP_WHOLE: 1.0,
}


def _temp_step_to_selector_value(temp_step: Any) -> str:
    """Convert a stored temp step value to a valid selector option key."""
    try:
        stored_temp_step = float(temp_step)
    except (TypeError, ValueError):
        return DEFAULT_TEMP_STEP_SELECTOR_VALUE

    for selector_value, value in TEMP_STEP_SELECTOR_VALUES.items():
        if stored_temp_step == value:
            return selector_value

    return DEFAULT_TEMP_STEP_SELECTOR_VALUE


def _selector_value_to_temp_step(selector_value: str) -> float | None:
    """Convert a selector option key back to a temp step value."""
    return TEMP_STEP_SELECTOR_VALUES.get(selector_value)


def _supports_half_degree_step(protocol_const: str, model: int) -> bool:
    """Return True when the protocol/model appears to support 0.5 C steps."""
    if not hasattr(irhvac, protocol_const):
        return False

    try:
        ac = irhvac.IRac(0)
        ac.next.protocol = getattr(irhvac, protocol_const)
        ac.next.model = model
        ac.next.celsius = True
        ac.next.power = True

        # Use COOL when available so temperature changes are encoded.
        if hasattr(irhvac, "opmode_t_kCool"):
            ac.next.mode = irhvac.opmode_t_kCool

        ac.next.degrees = 22.0
        ac.resetTiming()
        ac.sendAc()
        timings_whole = list(ac.getTiming())
        ac.resetTiming()

        ac.next.degrees = 22.5
        ac.sendAc()
        timings_half = list(ac.getTiming())
        ac.resetTiming()

        # If half-step is supported, command payload should change.
        return bool(timings_whole and timings_half and timings_whole != timings_half)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug(
            "Failed to probe temp_step support for protocol=%s model=%s: %s",
            protocol_const,
            model,
            err,
        )
        return False


def _supported_temp_step_options(protocol_const: str, model: int) -> list[str]:
    """Return supported temp_step options as selector values."""
    options = [TEMP_STEP_WHOLE]
    if _supports_half_degree_step(protocol_const, model):
        options.insert(0, TEMP_STEP_HALF)
    return options


def _probe_timings(protocol_const: str, model: int) -> list[int] | None:
    """Return generated timings for a probe state, or None if unavailable."""
    if not hasattr(irhvac, protocol_const):
        return None

    try:
        ac = irhvac.IRac(0)
        ac.next.protocol = getattr(irhvac, protocol_const)
        ac.next.model = model
        ac.next.celsius = True
        ac.next.power = True

        if hasattr(irhvac, "opmode_t_kCool"):
            ac.next.mode = irhvac.opmode_t_kCool

        ac.next.degrees = 22.0
        ac.resetTiming()
        ac.sendAc()
        timings = list(ac.getTiming())
        ac.resetTiming()
        return timings or None
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug(
            "Failed to probe timings for protocol=%s model=%s: %s",
            protocol_const,
            model,
            err,
        )
        return None


def _supports_model_variants(protocol_const: str) -> bool:
    """Return True when the protocol appears to expose model variants."""
    default_timings = _probe_timings(protocol_const, DEFAULT_MODEL)

    for candidate_model in (0, 1, 2, 3, 4, 5, 10):
        candidate_timings = _probe_timings(protocol_const, candidate_model)
        if candidate_timings is None:
            continue

        if default_timings is None or candidate_timings != default_timings:
            return True

    return False


class IrRemoteHvacConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for IR Remote HVAC."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._is_reconfigure = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: select emitter entity and give the device a name."""
        errors: dict[str, str] = {}

        emitters = infrared.async_get_emitters(self.hass)
        if not emitters:
            return self.async_abort(reason="no_emitters")

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_protocol()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_EMITTER_ENTITY_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=emitters,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_protocol(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: select the AC protocol and optionally open advanced settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            protocol_label = user_input[CONF_PROTOCOL]
            protocol_const = SUPPORTED_PROTOCOLS.get(protocol_label)
            if protocol_const is None:
                errors[CONF_PROTOCOL] = "invalid_protocol"
            else:
                # Validate that the constant actually exists in irhvac
                if not hasattr(irhvac, protocol_const):
                    errors[CONF_PROTOCOL] = "protocol_unavailable"

            if not errors:
                self._data[CONF_PROTOCOL] = protocol_const
                if user_input.get(CONF_SHOW_ADVANCED, False):
                    return await self.async_step_advanced()

                self._data[CONF_MODEL] = DEFAULT_MODEL
                return await self.async_step_temp_step()

        schema = vol.Schema(
            {
                vol.Required(CONF_PROTOCOL): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=PROTOCOL_LABELS,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SHOW_ADVANCED, default=False
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="protocol",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: optional advanced protocol settings."""
        errors: dict[str, str] = {}

        if not _supports_model_variants(str(self._data[CONF_PROTOCOL])):
            self._data[CONF_MODEL] = DEFAULT_MODEL
            return await self.async_step_temp_step()

        if user_input is not None:
            self._data[CONF_MODEL] = user_input.get(CONF_MODEL, DEFAULT_MODEL)
            return await self.async_step_temp_step()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MODEL, default=DEFAULT_MODEL
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-1,
                        max=99,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="advanced",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_temp_step(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: choose temperature step supported by selected model."""
        errors: dict[str, str] = {}

        protocol_const = str(self._data[CONF_PROTOCOL])
        model = int(self._data.get(CONF_MODEL, DEFAULT_MODEL))
        supported_steps = _supported_temp_step_options(protocol_const, model)

        if user_input is not None:
            selected_step = str(user_input.get(CONF_TEMP_STEP, DEFAULT_TEMP_STEP))
            selected_temp_step = _selector_value_to_temp_step(selected_step)
            if selected_step not in supported_steps or selected_temp_step is None:
                errors[CONF_TEMP_STEP] = "invalid_temp_step"
            else:
                self._data[CONF_TEMP_STEP] = selected_temp_step
                unique_id = "_".join(
                    (
                        str(self._data[CONF_EMITTER_ENTITY_ID]),
                        protocol_const,
                        str(model),
                    )
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                if self._is_reconfigure:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(),
                        data=self._data,
                        title=self._data[CONF_NAME],
                        unique_id=unique_id,
                    )
                return self.async_create_entry(
                    title=self._data[CONF_NAME],
                    data=self._data,
                )

        default_step = _temp_step_to_selector_value(
            self._data.get(CONF_TEMP_STEP, DEFAULT_TEMP_STEP)
        )
        if default_step not in supported_steps:
            default_step = supported_steps[0]

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TEMP_STEP, default=default_step
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=supported_steps,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key="temp_step",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="temp_step",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> IrRemoteHvacOptionsFlow:
        """Return the options flow handler."""
        return IrRemoteHvacOptionsFlow(config_entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow changing the emitter and protocol without recreating the entry."""
        emitters = infrared.async_get_emitters(self.hass)
        if not emitters:
            return self.async_abort(reason="no_emitters")

        reconfigure_entry = self._get_reconfigure_entry()
        if user_input is not None:
            self._is_reconfigure = True
            self._data = dict(reconfigure_entry.data)
            self._data.update(user_input)
            return await self.async_step_protocol()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NAME,
                    default=reconfigure_entry.title,
                ): str,
                vol.Required(
                    CONF_EMITTER_ENTITY_ID,
                    default=reconfigure_entry.data[CONF_EMITTER_ENTITY_ID],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=emitters,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors={},
        )


class IrRemoteHvacOptionsFlow(OptionsFlow):
    """Handle options for an existing IR Remote HVAC entry."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to update temperature range and step."""
        errors: dict[str, str] = {}

        current = self._config_entry.options or self._config_entry.data
        protocol_const = str(self._config_entry.data.get(CONF_PROTOCOL, ""))
        model = int(self._config_entry.data.get(CONF_MODEL, DEFAULT_MODEL))
        supported_steps = _supported_temp_step_options(protocol_const, model)

        if user_input is not None:
            selected_step = str(user_input[CONF_TEMP_STEP])
            selected_temp_step = _selector_value_to_temp_step(selected_step)
            if user_input[CONF_MIN_TEMP] >= user_input[CONF_MAX_TEMP]:
                errors["base"] = "invalid_temp_range"
            elif selected_step not in supported_steps or selected_temp_step is None:
                errors[CONF_TEMP_STEP] = "invalid_temp_step"
            else:
                normalized = dict(user_input)
                normalized[CONF_TEMP_STEP] = selected_temp_step
                normalized[CONF_DEBOUNCE_DELAY] = float(
                    user_input.get(
                        CONF_DEBOUNCE_DELAY,
                        current.get(CONF_DEBOUNCE_DELAY, DEFAULT_DEBOUNCE_DELAY),
                    )
                )
                current_temperature_sensor_entity_id = user_input.get(
                    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID
                )
                if current_temperature_sensor_entity_id:
                    normalized[CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID] = (
                        current_temperature_sensor_entity_id
                    )
                current_humidity_sensor_entity_id = user_input.get(
                    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID
                )
                if current_humidity_sensor_entity_id:
                    normalized[CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID] = (
                        current_humidity_sensor_entity_id
                    )
                power_sensor_entity_id = user_input.get(CONF_POWER_SENSOR_ENTITY_ID)
                if power_sensor_entity_id:
                    normalized[CONF_POWER_SENSOR_ENTITY_ID] = power_sensor_entity_id
                normalized[CONF_POWER_THRESHOLD] = float(
                    user_input.get(
                        CONF_POWER_THRESHOLD,
                        current.get(CONF_POWER_THRESHOLD, DEFAULT_POWER_THRESHOLD),
                    )
                )
                return self.async_create_entry(title="", data=normalized)

        default_temp_step = _temp_step_to_selector_value(
            current.get(CONF_TEMP_STEP, DEFAULT_TEMP_STEP)
        )
        if default_temp_step not in supported_steps:
            default_temp_step = supported_steps[0]

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MIN_TEMP,
                    default=float(current.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0,
                        max=40.0,
                        step=0.5,
                        unit_of_measurement="°C",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_MAX_TEMP,
                    default=float(current.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0,
                        max=45.0,
                        step=0.5,
                        unit_of_measurement="°C",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_TEMP_STEP,
                    default=default_temp_step,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=supported_steps,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key="temp_step",
                    )
                ),
                vol.Optional(
                    CONF_DEBOUNCE_DELAY,
                    default=float(
                        current.get(CONF_DEBOUNCE_DELAY, DEFAULT_DEBOUNCE_DELAY)
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0,
                        max=10.0,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(
                    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID,
                    default=current.get(CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"],
                        device_class=[SensorDeviceClass.TEMPERATURE],
                    )
                ),
                vol.Optional(
                    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID,
                    default=current.get(CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"],
                        device_class=[SensorDeviceClass.HUMIDITY],
                    )
                ),
                vol.Optional(
                    CONF_POWER_SENSOR_ENTITY_ID,
                    default=current.get(CONF_POWER_SENSOR_ENTITY_ID),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"],
                        device_class=[SensorDeviceClass.POWER],
                    )
                ),
                vol.Optional(
                    CONF_POWER_THRESHOLD,
                    default=float(
                        current.get(CONF_POWER_THRESHOLD, DEFAULT_POWER_THRESHOLD)
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0,
                        max=10000.0,
                        step=0.1,
                        unit_of_measurement="W",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
