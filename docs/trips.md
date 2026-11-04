# Trips, statistics and archives

[Back to setup guide](../README.md)

## Trip readings

Trip queries run every **60 minutes**, separately from status polling, and normally cover **yesterday and today in UTC**. After a gap, they reach back to the last successful fetch, up to **7 days** including today. First setup also checks this wider window. If Pleos refuses the range, the integration retries with two days. The diagnostic sensor's `query` attribute shows the dates requested.

Trip sensors keep their results between queries. New vehicles are queried immediately; failed requests retry on the next status poll. `no_data` results wait for the normal trip interval. Pleos delivers trips with a delay, so polling more often may return the same data.

Full returned trips remain in the diagnostic sensor's `data` attribute; enable that diagnostic entity if it is disabled. Individual trip sensors show the latest trip's final sample when valid, with trip start, duration and sample index.

Missing final samples (`null`, the text `"null"`, or `"invalid"`) do not produce a reading or a rejected-value warning. Earlier samples are not substituted for a missing final state. The speed-limit-assist value `warning` is a setting, not an active vehicle fault; `none` is also a valid setting.

Numeric trip sensors have `trip_min`, `trip_max`, `trip_mean`, `valid_samples` and `total_samples` attributes. Summaries use valid samples in the final sample's unit; the mean is per sample, not time-weighted. Home Assistant can convert the sensor state to your preferred unit without converting these attributes. Each endpoint reports its own latest trip, so its readings may refer to a different drive.

**Trip distance (last trip)** is the difference between the first and last valid odometer samples of the latest driving trip. Its `odometer_start` and `odometer_end` attributes are in kilometres. An invalid final sample does not become a reading. Existing **(last trip)** entities for undocumented fields continue updating. New undocumented fields stay in the diagnostic payload and trip summaries without creating new entities. Individual samples have no documented timestamps.

## Trip event entity

Each car has a **Trip** event entity (`event.<car>_trip`). It fires once for every trip the API returns, oldest first, also when several trips arrive in one query and for trips found at setup or by the catch-up after downtime. Each firing is an ordinary recorded state change, so Home Assistant's history and its InfluxDB integration keep every trip, and automations can trigger on it.

- `event_type`: `powertrains_data`, `speed`, `driving_data` or `status_data`, the trip endpoint the trip came from. A drive usually appears once per endpoint.
- Attributes: `trip_start` (the drive's start), `duration_s`, `distance_km` (driving trips), and each trip field named like its **(last trip)** sensor without the endpoint prefix, for example `location_speed`, with `_min`, `_max` and `_mean` for numbers. Units are those of the matching sensor. Sample arrays are not included.
- The event's own time is when Home Assistant received the trip, not when it was driven; use `trip_start` for the drive time.
- Fired trips are remembered across restarts for nine days. After a restart, Home Assistant restores the last event with its original time; that is not a new trip.

## Trip statistics

Trips reach Home Assistant up to a day after the drive, so sensor states and Trip events carry the time they arrived. For correct daily and hourly figures, the integration also imports **long-term statistics at the hour each trip started**:

| Statistic ID | Content | Type |
| --- | --- | --- |
| `pleos_vehicle_data:<vehicle>_distance` | Distance driven, from driving trips' odometer samples | Sum, km |
| `pleos_vehicle_data:<vehicle>_driving_time` | Driving time, from driving trips' duration | Sum, min |
| `pleos_vehicle_data:<vehicle>_speed` | Speed while driving, from speed trips' samples | Hourly mean, minimum and maximum, km/h |

`<vehicle>` is this integration's vehicle ID (not the VIN). Show them with a **Statistics graph** card, for example kilometres per day:

```yaml
type: statistics-graph
title: Distance per day
entities:
  - pleos_vehicle_data:kia_0123456789abcdef_distance
stat_types:
  - change
period: day
chart_type: bar
```

- Pleos gives no time per sample, so a whole trip counts in the hour it started.
- A trip that arrives late, or again with more samples, corrects its hour; all later sums are imported again, so totals stay right.
- Every returned trip counts, including trips found at setup or by the catch-up after downtime. Earlier drives are not imported.
- Statistics are kept in Home Assistant's database and shown in its statistics graphs and **Developer tools → Statistics**. The InfluxDB integration does not export them.
- Removing the integration also removes these statistics.

## Trip events

Enable **Trip events** in the [options](configuration.md#options) to fire a `pleos_vehicle_data_trip` event for each new trip. It is off by default. Events contain every returned sample for use in automations or exports, for example through Node-RED or AppDaemon:

| Key | Content |
| --- | --- |
| `vehicle_id` | Stable vehicle ID used by this integration (not the VIN) |
| `device_id` | Home Assistant device, once the vehicle's device exists |
| `brand` | `hyundai`, `kia` or `genesis` |
| `endpoint` | `powertrains/data`, `speed`, `driving/data` or `status/data` |
| `trip_start`, `trip_length_ms` | Trip start time and duration as returned |
| `trip` | The complete trip object, including all sample arrays |

Trips are identified by start time, vehicle and endpoint. Each endpoint sends its own event, including for late arrivals within the query window. Sent trips are remembered across restarts for nine days. Events fire after a complete poll; first setup sends events for trips already in the window.

The recorder stores events, including their sample arrays. To keep them out of the database, add this to `configuration.yaml`:

```yaml
recorder:
  exclude:
    event_types:
      - pleos_vehicle_data_trip
```

Sensor history contains the latest trip at each poll, so several trips arriving together produce one latest-trip state. To record every trip, use the [**Trip** event entity](#trip-event-entity); for every sample, use trip events or the archive.

## Response archive

The optional archive keeps raw snapshot payloads and trip objects, including fields this integration does not understand yet. The archive is off by default and can be enabled under **Storage** in the [options](configuration.md#options).

- Files: `<config>/pleos_vehicle_data/archive/<account>/<YYYY-MM>/<YYYY-MM-DD>.jsonl.gz`, one gzip-compressed JSON object per line, by UTC day of the request. Existing entries keep their manufacturer folder; new entries get a separate account folder. The **Archive** sensor shows the exact path.
- A snapshot or the vehicle list is written when its content changes; an unchanged response is not written again. Each trip is written once, and again only if Pleos later returns different content for it. Restarts do not cause duplicates.
- Each record has `schema`, `kind` (`vehicles`, `snapshot`, `trip` or `trip_response`), `fetched_at`, `endpoint`, `vehicle_id`, the trip `query` and the API's `api_metadata`, plus the response in `data` or the trip in `trip`.
- Files remain until you delete them, including after removing the integration. Read them with `zcat`, `zgrep` or Python's `gzip.open`.
- The **Archive** diagnostic sensor on the account's API device shows `ok` or `error`, the folder, the last write and the last error.

The archive contains the VIN in the vehicle list and the car's locations. Treat the folder like the rest of your Home Assistant configuration.
