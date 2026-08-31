"""Switch entity mirroring and controlling the IR Remote HVAC power state."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_HVAC_STATE_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the IR HVAC power switch from a config entry."""
    climate_entity = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([IrRemoteHvacPowerSwitch(config_entry, climate_entity)])


class IrRemoteHvacPowerSwitch(SwitchEntity):
    """Switch that mirrors and controls the power state of the paired climate entity.

    Talks to the climate entity directly (rather than via entity_id/service
    calls) so it works regardless of entity registration ordering.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "power"

    def __init__(self, config_entry: ConfigEntry, climate_entity: Any) -> None:
        self._climate_entity = climate_entity
        self._entry_id = config_entry.entry_id
        self._attr_unique_id = f"{config_entry.entry_id}_power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return True if the paired climate entity is currently on."""
        return self._climate_entity.is_on

    @property
    def available(self) -> bool:
        """Return True if the paired climate entity is available."""
        return self._climate_entity.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to climate entity state updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_HVAC_STATE_UPDATED}_{self._entry_id}",
                self._handle_climate_updated,
            )
        )

    @callback
    def _handle_climate_updated(self) -> None:
        """Refresh switch state when the paired climate entity changes."""
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the HVAC on using its last active mode."""
        await self._climate_entity.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the HVAC off."""
        await self._climate_entity.async_turn_off()
