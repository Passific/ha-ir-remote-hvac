"""Climate entity for IR Remote HVAC using pyhvac 0.2."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import RestoreClimate
from homeassistant.components.climate.const import (
    ATTR_FAN_MODE,
    ATTR_PRESET_MODE,
    ATTR_SWING_MODE,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    PRESET_ECO,
    PRESET_NONE,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.infrared import (  # type: ignore[reportMissingImports]
    InfraredEmitterConsumerEntity,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    PRECISION_HALVES,
    PRECISION_WHOLE,
    STATE_UNAVAILABLE,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util.unit_conversion import PowerConverter, TemperatureConverter
from infrared_protocols.commands import Command  # type: ignore[reportMissingImports]
from pyhvac.ir.formats import to_raw
from pyhvac.state import HvacState

from .const import (
    CONF_BRAND,
    CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID,
    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID,
    CONF_DEBOUNCE_DELAY,
    CONF_EMITTER_ENTITY_ID,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_MODEL,
    CONF_MODULATION,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_POWER_THRESHOLD,
    CONF_TEMP_STEP,
    DEFAULT_DEBOUNCE_DELAY,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_MODULATION,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_TEMP_STEP,
    DOMAIN,
    FAN_MAX,
    FAN_MIN,
    PRESET_QUIET,
    PRESET_TURBO,
    SIGNAL_HVAC_STATE_UPDATED,
)
from .pyhvac_helpers import get_device

_LOGGER = logging.getLogger(__name__)

# All supported HVAC modes (the underlying library ignores unsupported ones)
_FAN_MODE_RANKS = [FAN_MIN, FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_MAX]
_FAN_MODES = [FAN_AUTO, *_FAN_MODE_RANKS]

_SWITCH_FEATURES = ("light", "cleaning", "purifier", "sleep", "power_limit")

_HA_TO_PYHVAC_MODE = {
    HVACMode.AUTO: "auto",
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "heat",
    HVACMode.DRY: "dry",
    HVACMode.FAN_ONLY: "fan",
}

_MAX_IR_SEND_DURATION_MS = 5000.0
_IR_BURST_COUNT = 1
_IR_BURST_GAP_MS = 40
_IR_MIN_COMMAND_INTERVAL_S = 2.0

_REPAIR_ISSUE_KEY_EXTERNAL_ENTITY_UNAVAILABLE = "external_entity_unavailable"
_EXTERNAL_ENTITY_REPAIR_GRACE_PERIOD_S = 300


def _mode_to_hvac_action(hvac_mode: HVACMode) -> HVACAction:
    """Map HVAC mode to the closest HVAC action."""
    return {
        HVACMode.COOL: HVACAction.COOLING,
        HVACMode.HEAT: HVACAction.HEATING,
        HVACMode.DRY: HVACAction.DRYING,
        HVACMode.FAN_ONLY: HVACAction.FAN,
    }.get(hvac_mode, HVACAction.IDLE)


class IrHvacRawCommand(Command):
    """Wrap pyhvac signed raw timings as an infrared_protocols Command."""

    def __init__(self, timings: list[int], modulation: int = 38000) -> None:
        super().__init__(modulation=modulation)
        self._timings = list(timings)

    def get_raw_timings(self) -> list[int]:
        return self._timings


def _coerce_float(value: Any, fallback: float) -> float:
    """Return value as float, or fallback when stored config is malformed."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_int(value: Any, fallback: int) -> int:
    """Return value as int, or fallback when stored config is malformed."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the IR HVAC climate entity from a config entry."""
    try:
        device = get_device(
            str(config_entry.data[CONF_BRAND]),
            str(config_entry.data[CONF_MODEL]),
        )
    except (KeyError, ValueError) as err:
        raise HomeAssistantError(
            "The selected pyhvac brand/model is unavailable. Reconfigure this device."
        ) from err

    # Resolve effective min/max from options (if set) then data, then defaults
    options = config_entry.options or {}
    min_temp = _coerce_float(
        options.get(
            CONF_MIN_TEMP, config_entry.data.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
        ),
        DEFAULT_MIN_TEMP,
    )
    max_temp = _coerce_float(
        options.get(
            CONF_MAX_TEMP, config_entry.data.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
        ),
        DEFAULT_MAX_TEMP,
    )
    if min_temp >= max_temp:
        min_temp = DEFAULT_MIN_TEMP
        max_temp = DEFAULT_MAX_TEMP

    temp_step = _coerce_float(
        options.get(
            CONF_TEMP_STEP, config_entry.data.get(CONF_TEMP_STEP, DEFAULT_TEMP_STEP)
        ),
        DEFAULT_TEMP_STEP,
    )
    if temp_step not in (0.5, 1.0):
        temp_step = DEFAULT_TEMP_STEP
    if temp_step == 0.5 and 5 not in device.capabilities.temperature.decimals:
        temp_step = DEFAULT_TEMP_STEP

    fallback_modulation = _coerce_int(
        options.get(
            CONF_MODULATION,
            config_entry.data.get(CONF_MODULATION, DEFAULT_MODULATION),
        ),
        DEFAULT_MODULATION,
    )
    debounce_delay_s = _coerce_float(
        options.get(
            CONF_DEBOUNCE_DELAY,
            config_entry.data.get(CONF_DEBOUNCE_DELAY, DEFAULT_DEBOUNCE_DELAY),
        ),
        DEFAULT_DEBOUNCE_DELAY,
    )
    current_temperature_sensor_entity_id = options.get(
        CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID
    ) or config_entry.data.get(CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID)
    current_humidity_sensor_entity_id = options.get(
        CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID
    ) or config_entry.data.get(CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID)
    power_sensor_entity_id = options.get(
        CONF_POWER_SENSOR_ENTITY_ID
    ) or config_entry.data.get(CONF_POWER_SENSOR_ENTITY_ID)
    power_threshold_w = _coerce_float(
        options.get(
            CONF_POWER_THRESHOLD,
            config_entry.data.get(CONF_POWER_THRESHOLD, DEFAULT_POWER_THRESHOLD),
        ),
        DEFAULT_POWER_THRESHOLD,
    )
    protocol_carrier = getattr(device.PROTOCOL, "carrier", fallback_modulation)
    modulation = (
        int(protocol_carrier)
        if isinstance(protocol_carrier, (int, float))
        and not isinstance(protocol_carrier, bool)
        else fallback_modulation
    )

    entity = IrRemoteHvacClimate(
        config_entry=config_entry,
        device=device,
        min_temp=min_temp,
        max_temp=max_temp,
        temp_step=temp_step,
        modulation=modulation,
        debounce_delay_s=debounce_delay_s,
        current_temperature_sensor_entity_id=current_temperature_sensor_entity_id,
        current_humidity_sensor_entity_id=current_humidity_sensor_entity_id,
        power_sensor_entity_id=power_sensor_entity_id,
        power_threshold_w=power_threshold_w,
    )
    # Keep a reference so the companion power switch entity can call back into it.
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = entity
    async_add_entities([entity])


