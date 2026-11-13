"""Phase 95: the bundled cards redraw only on change (run in Node; skipped when Node is absent)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WWW = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer" / "www"

_HARNESS = """
const fs = require("fs");
const defined = {};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {}; return this.shadowRoot; } };
globalThis.window = globalThis;
globalThis.customElements = {
  get: (tag) => defined[tag],
  define: (tag, cls) => { defined[tag] = cls; },
  whenDefined: () => Promise.resolve(),
};
for (const file of process.argv.slice(1)) (0, eval)(fs.readFileSync(file, "utf8"));
setTimeout(() => {
  const out = {};
  // Flow card: a new hass object only redraws when a watched state object changed.
  const flow = new defined["sunsynk-optimizer-flow-card"]();
  let renders = 0;
  flow._render = () => { renders += 1; };
  flow.setConfig({ entities: { solar_power: "sensor.pv", battery_soc: "sensor.soc" }, extras: { target_soc: "sensor.target" } });
  const states = { "sensor.pv": { state: "1" }, "sensor.soc": { state: "50" }, "sensor.target": { state: "80" }, "light.x": { state: "on" } };
  flow.hass = { states };
  flow.hass = { states: { ...states, "light.x": { state: "off" } } };      // unrelated entity
  out.unrelated = renders;
  flow.hass = { states: { ...states, "sensor.soc": { state: "51" } } };    // watched entity
  out.watched = renders;
  flow.hass = { states: { ...states, "sensor.target": { state: "81" } } }; // watched extra
  out.extra = renders;
  flow.setConfig({ entities: { solar_power: "sensor.pv" }, extras: {} });  // new config always redraws
  out.reconfig = renders;
  out.watch = flow._watch;
  // Day chart: after midnight a card following today moves on; a card on an older day does not.
  const chart = new defined["sunsynk-optimizer-day-chart"]();
  const went = [];
  chart._go = (day) => { went.push(day.getDate()); };
  chart._load = () => { went.push("load"); };
  chart._syncNav = () => { went.push("nav"); };
  chart._built = true;
  chart._tick();                                                            // still today: refresh only
  const yesterday = new Date(chart._day.getTime() - 86400000);
  chart._day = yesterday;
  chart._tick();                                                            // following: jump to today
  chart._following = false;
  chart._tick();                                                            // browsing history: only re-enable the buttons
  out.ticks = went;
  out.today = new Date().getDate();
  console.log(JSON.stringify(out));
}, 0);
"""


@pytest.fixture(scope="module")
def card_result():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    files = [str(WWW / "sunsynk-optimizer-flow-card.js"), str(WWW / "sunsynk-optimizer-day-chart.js"), str(WWW / "sunsynk-optimizer-heatmap.js")]
    run = subprocess.run([node, "-e", _HARNESS, *files], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_flow_card_redraws_only_for_watched_entities(card_result):
    assert card_result["unrelated"] == 1
    assert card_result["watched"] == 2
    assert card_result["extra"] == 3
    assert card_result["reconfig"] == 4
    assert card_result["watch"] == ["sensor.pv"]


def test_day_chart_follows_today_across_midnight(card_result):
    assert card_result["ticks"] == ["load", card_result["today"], "nav"]


def test_day_chart_pointer_move_does_not_rebuild_the_series():
    src = (WWW / "sunsynk-optimizer-day-chart.js").read_text("utf-8")
    move = src[src.index("const move = (ev) =>"):src.index('addEventListener("pointermove"')]
    assert "if (bin === this._cursor) return;" in move
    assert "this._drawCursor();" in move and "this._draw();" not in move
