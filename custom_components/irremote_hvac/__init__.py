"""The IR Remote HVAC integration."""

from __future__ import annotations

import importlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir

from .const import CONF_LEGACY_PROTOCOL, DOMAIN
from .pyhvac_helpers import migrate_legacy_device

PLATFORMS = ["climate", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up IR Remote HVAC from a config entry."""
    if CONF_LEGACY_PROTOCOL in entry.data:
        await async_migrate_entry(hass, entry)

    ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_legacy_device_migration")

    for platform in PLATFORMS:
        await hass.async_add_executor_job(
            importlib.import_module,
            f"{__name__}.{platform}",
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Re-load the entity if options change
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate stored device selection to the pyhvac 0.2 brand/model format."""
    try:
        data = migrate_legacy_device(dict(entry.data))
    except ValueError as err:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{entry.entry_id}_legacy_device_migration",
            config_entry_id=entry.entry_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="legacy_device_migration",
            translation_placeholders={"name": entry.title},
        )
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="legacy_device_migration",
        ) from err

    hass.config_entries.async_update_entry(entry, data=data, version=2)
    ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_legacy_device_migration")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unload IR Remote HVAC config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
