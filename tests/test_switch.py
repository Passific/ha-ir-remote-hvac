"""Tests for the IR Remote HVAC power switch entity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import PlatformNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irremote_hvac.const import DOMAIN
from custom_components.irremote_hvac.switch import (
    IrRemoteHvacPowerSwitch,
    async_setup_entry,
)


def _build_switch() -> tuple[IrRemoteHvacPowerSwitch, SimpleNamespace]:
    """Build a switch wired to a fake climate entity."""
    climate_entity = SimpleNamespace(
        is_on=False,
        available=True,
        async_turn_on=AsyncMock(),
        async_turn_off=AsyncMock(),
    )
    config_entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")
    switch = IrRemoteHvacPowerSwitch(config_entry, climate_entity)
    return switch, climate_entity


async def test_setup_entry_waits_for_climate_entity(hass) -> None:
    """Switch setup should retry if the climate entity is not available yet."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")

    with pytest.raises(PlatformNotReady):
        await async_setup_entry(hass, entry, AsyncMock())


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
