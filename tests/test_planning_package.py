"""Guard for the phase 98 split: the public surface of ``planning`` must not change."""

from pathlib import Path

NAMES = Path(__file__).with_name("planning_names.txt").read_text().split()


def test_every_name_is_still_exported(planning):
    missing = [n for n in NAMES if not hasattr(planning, n)]
    assert missing == []


def test_all_lists_exactly_the_exported_names(planning):
    assert sorted(planning.__all__) == sorted(NAMES)


def test_no_import_cycles_between_submodules(planning):
    """core is a sink: it must not import any sibling planning module."""
    src = (Path(planning.__file__).parent / "core.py").read_text()
    assert "from ." not in src
