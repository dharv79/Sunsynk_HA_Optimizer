/* Sunsynk Optimizer flow card — served by the integration, no HACS install needed.
 * Usage: type: custom:sunsynk-optimizer-flow-card (the dashboard generator writes the config).
 * Sign conventions (SolarSynkV3): grid > 0 import, < 0 export; battery > 0 discharge, < 0 charge.
 * Missing / unavailable values show "—" and stop the flow; they are never treated as 0. */
(() => {
  const TAG = "sunsynk-optimizer-flow-card";

  const IDLE_W = 15; // below this a flow is considered idle
  const NODE = {
    solar: { x: 90, y: 6, w: 180, h: 80 },
    grid: { x: 4, y: 132, w: 100, h: 80 },
    home: { x: 256, y: 132, w: 100, h: 80 },
    batt: { x: 50, y: 258, w: 260, h: 86 },
  };
  const HUB = { x: 180, y: 172, r: 28 };

  const CSS = `
  :host { display:block; }
  ha-card { display:block; padding:16px; container-type:inline-size; overflow:hidden;
    --so-solar:#f5a623; --so-grid:#4c8dff; --so-export:#3ecf8e; --so-home:#b36bff;
    --so-charge:#2ec4b6; --so-discharge:#ff7a59; --so-bad:#ff5a5f; --so-ok:#3ecf8e; --so-warn:#f5a623;
    --so-line:var(--divider-color, rgba(127,127,127,.3)); }
  .head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:4px; }
  .title { font-size:16px; font-weight:600; color:var(--primary-text-color); }
  .pill { font-size:12px; padding:3px 10px; border-radius:999px; font-weight:600;
    background:color-mix(in srgb, var(--so-ok) 16%, transparent); color:var(--so-ok); white-space:nowrap; }
  .pill.warn { background:color-mix(in srgb, var(--so-warn) 18%, transparent); color:var(--so-warn); }
  .pill.bad { background:color-mix(in srgb, var(--so-bad) 18%, transparent); color:var(--so-bad); }
  .wrap { display:grid; gap:16px; grid-template-columns:1fr; }
  @container (min-width: 720px) { .wrap { grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:center; } }
  svg { width:100%; max-width:520px; margin:0 auto; display:block; }
  .side { display:flex; flex-direction:column; gap:14px; min-width:0; }
  .sec { font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--secondary-text-color); margin-bottom:6px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(96px,1fr)); gap:8px; }
  .chip { border-radius:12px; padding:8px 10px; min-width:0;
    background:color-mix(in srgb, var(--primary-text-color) 5%, transparent);
    border:1px solid color-mix(in srgb, var(--primary-text-color) 8%, transparent); }
  .chip .k { font-size:11px; color:var(--secondary-text-color); }
  .chip .v { font-size:15px; font-weight:600; color:var(--primary-text-color); margin-top:2px; overflow-wrap:anywhere; }
  .chip .h { font-size:11px; color:var(--secondary-text-color); margin-top:1px; overflow-wrap:anywhere; }
  .chip.dim .v { color:var(--disabled-text-color, var(--secondary-text-color)); }

  /* nodes */
  .node rect.bg { fill:color-mix(in srgb, var(--card-background-color, #1c1c1c) 82%, var(--c) 18%);
    stroke:color-mix(in srgb, var(--c) 55%, transparent); stroke-width:1.2; transition:opacity .4s; }
  .node.off { opacity:.45; }
  .node text { font-family:inherit; fill:var(--primary-text-color); text-anchor:middle; }
  .node .lab { font-size:10px; fill:var(--secondary-text-color); letter-spacing:.06em; text-transform:uppercase; }
  .node .val { font-size:17px; font-weight:700; }
  .node .sub { font-size:10px; fill:var(--secondary-text-color); }
  .node ha-icon { --mdc-icon-size:20px; color:var(--c); display:flex; }
  .solar { --c:var(--so-solar); } .gridn { --c:var(--so-grid); } .gridn.export { --c:var(--so-export); }
  .home { --c:var(--so-home); } .batt { --c:var(--so-charge); } .batt.discharge { --c:var(--so-discharge); }
  .batt.low { --c:var(--so-bad); }
  .hub circle.core { fill:color-mix(in srgb, var(--card-background-color, #1c1c1c) 80%, var(--primary-text-color) 8%);
    stroke:var(--so-line); stroke-width:1; }
  .hub circle.track { fill:none; stroke:var(--so-line); stroke-width:4; }
  .hub circle.arc { fill:none; stroke:var(--so-ok); stroke-width:4; stroke-linecap:round;
    transform:rotate(-90deg); transform-origin:${HUB.x}px ${HUB.y}px; transition:stroke-dasharray .6s; }
  .hub text { text-anchor:middle; fill:var(--primary-text-color); font-family:inherit; }
  .hub .pc { font-size:12px; font-weight:700; } .hub .pl { font-size:6.5px; fill:var(--secondary-text-color); letter-spacing:.05em; }

  /* flow lines */
  .flow { fill:none; stroke:var(--c); stroke-width:3; stroke-linecap:round; stroke-dasharray:1.5 9;
    opacity:.18; filter:none; }
  .flow.on { opacity:1; filter:drop-shadow(0 0 3px var(--c));
    animation:so-dash var(--dur,2s) linear infinite; }
  .flow.in { animation-direction:normal; } .flow.out { animation-direction:reverse; }
  .flow.on { stroke-width:var(--w,3); }
  @keyframes so-dash { from { stroke-dashoffset:0; } to { stroke-dashoffset:-10.5; } }
  .l-solar { --c:var(--so-solar); } .l-grid { --c:var(--so-grid); } .l-grid.export { --c:var(--so-export); }
  .l-home { --c:var(--so-home); } .l-batt { --c:var(--so-charge); } .l-batt.discharge { --c:var(--so-discharge); }
  @media (prefers-reduced-motion: reduce) { .flow.on { animation:none; stroke-dasharray:none; } }

  .bar-bg { fill:color-mix(in srgb, var(--primary-text-color) 12%, transparent); }
  .bar-fill { fill:var(--c); transition:width .6s; }
  .tgt { stroke:var(--primary-text-color); stroke-width:1.5; stroke-dasharray:2 2; }
  `;

  const ICON = (id, x, y) =>
    `<foreignObject x="${x}" y="${y}" width="22" height="22"><ha-icon icon="${id}"></ha-icon></foreignObject>`;

  const flowPath = (cls, d) => `<path class="flow l-${cls}" data-f="${cls}" d="${d}"/>`;

  const nodeBox = (k, cls, icon, label, extra = "") => {
    const n = NODE[k];
    const cx = n.x + n.w / 2;
    return `<g class="node ${cls}" data-n="${k}">
      <rect class="bg" x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="16"/>
      ${ICON(icon, cx - 11, n.y + 6)}
      <text class="lab" x="${cx}" y="${n.y + 40}">${label}</text>
      ${extra}
    </g>`;
  };

  const TEMPLATE = `
  <ha-card>
    <div class="head"><div class="title">Live power flow</div><div class="pill" data-k="pill">—</div></div>
    <div class="wrap">
      <svg viewBox="0 0 360 350" role="img" aria-label="Power flow">
        ${flowPath("solar", `M${HUB.x} ${HUB.y - HUB.r - 5} L${HUB.x} ${NODE.solar.y + NODE.solar.h}`)}
        ${flowPath("grid", `M${HUB.x - HUB.r - 5} ${HUB.y} L${NODE.grid.x + NODE.grid.w} ${HUB.y}`)}
        ${flowPath("home", `M${HUB.x + HUB.r + 5} ${HUB.y} L${NODE.home.x} ${HUB.y}`)}
        ${flowPath("batt", `M${HUB.x} ${HUB.y + HUB.r + 5} L${HUB.x} ${NODE.batt.y}`)}
        ${nodeBox("solar", "solar", "mdi:solar-power-variant", "Solar", `
          <text class="val" x="180" y="${NODE.solar.y + 60}" data-k="solar">—</text>
          <text class="sub" x="180" y="${NODE.solar.y + 74}" data-k="strings"></text>`)}
        ${nodeBox("grid", "gridn", "mdi:transmission-tower", "Grid", `
          <text class="val" x="${NODE.grid.x + 50}" y="${NODE.grid.y + 60}" data-k="grid">—</text>
          <text class="sub" x="${NODE.grid.x + 50}" y="${NODE.grid.y + 74}" data-k="gridsub"></text>`)}
        ${nodeBox("home", "home", "mdi:home-lightning-bolt", "Home", `
          <text class="val" x="${NODE.home.x + 50}" y="${NODE.home.y + 60}" data-k="home">—</text>
          <text class="sub" x="${NODE.home.x + 50}" y="${NODE.home.y + 74}" data-k="homesub"></text>`)}
        <g class="hub">
          <circle class="core" cx="${HUB.x}" cy="${HUB.y}" r="${HUB.r}"/>
          <circle class="track" cx="${HUB.x}" cy="${HUB.y}" r="${HUB.r + 5}"/>
          <circle class="arc" data-k="arc" cx="${HUB.x}" cy="${HUB.y}" r="${HUB.r + 5}" stroke-dasharray="0 999"/>
          <text class="pc" x="${HUB.x}" y="${HUB.y + 2}" data-k="self">—</text>
          <text class="pl" x="${HUB.x}" y="${HUB.y + 12}">OWN POWER</text>
        </g>
        ${nodeBox("batt", "batt", "mdi:battery-high", "Battery", `
          <text class="val" x="${NODE.batt.x + 62}" y="${NODE.batt.y + 62}" data-k="soc" style="text-anchor:end">—</text>
          <text class="val" x="${NODE.batt.x + 130}" y="${NODE.batt.y + 62}" data-k="batt" style="font-size:14px;text-anchor:start">—</text>
          <rect class="bar-bg" x="${NODE.batt.x + 18}" y="${NODE.batt.y + 70}" width="224" height="7" rx="3.5"/>
          <rect class="bar-fill" data-k="bar" x="${NODE.batt.x + 18}" y="${NODE.batt.y + 70}" width="0" height="7" rx="3.5" style="fill:var(--c)"/>
          <line class="tgt" data-k="tgt" x1="-9" x2="-9" y1="${NODE.batt.y + 66}" y2="${NODE.batt.y + 81}" opacity="0"/>
          <text class="sub" x="${NODE.batt.x + NODE.batt.w - 14}" y="${NODE.batt.y + 20}" data-k="eta" style="text-anchor:end"></text>`)}
      </svg>
      <div class="side">
        <div><div class="sec">Today</div><div class="grid" data-k="today"></div></div>
        <div data-k="xwrap"><div class="sec">Optimizer</div><div class="grid" data-k="extras"></div></div>
      </div>
    </div>
  </ha-card>`;

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  class SunsynkOptimizerFlowCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._built = false;
    }

    static getStubConfig() {
      return { entities: {}, extras: {} };
    }

    setConfig(config) {
      if (!config || typeof config !== "object") throw new Error("Invalid configuration");
      this._cfg = {
        invert_battery: false,
        invert_grid: false,
        battery_kwh: 0,
        reserve_soc: 20,
        title: "Live power flow",
        ...config,
        entities: { ...(config.entities || {}) },
        extras: { ...(config.extras || {}) },
      };
      this._built = false;
      // Every entity the card reads, so a state change elsewhere in Home Assistant is not a redraw.
      this._watch = [...new Set([...Object.values(this._cfg.entities), ...Object.values(this._cfg.extras)]
        .filter((id) => typeof id === "string" && id))];
      this._seen = null;
      if (this._hass) this.hass = this._hass;
    }

    getCardSize() {
      return 8;
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._cfg) return;
      // Home Assistant replaces a state object when it changes, so identity is enough.
      const seen = this._watch.map((id) => hass.states[id]);
      if (this._seen && seen.every((s, i) => s === this._seen[i])) return;
      this._seen = seen;
      this._render();
    }

    // ---- value helpers -------------------------------------------------
    _obj(id) {
      if (!id || !this._hass) return null;
      const s = this._hass.states[id];
      if (!s || s.state === "unknown" || s.state === "unavailable" || s.state === "") return null;
      return s;
    }

    _num(id) {
      const s = this._obj(id);
      if (!s) return null;
      const v = parseFloat(s.state);
      return Number.isFinite(v) ? v : null;
    }

    _watts(id) {
      const s = this._obj(id);
      const v = s ? parseFloat(s.state) : NaN;
      if (!Number.isFinite(v)) return null;
      const u = (s.attributes && s.attributes.unit_of_measurement) || "W";
      return u === "kW" ? v * 1000 : u === "MW" ? v * 1e6 : v;
    }

    _kwh(id) {
      const s = this._obj(id);
      const v = s ? parseFloat(s.state) : NaN;
      if (!Number.isFinite(v)) return null;
      const u = (s.attributes && s.attributes.unit_of_measurement) || "kWh";
      return u === "Wh" ? v / 1000 : u === "MWh" ? v * 1000 : v;
    }

    _txt(id) {
      const s = this._obj(id);
      return s ? s.state : null;
    }

    static fmtW(w) {
      if (w === null) return "—";
      const a = Math.abs(w);
      return a >= 1000 ? `${(a / 1000).toFixed(2)} kW` : `${Math.round(a)} W`;
    }

    static fmtKwh(v) {
      return v === null ? "—" : `${v.toFixed(1)} kWh`;
    }

    static fmtDur(h) {
      if (!Number.isFinite(h) || h <= 0) return "";
      const m = Math.round(h * 60);
      return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
    }

    // ---- DOM -----------------------------------------------------------
    _build() {
      this.shadowRoot.innerHTML = `<style>${CSS}</style>${TEMPLATE}`;
      const r = this.shadowRoot;
      this._el = {};
      r.querySelectorAll("[data-k]").forEach((e) => (this._el[e.dataset.k] = e));
      this._flow = {};
      r.querySelectorAll("[data-f]").forEach((e) => (this._flow[e.dataset.f] = e));
      this._node = {};
      r.querySelectorAll("[data-n]").forEach((e) => (this._node[e.dataset.n] = e));
      r.querySelector(".title").textContent = this._cfg.title;
      this._chips = {};
      this._built = true;
    }

    _setFlow(key, watts, towardHub, extraCls = "") {
      const f = this._flow[key];
      const active = watts !== null && Math.abs(watts) >= IDLE_W;
      f.setAttribute("class", `flow l-${key} ${extraCls}${active ? ` on ${towardHub ? "in" : "out"}` : ""}`);
      if (active) {
        const a = Math.abs(watts);
        f.style.setProperty("--dur", `${clamp(3.2 - a / 1800, 0.7, 3.2).toFixed(2)}s`);
        f.style.setProperty("--w", `${clamp(2 + a / 1500, 2, 5).toFixed(1)}`);
      }
    }

    _chip(host, id, key, value, hint, dim) {
      let c = this._chips[id];
      if (!c) {
        c = this._chips[id] = document.createElement("div");
        c.className = "chip";
        c.innerHTML = `<div class="k"></div><div class="v"></div><div class="h"></div>`;
        c.children[0].textContent = key;
        host.appendChild(c);
      }
      c.classList.toggle("dim", !!dim);
      if (c.children[1].textContent !== value) c.children[1].textContent = value;
      const h = hint || "";
      if (c.children[2].textContent !== h) c.children[2].textContent = h;
      c.children[2].style.display = h ? "" : "none";
    }

    _render() {
      if (!this._built) this._build();
      const F = SunsynkOptimizerFlowCard;
      const e = this._cfg.entities;
      const x = this._cfg.extras;

      // ---- raw readings
      let solar = this._watts(e.solar_power);
      const pv1 = this._watts(e.pv1_power);
      const pv2 = this._watts(e.pv2_power);
      if (solar === null && (pv1 !== null || pv2 !== null)) solar = (pv1 || 0) + (pv2 || 0);
      const home = this._watts(e.load_power);
      let grid = this._watts(e.grid_power);
      if (grid !== null && this._cfg.invert_grid) grid = -grid;
      let batt = this._watts(e.battery_power);
      if (batt !== null && this._cfg.invert_battery) batt = -batt;
      const soc = this._num(e.battery_soc);

      // ---- solar
      this._setFlow("solar", solar, true);
      this._node.solar.classList.toggle("off", solar === null);
      this._el.solar.textContent = F.fmtW(solar);
      const names = [this._cfg.pv1_name || "PV1", this._cfg.pv2_name || "PV2"];
      const parts = [];
      if (pv1 !== null) parts.push(`${names[0]} ${F.fmtW(pv1)}`);
      if (pv2 !== null) parts.push(`${names[1]} ${F.fmtW(pv2)}`);
      this._el.strings.textContent = parts.join("  ·  ");

      // ---- grid
      const exporting = grid !== null && grid < -IDLE_W;
      this._setFlow("grid", grid, !exporting, exporting ? "export" : "");
      this._node.grid.classList.toggle("export", exporting);
      this._node.grid.classList.toggle("off", grid === null);
      this._el.grid.textContent = F.fmtW(grid);
      this._el.gridsub.textContent =
        grid === null ? "" : Math.abs(grid) < IDLE_W ? "Idle" : exporting ? "Exporting" : "Importing";

      // ---- home
      this._setFlow("home", home, false);
      this._node.home.classList.toggle("off", home === null);
      this._el.home.textContent = F.fmtW(home);
      const aux = this._watts(e.aux_power);
      this._el.homesub.textContent = aux !== null && aux >= IDLE_W ? `incl. UPS ${F.fmtW(aux)}` : "";

      // ---- battery
      const discharging = batt !== null && batt > IDLE_W;
      const charging = batt !== null && batt < -IDLE_W;
      this._setFlow("batt", batt, discharging, discharging ? "discharge" : "");
      const bn = this._node.batt;
      bn.classList.toggle("discharge", discharging);
      bn.classList.toggle("low", soc !== null && soc < this._cfg.reserve_soc + 5);
      bn.classList.toggle("off", soc === null && batt === null);
      this._el.soc.textContent = soc === null ? "—" : `${Math.round(soc)}%`;
      this._el.batt.textContent = batt === null ? "" : `${F.fmtW(batt)} ${charging ? "charging" : discharging ? "discharging" : "idle"}`;
      const barW = 224;
      this._el.bar.setAttribute("width", soc === null ? 0 : ((clamp(soc, 0, 100) / 100) * barW).toFixed(1));
      const tgt = this._num(x.target_soc);
      if (tgt !== null) {
        const tx = NODE.batt.x + 18 + (clamp(tgt, 0, 100) / 100) * barW;
        this._el.tgt.setAttribute("x1", tx);
        this._el.tgt.setAttribute("x2", tx);
        this._el.tgt.setAttribute("opacity", "0.9");
      } else this._el.tgt.setAttribute("opacity", "0");
      let eta = "";
      const cap = this._cfg.battery_kwh > 0 ? this._cfg.battery_kwh : this._kwh(e.battery_capacity);
      if (soc !== null && batt !== null && cap && cap > 0 && cap < 200) {
        if (charging && soc < 99) eta = `Full in ~${F.fmtDur(((100 - soc) / 100) * cap / (Math.abs(batt) / 1000))}`;
        else if (discharging && soc > this._cfg.reserve_soc)
          eta = `${F.fmtDur(((soc - this._cfg.reserve_soc) / 100) * cap / (batt / 1000))} to ${this._cfg.reserve_soc}%`;
      }
      this._el.eta.textContent = eta;

      // ---- hub: share of the home's load not coming from the grid
      let self = null;
      if (home !== null && home > 50 && grid !== null) self = clamp(100 * (1 - Math.max(grid, 0) / home), 0, 100);
      this._el.self.textContent = self === null ? "—" : `${Math.round(self)}%`;
      const circ = 2 * Math.PI * (HUB.r + 5);
      this._el.arc.setAttribute("stroke-dasharray", `${self === null ? 0 : ((self / 100) * circ).toFixed(1)} 999`);
      this._el.arc.style.opacity = self === null || self < 1 ? "0" : "1";

      // ---- header pill: grid / outage / fault
      const outage = this._txt(x.grid_outage) === "on";
      const fault = this._txt(x.inverter_fault) === "on";
      const gs = (this._txt(e.grid_status) || "").toLowerCase();
      const pill = this._el.pill;
      let cls = "pill", text = "Normal";
      if (outage || gs === "off" || gs.includes("off-grid") || gs.includes("offline")) { cls = "pill bad"; text = "Grid outage"; }
      else if (fault) { cls = "pill bad"; text = "Inverter fault"; }
      else if (exporting) { text = "Exporting"; }
      else if (charging && grid !== null && grid > IDLE_W) { cls = "pill warn"; text = "Grid charging"; }
      else if (self !== null && self >= 99) { text = "Self-powered"; }
      else if (home === null && grid === null && soc === null) { cls = "pill warn"; text = "No data"; }
      pill.className = cls;
      pill.textContent = text;

      // ---- today strip
      const todayHost = this._el.today;
      const t = [
        ["Solar", e.solar_today], ["Home", e.load_today], ["Imported", e.grid_import_today],
        ["Exported", e.grid_export_today], ["Charged", e.battery_charge_today], ["Discharged", e.battery_discharge_today],
      ];
      t.forEach(([k, id]) => {
        if (!id) return;
        const v = this._kwh(id);
        this._chip(todayHost, `t_${k}`, k, F.fmtKwh(v), "", v === null);
      });

      // ---- optimizer extras
      const xHost = this._el.extras;
      let shown = 0;
      if (x.target_soc) {
        const v = this._num(x.target_soc);
        shown++;
        this._chip(xHost, "x_target", "Tonight's target", v === null ? "—" : `${Math.round(v)}%`,
          soc !== null && v !== null ? (v > soc ? `+${Math.round(v - soc)}% to charge` : "No top-up needed") : "", v === null);
      }
      if (x.import_window) {
        const v = this._txt(x.import_window);
        shown++;
        this._chip(xHost, "x_window", "Charge window", v || "—", "", !v);
      }
      if (x.price_now) {
        const o = this._obj(x.price_now);
        const v = this._num(x.price_now);
        shown++;
        this._chip(xHost, "x_price", "Cost of 1 kWh now", v === null ? "—" : `${v.toFixed(1)} p`,
          o && o.attributes && o.attributes.source ? `from ${o.attributes.source}` : "", v === null);
      }
      if (x.forecast) {
        const v = this._kwh(x.forecast);
        shown++;
        this._chip(xHost, "x_fc", "Solar forecast", F.fmtKwh(v), "today", v === null);
      }
      if (x.evening_export) {
        const v = this._txt(x.evening_export);
        shown++;
        this._chip(xHost, "x_exp", "Evening export", v || "—", "", !v);
      }
      this._el.xwrap.style.display = shown ? "" : "none";
    }
  }

  // The frontend swaps in a scoped custom-element registry while it boots. An element defined
  // before that is invisible to it and the card renders "Configuration error" (a load-order race),
  // so define once the app shell exists. The timeout covers pages without <home-assistant>.
  const define = () => {
    if (customElements.get(TAG)) return;
    customElements.define(TAG, SunsynkOptimizerFlowCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: TAG,
      name: "Sunsynk Optimizer flow card",
      description: "Animated power flow with the optimizer's plan, price and forecast.",
    });
  };
  Promise.race([customElements.whenDefined("home-assistant"), new Promise((r) => setTimeout(r, 10000))]).then(define);
})();
