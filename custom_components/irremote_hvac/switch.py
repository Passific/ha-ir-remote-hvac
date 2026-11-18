"""Power and accessory switches for IR Remote HVAC."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_HVAC_STATE_UPDATED

if TYPE_CHECKING:
    from .climate import IrRemoteHvacClimate


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the IR HVAC power switch from a config entry."""
    climate_entity = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if climate_entity is None:
        raise PlatformNotReady("Waiting for IR HVAC climate entity")

    async_add_entities(
        [
            IrRemoteHvacPowerSwitch(config_entry, climate_entity),
            *(
                IrRemoteHvacFeatureSwitch(config_entry, climate_entity, feature)
                for feature in climate_entity.switch_features
            ),
        ]
    )


class _IrRemoteHvacSwitch(SwitchEntity):
    """A switch backed by the paired climate entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, config_entry: ConfigEntry, climate_entity: IrRemoteHvacClimate
    ) -> None:
        self._climate_entity = climate_entity
        self._entry_id = config_entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
        )

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
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Allow the entity ID to change without removing the entity."""
        super().async_entity_id_changed(old_entity_id)

    @callback
    def _handle_climate_updated(self) -> None:
        """Refresh switch state when the paired climate entity changes."""
        self.async_write_ha_state()


class IrRemoteHvacPowerSwitch(_IrRemoteHvacSwitch):
    """Mirror and control the power state of the paired climate entity."""

    _attr_translation_key = "power"

    def __init__(
        self, config_entry: ConfigEntry, climate_entity: IrRemoteHvacClimate
    ) -> None:
        super().__init__(config_entry, climate_entity)
        self._attr_unique_id = f"{config_entry.entry_id}_power"

    @property
    def is_on(self) -> bool:
        """Return True if the paired climate entity is currently on."""
        return self._climate_entity.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the HVAC on using its last active mode."""
        await self._climate_entity.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the HVAC off."""
        await self._climate_entity.async_turn_off()


class IrRemoteHvacFeatureSwitch(_IrRemoteHvacSwitch):
    """Mirror and control one on/off accessory feature."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        climate_entity: IrRemoteHvacClimate,
        feature: str,
    ) -> None:
        super().__init__(config_entry, climate_entity)
        self._feature = feature
        self._attr_translation_key = feature
        self._attr_unique_id = f"{config_entry.entry_id}_{feature}"

    @property
    def is_on(self) -> bool:
        """Return the feature state tracked by the climate entity."""
        return self._climate_entity.is_feature_on(self._feature)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the accessory feature."""
        await self._climate_entity.async_set_feature(self._feature, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the accessory feature."""
        await self._climate_entity.async_set_feature(self._feature, False)
