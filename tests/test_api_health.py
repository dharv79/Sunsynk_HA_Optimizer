"""Phase 101: cloud API counters, failure classes, history, three-day alert, weekly totals."""


class _Status(Exception):
    def __init__(self, status):
        self.status = status


def test_classify_failure(planning):
    c = planning.classify_failure
    assert c(TimeoutError()) == "timeout"
    assert c(_Status(401)) == "401"
    assert c(_Status(503)) == "5xx"
    assert c(_Status(404)) == "other"
    assert c(ValueError("x")) == "other"


def test_take_summarises_then_resets(planning):
    s = planning.ApiStats()
    for kind, lat in ((None, 0.5), (None, 1.5), ("timeout", 30.0), ("5xx", 2.0)):
        s.record(kind, lat)
    s.note_relogin()
    s.note_push_retry()
    out = s.take("2026-11-13")
    assert out["calls"] == 4 and out["failures_total"] == 2
    assert out["failures"] == {"timeout": 1, "401": 0, "5xx": 1, "other": 0}
    assert out["success_rate_pct"] == 50.0
    assert out["median_latency_s"] == 1.75 and out["worst_latency_s"] == 30.0
    assert out["relogins"] == 1 and out["push_retries"] == 1
    again = s.take("2026-11-14")
    assert again["calls"] == 0 and again["success_rate_pct"] is None and again["median_latency_s"] is None


def test_unknown_kind_counts_as_other(planning):
    s = planning.ApiStats()
    s.record("weird", 1.0)
    assert s.take("d")["failures"]["other"] == 1


def _day(date, calls, failed):
    return {"date": date, "calls": calls, "failures_total": failed}


def test_history_replaces_same_date_and_caps(planning):
    h = []
    for n in range(1, 21):
        h = planning.append_api_day(h, _day(f"2026-10-{n:02d}", 10, 0))
    assert len(h) == planning.API_HISTORY_DAYS and h[-1]["date"] == "2026-10-20"
    h = planning.append_api_day(h, _day("2026-10-20", 10, 5))
    assert len(h) == planning.API_HISTORY_DAYS and h[-1]["failures_total"] == 5


def test_alert_needs_three_bad_days_in_a_row(planning):
    bad = [_day("a", 20, 5), _day("b", 20, 4), _day("c", 20, 3)]
    assert planning.api_unreliable_streak(bad) == 3 and planning.api_alert_due(bad)
    assert not planning.api_alert_due([_day("a", 20, 0), *bad[1:]])
    assert not planning.api_alert_due(bad[:2])


def test_quiet_or_unknown_day_breaks_the_streak(planning):
    few = [_day("a", 20, 5), _day("b", 3, 3), _day("c", 20, 5)]
    assert planning.api_unreliable_streak(few) == 1
    assert planning.api_unreliable_streak([{"date": "a"}]) == 0
    assert planning.api_unreliable_streak(None) == 0


def test_threshold_is_exclusive(planning):
    assert planning.api_unreliable_streak([_day("a", 20, 2)]) == 0  # exactly 10%


def test_week_totals(planning):
    days = [
        {**_day("a", 100, 2), "worst_latency_s": 3.0, "relogins": 1, "push_retries": 0},
        {**_day("b", 100, 8), "worst_latency_s": 12.0, "relogins": 0, "push_retries": 2},
    ]
    w = planning.api_week(days)
    assert w["calls"] == 200 and w["failures_total"] == 10 and w["success_rate_pct"] == 95.0
    assert w["worst_latency_s"] == 12.0 and w["relogins"] == 1 and w["push_retries"] == 2
    assert planning.api_week([])["success_rate_pct"] is None


# ---- Phase 121: per-route failures, identifier-free detail, read-back stop -----------------------------


class _Err(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        if status is not None:
            self.status = status


def test_api_route_strips_ids_and_query(planning):
    r = planning.api_route
    assert r("https://api.sunsynk.net/api/v1/plant/123456/income") == "/api/v1/plant/{id}/income"
    assert r("https://api.sunsynk.net/api/v1/event/2305A1234/realtime?lan=en") == "/api/v1/event/{id}/realtime"
    assert r("https://api.sunsynk.net/api/v1/workdata/dynamic?sn=2305A1234&page=1") == "/api/v1/workdata/dynamic"
    assert r("https://api.sunsynk.net/api/v1/inverters/count") == "/api/v1/inverters/count"


def test_failure_detail_masks_identifiers(planning):
    d = planning.failure_detail(_Err("{'code': 102, 'msg': 'plant 1234567 for a@b.com', 'url': 'https://x/1'}", 404))
    assert d.startswith("_Err 404: ")
    assert "1234567" not in d and "a@b.com" not in d and "https://" not in d and "102" in d
    assert len(planning.failure_detail(_Err("x" * 500))) == planning.API_DETAIL_CHARS
    assert planning.failure_detail(TimeoutError()) == "TimeoutError"


def test_failures_counted_per_route_first_detail_kept(planning):
    s = planning.ApiStats()
    s.record("other", 0.1, route="GET /a", detail="first")
    s.record("other", 0.1, route="GET /a", detail="second")
    s.record("timeout", 30.0, route="GET /b", detail="slow")
    s.record(None, 0.1, route="GET /a")
    s.record("other", 0.1)  # no route: kind counted, route map untouched
    out = s.take("d")
    assert out["failures_by_route"] == {"GET /a": 2, "GET /b": 1}
    assert out["failure_detail"] == {"GET /a": "first", "GET /b": "slow"}
    assert out["failures"]["other"] == 3
    assert s.take("e")["failures_by_route"] == {}


def test_read_back_stops_after_limit(planning):
    assert not planning.read_back_should_stop(0)
    assert not planning.read_back_should_stop(planning.READ_BACK_FAIL_LIMIT - 1)
    assert planning.read_back_should_stop(planning.READ_BACK_FAIL_LIMIT)
