"""Tests for pyhvac device selection and legacy-entry migration."""

from __future__ import annotations

import pytest

from custom_components.irremote_hvac.const import (
    CONF_BRAND,
    CONF_MODEL,
    CONF_LEGACY_PROTOCOL,
)
from custom_components.irremote_hvac.pyhvac_helpers import migrate_legacy_device


def test_migrate_legacy_protocol_to_registered_device() -> None:
    """A legacy protocol with a unique generic device maps to brand and model."""
    migrated = migrate_legacy_device({CONF_LEGACY_PROTOCOL: "AIRTON", CONF_MODEL: -1})

    assert migrated[CONF_BRAND] == "Airton"
    assert migrated[CONF_MODEL] == "AIRTON protocol"
    assert CONF_LEGACY_PROTOCOL not in migrated


def test_migrate_rejects_legacy_model_variant() -> None:
    """Numeric IRremoteESP8266 model variants are not silently reinterpreted."""
    with pytest.raises(ValueError, match="cannot be mapped automatically"):
        migrate_legacy_device({CONF_LEGACY_PROTOCOL: "AIRTON", CONF_MODEL: 2})


def test_migrate_rejects_ambiguous_legacy_protocol() -> None:
    """Legacy protocol entries with multiple 0.2 devices require user selection."""
    with pytest.raises(ValueError, match="not have one unambiguous"):
        migrate_legacy_device({CONF_LEGACY_PROTOCOL: "ARGO", CONF_MODEL: -1})


def test_migrate_leaves_new_device_selection_unchanged() -> None:
    data = {CONF_BRAND: "Airton", CONF_MODEL: "AIRTON protocol"}

    assert migrate_legacy_device(data) is data
