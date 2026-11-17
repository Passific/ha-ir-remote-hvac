"""Tests for the IR Remote HVAC integration lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant.common import MockConfigEntry

from custom_components.irremote_hvac import (
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.irremote_hvac.const import (
    CONF_BRAND,
    CONF_EMITTER_ENTITY_ID,
    CONF_LEGACY_PROTOCOL,
    CONF_MODEL,
    DOMAIN,
)


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """Set up and unload all integration platforms."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms_or_raise = AsyncMock()

    assert await async_setup_entry(hass, entry) is None
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    assert await async_unload_entry(hass, entry) is None
    hass.config_entries.async_unload_platforms_or_raise.assert_awaited_once()


async def test_failed_unload_preserves_runtime_data(hass: HomeAssistant) -> None:
    """Keep runtime data when Home Assistant cannot unload the platforms."""
    entry = MockConfigEntry(domain=DOMAIN)
    hass.data[DOMAIN] = {entry.entry_id: object()}
    hass.config_entries.async_unload_platforms_or_raise = AsyncMock(
        side_effect=ConfigEntryError("Unable to unload")
    )

    with pytest.raises(ConfigEntryError):
        await async_unload_entry(hass, entry)
    assert entry.entry_id in hass.data[DOMAIN]


async def test_setup_migrates_legacy_device_selection(hass: HomeAssistant) -> None:
    """Migrate an unambiguous pyhvac 0.1 config entry without changing its ID."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="infrared.emitter_AIRTON_-1",
        data={
            CONF_EMITTER_ENTITY_ID: "infrared.emitter",
            CONF_LEGACY_PROTOCOL: "AIRTON",
            CONF_MODEL: -1,
        },
    )
    entry.add_to_hass(hass)
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    assert await async_setup_entry(hass, entry) is None

    assert entry.data[CONF_BRAND] == "Airton"
    assert entry.data[CONF_MODEL] == "AIRTON protocol"
    assert CONF_LEGACY_PROTOCOL not in entry.data
    assert entry.unique_id == "infrared.emitter_AIRTON_-1"
    assert entry.version == 2


async def test_unmappable_legacy_entry_raises_config_entry_error(
    hass: HomeAssistant,
) -> None:
    """Expose a migration failure without replacing the saved configuration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LEGACY_PROTOCOL: "AIRTON", CONF_MODEL: 123},
    )
    entry.add_to_hass(hass)

    with pytest.raises(ConfigEntryError) as exc_info:
        await async_migrate_entry(hass, entry)

    assert exc_info.value.translation_key == "legacy_device_migration"
    assert entry.data[CONF_LEGACY_PROTOCOL] == "AIRTON"
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{entry.entry_id}_legacy_device_migration"
    )
    assert issue is not None
    assert issue.config_entry_id == entry.entry_id
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "legacy_device_migration"
    assert issue.translation_placeholders == {"name": entry.title}


@pytest.mark.parametrize("reconfigure", [False, True])
async def test_resolved_migration_clears_repair(
    hass: HomeAssistant, reconfigure: bool
) -> None:
    """Clear a failed-migration repair on retry or setup after reconfiguration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LEGACY_PROTOCOL: "AIRTON", CONF_MODEL: 123},
    )
    entry.add_to_hass(hass)
    with pytest.raises(ConfigEntryError):
        await async_migrate_entry(hass, entry)

    issue_id = f"{entry.entry_id}_legacy_device_migration"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    if reconfigure:
        hass.config_entries.async_update_entry(
            entry,
            data={CONF_BRAND: "Airton", CONF_MODEL: "AIRTON protocol"},
            version=2,
        )
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        await async_setup_entry(hass, entry)
    else:
        hass.config_entries.async_update_entry(
            entry, data={CONF_LEGACY_PROTOCOL: "AIRTON", CONF_MODEL: -1}
        )
        await async_migrate_entry(hass, entry)

    assert registry.async_get_issue(DOMAIN, issue_id) is None


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
