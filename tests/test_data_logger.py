"""Unit tests for the pure adaptive-learning logic in data_logger.py.

These lock in the behaviour worked out by hand-modelling for b33 (median forecast
correction), b34 (drain excludes no-charge nights), and b35 (charge-rate history
seed), so future changes are checked automatically instead of re-modelled.
"""

from __future__ import annotations

import statistics

from conftest import make_day


# --------------------------------------------------------------------------- #
# _percentile                                                                  #
# --------------------------------------------------------------------------- #

def test_percentile_empty_and_single(dl):
    assert dl._percentile([], 50) == 0.0
    assert dl._percentile([7.0], 75) == 7.0


def test_percentile_matches_statistics_median(dl):
    for vals in ([1, 2, 3, 4, 5], [1, 2, 3, 4], [2.35, 1.41, 1.86, 2.03, 1.94]):
        assert dl._percentile(vals, 50) == statistics.median(vals)


def test_percentile_p75_interpolates(dl):
    # rank = 0.75 * (5-1) = 3.0 -> exactly the 4th ordered value
    assert dl._percentile([10, 20, 30, 40, 50], 75) == 40


# --------------------------------------------------------------------------- #
# compute_forecast_correction (b33: median, not mean)                          #
# --------------------------------------------------------------------------- #

def test_forecast_correction_neutral_below_threshold(dl):
    days = [make_day() for _ in range(dl._MIN_DAYS_FORECAST_CORRECTION - 1)]
    assert dl.compute_forecast_correction(days) == 1.0


def test_forecast_correction_is_median_not_mean(dl):
    # Six ordinary ratios of ~1.6 plus one wild outlier day.
    days = [make_day(solar_forecast_kwh=10.0, actual_solar_kwh=16.0) for _ in range(6)]
    days.append(make_day(solar_forecast_kwh=0.6, actual_solar_kwh=6.0))  # ratio 10
    factor = dl.compute_forecast_correction(days)
    # Median is immune to the outlier; a mean would be dragged far above 1.6.
    assert factor == 1.6


def test_forecast_correction_skips_near_zero_forecast(dl):
    days = [make_day(solar_forecast_kwh=10.0, actual_solar_kwh=15.0) for _ in range(7)]
    days += [make_day(solar_forecast_kwh=0.2, actual_solar_kwh=5.0) for _ in range(3)]
    # The <=0.5 forecast days are filtered out, so the factor stays at 1.5.
    assert dl.compute_forecast_correction(days) == 1.5


def test_forecast_correction_capped(dl):
    days = [make_day(solar_forecast_kwh=1.0, actual_solar_kwh=100.0) for _ in range(7)]
    assert dl.compute_forecast_correction(days) == 3.0  # capped at 3.0
    days = [make_day(solar_forecast_kwh=100.0, actual_solar_kwh=1.0) for _ in range(7)]
    assert dl.compute_forecast_correction(days) == 0.5  # floored at 0.5


# --------------------------------------------------------------------------- #
# _is_drain_night + compute_overnight_drain_adjustment (b34)                   #
# --------------------------------------------------------------------------- #

def test_is_drain_night_requires_real_charge(dl):
    # initial >= target => no-charge night => excluded
    assert dl._is_drain_night(make_day(initial_soc=80, target_soc=55)) is False
    assert dl._is_drain_night(make_day(initial_soc=40, target_soc=55)) is True


def test_is_drain_night_excludes_solar_contaminated_and_full_day(dl):
    assert dl._is_drain_night(make_day(morning_pv_power=250)) is False
    assert dl._is_drain_night(make_day(is_full_day=True)) is False
    assert dl._is_drain_night(make_day(overnight_drain_pct=None)) is False
    assert dl._is_drain_night(make_day(overnight_drain_pct=-3)) is False


def test_drain_adjustment_fallback_below_threshold(dl):
    days = [make_day() for _ in range(dl._MIN_DAYS_SOC_ADJUSTMENT - 1)]
    assert dl.compute_overnight_drain_adjustment(days) == dl._DEFAULT_DRAIN_ADJUSTMENT


def test_drain_adjustment_excludes_no_charge_nights(dl):
    # 6 real-charge nights at 12% drain; adding a huge no-charge night must not move it.
    real = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=12.0) for _ in range(6)]
    no_charge = make_day(initial_soc=90, target_soc=55, overnight_drain_pct=48.0)
    assert dl.compute_overnight_drain_adjustment(real + [no_charge]) == 10  # round(12/5)*5


