# Changelog

## 0.1.0

- Extract the Nominatim-compatible request/response client from Pleos Vehicle Data, retaining explicit provider selection, caller-owned sessions, API keys, attribution, timeout and redirect restrictions.
- Validate WGS84 coordinates before sending requests and expose sanitized error codes without request details.
- Keep polling, rounding, caching and Home Assistant entity behavior in the integration.
