"""Tests for the IR Remote HVAC config flow."""
# pyright: reportTypedDictNotRequiredAccess=false

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irremote_hvac import config_flow
from custom_components.irremote_hvac.const import (
    CONF_EMITTER_ENTITY_ID,
    CONF_MODEL,
    CONF_PROTOCOL,
    CONF_TEMP_STEP,
    DEFAULT_MODEL,
    DEFAULT_TEMP_STEP,
    DOMAIN,
)


def _emitters(_hass: HomeAssistant) -> list[str]:
    """Return deterministic test emitters."""
    _ = _hass
    return ["infrared.living_room", "infrared.office"]


async def test_user_step_aborts_when_no_emitters(hass: HomeAssistant) -> None:
    """The flow should abort when no IR emitters are available."""
    with patch.object(config_flow.infrared, "async_get_emitters", return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_emitters"


async def test_full_config_flow_creates_entry(hass: HomeAssistant) -> None:
    """The setup flow should create a uniquely identified config entry."""
    with patch.object(config_flow.infrared, "async_get_emitters", _emitters):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "Bedroom AC",
                CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            },
        )
        assert result["step_id"] == "protocol"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PROTOCOL: "Airton"}
        )
        assert result["step_id"] == "temp_step"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_TEMP_STEP: "1_0"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bedroom AC"
    assert result["data"][CONF_PROTOCOL] == "AIRTON"
    assert result["data"][CONF_MODEL] == DEFAULT_MODEL
    assert result["data"][CONF_TEMP_STEP] == DEFAULT_TEMP_STEP
    assert result["data"][CONF_EMITTER_ENTITY_ID] == "infrared.living_room"
    assert result["result"].unique_id == "infrared.living_room_AIRTON_-1"


def test_temp_step_conversion_handles_unexpected_values() -> None:
    """Unexpected temperature-step values should not crash conversion helpers."""
    assert config_flow._selector_value_to_temp_step("0_5") == 0.5
    assert config_flow._selector_value_to_temp_step("1_0") == 1.0
    assert config_flow._selector_value_to_temp_step("bad_value") is None
    assert config_flow._temp_step_to_selector_value(0.5) == "0_5"
    assert config_flow._temp_step_to_selector_value(1.0) == "1_0"
    assert config_flow._temp_step_to_selector_value("bad_value") == "1_0"


async def test_duplicate_config_flow_aborts(hass: HomeAssistant) -> None:
    """The same emitter, protocol, and model cannot be configured twice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="infrared.living_room_AIRTON_-1",
        data={
            "name": "Bedroom AC",
            CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            CONF_PROTOCOL: "AIRTON",
            CONF_MODEL: DEFAULT_MODEL,
            CONF_TEMP_STEP: DEFAULT_TEMP_STEP,
        },
    )
    entry.add_to_hass(hass)

    with patch.object(config_flow.infrared, "async_get_emitters", _emitters):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "Duplicate AC",
                CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PROTOCOL: "Airton"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_TEMP_STEP: "1_0"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_flow_updates_entry(hass: HomeAssistant) -> None:
    """Reconfiguration should update data, title, and unique ID."""
    hass.states.async_set("infrared.living_room", "available")
    hass.states.async_set("infrared.office", "available")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom AC",
        unique_id="infrared.living_room_AIRTON_-1",
        data={
            "name": "Bedroom AC",
            CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            CONF_PROTOCOL: "AIRTON",
            CONF_MODEL: DEFAULT_MODEL,
            CONF_TEMP_STEP: DEFAULT_TEMP_STEP,
        },
    )
    entry.add_to_hass(hass)

    with patch.object(config_flow.infrared, "async_get_emitters", _emitters):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "Office AC",
                CONF_EMITTER_ENTITY_ID: "infrared.office",
            },
        )
        assert result["step_id"] == "protocol"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PROTOCOL: "Airton"}
        )
        assert result["step_id"] == "temp_step"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_TEMP_STEP: "1_0"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Office AC"
    assert entry.data[CONF_EMITTER_ENTITY_ID] == "infrared.office"
    assert entry.unique_id == "infrared.office_AIRTON_-1"
