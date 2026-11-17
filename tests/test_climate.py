"""Tests for the IR Remote HVAC climate entity."""
# pyright: reportPrivateUsage=false
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate.const import PRESET_NONE, HVACAction, HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import State
from homeassistant.helpers import issue_registry as ir
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pyhvac import registry
from pytest_homeassistant.common import MockConfigEntry

from custom_components.irremote_hvac import climate
from custom_components.irremote_hvac.climate import (
    IrHvacRawCommand,
    IrRemoteHvacClimate,
    async_setup_entry,
)
from custom_components.irremote_hvac.const import (
    CONF_BRAND,
    CONF_DEBOUNCE_DELAY,
    CONF_EMITTER_ENTITY_ID,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_MODEL,
    CONF_POWER_THRESHOLD,
    CONF_TEMP_STEP,
    DEFAULT_DEBOUNCE_DELAY,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_TEMP_STEP,
    DOMAIN,
)


def _build_entity(monkeypatch) -> IrRemoteHvacClimate:
    """Build an entity with deterministic supported modes for unit tests."""
    monkeypatch.setattr(
        IrRemoteHvacClimate,
        "_detect_supported_hvac_modes",
        lambda _: [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT],
    )
    monkeypatch.setattr(
        IrRemoteHvacClimate,
        "_detect_supported_fan_modes",
        lambda _: [],
    )
    monkeypatch.setattr(
        IrRemoteHvacClimate,
        "_detect_supported_swing_modes",
        lambda _: [],
    )
    monkeypatch.setattr(
        IrRemoteHvacClimate,
        "_detect_supported_preset_modes",
        lambda _: [PRESET_NONE],
    )

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMITTER_ENTITY_ID: "infrared.emitter",
            CONF_BRAND: "Airton",
            CONF_MODEL: "AIRTON protocol",
        },
        options={},
        entry_id="entry-1",
        title="Test HVAC",
    )

    entity = IrRemoteHvacClimate(
        config_entry=config_entry,
        device=registry.get_device("Airton", "AIRTON protocol"),
        min_temp=16.0,
        max_temp=30.0,
        temp_step=1.0,
        modulation=38000,
        debounce_delay_s=0.0,
        current_temperature_sensor_entity_id=None,
        current_humidity_sensor_entity_id=None,
        power_sensor_entity_id=None,
        power_threshold_w=10.0,
    )
    entity.async_write_ha_state = lambda: None
    entity._schedule_ir_send = lambda: None
    return entity


