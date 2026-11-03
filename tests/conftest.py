"""Home Assistant fixtures for public lifecycle and configuration-flow tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pleos_client import PleosClient
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data.account import credential_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from support import CAR, OLD, stamp

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture
def pleos_entry(hass: HomeAssistant) -> MockConfigEntry:
    """An existing account with a stable namespace and nondefault options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kia",
        version=1,
        minor_version=1,
        unique_id=credential_id("kia", OLD["client_id"]),
        data=OLD | {"account_id": "kia_fixture_account"},
        options={"poll_minutes": 30, "stale_minutes": 90, "location": True},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def pleos_api(enable_custom_integrations: None) -> Generator[dict[str, AsyncMock]]:
    """Mock the library boundary; all Home Assistant setup remains real."""
    timestamp = stamp()

    def response(path: str) -> dict | None:
        if path.endswith("/batteries"):
            return {"timestamp": timestamp, "charge": {"stateOfCharge": 50, "charging": False}}
        if path.endswith("/driving"):
            return {"timestamp": timestamp, "odometer": {"value": 5177.5, "unit": "mile"}}
        if path.endswith("/locations"):
            return {"timestamp": timestamp, "location": {"latitude": 53.3, "longitude": -6.2, "type": 0}}
        return None

    with (
        patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])) as vehicles,
        patch.object(PleosClient, "get", AsyncMock(side_effect=response)) as request,
    ):
        yield {"vehicles": vehicles, "get": request}
