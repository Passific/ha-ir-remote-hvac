"""Tests for the IR Remote HVAC config flow."""
# pyright: reportTypedDictNotRequiredAccess=false

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.irremote_hvac import config_flow
from custom_components.irremote_hvac.const import (
    CONF_BRAND,
    CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID,
    CONF_DEBOUNCE_DELAY,
    CONF_EMITTER_ENTITY_ID,
    CONF_LEGACY_PROTOCOL,
    CONF_MODEL,
    CONF_TEMP_STEP,
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


@pytest.mark.parametrize(
    "sensors", [{}, {CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID: "sensor.room"}]
)
async def test_full_config_flow_creates_entry(hass: HomeAssistant, sensors) -> None:
    """Setup starts with the brand and allows sensors to be skipped or saved."""
    hass.states.async_set("infrared.living_room", "available")
    hass.states.async_set("sensor.room", "22")
    with patch.object(config_flow.infrared, "async_get_emitters", _emitters):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_BRAND: "Airspool"}
        )
        assert result["step_id"] == "model"
        assert {
            option["value"]: option["label"]
            for option in result["data_schema"].schema[CONF_MODEL].config["options"]
        } == {
            "AIRSPOOL protocol": "AIRSPOOL protocol (remote)",
            "Mini-split": "Mini-split",
        }

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_MODEL: "AIRSPOOL protocol"}
        )
        assert result["step_id"] == "device"
        assert (
            result["data_schema"]({CONF_EMITTER_ENTITY_ID: "infrared.living_room"})[
                "name"
            ]
            == "Airspool AIRSPOOL protocol"
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMITTER_ENTITY_ID: "infrared.living_room"},
        )
        assert result["step_id"] == "sensors"
        assert result["last_step"] is True
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=sensors
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Airspool AIRSPOOL protocol"
    assert result["data"][CONF_BRAND] == "Airspool"
    assert result["data"][CONF_MODEL] == "AIRSPOOL protocol"
    assert CONF_TEMP_STEP not in result["data"]
    assert result["data"][CONF_EMITTER_ENTITY_ID] == "infrared.living_room"
    assert (
        result["result"].unique_id == "infrared.living_room_Airspool_AIRSPOOL protocol"
    )
    assert result["result"].options == sensors

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, result["result"].entry_id)
    assert {entity.domain: entity.original_name for entity in entities} == {
        "climate": None,
        "switch": "Power",
    }

    configure = await hass.config_entries.options.async_init(result["result"].entry_id)
    if sensors:
        sensor_field = next(
            field
            for field in configure["data_schema"].schema
            if field == CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID
        )
        assert sensor_field.description["suggested_value"] == "sensor.room"
    configure = await hass.config_entries.options.async_configure(
        configure["flow_id"], user_input={CONF_DEBOUNCE_DELAY: 1.5}
    )
    assert configure["type"] is FlowResultType.CREATE_ENTRY
    assert configure["data"][CONF_DEBOUNCE_DELAY] == 1.5
    assert CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID not in configure["data"]
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(result["result"].entry_id)


def test_temp_step_conversion_handles_unexpected_values() -> None:
    """Unexpected temperature-step values should not crash conversion helpers."""
    assert config_flow._selector_value_to_temp_step("0_5") == 0.5
    assert config_flow._selector_value_to_temp_step("1_0") == 1.0
    assert config_flow._selector_value_to_temp_step("bad_value") is None
    assert config_flow._temp_step_to_selector_value(0.5) == "0_5"
    assert config_flow._temp_step_to_selector_value(1.0) == "1_0"
    assert config_flow._temp_step_to_selector_value("bad_value") == "1_0"


