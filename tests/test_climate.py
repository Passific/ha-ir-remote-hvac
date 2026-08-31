"""Tests for the IR Remote HVAC climate entity."""
# pyright: reportPrivateUsage=false
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate.const import PRESET_NONE, HVACAction, HVACMode
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irremote_hvac import climate
from custom_components.irremote_hvac.climate import (
    IrHvacRawCommand,
    IrRemoteHvacClimate,
)
from custom_components.irremote_hvac.const import (
    CONF_EMITTER_ENTITY_ID,
    CONF_MODEL,
    CONF_PROTOCOL,
    DEFAULT_MODEL,
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
            CONF_PROTOCOL: "TEST_PROTOCOL",
            CONF_MODEL: DEFAULT_MODEL,
        },
        options={},
        entry_id="entry-1",
        title="Test HVAC",
    )

    ac = SimpleNamespace(
        next=SimpleNamespace(
            model=None,
            power=False,
            mode=None,
            degrees=None,
            celsius=True,
            fanspeed=None,
            swingv=None,
            swingh=None,
            quiet=False,
            turbo=False,
            econo=False,
        ),
    )
    irhvac_module = SimpleNamespace(
        opmode_t_kAuto="AUTO",
        opmode_t_kHeat="HEAT",
    )

    entity = IrRemoteHvacClimate(
        config_entry=config_entry,
        ac=ac,
        irhvac_module=irhvac_module,
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
    assert entity._ac.next.mode == "HEAT"
    assert entity.extra_state_attributes["last_on_hvac_mode"] == HVACMode.HEAT


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


def test_async_turn_on_falls_back_to_preferred_mode_when_missing_history(monkeypatch) -> None:
    """Toggle-on should still fall back to a supported preferred mode when no history exists."""
    entity = _build_entity(monkeypatch)
    entity._last_on_hvac_mode = None
    entity._attr_hvac_mode = HVACMode.OFF
    entity.async_set_hvac_mode = AsyncMock()

    asyncio.run(entity.async_turn_on())

    entity.async_set_hvac_mode.assert_awaited_once_with(HVACMode.AUTO)


def test_raw_command_converts_spaces_to_negative_timings() -> None:
    """IRremoteESP8266 mark/space timings match the infrared API convention."""
    command = IrHvacRawCommand([9000, 4500, 560, 560], modulation=38000)

    assert command.get_raw_timings() == [9000, -4500, 560, -560]


def test_send_command_dispatches_generated_timings(monkeypatch) -> None:
    """Generated timings are dispatched through the infrared consumer API."""
    entity = _build_entity(monkeypatch)
    entity._ac.resetTiming = lambda: None
    entity._ac.sendAc = lambda: None
    entity._ac.getTiming = lambda: [9000, 4500, 560, 560]
    entity._send_command = AsyncMock()

    assert asyncio.run(entity._send_ir_command_now()) is True
    command = entity._send_command.await_args.args[0]
    assert command.get_raw_timings() == [9000, -4500, 560, -560]


def test_failed_debounced_send_restores_last_confirmed_state(monkeypatch) -> None:
    """A failed dispatch rolls optimistic entity and IR state back."""
    entity = _build_entity(monkeypatch)
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._ac.next.power = True
    entity._ac.next.mode = "HEAT"
    entity._pending_send_generation = 1
    entity._send_ir_command_now = AsyncMock(return_value=False)

    asyncio.run(entity._debounced_send(1))

    assert entity.hvac_mode == HVACMode.OFF
    assert entity._ac.next.power is False
    assert entity._ac.next.mode is None


def test_successful_debounced_send_confirms_current_state(monkeypatch) -> None:
    """A successful dispatch commits the updated state as the rollback target."""
    entity = _build_entity(monkeypatch)
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._ac.next.power = True
    entity._ac.next.mode = "HEAT"
    entity._pending_send_generation = 1
    entity._send_ir_command_now = AsyncMock(return_value=True)

    asyncio.run(entity._debounced_send(1))

    assert entity._confirmed_state["hvac_mode"] == HVACMode.HEAT
    assert entity._confirmed_state["ac_next"]["power"] is True


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
