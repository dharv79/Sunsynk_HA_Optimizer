"""Static guard: every self.<name> used in SunsynkOptimizer is defined on it.

Catches an edit that drops a method's def line (the body then silently joins
the previous method), which py_compile can't see.
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "sunsynk_optimizer"
OPTIMIZER_FILES = [PKG / "optimizer.py", *sorted(PKG.glob("opt_*.py"))]


class _Optimizer:
    """The optimizer's source is split over optimizer.py and opt_*.py mixins (phase 99): read them as one."""

    @staticmethod
    def read_text() -> str:
        return "\n".join(f.read_text() for f in OPTIMIZER_FILES)

    parent = PKG


OPTIMIZER = _Optimizer()


def _parse_optimizer() -> ast.Module:
    return ast.parse(OPTIMIZER.read_text())


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    """The class; SunsynkOptimizer is the merge of itself and its mixins' bodies."""
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if name == "SunsynkOptimizer":
        merged = ast.ClassDef(name=name, bases=[], keywords=[], body=[], decorator_list=[])
        for c in classes:
            if c.name == name or c.name.endswith("Mixin"):
                merged.body += c.body
        return merged
    return next(n for n in classes if n.name == name)


def test_self_attributes_are_defined():
    cls = _class(ast.parse(OPTIMIZER.read_text()), "SunsynkOptimizer")
    defined = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    used: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            if isinstance(node.ctx, ast.Store):
                defined.add(node.attr)
            else:
                used.add(node.attr)
    assert used - defined == set()


def _awaited_calls(fn: ast.AsyncFunctionDef) -> list[str]:
    """self.<method> names referenced in order (direct calls, lambdas and passed callables)."""
    nodes = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
    ]
    return [n.attr for n in sorted(nodes, key=lambda n: (n.lineno, n.col_offset))]


def test_startup_plan_is_followed_by_peak_cover():
    # The startup plan resets Flux 1 to the overnight window, so a restart
    # between 12:00 and 16:00 would wipe a peak cover top-up unless peak
    # cover re-checks straight after it.
    cls = _class(ast.parse(OPTIMIZER.read_text()), "SunsynkOptimizer")
    fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_async_initial_refresh")
    calls = _awaited_calls(fn)
    assert "async_run_import_plan" in calls and "_async_peak_cover_after_startup" in calls
    assert calls.index("_async_peak_cover_after_startup") > calls.index("async_run_import_plan")
    # The wrapper runs peak cover and retries it while SOC is unreadable or an outage is flagged.
    wrapper = next(
        n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_async_peak_cover_after_startup"
    )
    assert "async_run_peak_cover" in _awaited_calls(wrapper)


_MUTATORS = {"append", "extend", "insert", "pop", "popitem", "remove", "clear", "update", "setdefault", "sort"}


def test_nothing_mutates_the_cached_config():
    """Phase 67: self.cfg is a shared read-only copy; assigning into it or calling a mutator on it must fail CI."""
    cls = _class(ast.parse(OPTIMIZER.read_text()), "SunsynkOptimizer")

    def is_cfg(node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "cfg" and isinstance(node.value, ast.Name) and node.value.id == "self"

    bad = []
    for node in ast.walk(cls):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = node.targets if hasattr(node, "targets") else [node.target]
            bad += [node.lineno for t in targets if isinstance(t, ast.Subscript) and is_cfg(t.value)]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _MUTATORS and is_cfg(node.func.value):
                bad.append(node.lineno)
    assert bad == []


# HA timer helper → position of its callback argument.
_TIMER_HELPERS = {
    "async_call_later": 2, "async_track_point_in_time": 1, "async_track_point_in_utc_time": 1,
    "async_track_time_change": 1, "async_track_time_interval": 1, "async_track_state_change_event": 2,
}


def test_timer_callbacks_are_coroutines():
    """A plain lambda or sync method given to an HA timer runs in a worker thread, so update_state there
    writes entity state off the event loop (HA raises). Every callback must be an async def (or a partial of one)."""
    cls = _class(ast.parse(OPTIMIZER.read_text()), "SunsynkOptimizer")
    async_methods = {n.name for n in cls.body if isinstance(n, ast.AsyncFunctionDef)}
    tree = ast.parse(OPTIMIZER.read_text())
    local_async = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _TIMER_HELPERS):
            continue
        pos = _TIMER_HELPERS[node.func.id]
        cb = node.args[pos] if len(node.args) > pos else None
        if cb is None:
            continue
        if isinstance(cb, ast.Call) and isinstance(cb.func, ast.Name) and cb.func.id == "partial":
            cb = cb.args[0]
        if isinstance(cb, ast.Attribute) and isinstance(cb.value, ast.Name) and cb.value.id == "self":
            ok = cb.attr in async_methods
        elif isinstance(cb, ast.Name) and cb.id == "callback":
            ok = True  # the daily schedule table, checked below
        elif isinstance(cb, ast.Name):
            ok = cb.id in local_async
        else:
            ok = False
        if not ok:
            bad.append(node.lineno)
    assert bad == []
    daily = next(
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "daily" for t in n.targets)
    )
    scheduled = {
        n.attr for n in ast.walk(daily)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
    }
    assert scheduled and scheduled <= async_methods


SENSOR = OPTIMIZER.parent / "sensor.py"


def _key_branches(fn: ast.FunctionDef) -> list[tuple[str, ast.If]]:
    """(sensor key, if-node) for every top-level `if self._sensor_key == "<key>"` in a property."""
    out = []
    for node in fn.body:
        test = getattr(node, "test", None)
        if (isinstance(node, ast.If) and isinstance(test, ast.Compare) and isinstance(test.left, ast.Attribute)
                and test.left.attr == "_sensor_key" and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.comparators[0], ast.Constant)):
            out.append((test.comparators[0].value, node))
    return out


def test_sensor_states_are_scalars_and_keys_unique():
    # Phase 76 left a duplicate branch: mpc_plan's state became its whole dict (> 255 chars)
    # and dynamic_tariff_shadow's saving was unreachable.
    cls = _class(ast.parse(SENSOR.read_text()), "SunsynkOptimizerSensor")
    prop = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "native_value")
    branches = _key_branches(prop)
    keys = [k for k, _ in branches]
    assert len(keys) == len(set(keys)), "duplicate sensor key branch in native_value"
    for key, node in branches:
        for ret in (n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None):
            call = ret.value
            assert not (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "_as_dict"), (
                f"{key}: native_value returns a whole dict"
            )


def test_api_writes_only_in_push_helpers():
    """Do-not-break 5 (phase 99): only _async_post_with_status and _async_write_setting call the API write methods."""
    allowed = {"async_post_income": "_async_post_with_status", "async_write_settings": "_async_write_setting"}
    bad = []
    for f in OPTIMIZER_FILES:
        tree = ast.parse(f.read_text())
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and node.attr in allowed and fn.name != allowed[node.attr]:
                    bad.append((f.name, fn.name, node.attr))
                if isinstance(node, ast.Attribute) and node.attr in allowed:
                    assert f.name == "optimizer.py", f"{node.attr} used outside optimizer.py"
    assert bad == []


def test_mixins_do_not_import_the_optimizer():
    """Mixins import only opt_common, never optimizer.py or each other (no import cycles)."""
    for f in OPTIMIZER_FILES[1:]:
        for node in ast.parse(f.read_text()).body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                assert node.module not in {"optimizer"} and not (node.module or "").startswith("opt_") or node.module == "opt_common", (
                    f.name, node.module)
