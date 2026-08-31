"""Climate entity for IR Remote HVAC using IRremoteESP8266 / pyhvac."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import ClimateEntity
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
    ATTR_TEMPERATURE,
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
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.unit_conversion import PowerConverter, TemperatureConverter
from infrared_protocols.commands import Command  # type: ignore[reportMissingImports]

from .const import (
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
    CONF_PROTOCOL,
    CONF_TEMP_STEP,
    DEFAULT_DEBOUNCE_DELAY,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_MODEL,
    DEFAULT_MODULATION,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_TEMP_STEP,
    DOMAIN,
    FAN_MAX,
    FAN_MIN,
    FAN_MODE_TO_IRHVAC,
    HVAC_MODE_TO_IRHVAC,
    PRESET_QUIET,
    PRESET_TURBO,
    SIGNAL_HVAC_STATE_UPDATED,
    SWING_MODE_TO_IRHVAC,
)

_LOGGER = logging.getLogger(__name__)

# All supported HVAC modes (the underlying library ignores unsupported ones)
_HVAC_MODES = [
    HVACMode.OFF,
    HVACMode.AUTO,
    HVACMode.COOL,
    HVACMode.HEAT,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
]

_FAN_MODES = [FAN_AUTO, FAN_MIN, FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_MAX]

_SWING_MODES = [
    SWING_OFF,
    SWING_VERTICAL,
    SWING_HORIZONTAL,
    SWING_BOTH,
]

_PRESET_MODES = [PRESET_NONE, PRESET_QUIET, PRESET_TURBO, PRESET_ECO]

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
    """Wraps IRremoteESP8266 raw timings as an infrared_protocols Command.

    IRremoteESP8266 returns timings as a flat list of unsigned integers
    alternating mark/space (all positive). The infrared domain expects
    positive values for pulses and negative for spaces, so we convert here.
    """

    def __init__(self, timings: list[int], modulation: int = 38000) -> None:
        super().__init__(modulation=modulation)
        # Indices 0, 2, 4, … are marks (pulses) → keep positive.
        # Indices 1, 3, 5, … are spaces → negate.
        self._timings = [t if i % 2 == 0 else -t for i, t in enumerate(timings)]

    def get_raw_timings(self) -> list[int]:
        return self._timings


def _resolve_modulation(
    irhvac_module: Any,
    protocol_const: str,
    model: int,
    fallback_modulation: int,
) -> int:
    """Resolve the carrier frequency for the selected protocol/model."""
    if not hasattr(irhvac_module, protocol_const):
        return fallback_modulation

    try:
        ac = irhvac_module.IRac(0)
        ac.next.protocol = getattr(irhvac_module, protocol_const)
        ac.next.model = model

        # Some attributes (e.g. "modulation") are booleans in pyhvac internals.
        # Only accept realistic IR carrier frequencies in Hz.
        for attr in ("frequency", "carrier_frequency", "freq", "carrier", "modulation"):
            for target in (ac.next, ac):
                if not hasattr(target, attr):
                    continue

                value = getattr(target, attr)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)) and 10000 <= int(value) <= 100000:
                    return int(value)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug(
            "Failed to resolve modulation for protocol=%s model=%s: %s",
            protocol_const,
            model,
            err,
        )

    return fallback_modulation


def _normalize_daikin_timings(raw_timings: list[int]) -> list[int]:
    """Normalize DAIKIN timings to match real-world working pulse shape.

    pyhvac emits valid DAIKIN bit patterns, but some transmitters are sensitive
    to exact mark/space ratios. We map the standard DAIKIN 584-length payload
    to the pulse family observed from a known-good frame.
    """
    if len(raw_timings) != 584:
        return raw_timings

    normalized: list[int] = []
    last_index = len(raw_timings) - 1

    for index, value in enumerate(raw_timings):
        # Final trailing gap must be long enough for unit frame separation.
        if index == last_index and value >= 20000:
            normalized.append(109456)
            continue

        if index % 2 == 0:
            # Marks: ~428 us -> ~394 us, leader mark -> ~3612 us.
            if 3400 <= value <= 4200:
                normalized.append(3612)
            elif 320 <= value <= 560:
                normalized.append(394)
            else:
                normalized.append(value)
        else:
            # Spaces: short ~428 -> ~525, data-long ~1280 -> ~1412,
            # leader space ~1623 -> ~1839, frame gap ~29 ms -> ~26.5 ms.
            if 1500 <= value <= 2100:
                normalized.append(1839)
            elif 950 <= value <= 1500:
                normalized.append(1412)
            elif 22000 <= value <= 34000:
                normalized.append(26502)
            elif 320 <= value <= 700:
                normalized.append(525)
            else:
                normalized.append(value)

    return normalized


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
        from pyhvac import irhvac  # type: ignore[reportMissingImports]
    except ImportError as err:
        raise HomeAssistantError(
            "pyhvac is not installed. Add 'pyhvac>=0.1.6' to your requirements."
        ) from err

    protocol_const = config_entry.data[CONF_PROTOCOL]
    model = _coerce_int(config_entry.data.get(CONF_MODEL), DEFAULT_MODEL)

    if not hasattr(irhvac, protocol_const):
        raise HomeAssistantError(
            f"Protocol constant '{protocol_const}' not found in pyhvac.irhvac. "
            "Ensure pyhvac is up to date."
        )

    # GPIO pin 0 is a dummy value – pyhvac does not perform actual GPIO I/O
    # in a standard Python (non-ESP) environment; sendAc() just populates the
    # internal timing buffer which we then read back via getTiming().
    ac = irhvac.IRac(0)
    ac.next.protocol = getattr(irhvac, protocol_const)
    ac.next.model = model
    ac.next.celsius = True
    ac.next.power = False

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
    modulation = _resolve_modulation(irhvac, protocol_const, model, fallback_modulation)

    entity = IrRemoteHvacClimate(
        config_entry=config_entry,
        ac=ac,
        irhvac_module=irhvac,
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


class IrRemoteHvacClimate(InfraredEmitterConsumerEntity, RestoreEntity, ClimateEntity):
    """Climate entity that sends IR commands via IRremoteESP8266 / pyhvac.

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
        ac: Any,
        irhvac_module: Any,
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
        self._ac = ac
        self._irhvac = irhvac_module
        self._protocol = config_entry.data[CONF_PROTOCOL]
        self._model = _coerce_int(config_entry.data.get(CONF_MODEL), DEFAULT_MODEL)
        self._modulation = modulation
        self._debounce_delay_s = max(0.0, debounce_delay_s)
        self._current_temperature_sensor_entity_id = (
            current_temperature_sensor_entity_id
        )
        self._current_humidity_sensor_entity_id = current_humidity_sensor_entity_id
        self._power_sensor_entity_id = power_sensor_entity_id
        self._power_threshold_w = power_threshold_w
        self._power_sensor_state_remove_callback: Callable[[], None] | None = None
        self._external_entity_repair_timers: dict[str, Callable[[], None]] = {}

        self._attr_unique_id = config_entry.entry_id
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.title,
            manufacturer="IR Remote HVAC",
            model=f"{self._protocol} / model {self._model}",
            hw_version=f"Carrier {self._modulation} Hz",
        )

        self._attr_hvac_modes = self._detect_supported_hvac_modes()
        self._attr_hvac_mode = HVACMode.OFF
        self._last_on_hvac_mode = self._preferred_on_hvac_mode()

        self._attr_fan_modes = self._detect_supported_fan_modes()
        self._attr_fan_mode = self._attr_fan_modes[0] if self._attr_fan_modes else None

        self._attr_swing_modes = self._detect_supported_swing_modes()
        self._attr_swing_mode = (
            SWING_OFF if SWING_OFF in self._attr_swing_modes else None
        )

        self._attr_preset_modes = self._detect_supported_preset_modes()
        self._attr_preset_mode = (
            PRESET_NONE if PRESET_NONE in self._attr_preset_modes else None
        )

        self._attr_min_temp = min_temp
        self._attr_max_temp = max_temp
        self._attr_target_temperature_step = (
            PRECISION_HALVES if temp_step == 0.5 else PRECISION_WHOLE
        )
        self._attr_target_temperature = 22.0
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS

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
        self._confirmed_state = self._snapshot_runtime_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride]
        """Return extra entity attributes."""
        attributes = {
            "carrier_frequency_hz": self._modulation,
            "ir_protocol": self._protocol,
            "ir_model": self._model,
            "debounce_delay_s": self._debounce_delay_s,
            "power_threshold_w": self._power_threshold_w,
            "last_on_hvac_mode": self._last_on_hvac_mode,
        }

        if self._power_sensor_entity_id:
            attributes["power_sensor_entity_id"] = self._power_sensor_entity_id

        return attributes

    @property
    def current_temperature(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride]
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

        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._restore_from_last_state(last_state)

        self._confirmed_state = self._snapshot_runtime_state()

        if not self._tracked_entity_ids:
            return

        self._power_sensor_state_remove_callback = async_track_state_change_event(
            self.hass,
            self._tracked_entity_ids,
            self._handle_power_sensor_state_change,
        )
        self._safe_update_external_entity_repairs()

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
        """Create a repair issue if the entity is still unavailable after grace period."""
        self._external_entity_repair_timers.pop(entity_id, None)

        if not self._is_external_entity_unavailable(entity_id):
            return

        issue_id = self._external_entity_issue_id(entity_id)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
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
    def _clear_external_entity_repairs(self) -> None:
        """Delete repairs issues related to tracked external entities."""
        for entity_id, _role in self._tracked_entities_with_roles:
            _ = _role
            try:
                self._cancel_external_entity_repair_timer(entity_id)
                issue_id = self._external_entity_issue_id(entity_id)
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to clear external entity repair for %s: %s",
                    entity_id,
                    err,
                )

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
            self._ac.next.power = hvac_mode_state != HVACMode.OFF
            if hvac_mode_state != HVACMode.OFF:
                self._last_on_hvac_mode = hvac_mode_state
                irhvac_mode_name = HVAC_MODE_TO_IRHVAC.get(hvac_mode_state)
                if irhvac_mode_name and hasattr(self._irhvac, irhvac_mode_name):
                    self._ac.next.mode = getattr(self._irhvac, irhvac_mode_name)
            elif self._last_on_hvac_mode is not None:
                irhvac_mode_name = HVAC_MODE_TO_IRHVAC.get(self._last_on_hvac_mode)
                if irhvac_mode_name and hasattr(self._irhvac, irhvac_mode_name):
                    self._ac.next.mode = getattr(self._irhvac, irhvac_mode_name)
        else:
            self._attr_hvac_mode = HVACMode.OFF
            self._ac.next.power = False

        target_temperature = last_state.attributes.get(ATTR_TEMPERATURE)
        if target_temperature is not None:
            try:
                temperature = float(target_temperature)
            except (TypeError, ValueError):
                temperature = None
            else:
                self._attr_target_temperature = temperature
                self._ac.next.degrees = temperature
                self._ac.next.celsius = True

        fan_mode = last_state.attributes.get(ATTR_FAN_MODE)
        if fan_mode in self._attr_fan_modes:
            self._attr_fan_mode = fan_mode
            irhvac_fan_name = FAN_MODE_TO_IRHVAC.get(fan_mode)
            if irhvac_fan_name and hasattr(self._irhvac, irhvac_fan_name):
                self._ac.next.fanspeed = getattr(self._irhvac, irhvac_fan_name)

        swing_mode = last_state.attributes.get(ATTR_SWING_MODE)
        if swing_mode in self._attr_swing_modes:
            self._attr_swing_mode = swing_mode
            irhvac_swing_names = SWING_MODE_TO_IRHVAC.get(swing_mode)
            if irhvac_swing_names:
                swingv_name, swingh_name = irhvac_swing_names
                if hasattr(self._irhvac, swingv_name):
                    self._ac.next.swingv = getattr(self._irhvac, swingv_name)
                if hasattr(self._irhvac, swingh_name):
                    self._ac.next.swingh = getattr(self._irhvac, swingh_name)

        preset_mode = last_state.attributes.get(ATTR_PRESET_MODE)
        if preset_mode in self._attr_preset_modes:
            self._attr_preset_mode = preset_mode
            self._ac.next.quiet = preset_mode == PRESET_QUIET
            self._ac.next.turbo = preset_mode == PRESET_TURBO
            self._ac.next.econo = preset_mode == PRESET_ECO

        self.async_write_ha_state()

    def _snapshot_runtime_state(self) -> dict[str, Any]:
        """Snapshot the current runtime state for rollback on send failures."""
        ac_next_attrs = {}
        for attr in (
            "model",
            "power",
            "mode",
            "degrees",
            "celsius",
            "fanspeed",
            "swingv",
            "swingh",
            "quiet",
            "turbo",
            "econo",
        ):
            if hasattr(self._ac.next, attr):
                ac_next_attrs[attr] = getattr(self._ac.next, attr)

        return {
            "hvac_mode": self._attr_hvac_mode,
            "target_temperature": self._attr_target_temperature,
            "fan_mode": self._attr_fan_mode,
            "swing_mode": self._attr_swing_mode,
            "preset_mode": self._attr_preset_mode,
            "last_on_hvac_mode": self._last_on_hvac_mode,
            "ac_next": ac_next_attrs,
        }

    def _apply_runtime_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Restore runtime state from a previously captured snapshot."""
        self._attr_hvac_mode = snapshot["hvac_mode"]
        self._attr_target_temperature = snapshot["target_temperature"]
        self._attr_fan_mode = snapshot["fan_mode"]
        self._attr_swing_mode = snapshot["swing_mode"]
        self._attr_preset_mode = snapshot["preset_mode"]
        self._last_on_hvac_mode = snapshot["last_on_hvac_mode"]

        for attr, value in snapshot["ac_next"].items():
            if hasattr(self._ac.next, attr):
                setattr(self._ac.next, attr, value)

    def _new_probe_ac(self) -> Any:
        """Build a throwaway IRac instance used only for capability probing."""
        probe_ac = self._irhvac.IRac(0)

        # Copy baseline protocol/model settings from the runtime IRac instance.
        for attr in ("protocol", "model", "celsius", "power", "degrees"):
            if hasattr(self._ac.next, attr) and hasattr(probe_ac.next, attr):
                setattr(probe_ac.next, attr, getattr(self._ac.next, attr))

        return probe_ac

    def _probe_capability(self, apply_state: Callable[[Any], Any]) -> bool:
        """Return True if applying a state can generate a non-empty IR frame."""
        probe_ac = self._new_probe_ac()

        try:
            apply_state(probe_ac.next)
            probe_ac.sendAc()
            return bool(list(probe_ac.getTiming()))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Capability probe failed for protocol %s: %s", self._protocol, err
            )
            return False

    def _detect_supported_hvac_modes(self) -> list[HVACMode]:
        """Detect supported HVAC modes for this protocol/model."""
        supported: list[HVACMode] = [HVACMode.OFF]

        for mode in _HVAC_MODES:
            if mode == HVACMode.OFF:
                continue

            irhvac_mode_name = HVAC_MODE_TO_IRHVAC.get(mode)
            if not irhvac_mode_name or not hasattr(self._irhvac, irhvac_mode_name):
                continue

            irhvac_mode = getattr(self._irhvac, irhvac_mode_name)
            if self._probe_capability(
                lambda nxt, m=irhvac_mode: (
                    setattr(nxt, "power", True),
                    setattr(nxt, "mode", m),
                )
            ):
                supported.append(mode)

        return supported

    def _detect_supported_fan_modes(self) -> list[str]:
        """Detect supported fan modes for this protocol/model."""
        supported: list[str] = []

        for fan_mode in _FAN_MODES:
            irhvac_fan_name = FAN_MODE_TO_IRHVAC.get(fan_mode)
            if not irhvac_fan_name or not hasattr(self._irhvac, irhvac_fan_name):
                continue

            irhvac_fan = getattr(self._irhvac, irhvac_fan_name)
            if self._probe_capability(
                lambda nxt, f=irhvac_fan: (
                    setattr(nxt, "power", True),
                    setattr(nxt, "fanspeed", f),
                )
            ):
                supported.append(fan_mode)

        return supported

    def _detect_supported_swing_modes(self) -> list[str]:
        """Detect supported combined swing modes for this protocol/model."""
        supported: list[str] = []

        for swing_mode in _SWING_MODES:
            swing_names = SWING_MODE_TO_IRHVAC.get(swing_mode)
            if not swing_names:
                continue

            swingv_name, swingh_name = swing_names
            if not hasattr(self._irhvac, swingv_name) or not hasattr(
                self._irhvac, swingh_name
            ):
                continue

            swingv = getattr(self._irhvac, swingv_name)
            swingh = getattr(self._irhvac, swingh_name)
            if self._probe_capability(
                lambda nxt, v=swingv, h=swingh: (
                    setattr(nxt, "power", True),
                    setattr(nxt, "swingv", v),
                    setattr(nxt, "swingh", h),
                )
            ):
                supported.append(swing_mode)

        return supported

    def _detect_supported_preset_modes(self) -> list[str]:
        """Detect supported presets for this protocol/model."""
        supported: list[str] = [PRESET_NONE]

        for preset_mode in _PRESET_MODES:
            if preset_mode == PRESET_NONE:
                continue

            if self._probe_capability(
                lambda nxt, p=preset_mode: (
                    setattr(nxt, "power", True),
                    setattr(nxt, "quiet", p == PRESET_QUIET),
                    setattr(nxt, "turbo", p == PRESET_TURBO),
                    setattr(nxt, "econo", p == PRESET_ECO),
                )
            ):
                supported.append(preset_mode)

        return supported

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
                self._protocol,
            )
            return

        if hvac_mode == HVACMode.OFF:
            self._ac.next.power = False
        else:
            self._ac.next.power = True
            irhvac_mode_name = HVAC_MODE_TO_IRHVAC.get(hvac_mode)
            if irhvac_mode_name and hasattr(self._irhvac, irhvac_mode_name):
                self._ac.next.mode = getattr(self._irhvac, irhvac_mode_name)
            else:
                _LOGGER.warning(
                    "HVAC mode '%s' has no mapping for protocol %s; skipping mode set",
                    hvac_mode,
                    self._protocol,
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

        target_temperature = float(temperature)
        self._ac.next.degrees = float(temperature)
        self._ac.next.celsius = True

        self._attr_target_temperature = target_temperature
        self.async_write_ha_state()
        self._schedule_ir_send()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed and send IR command."""
        fan_modes = self._attr_fan_modes or []
        if fan_mode not in fan_modes:
            _LOGGER.warning(
                "Requested unsupported fan mode '%s' for protocol %s",
                fan_mode,
                self._protocol,
            )
            return

        irhvac_fan_name = FAN_MODE_TO_IRHVAC.get(fan_mode)
        if irhvac_fan_name and hasattr(self._irhvac, irhvac_fan_name):
            self._ac.next.fanspeed = getattr(self._irhvac, irhvac_fan_name)
        else:
            _LOGGER.warning(
                "Fan mode '%s' has no mapping for protocol %s; skipping",
                fan_mode,
                self._protocol,
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
                "Requested unsupported swing mode '%s' for protocol %s",
                swing_mode,
                self._protocol,
            )
            return

        irhvac_swing_names = SWING_MODE_TO_IRHVAC.get(swing_mode)
        if not irhvac_swing_names:
            _LOGGER.warning(
                "Swing mode '%s' has no mapping for protocol %s; skipping",
                swing_mode,
                self._protocol,
            )
            return

        swingv_name, swingh_name = irhvac_swing_names

        if hasattr(self._irhvac, swingv_name):
            self._ac.next.swingv = getattr(self._irhvac, swingv_name)
        else:
            _LOGGER.warning(
                "Vertical swing constant '%s' not available for protocol %s; skipping vertical swing",
                swingv_name,
                self._protocol,
            )
            return

        if hasattr(self._irhvac, swingh_name):
            self._ac.next.swingh = getattr(self._irhvac, swingh_name)
        else:
            _LOGGER.warning(
                "Horizontal swing constant '%s' not available for protocol %s; skipping horizontal swing",
                swingh_name,
                self._protocol,
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
                "Requested unsupported preset mode '%s' for protocol %s",
                preset_mode,
                self._protocol,
            )
            return

        self._ac.next.quiet = preset_mode == PRESET_QUIET
        self._ac.next.turbo = preset_mode == PRESET_TURBO
        self._ac.next.econo = preset_mode == PRESET_ECO

        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()
        self._schedule_ir_send()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending debounced send when entity is removed."""
        if self._power_sensor_state_remove_callback is not None:
            self._power_sensor_state_remove_callback()
            self._power_sensor_state_remove_callback = None
        self._clear_external_entity_repairs()
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

    def _generate_raw_timings(self) -> list[int] | None:
        """Generate raw timings for the configured protocol/model."""
        try:
            self._ac.next.model = self._model
            self._ac.resetTiming()
            self._ac.sendAc()
            raw_timings: list[int] = list(self._ac.getTiming())
            self._ac.resetTiming()

            if self._protocol == "DAIKIN":
                raw_timings = _normalize_daikin_timings(raw_timings)

            return raw_timings or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to generate IR timing for protocol '%s' model %s: %s",
                self._protocol,
                self._model,
                err,
            )
            return None

    async def _send_ir_command_now(self) -> bool:
        """Generate and dispatch an IR command.

        Returns True only when command dispatch succeeds.
        """
        raw_timings = self._generate_raw_timings()
        if not raw_timings:
            _LOGGER.warning(
                "pyhvac returned empty timing for protocol '%s'. "
                "The protocol may not support the requested state.",
                self._protocol,
            )
            return False

        timing_duration_us = sum(abs(t) for t in raw_timings)
        timing_duration_ms = timing_duration_us / 1000

        if timing_duration_ms > _MAX_IR_SEND_DURATION_MS:
            message = (
                f"Generated IR command for protocol '{self._protocol}' model {self._model} "
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

        command = IrHvacRawCommand(raw_timings, modulation=self._modulation)

        # _send_command is provided by InfraredEmitterConsumerEntity;
        # it wraps infrared.async_send_command with entity context.
        try:
            _LOGGER.debug(
                "Sending IR command entity=%s protocol=%s model=%s modulation=%s timing_len=%s timing_duration_ms=%.1f head=%s tail=%s",
                self.entity_id,
                self._protocol,
                self._model,
                self._modulation,
                len(raw_timings),
                timing_duration_ms,
                raw_timings[:10],
                raw_timings[-10:],
            )
            for attempt in range(_IR_BURST_COUNT):
                await self._send_command(command)
                if attempt < _IR_BURST_COUNT - 1:
                    await asyncio.sleep(_IR_BURST_GAP_MS / 1000)
            _LOGGER.debug(
                "Sent IR command entity=%s protocol=%s model=%s modulation=%s burst_count=%s",
                self.entity_id,
                self._protocol,
                self._model,
                self._modulation,
                _IR_BURST_COUNT,
            )
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to send IR command entity=%s protocol=%s model=%s modulation=%s: %s",
                self.entity_id,
                self._protocol,
                self._model,
                self._modulation,
                err,
            )
            if "Network timeout" in str(err) or "timeout" in str(err).lower():
                _LOGGER.warning(
                    "IR send timed out for entity=%s emitter=%s.",
                    self.entity_id,
                    self._infrared_emitter_entity_id,
                )
            return False