def test_drain_adjustment_uses_p75_and_caps_at_20(dl):
    drains = [5, 8, 10, 12, 30, 30, 30]  # p75 well up the distribution
    days = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=d) for d in drains]
    assert dl.compute_overnight_drain_adjustment(days) == 20  # capped


def test_count_drain_days_matches_compute_predicate(dl):
    days = [
        make_day(initial_soc=40, target_soc=55),   # counts
        make_day(initial_soc=90, target_soc=55),   # no-charge, excluded
        make_day(morning_pv_power=300),            # solar, excluded
    ]
    assert dl.count_drain_adjustment_days(days) == 1


# --------------------------------------------------------------------------- #
# compute_soc_target_adjustment                                                #
# --------------------------------------------------------------------------- #

def test_soc_nudge_thresholds(dl):
    def band(evening):
        days = [make_day(evening_soc=evening, actual_solar_kwh=5.0) for _ in range(5)]
        return dl.compute_soc_target_adjustment(days, "summer_like")

    assert band(40) == -5   # ends too full -> charge less
    assert band(10) == 5    # ends too empty -> charge more
    assert band(27) == 0    # inside the 20-35 dead-band


def test_soc_nudge_below_threshold_is_zero(dl):
    days = [make_day(evening_soc=40, actual_solar_kwh=5.0) for _ in range(4)]
    assert dl.compute_soc_target_adjustment(days, "summer_like") == 0


# --------------------------------------------------------------------------- #
# last_known_charge_rate_kw (b35 seed)                                          #
# --------------------------------------------------------------------------- #

def test_last_known_charge_rate_picks_most_recent_non_null(dl):
    days = [
        make_day(date="2026-07-10", effective_charge_rate_kw=None),
        make_day(date="2026-07-06", effective_charge_rate_kw=1.13),
        make_day(date="2026-07-01", effective_charge_rate_kw=1.34),
    ]
    assert dl.last_known_charge_rate_kw(days) == 1.13


def test_last_known_charge_rate_none_when_all_null(dl):
    days = [make_day(effective_charge_rate_kw=None) for _ in range(3)]
    assert dl.last_known_charge_rate_kw(days) is None


# --------------------------------------------------------------------------- #
# compute_effective_charge_rate_kw                                              #
# --------------------------------------------------------------------------- #

def test_effective_charge_rate_none_below_three_days(dl):
    days = [make_day(initial_soc=20, target_soc=60, morning_soc=50, flux1_end="04:00")]
    assert dl.compute_effective_charge_rate_kw(days, 10.0, 15) is None


def test_effective_charge_rate_positive_with_enough_days(dl):
    days = [
        make_day(initial_soc=20, target_soc=60, morning_soc=52, flux1_end="04:00")
        for _ in range(4)
    ]
    rate = dl.compute_effective_charge_rate_kw(days, 10.0, 15)
    assert rate is not None and rate > 0


def test_import_plan_log_keeps_fields_read_back_by_pairing(DataLogger):
    """`away` and `effective_charge_rate_kw` must survive the log round trip.

    _pair_records reads both back (home/away split, last-known charge-rate
    fallback); if the import_plan log drops them, every day reads as home and
    the history seed is always None.
    """
    import asyncio

    written = []
    logger = object.__new__(DataLogger)

    async def _capture(record):
        written.append(record)

    logger._async_append = _capture
    plan = {"date": "2026-09-01", "target_soc": 50, "away": True, "effective_charge_rate_kw": 2.4, "payload": {}}
    asyncio.run(logger.async_log_import_plan(plan))
    rec = written[0]
    assert rec["away"] is True and rec["effective_charge_rate_kw"] == 2.4
    assert "payload" not in rec

    actuals = {"type": "day_actuals", "date": "2026-09-01", "evening_soc": 30.0, "actual_solar_kwh": 10.0}
    paired = logger._pair_records([rec, actuals])
    assert paired[0]["away"] is True
    assert DataLogger.last_known_charge_rate_kw(paired) == 2.4


# --------------------------------------------------------------------------- #
# Unavailable readings are None, never 0, and are left out of learning          #
# --------------------------------------------------------------------------- #

