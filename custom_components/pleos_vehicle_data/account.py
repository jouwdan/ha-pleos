"""Stable account namespaces, independent of replaceable API credentials."""

import hashlib
from typing import cast

from .models import PleosConfigEntry


def account_id(entry: PleosConfigEntry) -> str:
    # Existing installations keep their device IDs, statistics and archive directory.
    return cast(str, entry.data.get("account_id", entry.data["brand"]))


def credential_id(brand: str, client_id: str) -> str:
    return brand + "_" + hashlib.sha256(client_id.strip().encode()).hexdigest()
