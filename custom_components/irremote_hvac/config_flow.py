"""Config flow for IR Remote HVAC integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import (
    infrared,  # type: ignore[reportAttributeAccessIssue]
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from pyhvac import brands, registry

from .const import (
    CONF_BRAND,
    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID,
    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID,
    CONF_DEBOUNCE_DELAY,
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
_REMOTE_MODELS = {
    (brand, model) for brand, model, kind, *_ in brands.MODELS if kind == "remote"
}
_SENSOR_DEVICE_CLASSES = {
    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID: SensorDeviceClass.TEMPERATURE,
    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID: SensorDeviceClass.HUMIDITY,
    CONF_POWER_SENSOR_ENTITY_ID: SensorDeviceClass.POWER,
}


def _sensor_fields() -> dict[vol.Optional, selector.EntitySelector]:
    """Return optional selectors shared by setup and Configure."""
    return {
        vol.Optional(key): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class=device_class)
        )
        for key, device_class in _SENSOR_DEVICE_CLASSES.items()
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
        self._warned_name: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start setup by selecting the HVAC brand."""
        if not infrared.async_get_emitters(self.hass):
            return self.async_abort(reason="no_emitters")
        return await self._async_brand_form("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start reconfiguration with the current device selection if known."""
        if not infrared.async_get_emitters(self.hass):
            return self.async_abort(reason="no_emitters")

        if user_input is None:
            entry = self._get_reconfigure_entry()
            self._data = dict(entry.data)
            if CONF_LEGACY_PROTOCOL in self._data:
                try:
                    self._data = migrate_legacy_device(self._data)
                except ValueError:
                    self._data.pop(CONF_LEGACY_PROTOCOL, None)
                    self._data.pop(CONF_MODEL, None)
            self._data[CONF_NAME] = entry.title

        return await self._async_brand_form("reconfigure", user_input)

    async def _async_brand_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Select the HVAC brand."""
        available_brands = registry.brands()
        errors: dict[str, str] = {}

        if user_input is not None:
            brand = user_input[CONF_BRAND]
            if brand not in available_brands:
                errors[CONF_BRAND] = "invalid_brand"
            else:
                self._data[CONF_BRAND] = brand
                return await self.async_step_model()

        schema = vol.Schema(
            {
                vol.Required(CONF_BRAND): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=available_brands,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {CONF_BRAND: brand}
                if (brand := self._data.get(CONF_BRAND)) in available_brands
                else None,
            ),
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the model or remote for the chosen brand."""
        errors: dict[str, str] = {}
        brand = str(self._data[CONF_BRAND])
        models = registry.models(brand)

        if user_input is not None:
            model = str(user_input[CONF_MODEL])
            if model not in models:
                errors[CONF_MODEL] = "invalid_model"
            else:
                self._data[CONF_MODEL] = model
                return await self.async_step_device()

        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=model,
                                label=f"{model} (remote)"
                                if (brand, model) in _REMOTE_MODELS
                                else model,
                            )
                            for model in models
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="model",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {CONF_MODEL: model}
                if (model := self._data.get(CONF_MODEL)) in models
                else None,
            ),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the unit and select the emitter that controls it."""
        emitters = infrared.async_get_emitters(self.hass)
        if not emitters:
            return self.async_abort(reason="no_emitters")
        errors: dict[str, str] = {}
        name = self._data.get(
            CONF_NAME, f"{self._data[CONF_BRAND]} {self._data[CONF_MODEL]}"
        )
        emitter = self._data.get(CONF_EMITTER_ENTITY_ID)
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            emitter = user_input[CONF_EMITTER_ENTITY_ID]

        normalized_name = name.strip().casefold()
        name_in_use = any(
            entry.entry_id != self.context.get("entry_id")
            and entry.title.strip().casefold() == normalized_name
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

        if user_input is not None:
            if not name:
                errors[CONF_NAME] = "invalid_name"
            if emitter not in emitters:
                errors[CONF_EMITTER_ENTITY_ID] = "invalid_emitter"
            if not errors and (not name_in_use or self._warned_name == normalized_name):
                self._data.update({CONF_NAME: name, CONF_EMITTER_ENTITY_ID: emitter})
                unique_id = (
                    f"{emitter}_{self._data[CONF_BRAND]}_{self._data[CONF_MODEL]}"
                )
                if self._device_already_configured(unique_id):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(unique_id)
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(),
                        data=self._data,
                        title=name,
                        unique_id=unique_id,
                    )
                return await self.async_step_sensors()

        self._warned_name = normalized_name if name_in_use else None
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=name): str,
                vol.Required(CONF_EMITTER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(include_entities=emitters)
                ),
            }
        )
        return self.async_show_form(
            step_id="device_name_in_use" if name_in_use else "device",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {CONF_EMITTER_ENTITY_ID: emitter} if emitter in emitters else None,
            ),
            errors=errors,
            last_step=self.source == SOURCE_RECONFIGURE,
        )

    async def async_step_device_name_in_use(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to keep a shared name or choose another."""
        return await self.async_step_device(user_input)

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally link sensors before creating the entry."""
        if user_input is None:
            return self.async_show_form(
                step_id="sensors",
                data_schema=vol.Schema(_sensor_fields()),
                last_step=True,
            )
        return self.async_create_entry(
            title=self._data[CONF_NAME], data=self._data, options=user_input
        )

    def _device_already_configured(self, unique_id: str) -> bool:
        """Check canonical brand/model data, including migrated legacy entries."""
        reconfigure_entry_id = (
            self._get_reconfigure_entry().entry_id
            if self.source == SOURCE_RECONFIGURE
            else None
        )
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == reconfigure_entry_id:
                continue
            if entry.unique_id == unique_id:
                return True
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


class IrRemoteHvacOptionsFlow(OptionsFlow):
    """Handle options for an existing IR Remote HVAC entry."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to update temperature range and step."""
        errors: dict[str, str] = {}

        current = {**self._config_entry.data, **self._config_entry.options}
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
                for key in _SENSOR_DEVICE_CLASSES:
                    if key not in normalized and key in self._config_entry.data:
                        normalized[key] = None  # Clear a sensor saved in legacy data.
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
                **_sensor_fields(),
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
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    key: current[key]
                    for key in _SENSOR_DEVICE_CLASSES
                    if current.get(key)
                },
            ),
            errors=errors,
        )