def test_forecast_correction_skips_days_without_actual_solar(dl):
    n = dl._MIN_DAYS_FORECAST_CORRECTION
    days = [make_day(actual_solar_kwh=30.0) for _ in range(n)] + [make_day(actual_solar_kwh=None)] * 5
    assert dl.compute_forecast_correction(days) == 1.5
    assert dl.count_forecast_correction_days(days) == n


def test_evening_nudge_ignores_days_without_evening_soc(dl):
    days = [make_day(evening_soc=None) for _ in range(10)]
    assert dl.compute_soc_target_adjustment(days, "summer_like") == 0
    assert dl.count_soc_adjustment_days(days, "summer_like") == 0


def test_charge_rate_counter_matches_the_calibration_filter(dl):
    # 02:00 -> 04:30 used to count only from >2.5h; 02:15 (0.25h) charges count in both.
    days = [make_day(target_soc=80, initial_soc=40, flux1_end=end) for end in ("02:15", "04:30", "06:30")]
    assert dl.count_charge_rate_calibration_days(days) == 2


def test_records_carry_the_manifest_version():
    import json
    import sys
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[1] / "custom_components" / "sunsynk_optimizer" / "manifest.json"
    version = json.loads(manifest.read_text())["version"]
    assert sys.modules["sunsynk_optimizer.const"].INTEGRATION_VERSION == version
    rec = sys.modules["sunsynk_optimizer.data_logger"]._record("day_kpis", date="2026-10-29")
    assert rec["version"] == version and rec["date"] == "2026-10-29"


# --------------------------------------------------------------------------- #
# Phase 63: drain measured to 05:00                                            #
# --------------------------------------------------------------------------- #

def test_drain_pct_to_0500_prefers_snapshot_and_scales_fallback():
    from conftest import _planning
    f = _planning.drain_pct_to_0500
    assert f(59, 52, 20.0, "04:00") == 7.0            # 05:00 snapshot wins
    assert f(59, None, 20.0, "04:00") == 10.0         # window 2h -> x(1/2)
    assert f(59, None, 20.0, "05:30") == 20.0         # window <= 1h keeps 06:00 figure
    assert f(59, None, 20.0, "") == 20.0              # unparseable end keeps 06:00 figure
    assert f(None, None, None, "04:00") is None       # missing stays None (rule 11)


def test_drain_adjustment_uses_0500_drain_when_present(dl):
    days = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=20.0,
                     overnight_drain_pct_0500=10.0) for _ in range(6)]
    assert dl.compute_overnight_drain_adjustment(days) == 10
    dl._DRAIN_USE_0500 = False
    assert dl.compute_overnight_drain_adjustment(days) == 20


# --------------------------------------------------------------------------- #
# Phase 53: exclude charge-shortfall nights from the drain sample              #
# --------------------------------------------------------------------------- #

def test_charge_shortfall_suspect_scales_with_gap_hours():
    from conftest import _planning
    f = _planning.charge_shortfall_suspect
    assert f(3.0, "05:00") is False        # full window: only the base allowance
    assert f(6.0, "05:00") is True
    assert f(12.0, "03:00") is False       # 2h gap allows 13 points
    assert f(14.0, "03:00") is True
    assert f(None, "03:00") is None        # missing stays None (rule 11)
    assert f(10.0, "") is None


def test_drain_adjustment_ignores_short_charge_nights(dl):
    good = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct_0500=5.0,
                     overnight_drain_pct=5.0) for _ in range(6)]
    short = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct_0500=20.0,
                      overnight_drain_pct=20.0, charge_short=True) for _ in range(4)]
    assert dl.compute_overnight_drain_adjustment(good + short) == 5
    dl._DRAIN_EXCLUDE_SHORT = False
    assert dl.compute_overnight_drain_adjustment(good + short) == 20
    dl._DRAIN_EXCLUDE_SHORT = True
    # too few clean nights: keep every night rather than fall back to the default
    assert dl.compute_overnight_drain_adjustment(good[:2] + short) == 20


# Phase 62: full-window nights carry no drain
def test_full_window_nights_excluded_from_drain(dl):
    normal = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct_0500=10.0,
                       overnight_drain_pct=10.0) for _ in range(6)]
    full = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct_0500=0.0,
                     overnight_drain_pct=0.0, full_window=True) for _ in range(6)]
    assert dl.compute_overnight_drain_adjustment(normal + full) == 10


