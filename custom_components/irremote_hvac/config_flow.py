"""Config flow for IR Remote HVAC integration."""

from __future__ import annotations

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
from pyhvac import registry

from .const import (
    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID,
    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID,
    CONF_DEBOUNCE_DELAY,
    CONF_BRAND,
    CONF_EMITTER_ENTITY_ID,
    CONF_LEGACY_PROTOCOL,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_MODEL,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_POWER_THRESHOLD,
    CONF_TEMP_STEP,
    DEFAULT_DEBOUNCE_DELAY,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_TEMP_STEP,
    DOMAIN,
)
from .pyhvac_helpers import get_device, migrate_legacy_device

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


def _supported_temp_step_options(device: Any) -> list[str]:
    """Return temperature steps supported by the selected device."""
    decimals = device.capabilities.temperature.decimals
    options = []
    if 5 in decimals:
        options.append(TEMP_STEP_HALF)
    if 0 in decimals or not options:
        options.append(TEMP_STEP_WHOLE)
    return options


class IrRemoteHvacConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for IR Remote HVAC."""

    VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._is_reconfigure = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: select emitter entity and give the device a name."""
        emitters = infrared.async_get_emitters(self.hass)
        if not emitters:
            return self.async_abort(reason="no_emitters")

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_brand()

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
        )

    async def async_step_brand(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: select the HVAC brand."""
        errors: dict[str, str] = {}

        if user_input is not None:
            brand = user_input[CONF_BRAND]
            if brand not in registry.brands():
                errors[CONF_BRAND] = "invalid_brand"
            else:
                self._data[CONF_BRAND] = brand
                return await self.async_step_model()

        schema = vol.Schema(
            {
                vol.Required(CONF_BRAND): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=registry.brands(),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="brand",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: select the model for the chosen brand."""
        errors: dict[str, str] = {}
        brand = str(self._data[CONF_BRAND])
        models = registry.models(brand)

        if user_input is not None:
            model = str(user_input[CONF_MODEL])
            if model not in models:
                errors[CONF_MODEL] = "invalid_model"
            else:
                self._data[CONF_MODEL] = model
                return await self.async_step_temp_step()

        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=models,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="model",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_temp_step(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: choose temperature step supported by selected model."""
        errors: dict[str, str] = {}

        device = get_device(str(self._data[CONF_BRAND]), str(self._data[CONF_MODEL]))
        supported_steps = _supported_temp_step_options(device)

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
                        str(self._data[CONF_BRAND]),
                        str(self._data[CONF_MODEL]),
                    )
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                if self._device_already_configured():
                    return self.async_abort(reason="already_configured")
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

    def _device_already_configured(self) -> bool:
        """Check canonical brand/model data, including migrated legacy entries."""
        reconfigure_entry_id = (
            self._get_reconfigure_entry().entry_id if self._is_reconfigure else None
        )
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == reconfigure_entry_id:
                continue
            data = entry.data
            if CONF_LEGACY_PROTOCOL in data:
                try:
                    data = migrate_legacy_device(dict(data))
                except ValueError:
                    continue
            if (
                data.get(CONF_EMITTER_ENTITY_ID) == self._data[CONF_EMITTER_ENTITY_ID]
                and data.get(CONF_BRAND) == self._data[CONF_BRAND]
                and data.get(CONF_MODEL) == self._data[CONF_MODEL]
            ):
                return True
        return False

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
            try:
                self._data = migrate_legacy_device(dict(reconfigure_entry.data))
            except ValueError:
                self._data = dict(reconfigure_entry.data)
                self._data.pop(CONF_LEGACY_PROTOCOL, None)
                self._data.pop(CONF_MODEL, None)
            self._data.update(user_input)
            return await self.async_step_brand()

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
        device = get_device(
            str(self._config_entry.data[CONF_BRAND]),
            str(self._config_entry.data[CONF_MODEL]),
        )
        supported_steps = _supported_temp_step_options(device)

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
                    normalized[
                        CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID
                    ] = current_temperature_sensor_entity_id
                current_humidity_sensor_entity_id = user_input.get(
                    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID
                )
                if current_humidity_sensor_entity_id:
                    normalized[
                        CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID
                    ] = current_humidity_sensor_entity_id
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
