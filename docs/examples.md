# Automation examples

[Back to setup guide](../README.md)

In **Settings → Automations & scenes**, create an empty automation, open **⋮ → Edit in YAML**, and paste one example. Replace every `my_car` entity ID with the ID shown on your vehicle's device page. The current English names are **Battery level** (`sensor.my_car_battery_level`) and **Trip** (`event.my_car_trip`); existing or renamed entities can have different IDs.

These examples create Home Assistant notifications without requiring a phone notification service. They do not control the vehicle. Check the automation's **Traces** after a matching report arrives.

## Notify when a fresh battery report reaches a target

This creates a notification when the battery is reported at 80% or higher. Change `80` to your target. It runs when the whole trigger changes from false to true, including when a fresh report replaces a stale one. It does not repeat while the trigger stays true.

Unknown, unavailable, retained and stale readings are rejected. The timestamp check also rejects reports older than an hour, even if your configured stale threshold is longer or no new poll has updated `data_stale`. Reduce `3600` for a stricter age limit. A missing timestamp never counts as fresh.

```yaml
alias: Pleos - battery target reported
triggers:
  - trigger: template
    value_template: >-
      {% set entity = 'sensor.my_car_battery_level' %}
      {% set reported = as_timestamp(state_attr(entity, 'reported_at'), none) %}
      {{ has_value(entity)
         and states(entity) | float(-1) >= 80
         and state_attr(entity, 'data_stale') is false
         and state_attr(entity, 'last_known') is not true
         and reported is not none
         and 0 <= as_timestamp(now()) - reported <= 3600 }}
actions:
  - action: persistent_notification.create
    data:
      notification_id: pleos_my_car_battery_target
      title: Vehicle battery target reported
      message: >-
        Battery level: {{ states('sensor.my_car_battery_level') }}%.
        Vehicle report: {{ state_attr('sensor.my_car_battery_level', 'reported_at') }}.
mode: single
```

Snapshots are requested every 15 minutes, but the car uploads on its own schedule. This notification can arrive after charging has stopped; it does not prove that the car is charging now or that a charger should be switched off. Manual refresh cannot make the car upload a new report.

## Notify when a completed driving trip arrives

The **Trip** event entity works without enabling the optional raw **Trip events** setting. Home Assistant's built-in `event.received` trigger filters for `driving_data`, so the other three endpoints for the same drive do not produce three extra notifications. It ignores unavailable states and restoration of an existing event on startup.

```yaml
alias: Pleos - completed driving trip received
triggers:
  - trigger: event.received
    target:
      entity_id: event.my_car_trip
    options:
      event_type:
        - driving_data
conditions:
  - condition: template
    value_template: >-
      {% set received = as_timestamp(trigger.to_state.state, none) %}
      {% set started = as_timestamp(trigger.to_state.attributes.get('trip_start'), none) %}
      {{ received is not none and started is not none
         and 0 <= as_timestamp(now()) - received <= 60
         and 0 <= as_timestamp(now()) - started <= 172800 }}
actions:
  - action: persistent_notification.create
    data:
      notification_id: pleos_my_car_latest_trip
      title: Completed driving trip received
      message: >-
        Drive started: {{ trigger.to_state.attributes.trip_start }}.
        {% set distance = trigger.to_state.attributes.get('distance_km') %}
        {% if is_number(distance) %}
          Distance: {{ distance | float | round(1) }} km.
        {% else %}
          Distance was not reported.
        {% endif %}
mode: queued
max: 20
```

The event timestamp is the **arrival time**; `trip_start` is the **drive time**. Trips are checked every 60 minutes and can arrive hours later or the next day. The guard accepts arrivals from the last minute for drives started within 48 hours (`172800` seconds), and skips older catch-up trips after downtime. Adjust that window if you want older trips. It deliberately does not apply current-snapshot freshness rules to historical drives.

Several trips can arrive in one poll. Queued mode processes them separately; the fixed notification ID keeps only the latest message. A transition directly from unavailable to a restored event is ignored by the built-in trigger, so this is a convenience notification, not a complete trip ledger. Use the [trip statistics](trips.md#trip-statistics) for recorded distance and driving time, including delayed reports.
