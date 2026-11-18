"""Tests for the IR Remote HVAC switch entities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import PlatformNotReady
from pytest_homeassistant.common import MockConfigEntry

from custom_components.irremote_hvac.climate import IrRemoteHvacClimate
from custom_components.irremote_hvac.climate import async_setup_entry as setup_climate
from custom_components.irremote_hvac.const import (
    CONF_BRAND,
    CONF_EMITTER_ENTITY_ID,
    CONF_MODEL,
    DOMAIN,
)
from custom_components.irremote_hvac.switch import (
    IrRemoteHvacFeatureSwitch,
    IrRemoteHvacPowerSwitch,
    async_setup_entry,
)


def _build_switch() -> tuple[IrRemoteHvacPowerSwitch, MagicMock]:
    """Build a switch wired to a fake climate entity."""
    climate_entity = MagicMock(spec=IrRemoteHvacClimate)
    climate_entity.is_on = False
    climate_entity.available = True
    config_entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")
    switch = IrRemoteHvacPowerSwitch(config_entry, climate_entity)
    return switch, climate_entity


async def test_setup_entry_waits_for_climate_entity(hass) -> None:
    """Switch setup should retry if the climate entity is not available yet."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")

    with pytest.raises(PlatformNotReady):
        await async_setup_entry(hass, entry, AsyncMock())


@pytest.mark.parametrize(
    ("brand", "model", "features"),
    [
        ("Airton", "AIRTON protocol", {"light", "sleep", "purifier"}),
        ("Airspool", "Mini-split", {"light", "sleep", "power_limit"}),
        ("AEG", "Chillflex Pro AXP26U558HW", {"light", "cleaning"}),
    ],
)
async def test_setup_adds_only_supported_accessory_switches(
    hass, brand, model, features
) -> None:
    """Keep the original power switch and expose only supported accessories."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-1",
        data={
            CONF_BRAND: brand,
            CONF_MODEL: model,
            CONF_EMITTER_ENTITY_ID: "infrared.emitter",
        },
    )
    await setup_climate(hass, entry, lambda _: None)
    added = []

    await async_setup_entry(hass, entry, added.extend)

    power, *accessories = added
    assert isinstance(power, IrRemoteHvacPowerSwitch)
    assert power.unique_id == "entry-1_power"
    assert all(isinstance(switch, IrRemoteHvacFeatureSwitch) for switch in accessories)
    assert {switch.unique_id for switch in accessories} == {
        f"entry-1_{feature}" for feature in features
    }


async def test_accessory_switch_uses_shared_climate_state() -> None:
    """Service calls update the feature on the paired climate entity."""
    entity = MagicMock(spec=IrRemoteHvacClimate)
    entity.is_feature_on.return_value = True
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")
    switch = IrRemoteHvacFeatureSwitch(entry, entity, "light")

    assert switch.is_on is True
    await switch.async_turn_on()
    await switch.async_turn_off()
    assert [call.args for call in entity.async_set_feature.await_args_list] == [
        ("light", True),
        ("light", False),
    ]


def test_is_on_mirrors_climate_entity() -> None:
    """The switch should report the paired climate entity's on/off state."""
    switch, climate_entity = _build_switch()

    assert switch.is_on is False

    climate_entity.is_on = True
    assert switch.is_on is True


def test_turn_on_delegates_to_climate_entity() -> None:
    """Turning the switch on should call the climate entity's turn_on."""
    switch, climate_entity = _build_switch()

    asyncio.run(switch.async_turn_on())

    climate_entity.async_turn_on.assert_awaited_once()


def test_turn_off_delegates_to_climate_entity() -> None:
    """Turning the switch off should call the climate entity's turn_off."""
    switch, climate_entity = _build_switch()

    asyncio.run(switch.async_turn_off())

    climate_entity.async_turn_off.assert_awaited_once()
