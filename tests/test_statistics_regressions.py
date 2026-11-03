"""Regression checks for historical corrections and retained hourly totals."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.pleos_vehicle_data.statistics import TripStatistics, trip_share
from support import stamp
from test_statistics import drive


async def test_partial_reimport_keeps_earlier_distance_in_the_cumulative_sum(hass: HomeAssistant) -> None:
    """Correcting a later trip must include earlier hours without reimporting them."""
    statistics = TripStatistics(hass, "fixture-account")
    await statistics.load()
    hass.config.components.add("recorder")
    early, late = stamp(600), stamp(120)
    with patch("custom_components.pleos_vehicle_data.statistics.async_add_external_statistics") as write:
        statistics.add("fixture-car", "driving/data", drive(early, 0, 10), "Fixture EV")
        statistics.add("fixture-car", "driving/data", drive(late, 10, 15), "Fixture EV")
        await statistics.flush(True)
        write.reset_mock()
        statistics.add("fixture-car", "driving/data", drive(late, 10, 17), "Fixture EV")
        await statistics.flush(True)
        distances = [call.args[2] for call in write.call_args_list if call.args[1]["statistic_id"].endswith("_distance")]
    assert len(distances) == 1
    assert len(distances[0]) == 1
    assert distances[0][0]["sum"] == 17


async def test_pruning_trip_deduplication_keeps_historical_totals(hass: HomeAssistant) -> None:
    """Trips outside the API query horizon expire without deleting their totals."""
    statistics = TripStatistics(hass, "fixture-account")
    await statistics.load()
    statistics.add("fixture-car", "driving/data", drive(stamp(10 * 24 * 60), 0, 10), "Fixture EV")
    await statistics.flush(True)
    restored = TripStatistics(hass, "fixture-account")
    await restored.load()
    assert restored.ledger["trips"]["fixture-car"] == {}
    assert sum(hour["km"] for hour in restored.ledger["hours"]["fixture-car"].values()) == 10
    assert trip_share("driving/data", {"triggerStartTime": "invalid", "odometer": {"value": [0, 10]}}) is None