async def test_setup_entry_falls_back_for_malformed_numeric_options(hass) -> None:
    """Malformed stored numeric options should not prevent setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMITTER_ENTITY_ID: "infrared.emitter",
            CONF_BRAND: "Airton",
            CONF_MODEL: "AIRTON protocol",
        },
        options={
            CONF_MIN_TEMP: "hot",
            CONF_MAX_TEMP: "cold",
            CONF_TEMP_STEP: "tiny",
            CONF_DEBOUNCE_DELAY: "fast",
            CONF_POWER_THRESHOLD: "high",
        },
        entry_id="entry-1",
        title="Test HVAC",
    )
    added_entities = []

    await async_setup_entry(hass, entry, added_entities.extend)

    [entity] = added_entities
    assert entity._model == "AIRTON protocol"
    assert entity._attr_min_temp == DEFAULT_MIN_TEMP
    assert entity._attr_max_temp == DEFAULT_MAX_TEMP
    assert entity._attr_target_temperature_step == DEFAULT_TEMP_STEP
    assert entity._debounce_delay_s == DEFAULT_DEBOUNCE_DELAY
    assert entity._power_threshold_w == DEFAULT_POWER_THRESHOLD


def test_restore_from_last_state_recovers_last_on_mode(monkeypatch) -> None:
    """A restored OFF state should still remember the last active HVAC mode."""
    entity = _build_entity(monkeypatch)

    last_state = SimpleNamespace(
        state=HVACMode.OFF,
        attributes={"last_on_hvac_mode": HVACMode.HEAT},
    )

    entity._restore_from_last_state(last_state)

    assert entity._attr_hvac_mode == HVACMode.OFF
    assert entity._last_on_hvac_mode == HVACMode.HEAT
    assert entity._build_target_state().mode == "heat"
    assert entity._build_target_state().power is False
    assert entity.extra_state_attributes["last_on_hvac_mode"] == HVACMode.HEAT


def test_restore_from_last_state_recovers_pyhvac_previous_state(monkeypatch) -> None:
    """A saved pyhvac state remains available to toggle-based encoders."""
    entity = _build_entity(monkeypatch)
    saved_state = {
        "power": True,
        "mode": "cool",
        "temperature": 23.0,
        "fan": "auto",
        "swing_v": "off",
        "swing_h": "off",
        "features": {"powerful": False},
    }

    entity._restore_from_last_state(
        SimpleNamespace(
            state=HVACMode.COOL,
            attributes={"pyhvac_state": saved_state},
        )
    )

    assert entity._last_command_state.to_dict() == saved_state


@pytest.mark.parametrize("has_unit", [False, True])
async def test_restores_fahrenheit_target_in_native_unit(
    hass, monkeypatch, has_unit
) -> None:
    """Restore both legacy and unit-tagged state temperatures as Celsius."""
    entity = _build_entity(monkeypatch)
    entity.hass = hass
    hass.config.units = US_CUSTOMARY_SYSTEM
    attributes = {"temperature": 75.2}
    if has_unit:
        attributes["temperature_unit"] = UnitOfTemperature.FAHRENHEIT
    state = State("climate.test", HVACMode.HEAT, attributes)
    monkeypatch.setattr(
        climate.InfraredEmitterConsumerEntity, "async_added_to_hass", AsyncMock()
    )
    entity.async_get_last_state = AsyncMock(return_value=state)
    entity.async_get_last_extra_data = AsyncMock(return_value=None)
    entity.async_on_remove = MagicMock()
    entity._refresh_external_entity_repairs = MagicMock()
    monkeypatch.setattr(climate, "async_track_state_change_event", MagicMock())

    await entity.async_added_to_hass()

    assert entity.native_temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.native_target_temperature == pytest.approx(24.0)
    assert entity._build_target_state().temperature == pytest.approx(24.0)


async def test_native_current_temperature_converts_sensor_to_celsius(
    hass, monkeypatch
) -> None:
    """Expose external Fahrenheit sensor readings in the device's native unit."""
    entity = _build_entity(monkeypatch)
    entity.hass = hass
    entity._current_temperature_sensor_entity_id = "sensor.room_temperature"
    hass.states.async_set(
        "sensor.room_temperature",
        "75.2",
        {
            "device_class": "temperature",
            "unit_of_measurement": UnitOfTemperature.FAHRENHEIT,
        },
    )

    assert entity.native_current_temperature == pytest.approx(24.0)


def test_async_turn_on_uses_restored_last_on_mode(monkeypatch) -> None:
    """Toggle-on should restore the last used non-off mode after a restart."""
    entity = _build_entity(monkeypatch)
    entity._restore_from_last_state(
        SimpleNamespace(
            state=HVACMode.OFF,
            attributes={"last_on_hvac_mode": HVACMode.HEAT},
        )
    )
    entity.async_set_hvac_mode = AsyncMock()

    asyncio.run(entity.async_turn_on())

    entity.async_set_hvac_mode.assert_awaited_once_with(HVACMode.HEAT)


def test_async_turn_on_falls_back_to_preferred_mode_when_missing_history(
    monkeypatch,
) -> None:
    """Toggle-on should use a preferred mode when no history exists."""
    entity = _build_entity(monkeypatch)
    entity._last_on_hvac_mode = None
    entity._attr_hvac_mode = HVACMode.OFF
    entity.async_set_hvac_mode = AsyncMock()

    asyncio.run(entity.async_turn_on())

    entity.async_set_hvac_mode.assert_awaited_once_with(HVACMode.AUTO)


def test_raw_command_converts_spaces_to_negative_timings() -> None:
    """pyhvac signed mark/space timings match the infrared API convention."""
    command = IrHvacRawCommand([9000, -4500, 560, -560], modulation=38000)

    assert command.get_raw_timings() == [9000, -4500, 560, -560]


