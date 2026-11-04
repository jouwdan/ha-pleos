"""Archive repairs follow the public account lifecycle and stay account-scoped."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pleos_client import ApiError, AuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data.const import DOMAIN


@pytest.mark.parametrize("cleanup", ["disable", "remove"])
async def test_archive_repair_survives_reload_and_cleans_only_its_account(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock], cleanup: str,
) -> None:
    """Disabling even while offline or removing the account clears its warning."""
    hass.config_entries.async_update_entry(pleos_entry, options={**pleos_entry.options, "archive": True})
    issue_id = f"archive_write_failed_{pleos_entry.entry_id}"
    registry = ir.async_get(hass)
    with patch("custom_components.pleos_vehicle_data.archive.write_records", side_effect=OSError(28, "No space left")):
        assert await hass.config_entries.async_setup(pleos_entry.entry_id)
        await hass.async_block_till_done()
        assert pleos_entry.state is ConfigEntryState.LOADED
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert await hass.config_entries.async_reload(pleos_entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get_issue(DOMAIN, issue_id) is issue

    # An independent account's warning must survive this account's cleanup.
    ir.async_create_issue(
        hass, DOMAIN, "archive_write_failed_other_account", is_fixable=False,
        severity=ir.IssueSeverity.WARNING, translation_key="archive_write_failed",
        translation_placeholders={"directory": "/other-account/archive"},
    )
    if cleanup == "disable":
        hass.config_entries.async_update_entry(pleos_entry, options={**pleos_entry.options, "archive": False})
        pleos_api["vehicles"].side_effect = ApiError(0, "connection_failed")
        assert not await hass.config_entries.async_reload(pleos_entry.entry_id)
    else:
        assert await hass.config_entries.async_remove(pleos_entry.entry_id) == {"require_restart": False}
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert registry.async_get_issue(DOMAIN, "archive_write_failed_other_account") is not None


@pytest.mark.parametrize("failure", [ApiError(0, "connection_failed"), ApiError(429, "rate_limited"), AuthError()])
async def test_cloud_failures_do_not_create_archive_repairs(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock], failure: Exception,
) -> None:
    """Only a real archive write failure asks the user to fix local storage."""
    hass.config_entries.async_update_entry(pleos_entry, options={**pleos_entry.options, "archive": True})
    pleos_api["vehicles"].side_effect = failure
    assert not await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"archive_write_failed_{pleos_entry.entry_id}") is None
