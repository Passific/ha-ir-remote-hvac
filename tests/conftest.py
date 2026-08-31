"""Shared fixtures for IR Remote HVAC integration tests."""

from __future__ import annotations

import sys
from enum import IntFlag
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest


def _install_pyhvac_stub() -> None:
    """Provide the small pyhvac API surface required by config-flow tests."""
    if "pyhvac" in sys.modules:
        return

    pyhvac = cast(Any, ModuleType("pyhvac"))
    irhvac = cast(Any, ModuleType("pyhvac.irhvac"))

    class IRac:
        def __init__(self, _pin: int) -> None:
            _ = _pin
            self.next = SimpleNamespace(
                protocol=None,
                model=None,
                celsius=True,
                power=False,
                mode=None,
                degrees=None,
            )

        def resetTiming(self) -> None:
            return None

        def sendAc(self) -> None:
            return None

        def getTiming(self) -> list[int]:
            return [100, -100]

    class Mode(IntFlag):
        COOL = 1
        HEAT = 2
        AUTO = 4

    irhvac.IRac = IRac
    irhvac.AIRTON = object()
    irhvac.opmode_t_kCool = Mode.COOL
    irhvac.opmode_t_kHeat = Mode.HEAT
    irhvac.opmode_t_kAuto = Mode.AUTO
    pyhvac.irhvac = irhvac
    sys.modules["pyhvac"] = pyhvac
    sys.modules["pyhvac.irhvac"] = irhvac


_install_pyhvac_stub()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading this custom integration in every test."""
    _ = enable_custom_integrations
    yield
