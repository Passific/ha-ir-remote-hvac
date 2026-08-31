"""Tests for the IR Remote HVAC integration lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irremote_hvac import async_setup_entry, async_unload_entry
from custom_components.irremote_hvac.const import DOMAIN


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """Set up and unload all integration platforms."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await async_setup_entry(hass, entry) is True
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    assert await async_unload_entry(hass, entry) is True
    hass.config_entries.async_unload_platforms.assert_awaited_once()


async def test_options_update_reloads_entry(hass: HomeAssistant) -> None:
    """Reload the config entry after option changes."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_reload = AsyncMock()

    await async_setup_entry(hass, entry)
    assert len(entry.update_listeners) == 1
    await entry.update_listeners[0](hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