# --------------------------------------------------------------------------- #
# Phase 61: measured charge rate                                                #
# --------------------------------------------------------------------------- #

def _measured_day(date, kw, temp=12.0, full_window=False):
    return {"date": date, "measured_charge_kw": kw, "battery_temp_c": temp, "full_window": full_window}


def test_compute_measured_charge_rate(dl):
    days = [_measured_day(f"2026-10-{d:02d}", 2.0 + d / 100) for d in range(1, 6)]
    kw, at_temp = dl.compute_measured_charge_rate_kw(days, 12.0)
    assert at_temp is True and 2.0 < kw < 2.1
    assert dl.compute_measured_charge_rate_kw(days[:2], 12.0) is None


def test_compute_measured_charge_rate_skips_full_window_and_none(dl):
    days = [_measured_day(f"2026-10-0{d}", 2.0) for d in range(1, 4)]
    days[0]["full_window"] = True
    days[1]["measured_charge_kw"] = None
    assert dl.compute_measured_charge_rate_kw(days, 12.0) is None


def test_meter_delta_rejects_reset(dl):
    from conftest import _data_logger
    _meter_delta = _data_logger._meter_delta
    assert _meter_delta({"load_kwh": 1}, {"load_kwh": 4}, "load_kwh") == 3
    assert _meter_delta({"load_kwh": 5}, {"load_kwh": 1}, "load_kwh") is None
    assert _meter_delta({}, {"load_kwh": 1}, "load_kwh") is None


# --------------------------------------------------------------------------- #
# Phase 87: plan fields read from paired days must survive _pair_records        #
# --------------------------------------------------------------------------- #

def _pair_one(DataLogger, **plan_fields):
    logger = object.__new__(DataLogger)
    plan = {"type": "import_plan", "date": "2026-11-01", "target_soc": 100, "soc": 30.0, **plan_fields}
    actuals = {"type": "day_actuals", "date": "2026-11-01", "evening_soc": 25.0, "actual_solar_kwh": 2.0}
    return logger._pair_records([plan, actuals])[0]


def test_paired_day_carries_cloud_cover_and_user_override(DataLogger):
    day = _pair_one(DataLogger, cloud_cover_pct=82.0, user_override={"target_soc": 60})
    assert day["cloud_cover_pct"] == 82.0
    assert day["user_override"] == {"target_soc": 60}


def test_paired_day_missing_fields_are_none(DataLogger):
    day = _pair_one(DataLogger)
    assert day["cloud_cover_pct"] is None and day["user_override"] is None


def test_override_night_is_not_a_drain_or_nudge_sample(DataLogger):
    day = make_day(user_override={"target_soc": 60}, target_soc=60, initial_soc=30.0)
    assert DataLogger._is_drain_night(day) is False


def test_cloud_bucket_reaches_quantile_samples(DataLogger, planning):
    day = _pair_one(DataLogger, raw_forecast_kwh=3.0, cloud_cover_pct=95.0, forecast_band="winter_like")
    cloud = planning.forecast_ratio_samples([day])[0]["cloud"]
    assert cloud is not None and cloud == planning.cloud_bucket(95.0)


# --------------------------------------------------------------------------- #
# Phase 91: log read performance                                               #
# --------------------------------------------------------------------------- #

import json
import os
from datetime import datetime, timedelta, timezone


def _disk_logger(DataLogger, tmp_path):
    dl = object.__new__(DataLogger)
    dl._data_dir = str(tmp_path)
    return dl


