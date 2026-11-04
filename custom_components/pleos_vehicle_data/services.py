"""Services for Pleos Vehicle Data."""

from typing import cast

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN, SERVICE_REFRESH
from .models import PleosConfigEntry

SERVICE_REFRESH_SCHEMA = probatio.Schema({probatio.Required(ATTR_CONFIG_ENTRY_ID): str})


async def _async_refresh(call: ServiceCall) -> None:
    hass = call.hass
    entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": call.data[ATTR_CONFIG_ENTRY_ID]},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    await cast(PleosConfigEntry, entry).runtime_data.async_refresh_for_service()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _async_refresh, schema=SERVICE_REFRESH_SCHEMA
    )