def test_send_command_dispatches_generated_timings(monkeypatch) -> None:
    """A real pyhvac command is converted to signed raw timings for infrared."""
    entity = _build_entity(monkeypatch)
    entity._attr_hvac_mode = HVACMode.COOL
    entity._send_command = AsyncMock()

    assert asyncio.run(entity._send_ir_command_now()) is True
    command = entity._send_command.await_args.args[0]
    timings = command.get_raw_timings()
    assert timings
    assert timings[0] > 0
    assert timings[1] < 0
    assert entity._last_command_state is not None
    assert entity._last_command_state.power is True
    assert entity._last_command_state.mode == "cool"
    assert entity.extra_state_attributes["pyhvac_state"]["mode"] == "cool"


def test_failed_debounced_send_restores_last_confirmed_state(monkeypatch) -> None:
    """A failed dispatch rolls optimistic entity and IR state back."""
    entity = _build_entity(monkeypatch)
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._pending_send_generation = 1
    entity._send_ir_command_now = AsyncMock(return_value=False)

    asyncio.run(entity._debounced_send(1))

    assert entity.hvac_mode == HVACMode.OFF
    assert entity._last_command_state is None


def test_successful_debounced_send_confirms_current_state(monkeypatch) -> None:
    """A successful dispatch commits the updated state as the rollback target."""
    entity = _build_entity(monkeypatch)
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._pending_send_generation = 1
    entity._send_ir_command_now = AsyncMock(return_value=True)

    asyncio.run(entity._debounced_send(1))

    assert entity._confirmed_state["hvac_mode"] == HVACMode.HEAT


async def test_power_sensor_changes_hvac_action(hass, monkeypatch) -> None:
    """A configured power sensor distinguishes active heating from idle."""
    entity = _build_entity(monkeypatch)
    entity._power_sensor_entity_id = "sensor.ac_power"
    entity.hass = hass
    entity._attr_hvac_mode = HVACMode.HEAT
    hass.states.async_set(
        "sensor.ac_power",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    assert entity.hvac_action is HVACAction.HEATING

    hass.states.async_set(
        "sensor.ac_power",
        "5",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    assert entity.hvac_action is HVACAction.IDLE


async def test_rapid_updates_send_only_the_latest_state(hass, monkeypatch) -> None:
    """A later update cancels the earlier pending debounced send."""
    entity = _build_entity(monkeypatch)
    entity.hass = hass
    entity._schedule_ir_send = MethodType(IrRemoteHvacClimate._schedule_ir_send, entity)
    entity._debounce_delay_s = 0.0
    entity._send_ir_command_now = AsyncMock(return_value=True)

    entity._schedule_ir_send()
    entity._schedule_ir_send()
    assert entity._pending_send_task is not None
    await entity._pending_send_task

    entity._send_ir_command_now.assert_awaited_once()


def test_available_external_entity_clears_pending_repair(hass, monkeypatch) -> None:
    """An external entity recovery cancels its pending repair timer."""
    entity = _build_entity(monkeypatch)
    entity.hass = hass
    entity._power_sensor_entity_id = "sensor.ac_power"
    cancel_timer = MagicMock()
    schedule_timer = MagicMock(return_value=cancel_timer)
    monkeypatch.setattr(climate, "async_call_later", schedule_timer)
    delete_issue = MagicMock()
    monkeypatch.setattr(climate.ir, "async_delete_issue", delete_issue)

    entity._update_external_entity_repairs()
    assert entity._power_sensor_entity_id in entity._external_entity_repair_timers

    hass.states.async_set("sensor.ac_power", "100")
    entity._update_external_entity_repairs()

    cancel_timer.assert_called_once()
    delete_issue.assert_called_once()


async def test_repair_refresh_keeps_ignored_issues_and_removes_stale(
    hass, monkeypatch
) -> None:
    """Reloads preserve ignored issues and remove issues for removed sensors."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")
    entry.add_to_hass(hass)
    entity = _build_entity(monkeypatch)
    entity.hass = hass
    entity._power_sensor_entity_id = "sensor.ac_power"
    monkeypatch.setattr(
        climate, "async_call_later", MagicMock(return_value=lambda: None)
    )
    registry = ir.async_get(hass)
    entity._handle_external_entity_grace_elapsed("sensor.ac_power", "power sensor")
    issue_id = entity._external_entity_issue_id("sensor.ac_power")
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.config_entry_id == entry.entry_id
    registry.async_ignore(DOMAIN, issue_id, True)

    entity._refresh_external_entity_repairs()
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None

    entity._power_sensor_entity_id = None
    entity._refresh_external_entity_repairs()
    assert registry.async_get_issue(DOMAIN, issue_id) is None