class IrRemoteHvacClimate(InfraredEmitterConsumerEntity, RestoreClimate):
    """Climate entity that sends pyhvac IR commands through Home Assistant.

    Inherits InfraredEmitterConsumerEntity so that availability tracks the
    configured emitter and _send_command() dispatches through the infrared
    domain helper (including context propagation and state updates).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "irremote_hvac"

    def __init__(
        self,
        config_entry: ConfigEntry,
        device: Any,
        min_temp: float,
        max_temp: float,
        temp_step: float,
        modulation: int,
        debounce_delay_s: float,
        current_temperature_sensor_entity_id: str | None,
        current_humidity_sensor_entity_id: str | None,
        power_sensor_entity_id: str | None,
        power_threshold_w: float,
    ) -> None:
        self._infrared_emitter_entity_id: str = config_entry.data[
            CONF_EMITTER_ENTITY_ID
        ]
        self._entry_id: str = config_entry.entry_id
        self._device = device
        self._brand = config_entry.data[CONF_BRAND]
        self._protocol = device.PROTOCOL.name
        self._model = config_entry.data[CONF_MODEL]
        self._modulation = modulation
        self._debounce_delay_s = max(0.0, debounce_delay_s)
        self._current_temperature_sensor_entity_id = (
            current_temperature_sensor_entity_id
        )
        self._current_humidity_sensor_entity_id = current_humidity_sensor_entity_id
        self._power_sensor_entity_id = power_sensor_entity_id
        self._power_threshold_w = power_threshold_w
        self._external_entity_repair_timers: dict[str, Callable[[], None]] = {}

        self._attr_unique_id = config_entry.entry_id
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.title,
            manufacturer="IR Remote HVAC",
            model=f"{self._brand} / {self._model}",
            hw_version=f"Carrier {self._modulation} Hz",
        )

        self._attr_hvac_modes = self._detect_supported_hvac_modes()
        self._attr_hvac_mode = HVACMode.OFF
        self._last_on_hvac_mode = self._preferred_on_hvac_mode()

        self._fan_values_by_mode = self._build_fan_mapping()
        self._attr_fan_modes = self._detect_supported_fan_modes()
        self._attr_fan_mode = self._attr_fan_modes[0] if self._attr_fan_modes else None

        self._swing_values_by_mode = self._build_swing_mapping()
        self._attr_swing_modes = self._detect_supported_swing_modes()
        self._attr_swing_mode = (
            SWING_OFF if SWING_OFF in self._attr_swing_modes else None
        )

        self._preset_values_by_mode = self._build_preset_mapping()
        self._attr_preset_modes = self._detect_supported_preset_modes()
        self._attr_preset_mode = (
            PRESET_NONE if PRESET_NONE in self._attr_preset_modes else None
        )

        self._switch_feature_states: dict[str, bool] = {}
        for feature in _SWITCH_FEATURES:
            choice = device.capabilities.features.get(feature)
            if (
                choice is not None
                and len(choice.values) == 2
                and any(value is False for value in choice.values)
                and any(value is True for value in choice.values)
            ):
                self._switch_feature_states[feature] = False

        temperature_range = device.capabilities.temperature
        min_temp = max(min_temp, temperature_range.min)
        max_temp = min(max_temp, temperature_range.max)
        if min_temp >= max_temp:
            min_temp = temperature_range.min
            max_temp = temperature_range.max
        self._attr_min_temp = min_temp
        self._attr_max_temp = max_temp
        self._attr_target_temperature_step = (
            PRECISION_HALVES if temp_step == 0.5 else PRECISION_WHOLE
        )
        self._attr_native_target_temperature = 22.0
        self._attr_native_temperature_unit = UnitOfTemperature.CELSIUS

        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        if (
            HVACMode.OFF in self._attr_hvac_modes
            and self._last_on_hvac_mode is not None
        ):
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
        if self._attr_fan_modes:
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
        if self._attr_swing_modes:
            self._attr_supported_features |= ClimateEntityFeature.SWING_MODE
        if self._attr_preset_modes:
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE

        self._send_lock = asyncio.Lock()
        self._pending_send_task: asyncio.Task[None] | None = None
        self._pending_send_generation = 0
        self._last_send_monotonic = 0.0
        self._last_command_state: HvacState | None = None
        self._confirmed_state = self._snapshot_runtime_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride]
        """Return extra entity attributes."""
        attributes = {
            "carrier_frequency_hz": self._modulation,
            "ir_protocol": self._protocol,
            "ir_model": self._model,
            "ir_brand": self._brand,
            "debounce_delay_s": self._debounce_delay_s,
            "power_threshold_w": self._power_threshold_w,
            "last_on_hvac_mode": self._last_on_hvac_mode,
        }
        if self._last_command_state is not None:
            attributes["pyhvac_state"] = self._last_command_state.to_dict()

        if self._power_sensor_entity_id:
            attributes["power_sensor_entity_id"] = self._power_sensor_entity_id

        return attributes

    @property
    def native_current_temperature(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride]
        """Return the current temperature from the configured external sensor."""
        return self._get_entity_state_float(
            self._current_temperature_sensor_entity_id,
            expected_device_classes=(SensorDeviceClass.TEMPERATURE,),
            value_type="temperature",
        )

    @property
    def current_humidity(self) -> int | None:  # type: ignore[reportIncompatibleVariableOverride]
        """Return the current humidity from the configured external sensor."""
        humidity = self._get_entity_state_float(
            self._current_humidity_sensor_entity_id,
            expected_device_classes=(SensorDeviceClass.HUMIDITY,),
            value_type="humidity",
        )
        if humidity is None:
            return None

        return round(humidity)

    @property
    def hvac_action(self) -> HVACAction | None:  # type: ignore[reportIncompatibleVariableOverride]
        """Return the current HVAC action derived from power usage when available."""
        hvac_mode = self._attr_hvac_mode
        if hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if hvac_mode is None:
            return None

        power_state = self._get_power_sensor_state()
        if power_state is not None and power_state < self._power_threshold_w:
            return HVACAction.IDLE

        return _mode_to_hvac_action(hvac_mode)

    @property
    def is_on(self) -> bool:
        """Return True if the climate device is not off."""
        return self._attr_hvac_mode != HVACMode.OFF

    def async_write_ha_state(self) -> None:
        """Write entity state and notify companion entities (e.g. the power switch)."""
        super().async_write_ha_state()
        async_dispatcher_send(
            self.hass, f"{SIGNAL_HVAC_STATE_UPDATED}_{self._entry_id}"
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the power sensor so hvac_action stays current."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_external_entity_repair_timers)

        last_state = await self.async_get_last_state()
        climate_data = await self.async_get_last_climate_data(validate_range=True)
        if (
            climate_data is not None
            and climate_data.native_target_temperature is not None
        ):
            self._attr_native_target_temperature = (
                climate_data.native_target_temperature
            )
        if last_state is not None:
            self._restore_from_last_state(last_state)

        self._confirmed_state = self._snapshot_runtime_state()

        if self._tracked_entity_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._tracked_entity_ids,
                    self._handle_power_sensor_state_change,
                )
            )
        self._refresh_external_entity_repairs()

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Allow the entity ID to change without removing the entity."""
        super().async_entity_id_changed(old_entity_id)

    @callback
    def _handle_power_sensor_state_change(self, _event: Any) -> None:
        """Refresh climate state and repairs when tracked sensors change."""
        _ = _event
        self._safe_update_external_entity_repairs()
        self.async_write_ha_state()

    @property
    def _tracked_entity_ids(self) -> list[str]:
        """Return the entity ids that should refresh climate state."""
        entity_ids = [
            entity_id
            for entity_id in (
                self._infrared_emitter_entity_id,
                self._current_temperature_sensor_entity_id,
                self._current_humidity_sensor_entity_id,
                self._power_sensor_entity_id,
            )
            if entity_id
        ]
        return list(dict.fromkeys(entity_ids))

    @property
    def _tracked_entities_with_roles(self) -> list[tuple[str, str]]:
        """Return tracked external entities with a human-readable role label."""
        entities: list[tuple[str, str]] = []

        if self._infrared_emitter_entity_id:
            entities.append((self._infrared_emitter_entity_id, "IR emitter entity"))
        if self._current_temperature_sensor_entity_id:
            entities.append(
                (self._current_temperature_sensor_entity_id, "temperature sensor")
            )
        if self._current_humidity_sensor_entity_id:
            entities.append(
                (self._current_humidity_sensor_entity_id, "humidity sensor")
            )
        if self._power_sensor_entity_id:
            entities.append((self._power_sensor_entity_id, "power sensor"))

        return entities

    def _external_entity_issue_id(self, entity_id: str) -> str:
        """Build a stable issue id for a configured external entity."""
        sanitized_entity_id = entity_id.replace(".", "_")
        return f"{self._entry_id}_external_entity_unavailable_{sanitized_entity_id}"

    @callback
    def _safe_update_external_entity_repairs(self) -> None:
        """Update repairs while ensuring repair logic never breaks the entity."""
        try:
            self._update_external_entity_repairs()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to update external entity repairs for %s: %s",
                self.entity_id,
                err,
            )

    @callback
    def _is_external_entity_unavailable(self, entity_id: str) -> bool:
        """Return True if tracked external entity is missing or unavailable."""
        state = self.hass.states.get(entity_id)
        return state is None or state.state == STATE_UNAVAILABLE

    @callback
    def _cancel_external_entity_repair_timer(self, entity_id: str) -> None:
        """Cancel pending grace-period timer for an external entity."""
        cancel = self._external_entity_repair_timers.pop(entity_id, None)
        if cancel is not None:
            cancel()

    @callback
    def _schedule_external_entity_repair_timer(self, entity_id: str, role: str) -> None:
        """Start grace-period timer before creating unavailable entity repair."""
        if entity_id in self._external_entity_repair_timers:
            return

        @callback
        def handle_grace_elapsed(_now: Any) -> None:
            _ = _now
            self._handle_external_entity_grace_elapsed(entity_id, role)

        self._external_entity_repair_timers[entity_id] = async_call_later(
            self.hass,
            _EXTERNAL_ENTITY_REPAIR_GRACE_PERIOD_S,
            handle_grace_elapsed,
        )

    @callback
    def _handle_external_entity_grace_elapsed(self, entity_id: str, role: str) -> None:
        """Create a repair if the entity remains unavailable past the grace period."""
        self._external_entity_repair_timers.pop(entity_id, None)

        if not self._is_external_entity_unavailable(entity_id):
            return

        issue_id = self._external_entity_issue_id(entity_id)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            config_entry_id=self._entry_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_REPAIR_ISSUE_KEY_EXTERNAL_ENTITY_UNAVAILABLE,
            translation_placeholders={
                "entity_id": entity_id,
                "role": role,
            },
        )

    @callback
    def _update_external_entity_repairs(self) -> None:
        """Create or clear repairs for unavailable configured external entities."""
        for entity_id, role in self._tracked_entities_with_roles:
            issue_id = self._external_entity_issue_id(entity_id)
            if self._is_external_entity_unavailable(entity_id):
                self._schedule_external_entity_repair_timer(entity_id, role)
            else:
                self._cancel_external_entity_repair_timer(entity_id)
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def _refresh_external_entity_repairs(self) -> None:
        """Keep existing issues for unavailable entities and remove stale issues."""
        registry = ir.async_get(self.hass)
        prefix = f"{self._entry_id}_external_entity_unavailable_"
        with ir.async_refresh_issues(
            self.hass, DOMAIN, issue_id_pattern=re.escape(prefix)
        ):
            for entity_id, role in self._tracked_entities_with_roles:
                if not self._is_external_entity_unavailable(entity_id):
                    continue
                issue_id = self._external_entity_issue_id(entity_id)
                if registry.async_get_issue(DOMAIN, issue_id) is not None:
                    self._handle_external_entity_grace_elapsed(entity_id, role)
                else:
                    self._schedule_external_entity_repair_timer(entity_id, role)

    @callback
    def _cancel_external_entity_repair_timers(self) -> None:
        """Cancel pending repair timers without deleting existing issues."""
        for entity_id in list(self._external_entity_repair_timers):
            self._cancel_external_entity_repair_timer(entity_id)

    def _get_entity_state_float(
        self,
        entity_id: str | None,
        *,
        expected_device_classes: tuple[SensorDeviceClass, ...] | None = None,
        value_type: str | None = None,
    ) -> float | None:
        """Return the configured entity state as a float, if available."""
        if not entity_id or self.hass is None:
            return None

        state = self.hass.states.get(entity_id)
        if state is None:
            return None

        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None

        if expected_device_classes:
            device_class = state.attributes.get(ATTR_DEVICE_CLASS)
            if device_class not in expected_device_classes:
                return None

        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        if value_type == "temperature":
            return self._convert_temperature_to_celsius(value, unit)

        if value_type == "power":
            return self._convert_power_to_watts(value, unit)

        if value_type == "humidity":
            if unit not in (None, PERCENTAGE):
                return None
            if 0.0 <= value <= 100.0:
                return value
            return None

        return value

    def _get_power_sensor_state(self) -> float | None:
        """Return the configured power sensor value as watts, if available."""
        return self._get_entity_state_float(
            self._power_sensor_entity_id,
            expected_device_classes=(SensorDeviceClass.POWER,),
            value_type="power",
        )

    def _convert_temperature_to_celsius(
        self, value: float, unit: str | None
    ) -> float | None:
        """Convert a temperature value to Celsius when possible."""
        if unit is None:
            return value

        try:
            return TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        except (TypeError, ValueError):
            return None

    def _convert_power_to_watts(self, value: float, unit: str | None) -> float | None:
        """Convert a power value to watts when possible."""
        if unit is None:
            return value

        try:
            return PowerConverter.convert(value, unit, UnitOfPower.WATT)
        except (TypeError, ValueError):
            return None

    def _restore_from_last_state(self, last_state: Any) -> None:
        """Restore the cached climate state without sending IR."""
        restored_last_on_hvac_mode = last_state.attributes.get("last_on_hvac_mode")
        if (
            restored_last_on_hvac_mode in self._attr_hvac_modes
            and restored_last_on_hvac_mode != HVACMode.OFF
        ):
            self._last_on_hvac_mode = restored_last_on_hvac_mode

        hvac_mode_state = last_state.state
        if hvac_mode_state in self._attr_hvac_modes:
            self._attr_hvac_mode = hvac_mode_state
            if hvac_mode_state != HVACMode.OFF:
                self._last_on_hvac_mode = hvac_mode_state
        else:
            self._attr_hvac_mode = HVACMode.OFF

        fan_mode = last_state.attributes.get(ATTR_FAN_MODE)
        if fan_mode in self._attr_fan_modes:
            self._attr_fan_mode = fan_mode

        swing_mode = last_state.attributes.get(ATTR_SWING_MODE)
        if swing_mode in self._attr_swing_modes:
            self._attr_swing_mode = swing_mode

        preset_mode = last_state.attributes.get(ATTR_PRESET_MODE)
        if preset_mode in self._attr_preset_modes:
            self._attr_preset_mode = preset_mode

        saved_device_state = last_state.attributes.get("pyhvac_state")
        if isinstance(saved_device_state, dict):
            try:
                self._last_command_state = HvacState.from_dict(saved_device_state)
            except ValueError as err:
                _LOGGER.debug("Ignoring invalid restored pyhvac state: %s", err)
            else:
                for feature in self._switch_feature_states:
                    self._switch_feature_states[feature] = (
                        self._last_command_state.features.get(feature) is True
                    )

        self.async_write_ha_state()

    def _snapshot_runtime_state(self) -> dict[str, Any]:
        """Snapshot the current runtime state for rollback on send failures."""
        return {
            "hvac_mode": self._attr_hvac_mode,
            "target_temperature": self._attr_native_target_temperature,
            "fan_mode": self._attr_fan_mode,
            "swing_mode": self._attr_swing_mode,
            "preset_mode": self._attr_preset_mode,
            "last_on_hvac_mode": self._last_on_hvac_mode,
            "pyhvac_state": self._last_command_state,
            "switch_features": self._switch_feature_states.copy(),
        }

    def _apply_runtime_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Restore runtime state from a previously captured snapshot."""
        self._attr_hvac_mode = snapshot["hvac_mode"]
        self._attr_native_target_temperature = snapshot["target_temperature"]
        self._attr_fan_mode = snapshot["fan_mode"]
        self._attr_swing_mode = snapshot["swing_mode"]
        self._attr_preset_mode = snapshot["preset_mode"]
        self._last_on_hvac_mode = snapshot["last_on_hvac_mode"]
        self._last_command_state = snapshot["pyhvac_state"]
        self._switch_feature_states = snapshot["switch_features"].copy()

    def _detect_supported_hvac_modes(self) -> list[HVACMode]:
        """Detect HVAC modes supported by the selected device."""
        supported: list[HVACMode] = [HVACMode.OFF]
        supported_modes = set(self._device.capabilities.modes)
        for mode in (
            HVACMode.AUTO,
            HVACMode.COOL,
            HVACMode.HEAT,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        ):
            if _HA_TO_PYHVAC_MODE[mode] in supported_modes:
                supported.append(mode)

        return supported

    def _build_fan_mapping(self) -> dict[str, str]:
        """Map Home Assistant fan labels to pyhvac's canonical fan values."""
        choice = self._device.capabilities.fan
        if choice is None:
            return {}

        label_modes = {
            "min": FAN_MIN,
            "minimum": FAN_MIN,
            "lowest": FAN_MIN,
            "low": FAN_LOW,
            "medium": FAN_MEDIUM,
            "mid": FAN_MEDIUM,
            "high": FAN_HIGH,
            "max": FAN_MAX,
            "maximum": FAN_MAX,
            "highest": FAN_MAX,
        }
        levels = [value for value in choice.values if value != "auto"]
        fallback_by_count = {
            1: [FAN_LOW],
            2: [FAN_LOW, FAN_HIGH],
            3: [FAN_LOW, FAN_MEDIUM, FAN_HIGH],
            4: [FAN_MIN, FAN_LOW, FAN_HIGH, FAN_MAX],
            5: _FAN_MODE_RANKS,
        }.get(len(levels), _FAN_MODE_RANKS)
        mapping: dict[str, str] = {}
        used_modes: set[str] = set()

        if "auto" in choice.values:
            mapping[FAN_AUTO] = "auto"
            used_modes.add(FAN_AUTO)

        for index, value in enumerate(levels):
            label = str(choice.labels.get(value, "")).casefold()
            mode = label_modes.get(label)
            if mode is None or mode in used_modes:
                mode = (
                    fallback_by_count[index]
                    if index < len(fallback_by_count)
                    else str(value)
                )
            mapping[mode] = value
            used_modes.add(mode)

        return mapping

    def _detect_supported_fan_modes(self) -> list[str]:
        """Return the Home Assistant fan modes available for this device."""
        return [mode for mode in _FAN_MODES if mode in self._fan_values_by_mode] + [
            mode for mode in self._fan_values_by_mode if mode not in _FAN_MODES
        ]

    @staticmethod
    def _swing_off_value(choice: Any) -> str | None:
        if choice is None:
            return None
        return "off" if "off" in choice.values else choice.values[0]

    @staticmethod
    def _swing_active_value(choice: Any) -> str | None:
        if choice is None:
            return None
        for value in ("swing", "auto"):
            if value in choice.values:
                return value
        return next((value for value in choice.values if value != "off"), None)

    def _build_swing_mapping(self) -> dict[str, tuple[str | None, str | None]]:
        """Map combined Home Assistant swing modes to device swing axes."""
        capabilities = self._device.capabilities
        vertical = capabilities.swing_v
        horizontal = capabilities.swing_h
        if vertical is None and horizontal is None:
            return {}

        vertical_off = self._swing_off_value(vertical)
        horizontal_off = self._swing_off_value(horizontal)
        vertical_on = self._swing_active_value(vertical)
        horizontal_on = self._swing_active_value(horizontal)
        mapping: dict[str, tuple[str | None, str | None]] = {}
        if vertical_off is not None or horizontal_off is not None:
            mapping[SWING_OFF] = (vertical_off, horizontal_off)
        if vertical_on is not None:
            mapping[SWING_VERTICAL] = (vertical_on, horizontal_off)
        if horizontal_on is not None:
            mapping[SWING_HORIZONTAL] = (vertical_off, horizontal_on)
        if vertical_on is not None and horizontal_on is not None:
            mapping[SWING_BOTH] = (vertical_on, horizontal_on)
        return mapping

    def _detect_supported_swing_modes(self) -> list[str]:
        """Return combined swing modes available for this device."""
        return [
            mode
            for mode in (SWING_OFF, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH)
            if mode in self._swing_values_by_mode
        ]

    def _build_preset_mapping(self) -> dict[str, tuple[str, Any]]:
        """Map Home Assistant presets to enabled pyhvac feature values."""
        mapping = {}
        for preset, feature in (
            (PRESET_QUIET, "quiet"),
            (PRESET_TURBO, "powerful"),
            (PRESET_ECO, "economy"),
        ):
            choice = self._device.capabilities.features.get(feature)
            if choice is None:
                continue
            if True in choice.values:
                enabled_value: Any = True
            else:
                enabled_values = [
                    value for value in choice.values if value not in (False, "off")
                ]
                if not enabled_values:
                    continue
                enabled_value = enabled_values[-1]
            mapping[preset] = (feature, enabled_value)
        return mapping

    def _detect_supported_preset_modes(self) -> list[str]:
        """Return presets available for this device."""
        if not self._preset_values_by_mode:
            return []
        return [PRESET_NONE, *self._preset_values_by_mode]

    def _build_target_state(self) -> HvacState:
        """Translate current entity settings into a normalized pyhvac state."""
        hvac_mode = self._attr_hvac_mode
        if hvac_mode == HVACMode.OFF:
            hvac_mode = self._last_on_hvac_mode or self._preferred_on_hvac_mode()
        pyhvac_mode = _HA_TO_PYHVAC_MODE[hvac_mode]

        fan = self._fan_values_by_mode.get(self._attr_fan_mode, "auto")
        swing_v, swing_h = self._swing_values_by_mode.get(
            self._attr_swing_mode, (None, None)
        )
        if swing_v is None:
            swing_v = self._swing_off_value(self._device.capabilities.swing_v) or "off"
        if swing_h is None:
            swing_h = self._swing_off_value(self._device.capabilities.swing_h) or "off"

        features: dict[str, bool | str] = {}
        for name, choice in self._device.capabilities.features.items():
            if False in choice.values:
                features[name] = False
            elif "off" in choice.values:
                features[name] = "off"
            else:
                features[name] = choice.values[0]
        selected_preset = self._preset_values_by_mode.get(self._attr_preset_mode)
        if selected_preset is not None:
            feature, value = selected_preset
            features[feature] = value
        features.update(self._switch_feature_states)

        return HvacState(
            power=self._attr_hvac_mode != HVACMode.OFF,
            mode=pyhvac_mode,
            temperature=float(self._attr_native_target_temperature),
            fan=fan,
            swing_v=swing_v,
            swing_h=swing_h,
            features=features,
        )

    # ------------------------------------------------------------------
    # Climate entity service handlers
    # ------------------------------------------------------------------

    async def async_turn_on(self) -> None:
        """Turn the climate entity on using the last active HVAC mode."""
        if self._attr_hvac_mode != HVACMode.OFF:
            return

        mode_to_restore = self._last_on_hvac_mode or self._preferred_on_hvac_mode()
        if mode_to_restore is None:
            _LOGGER.warning(
                "No supported non-off HVAC mode available for %s", self.entity_id
            )
            return

        await self.async_set_hvac_mode(mode_to_restore)

    async def async_turn_off(self) -> None:
        """Turn the climate entity off."""
        if HVACMode.OFF not in self._attr_hvac_modes:
            _LOGGER.warning("HVAC off mode is not supported for %s", self.entity_id)
            return

        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC operating mode and send IR command."""
        if hvac_mode not in self._attr_hvac_modes:
            _LOGGER.warning(
                "Requested unsupported HVAC mode '%s' for protocol %s",
                hvac_mode,
                self._model,
            )
            return

        if hvac_mode != HVACMode.OFF:
            self._last_on_hvac_mode = hvac_mode

        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()
        self._schedule_ir_send()

    def _preferred_on_hvac_mode(self) -> HVACMode | None:
        """Return a preferred non-off HVAC mode for turn_on/toggle actions."""
        for preferred_mode in (
            HVACMode.AUTO,
            HVACMode.COOL,
            HVACMode.HEAT,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        ):
            if preferred_mode in self._attr_hvac_modes:
                return preferred_mode

        for mode in self._attr_hvac_modes:
            if mode != HVACMode.OFF:
                return mode

        return None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature and send IR command."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        target_temperature = min(
            max(float(temperature), self._attr_min_temp), self._attr_max_temp
        )

        self._attr_native_target_temperature = target_temperature
        self.async_write_ha_state()
        self._schedule_ir_send()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed and send IR command."""
        fan_modes = self._attr_fan_modes or []
        if fan_mode not in fan_modes:
            _LOGGER.warning(
                "Requested unsupported fan mode '%s' for device %s",
                fan_mode,
                self._model,
            )
            return

        self._attr_fan_mode = fan_mode
        self.async_write_ha_state()
        self._schedule_ir_send()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set combined swing mode (vertical/horizontal) and send IR command."""
        swing_modes = self._attr_swing_modes or []
        if swing_mode not in swing_modes:
            _LOGGER.warning(
                "Requested unsupported swing mode '%s' for device %s",
                swing_mode,
                self._model,
            )
            return

        self._attr_swing_mode = swing_mode
        self.async_write_ha_state()
        self._schedule_ir_send()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset (quiet / turbo / eco / none) and send IR command."""
        preset_modes = self._attr_preset_modes or []
        if preset_mode not in preset_modes:
            _LOGGER.warning(
                "Requested unsupported preset mode '%s' for device %s",
                preset_mode,
                self._model,
            )
            return

        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()
        self._schedule_ir_send()

    @property
    def switch_features(self) -> tuple[str, ...]:
        """Return the supported accessory switches."""
        return tuple(self._switch_feature_states)

    def is_feature_on(self, feature: str) -> bool:
        """Return the state of a supported accessory switch."""
        return self._switch_feature_states[feature]

    async def async_set_feature(self, feature: str, enabled: bool) -> None:
        """Update an accessory through the same IR pipeline as climate controls."""
        if self._switch_feature_states[feature] == enabled:
            return
        self._switch_feature_states[feature] = enabled
        self.async_write_ha_state()
        self._schedule_ir_send()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending debounced send when entity is removed."""
        if self._pending_send_task and not self._pending_send_task.done():
            self._pending_send_task.cancel()
        await super().async_will_remove_from_hass()

    def _schedule_ir_send(self) -> None:
        """Debounce quick state changes and schedule a single send."""
        self._pending_send_generation += 1
        generation = self._pending_send_generation

        if self._pending_send_task and not self._pending_send_task.done():
            self._pending_send_task.cancel()

        self._pending_send_task = self.hass.async_create_task(
            self._debounced_send(generation)
        )

    async def _debounced_send(self, generation: int) -> None:
        """Wait for state to settle, then send latest command once."""
        try:
            await asyncio.sleep(self._debounce_delay_s)

            # A newer command superseded this scheduled send.
            if generation != self._pending_send_generation:
                return

            async with self._send_lock:
                elapsed = time.monotonic() - self._last_send_monotonic
                if self._last_send_monotonic and elapsed < _IR_MIN_COMMAND_INTERVAL_S:
                    await asyncio.sleep(_IR_MIN_COMMAND_INTERVAL_S - elapsed)

                # Re-check after waiting in case another update arrived.
                if generation != self._pending_send_generation:
                    return

                if await self._send_ir_command_now():
                    self._last_send_monotonic = time.monotonic()
                    self._confirmed_state = self._snapshot_runtime_state()
                    self.async_write_ha_state()
                else:
                    self._apply_runtime_state_snapshot(self._confirmed_state)
                    self.async_write_ha_state()
                    _LOGGER.warning(
                        "IR send failed for %s. Reverted to last confirmed state.",
                        self.entity_id,
                    )
        except asyncio.CancelledError:
            _LOGGER.debug(
                "Cancelled pending debounced IR send entity=%s", self.entity_id
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_command(self) -> Any | None:
        """Encode current entity state into a pyhvac command."""
        try:
            return self._device.encode(
                self._last_command_state,
                self._build_target_state(),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to encode IR command for %s / %s: %s",
                self._brand,
                self._model,
                err,
            )
            return None

    async def _send_ir_command_now(self) -> bool:
        """Generate and dispatch an IR command.

        Returns True only when command dispatch succeeds.
        """
        encoded_command = self._generate_command()
        if encoded_command is None:
            _LOGGER.warning(
                "pyhvac returned no command for device '%s / %s'. "
                "The protocol may not support the requested state.",
                self._brand,
                self._model,
            )
            return False

        raw_timings = to_raw(encoded_command.signal)
        if not raw_timings:
            _LOGGER.warning(
                "pyhvac returned empty timing for device '%s / %s'. "
                "The device may not support the requested state.",
                self._brand,
                self._model,
            )
            return False

        carrier = getattr(encoded_command.signal, "carrier", self._modulation)
        if not isinstance(carrier, (int, float)) or isinstance(carrier, bool):
            carrier = self._modulation
        modulation = int(carrier)

        timing_duration_us = sum(abs(t) for t in raw_timings)
        timing_duration_ms = timing_duration_us / 1000

        if timing_duration_ms > _MAX_IR_SEND_DURATION_MS:
            message = (
                f"Generated IR command for {self._brand} / {self._model} "
                f"is about {timing_duration_ms:.1f} ms long, which exceeds the "
                f"{_MAX_IR_SEND_DURATION_MS:.0f} ms infrared send limit."
            )
            _LOGGER.error(
                "%s entity=%s emitter=%s timing_len=%s head=%s tail=%s",
                message,
                self.entity_id,
                self._infrared_emitter_entity_id,
                len(raw_timings),
                raw_timings[:10],
                raw_timings[-10:],
            )
            raise HomeAssistantError(message)

        command = IrHvacRawCommand(raw_timings, modulation=modulation)

        # _send_command is provided by InfraredEmitterConsumerEntity;
        # it wraps infrared.async_send_command with entity context.
        try:
            _LOGGER.debug(
                "Sending IR command entity=%s brand=%s model=%s modulation=%s "
                "timing_len=%s timing_duration_ms=%.1f head=%s tail=%s",
                self.entity_id,
                self._brand,
                self._model,
                modulation,
                len(raw_timings),
                timing_duration_ms,
                raw_timings[:10],
                raw_timings[-10:],
            )
            for attempt in range(_IR_BURST_COUNT):
                await self._send_command(command)
                if attempt < _IR_BURST_COUNT - 1:
                    await asyncio.sleep(_IR_BURST_GAP_MS / 1000)
            self._last_command_state = encoded_command.state
            _LOGGER.debug(
                "Sent IR command entity=%s brand=%s model=%s modulation=%s "
                "burst_count=%s",
                self.entity_id,
                self._brand,
                self._model,
                modulation,
                _IR_BURST_COUNT,
            )
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to send IR command entity=%s brand=%s model=%s "
                "modulation=%s: %s",
                self.entity_id,
                self._brand,
                self._model,
                modulation,
                err,
            )
            if "Network timeout" in str(err) or "timeout" in str(err).lower():
                _LOGGER.warning(
                    "IR send timed out for entity=%s emitter=%s.",
                    self.entity_id,
                    self._infrared_emitter_entity_id,
                )
            return False
