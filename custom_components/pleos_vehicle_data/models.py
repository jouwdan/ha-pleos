"""Typed configuration entry shared by the integration entry points."""

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import PleosCoordinator

type PleosConfigEntry = ConfigEntry[PleosCoordinator]
