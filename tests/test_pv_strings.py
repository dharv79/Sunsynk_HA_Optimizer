"""Phase 49: PV string health."""


def test_share(planning):
    assert planning.string_share(3.0, 1.0) == 0.75
    assert planning.string_share(None, 1.0) is None
    assert planning.string_share(0.4, 0.3) is None  # too thin
    assert planning.string_share(0.0, 2.0) == 0.0   # a dead string is a real 0 share


def test_needs_history(planning):
    assert planning.string_ratio_anomaly([0.5] * 6, 0.2) is None
    assert planning.string_ratio_anomaly([0.5] * 7, None) is None


def test_normal_and_fault(planning):
    hist = [0.50, 0.51, 0.49, 0.50, 0.52, 0.48, 0.50, 0.51]
    ok = planning.string_ratio_anomaly(hist, 0.49)
    assert ok["anomalous"] is False and ok["weaker"] is None
    bad = planning.string_ratio_anomaly(hist, 0.30)
    assert bad["anomalous"] and bad["weaker"] == "mppt0"
    assert planning.string_ratio_anomaly(hist, 0.72)["weaker"] == "mppt1"


def test_small_move_below_floor_ignored(planning):
    hist = [0.500] * 10
    assert planning.string_ratio_anomaly(hist, 0.55)["anomalous"] is False  # < 8 points


def test_noisy_history_widens_threshold(planning):
    hist = [0.30, 0.55, 0.40, 0.62, 0.35, 0.58, 0.45, 0.50]
    assert planning.string_ratio_anomaly(hist, 0.36)["anomalous"] is False


def test_streak(planning):
    bad = {"weaker": "mppt0"}
    s = planning.string_streak(0, None, bad)
    s = planning.string_streak(*s, bad)
    assert s == (2, "mppt0")
    assert planning.string_streak(*s, None) == (2, "mppt0")  # unknown day holds
    assert planning.string_streak(*s, {"weaker": "mppt1"}) == (1, "mppt1")
    assert planning.string_streak(*s, {"weaker": None}) == (0, None)