@pytest.mark.parametrize("existing_name", ["Airton AIRTON protocol", "Bedroom AC"])
async def test_shared_name_warns_but_allows_another_emitter(
    hass: HomeAssistant, existing_name: str
) -> None:
    """Warn about a prefilled or typed name without making it unique."""
    hass.states.async_set("infrared.office", "available")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=existing_name,
        unique_id="infrared.living_room_Airton_AIRTON protocol",
        data={
            CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            CONF_BRAND: "Airton",
            CONF_MODEL: "AIRTON protocol",
        },
    )
    entry.add_to_hass(hass)
    with patch.object(config_flow.infrared, "async_get_emitters", _emitters):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        for user_input in ({CONF_BRAND: "Airton"}, {CONF_MODEL: "AIRTON protocol"}):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input=user_input
            )

        device_input = {
            "name": existing_name,
            CONF_EMITTER_ENTITY_ID: "infrared.office",
        }
        if existing_name == "Bedroom AC":
            assert result["step_id"] == "device"
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input=device_input
            )
        assert result["step_id"] == "device_name_in_use"
        assert not result["errors"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=device_input
        )
        assert result["step_id"] == "sensors"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == entry.title
    assert result["result"].unique_id == "infrared.office_Airton_AIRTON protocol"
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(result["result"].entry_id)


async def test_duplicate_config_flow_aborts(hass: HomeAssistant) -> None:
    """The same emitter, protocol, and model cannot be configured twice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="infrared.living_room_Airton_AIRTON protocol",
        data={
            "name": "Bedroom AC",
            CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            CONF_BRAND: "Airton",
            CONF_MODEL: "AIRTON protocol",
            CONF_TEMP_STEP: DEFAULT_TEMP_STEP,
        },
    )
    entry.add_to_hass(hass)

    with patch.object(config_flow.infrared, "async_get_emitters", _emitters):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_BRAND: "Airton"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_MODEL: "AIRTON protocol"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "Duplicate AC",
                CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("emitter", ["infrared.living_room", "infrared.office"])
async def test_reconfigure_flow_updates_entry(
    hass: HomeAssistant, emitter: str
) -> None:
    """Reconfiguration can keep or change the emitter without replacing options."""
    hass.states.async_set("infrared.living_room", "available")
    hass.states.async_set("infrared.office", "available")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom AC",
        unique_id="infrared.living_room_Airton_AIRTON protocol",
        data={
            "name": "Bedroom AC",
            CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            CONF_BRAND: "Airton",
            CONF_MODEL: "AIRTON protocol",
            CONF_TEMP_STEP: DEFAULT_TEMP_STEP,
        },
        options={CONF_DEBOUNCE_DELAY: 1.5, CONF_TEMP_STEP: 1.0},
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
            result["flow_id"], user_input={CONF_BRAND: "Airton"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_MODEL: "AIRTON protocol"}
        )
        assert result["step_id"] == "device"
        assert (
            result["data_schema"]({CONF_EMITTER_ENTITY_ID: emitter})["name"]
            == "Bedroom AC"
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"name": "Renamed AC", CONF_EMITTER_ENTITY_ID: emitter},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Renamed AC"
    assert entry.data[CONF_EMITTER_ENTITY_ID] == emitter
    assert entry.unique_id == f"{emitter}_Airton_AIRTON protocol"
    assert entry.options == {CONF_DEBOUNCE_DELAY: 1.5, CONF_TEMP_STEP: 1.0}


async def test_reconfigure_repairs_unmapped_legacy_entry(hass: HomeAssistant) -> None:
    """A legacy model that cannot migrate can still be selected manually."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy AC",
        version=1,
        data={
            CONF_EMITTER_ENTITY_ID: "infrared.living_room",
            CONF_LEGACY_PROTOCOL: "AIRTON",
            CONF_MODEL: 42,
        },
    )
    entry.add_to_hass(hass)
    with patch.object(config_flow.infrared, "async_get_emitters", _emitters):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        for user_input in (
            {CONF_BRAND: "Airton"},
            {CONF_MODEL: "AIRTON protocol"},
            {CONF_EMITTER_ENTITY_ID: "infrared.living_room"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input=user_input
            )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert CONF_LEGACY_PROTOCOL not in entry.data
    assert entry.data[CONF_MODEL] == "AIRTON protocol"
