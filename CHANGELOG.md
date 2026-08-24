# Changelog

## Unreleased

### Fixed

- `GET /api/v1/{cvrNumber}` returnerer nu **alle** produktionsenheder. Tidligere
  blev Elastic Search-forespørgslen afkortet til 1000 hits, så virksomheder med
  flere p-enheder mistede resten uden nogen indikation i svaret — Københavns
  Kommune (CVR 64942212) returnerede 1000 af sine 2645 p-enheder. P-numrene
  hentes nu i batches, og `p_units` er sorteret stigende efter `p_number`.
