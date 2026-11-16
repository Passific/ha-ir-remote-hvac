"""Shared fixtures for IR Remote HVAC integration tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading this custom integration in every test."""
    _ = enable_custom_integrations
    yield