def _stamp(days_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_snapshot_index_matches_the_per_date_scan(_data_logger_module):
    mod = _data_logger_module
    records = [
        {"type": "meter_snapshot", "date": "2026-11-01", "time": "02:00", "soc": 40},
        {"type": "meter_snapshot", "date": "2026-11-01", "time": "02:00", "soc": 99},   # duplicate: first wins
        {"type": "meter_snapshot", "date": "2026-11-01", "time": "05:00", "soc": 80},
        {"type": "meter_snapshot", "date": "2026-11-02", "time": "05:00", "soc": 70},
        {"type": "meter_snapshot", "date": "2026-11-03"},                                # no time: ignored
        {"type": "day_actuals", "date": "2026-11-01", "time": "05:00"},
    ]
    index = mod.snapshots_by_date(records)
    for date in ("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"):
        assert index.get(date, {}) == mod.snapshots_for_date(records, date)
    assert index["2026-11-01"]["02:00"]["soc"] == 40


def test_file_is_parsed_once_until_it_changes(DataLogger, tmp_path, monkeypatch):
    dl = _disk_logger(DataLogger, tmp_path)
    dl._write_record({"type": "day_kpis", "date": "2026-11-01", "recorded_at": _stamp()})
    parses = []
    real = DataLogger._iter_jsonl
    monkeypatch.setattr(DataLogger, "_iter_jsonl", staticmethod(lambda path: parses.append(path) or real(path)))
    assert len(dl._read_recent(30)) == 1
    assert len(dl._read_recent(30)) == 1
    dl._write_record({"type": "day_kpis", "date": "2026-11-02", "recorded_at": _stamp()})   # kept in step, no re-read
    assert [r["date"] for r in dl._read_recent(30)] == ["2026-11-01", "2026-11-02"]
    assert len(parses) <= 1
    with open(dl._current_month_file(), "a", encoding="utf-8") as fh:                        # changed behind our back
        fh.write(json.dumps({"type": "day_kpis", "date": "2026-11-03", "recorded_at": _stamp()}) + "\n")
    assert len(dl._read_recent(30)) == 3


def test_read_returns_copies_and_honours_the_window(DataLogger, tmp_path):
    dl = _disk_logger(DataLogger, tmp_path)
    dl._write_record({"type": "note", "date": "a", "recorded_at": _stamp(0)})
    dl._write_record({"type": "note", "date": "b", "recorded_at": _stamp(0).replace("+00:00", "")})  # naive: skipped
    dl._write_record({"type": "note", "date": "c"})                                                    # no stamp: skipped
    first = dl._read_recent(30)
    assert [r["date"] for r in first] == ["a"]
    first[0]["date"] = "mutated"
    assert [r["date"] for r in dl._read_recent(30)] == ["a"]


def test_dedup_skips_same_type_date_time_and_sees_the_dates_own_month(DataLogger, tmp_path):
    dl = _disk_logger(DataLogger, tmp_path)
    snap = {"type": "meter_snapshot", "date": "2026-11-01", "time": "05:00", "recorded_at": _stamp()}
    dl._write_record(snap)
    dl._write_record(dict(snap))                                   # duplicate
    dl._write_record({**snap, "time": "16:00"})                    # other time: kept
    assert len(dl._read_recent(30)) == 2
    # A record dated in the previous month, already in that month's file, is not written again.
    prev = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
    date = prev.date().isoformat()
    old = {"type": "day_actuals", "date": date, "recorded_at": prev.isoformat()}
    (tmp_path / f"{prev:%Y-%m}.jsonl").write_text(json.dumps(old) + "\n")
    before = os.path.getsize(dl._current_month_file())
    dl._write_record({"type": "day_actuals", "date": date, "recorded_at": _stamp()})
    assert os.path.getsize(dl._current_month_file()) == before


def test_mpc_plan_goes_to_its_own_file_and_is_never_read_back(DataLogger, tmp_path):
    dl = _disk_logger(DataLogger, tmp_path)
    dl._write_record({"type": "mpc_plan", "date": "2026-11-01", "recorded_at": _stamp()})
    dl._write_record({"type": "day_kpis", "date": "2026-11-01", "recorded_at": _stamp()})
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{month}.jsonl", f"mpc-{month}.jsonl"]
    # An mpc_plan line written to the main log by an older version is skipped on read.
    with open(tmp_path / f"{month}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "mpc_plan", "date": "2026-11-01", "recorded_at": _stamp()}) + "\n")
    assert [r["type"] for r in dl._read_recent(30)] == ["day_kpis"]


def test_pruner_removes_old_side_files(DataLogger, tmp_path):
    dl = _disk_logger(DataLogger, tmp_path)
    for name in ("2020-01.jsonl", "mpc-2020-01.jsonl", "notes.jsonl"):
        (tmp_path / name).write_text("{}\n")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    (tmp_path / f"mpc-{month}.jsonl").write_text("{}\n")
    dl._prune_old_files(13)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"mpc-{month}.jsonl", "notes.jsonl"]
