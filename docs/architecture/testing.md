# Testing

How to run the unit tests and iterate against a real Home Assistant.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Tests

The pure adaptive-learning logic in `data_logger.py` and the pure planning maths in `planning.py` have unit tests under `tests/` that run **without** a Home Assistant install — `tests/conftest.py` stubs `homeassistant.core` and loads `data_logger.py` directly by file path (bypassing the package `__init__.py` and its coordinator/optimizer import chain). Run them with:

```bash
pip install pytest && python3 -m pytest
```

`tests/conftest.py` exposes a `planning` fixture (the `planning` module), a `dl` fixture (a bare `DataLogger` instance for calling its pure methods) and a `make_day(**overrides)` helper for building paired-day dicts. When you change any `compute_*` / `count_*` / `_is_drain_night` / `_percentile` logic, or anything in `planning.py`, add or update a test that pins the new behaviour. The `.github/workflows/tests.yml` workflow runs `py_compile` + `pytest` on every push to `main` and every PR. Testing code that imports HA more deeply (optimizer, coordinator) requires mocking `hass`; prefer extracting pure helpers so they can be tested directly.

### Home Assistant iteration

Beyond the unit tests, development requires a real or dev Home Assistant instance:

1. Copy `custom_components/sunsynk_optimizer/` into the HA instance's `custom_components/` directory.
2. Restart Home Assistant or reload the integration via **Settings → Devices & Services**.
3. Check **Settings → System → Logs** for errors from the `custom_components.sunsynk_optimizer` logger.

When iterating on logic, the **Test plan (dry run)** button is the primary way to test without waiting for scheduled events: it recomputes the full import plan and posts the complete plan JSON to the HA app notification, with no inverter push, no data-log write, and no state change. The other manual buttons are **Reset baseline** (restore configured Flux windows) and **Update dashboard** (regenerate the Lovelace YAML).

To validate Python syntax without a running HA instance:
```bash
python3 -m py_compile custom_components/sunsynk_optimizer/*.py
```

## Test files

| File | Covers |
|---|---|
| `tests/conftest.py` | HA stubs, direct module loading, `dl`/`planning`/`flux_helpers`/`safe_id` fixtures, `make_day` |
| `tests/test_data_logger.py` | `compute_*` / `count_*` adaptive-learning maths, `_is_drain_night`, `_percentile` |
| `tests/test_away_mode.py` | Home/away calibration split |
| `tests/test_day_actuals.py` | day_actuals / peak_window_usage logging and dedup |
| `tests/test_daily_cost.py` | daily_cost pairing, net cost, fill-only merge, month-boundary catch-up |
| `tests/test_planning.py` | `planning.py` target-SOC tree, bridge/ramp, charge rate, Flux 1 window, scoring |
| `tests/test_flux_helpers_cost.py` | `peak_import_price_pence_per_kwh` cost-trigger helper |
| `tests/test_dashboard.py` | Dashboard YAML structure |
| `tests/test_security.py` | `_safe_id` sanitising of entity IDs in generated YAML (b37 hardening) |
