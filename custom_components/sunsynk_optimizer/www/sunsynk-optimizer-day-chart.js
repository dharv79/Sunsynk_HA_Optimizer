/* Sunsynk Optimizer day chart — served by the integration, no HACS install needed.
 * Usage: type: custom:sunsynk-optimizer-day-chart (the dashboard generator writes the config).
 * One day of PV, battery, load and grid power (W, left axis) and battery SOC (%, right axis),
 * like the Sunsynk app's Generation chart, with ◀ / ▶ and a date picker to move between days.
 * Data: recorder history (minute detail, kept 10 days by default); older days fall back to the
 * hourly long-term statistics. Signs follow SolarSynkV3: grid > 0 import, battery > 0 discharge. */
(() => {
  const TAG = "sunsynk-optimizer-day-chart";

  const BIN_MIN = 5; // resample to 5-minute bins (288 per day)
  const BINS = (24 * 60) / BIN_MIN;
  // Plot margins in px. The SVG is drawn at the card's real width (ResizeObserver) so text stays 10 px.
  const PL = 40, PR = 34, PT = 12, PB = 26;
  const REFRESH_MS = 5 * 60 * 1000;

  // key, label, colour, axis
  const SERIES = [
    ["pv", "PV", "#3ecf8e", "w"],
    ["battery", "Battery", "#6c63ff", "w"],
    ["load", "Load", "#ff7a7a", "w"],
    ["grid", "Grid", "#f5b800", "w"],
    ["soc", "SOC", "#2ec4b6", "pct"],
  ];
  const TOTALS = [
    ["solar_today", "Solar"],
    ["load_today", "Home"],
    ["grid_import_today", "Imported"],
    ["grid_export_today", "Exported"],
    ["battery_charge_today", "Charged"],
    ["battery_discharge_today", "Discharged"],
  ];

  const CSS = `
  :host { display:block; }
  ha-card { display:block; padding:16px; overflow:hidden; }
  .head { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; }
  .title { font-size:16px; font-weight:600; color:var(--primary-text-color); }
  .nav { display:flex; align-items:center; gap:4px; }
  .nav button { border:0; background:color-mix(in srgb, var(--primary-text-color) 7%, transparent);
    color:var(--primary-text-color); border-radius:10px; min-width:36px; height:34px; font-size:15px; cursor:pointer;
    font-family:inherit; padding:0 10px; }
  .nav button:disabled { opacity:.35; cursor:default; }
  .nav input { border:1px solid color-mix(in srgb, var(--primary-text-color) 14%, transparent); border-radius:10px;
    background:transparent; color:var(--primary-text-color); height:32px; padding:0 8px; font:inherit; font-size:14px;
    color-scheme:light dark; }
  .legend { display:grid; grid-template-columns:repeat(auto-fill,minmax(130px,1fr)); gap:4px 14px; margin:12px 0 6px; }
  .lg { display:flex; align-items:center; gap:8px; font-size:13px; cursor:pointer; user-select:none;
    color:var(--primary-text-color); min-width:0; }
  .lg.off { opacity:.35; }
  .dot { width:10px; height:10px; border-radius:50%; flex:none; }
  .lg .v { margin-left:auto; font-weight:600; font-variant-numeric:tabular-nums; }
  .note { font-size:12px; color:var(--secondary-text-color); min-height:16px; }
  svg { width:100%; display:block; touch-action:pan-y; }
  .ax { font-size:10px; fill:var(--secondary-text-color); font-family:inherit; }
  .gl { stroke:var(--divider-color, rgba(127,127,127,.25)); stroke-width:1; stroke-dasharray:3 4; }
  .zero { stroke:var(--secondary-text-color); stroke-opacity:.5; stroke-width:1; }
  .ln { fill:none; stroke-width:1.6; stroke-linejoin:round; }
  .ar { stroke:none; opacity:.16; }
  .cur { stroke:var(--primary-text-color); stroke-opacity:.55; stroke-width:1; }
  .msg { font-size:13px; fill:var(--secondary-text-color); text-anchor:middle; font-family:inherit; }
  .sec { font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--secondary-text-color); margin:14px 0 6px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(96px,1fr)); gap:8px; }
  .chip { border-radius:12px; padding:8px 10px; min-width:0;
    background:color-mix(in srgb, var(--primary-text-color) 5%, transparent);
    border:1px solid color-mix(in srgb, var(--primary-text-color) 8%, transparent); }
  .chip .k { font-size:11px; color:var(--secondary-text-color); }
  .chip .v { font-size:15px; font-weight:600; color:var(--primary-text-color); margin-top:2px; }
  `;

  const pad = (n) => String(n).padStart(2, "0");
  const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const toMs = (t) => (typeof t === "number" ? (t < 1e12 ? t * 1000 : t) : Date.parse(t));
  const fmtW = (w) => (w === null || w === undefined ? "—" : Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${Math.round(w)} W`);
  const fmtV = (key, v) => (key === "soc" ? (v === null || v === undefined ? "—" : `${Math.round(v)}%`) : fmtW(v));
  const scale = (u, kind) => (kind === "kwh" ? (u === "Wh" ? 0.001 : u === "MWh" ? 1000 : 1) : u === "kW" ? 1000 : u === "MW" ? 1e6 : 1);

  /** Step-resample [[ms, value|null], ...] (sorted) onto the day's bins: each bin takes the mean of the
   * samples inside it, or carries the last known value forward. Bins after `nowMs` stay null. */
  function resample(points, dayStart, nowMs) {
    const out = new Array(BINS).fill(null);
    const step = BIN_MIN * 60000;
    let i = 0, last = null;
    for (let b = 0; b < BINS; b++) {
      const t0 = dayStart + b * step, t1 = t0 + step;
      if (t0 > nowMs) break;
      let sum = 0, n = 0;
      while (i < points.length && points[i][0] < t1) {
        const v = points[i][1];
        if (points[i][0] >= t0 && v !== null) { sum += v; n++; }
        last = v;
        i++;
      }
      out[b] = n ? sum / n : last;
    }
    return out;
  }

  class SunsynkOptimizerDayChart extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._day = startOfDay(new Date());
      this._following = true; // showing today: move on when the date rolls
      this._data = null;
      this._hidden = new Set();
      this._cursor = null;
      this._seq = 0;
      this._w = 720;
    }

    static getStubConfig() {
      return { entities: {} };
    }

    setConfig(config) {
      if (!config || typeof config !== "object") throw new Error("Invalid configuration");
      this._cfg = { title: "Day chart", invert_battery: false, invert_grid: false, ...config, entities: { ...(config.entities || {}) } };
      this._built = false;
      this._data = null;
      if (this._hass) this._render();
    }

    getCardSize() {
      return 9;
    }

    set hass(hass) {
      // History is fetched per day, not per state change: only the first hass (or a new config) loads.
      this._hass = hass;
      if (this._cfg && !this._built) this._render();
    }

    connectedCallback() {
      this._timer = setInterval(() => this._tick(), REFRESH_MS);
    }

    disconnectedCallback() {
      clearInterval(this._timer);
    }

    _tick() {
      if (this._isToday()) return this._load(true);
      // Past midnight: a card left on "today" moves to the new day; otherwise re-enable Next and Today.
      if (this._following) return this._go(startOfDay(new Date()));
      if (this._built) this._syncNav();
    }

    _isToday() {
      return isoDay(this._day) === isoDay(new Date());
    }

    _render() {
      this._build();
      this._load();
    }

    // ---- DOM -----------------------------------------------------------
    _build() {
      const r = this.shadowRoot;
      r.innerHTML = `<style>${CSS}</style><ha-card>
        <div class="head"><div class="title"></div>
          <div class="nav"><button data-a="prev" aria-label="Previous day">◀</button>
            <input type="date" data-a="date" aria-label="Day">
            <button data-a="next" aria-label="Next day">▶</button>
            <button data-a="today">Today</button></div></div>
        <div class="legend"></div>
        <div class="note"></div>
        <svg role="img" aria-label="Day chart"></svg>
        <div class="sec">Day totals</div><div class="grid"></div>
      </ha-card>`;
      r.querySelector(".title").textContent = this._cfg.title;
      this._svg = r.querySelector("svg");
      this._note = r.querySelector(".note");
      this._date = r.querySelector('[data-a="date"]');
      r.querySelector('[data-a="prev"]').onclick = () => this._go(addDays(this._day, -1));
      r.querySelector('[data-a="next"]').onclick = () => this._go(addDays(this._day, 1));
      r.querySelector('[data-a="today"]').onclick = () => this._go(startOfDay(new Date()));
      this._date.onchange = () => {
        const [y, m, d] = (this._date.value || "").split("-").map(Number);
        if (y && m && d) this._go(new Date(y, m - 1, d));
      };
      const lg = r.querySelector(".legend");
      this._lg = {};
      for (const [key, label, col] of SERIES) {
        const el = document.createElement("div");
        el.className = "lg";
        el.innerHTML = `<span class="dot" style="background:${col}"></span><span></span><span class="v">—</span>`;
        el.children[1].textContent = label;
        el.onclick = () => {
          this._hidden.has(key) ? this._hidden.delete(key) : this._hidden.add(key);
          el.classList.toggle("off", this._hidden.has(key));
          this._draw();
        };
        lg.appendChild(el);
        this._lg[key] = el;
      }
      const grid = r.querySelector(".grid");
      this._tot = {};
      for (const [key, label] of TOTALS) {
        if (!this._cfg.entities[key]) continue;
        const c = document.createElement("div");
        c.className = "chip";
        c.innerHTML = `<div class="k"></div><div class="v">—</div>`;
        c.children[0].textContent = label;
        grid.appendChild(c);
        this._tot[key] = c.children[1];
      }
      r.querySelector(".sec").style.display = Object.keys(this._tot).length ? "" : "none";
      const move = (ev) => {
        const rect = this._svg.getBoundingClientRect();
        if (!rect.width) return;
        const x = ((ev.clientX - rect.left) / rect.width) * this._w;
        const bin = Math.max(0, Math.min(BINS - 1, Math.floor(((x - PL) / (this._w - PL - PR)) * BINS)));
        if (bin === this._cursor) return; // same 5-minute bin: nothing to redraw
        this._cursor = bin;
        this._drawCursor();
      };
      this._svg.addEventListener("pointermove", move);
      this._svg.addEventListener("pointerdown", move);
      this._svg.addEventListener("pointerleave", (ev) => {
        if (ev.pointerType === "touch" || this._cursor === null) return; // keep a tapped time until the next tap
        this._cursor = null;
        this._drawCursor();
      });
      if (this._ro) this._ro.disconnect();
      this._ro = new ResizeObserver(() => {
        const w = Math.max(280, Math.round(this._svg.clientWidth));
        if (w !== this._w) {
          this._w = w;
          this._draw();
        }
      });
      this._ro.observe(this._svg);
      this._built = true;
      this._syncNav();
    }

    _syncNav() {
      const today = startOfDay(new Date());
      this._date.value = isoDay(this._day);
      this._date.max = isoDay(today);
      this.shadowRoot.querySelector('[data-a="next"]').disabled = this._day >= today;
      this.shadowRoot.querySelector('[data-a="today"]').disabled = this._day >= today;
    }

    _go(day) {
      const today = startOfDay(new Date());
      this._day = day > today ? today : day;
      this._following = this._day >= today;
      this._cursor = null;
      this._syncNav();
      this._load();
    }

    // ---- data ----------------------------------------------------------
    _ids() {
      const e = this._cfg.entities;
      const power = [e.pv1_power, e.pv2_power, e.battery_power, e.load_power, e.grid_power, e.battery_soc].filter(Boolean);
      const totals = TOTALS.map(([k]) => e[k]).filter(Boolean);
      return { power, totals, all: [...new Set([...power, ...totals])] };
    }

    _unit(id) {
      const s = this._hass && this._hass.states[id];
      return (s && s.attributes && s.attributes.unit_of_measurement) || "";
    }

    async _load(quiet = false) {
      if (!this._hass || !this._built) return;
      const seq = ++this._seq;
      const dayStart = this._day.getTime();
      const dayEnd = addDays(this._day, 1).getTime();
      const nowMs = Date.now();
      const { all } = this._ids();
      if (!all.length) {
        this._data = { empty: true, mode: "none" };
        this._draw();
        return;
      }
      if (!quiet) {
        this._note.textContent = "Loading…";
        this._data = null;
        this._draw();
      }
      let raw = {}, mode = "detail";
      try {
        const res = await this._hass.callWS({
          type: "history/history_during_period",
          start_time: new Date(dayStart).toISOString(),
          end_time: new Date(Math.min(dayEnd, nowMs)).toISOString(),
          entity_ids: all,
          minimal_response: true,
          no_attributes: true,
          significant_changes_only: false,
        });
        for (const id of all) {
          raw[id] = (res[id] || []).map((p) => {
            const v = parseFloat(p.s);
            return [toMs(p.lu !== undefined ? p.lu : p.last_updated), Number.isFinite(v) ? v : null];
          }).filter((p) => Number.isFinite(p[0]));
        }
      } catch (err) {
        raw = {};
      }
      // History holds the day's start state even when the rest was purged; need real samples inside the day.
      const inside = (id) => (raw[id] || []).filter((p) => p[0] >= dayStart && p[1] !== null).length;
      if (!this._ids().power.some((id) => inside(id) > 2)) {
        mode = "hourly";
        raw = {};
        try {
          const res = await this._hass.callWS({
            type: "recorder/statistics_during_period",
            start_time: new Date(dayStart).toISOString(),
            end_time: new Date(dayEnd).toISOString(),
            statistic_ids: all,
            period: "hour",
            types: ["mean", "max", "state"],
          });
          for (const id of all) {
            raw[id] = (res[id] || []).map((r) => {
              const v = r.mean !== undefined && r.mean !== null ? r.mean : r.state !== undefined && r.state !== null ? r.state : r.max;
              return [toMs(r.start), Number.isFinite(v) ? v : null];
            });
          }
        } catch (err) {
          raw = {};
        }
      }
      if (seq !== this._seq) return; // a newer day was requested meanwhile
      this._data = this._shape(raw, dayStart, Math.min(nowMs, dayEnd), mode);
      this._draw();
    }

    _shape(raw, dayStart, endMs, mode) {
      const e = this._cfg.entities;
      const series = (id, kind = "w") => {
        if (!id || !raw[id] || !raw[id].length) return null;
        const k = kind === "pct" ? 1 : scale(this._unit(id), "w");
        return resample(raw[id].map(([t, v]) => [t, v === null ? null : v * k]), dayStart, endMs);
      };
      const comb = (a, b, f) => (a || b ? a ? a.map((v, i) => f(v, b ? b[i] : null)) : b.map((v) => f(null, v)) : null);
      const pv = comb(series(e.pv1_power), series(e.pv2_power), (a, b) => (a === null && b === null ? null : (a || 0) + (b || 0)));
      const sign = (arr, inv) => (arr && inv ? arr.map((v) => (v === null ? null : -v)) : arr);
      const s = {
        pv,
        battery: sign(series(e.battery_power), this._cfg.invert_battery),
        load: series(e.load_power),
        grid: sign(series(e.grid_power), this._cfg.invert_grid),
        soc: series(e.battery_soc, "pct"),
      };
      const totals = {};
      for (const [key] of TOTALS) {
        const id = e[key];
        const pts = (raw[id] || []).filter((p) => p[0] >= dayStart && p[0] < endMs && p[1] !== null);
        // Daily counters reset at midnight: the day's total is its last reading before the day ended.
        totals[key] = pts.length ? pts[pts.length - 1][1] * scale(this._unit(id), "kwh") : null;
      }
      const empty = !Object.values(s).some((a) => a && a.some((v) => v !== null));
      return { s, totals, mode, empty };
    }

    // ---- drawing -------------------------------------------------------
    _draw() {
      if (!this._built) return;
      const d = this._data;
      const svg = this._svg;
      const W = this._w, H = W < 560 ? 240 : 320, PW = W - PL - PR, PH = H - PT - PB;
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const x = (b) => PL + (b / BINS) * PW;
      let out = "";
      // time axis
      for (let h = 0; h <= 24; h += W < 420 ? 6 : 3) {
        const xx = PL + (h / 24) * PW;
        out += `<text class="ax" x="${xx}" y="${H - 8}" text-anchor="middle">${pad(h % 24)}:00</text>`;
      }
      if (!d || d.empty) {
        out += `<text class="msg" x="${PL + PW / 2}" y="${PT + PH / 2}">${!d ? "Loading…" : "No data for this day"}</text>`;
        svg.innerHTML = out;
        this._geo = null;
        this._note.textContent = d && d.empty
          ? d.mode === "none" ? "No entities configured." : "Home Assistant has no history or long-term statistics for these sensors on this day."
          : "Loading…";
        for (const [key] of SERIES) this._lg[key].querySelector(".v").textContent = "—";
        for (const k in this._tot) this._tot[k].textContent = "—";
        return;
      }
      this._note.textContent = d.mode === "hourly"
        ? "Hourly averages: detailed history for this day has been purged (Home Assistant keeps it 10 days by default)."
        : "";
      const vis = SERIES.filter(([k]) => !this._hidden.has(k) && d.s[k]);
      let lo = 0, hi = 0;
      for (const [k, , , ax] of vis) {
        if (ax !== "w") continue;
        for (const v of d.s[k]) if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      }
      if (hi - lo < 500) hi = lo + 500;
      // Round the W axis to a clean step (1, 2, 2.5 or 5 × 10^n) so ticks read 0, 2k, 4k…
      const raw = (hi - lo) / 4, mag = 10 ** Math.floor(Math.log10(raw));
      const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= raw);
      lo = Math.floor(lo / step) * step;
      hi = Math.ceil(hi / step) * step;
      const span = hi - lo;
      const yw = (v) => PT + ((hi - v) / span) * PH;
      const yp = (v) => PT + ((100 - v) / 100) * PH;
      // grid lines + W axis
      for (let v = lo; v <= hi + step / 2; v += step) {
        const yy = yw(v);
        out += `<line class="gl" x1="${PL}" x2="${PL + PW}" y1="${yy}" y2="${yy}"/>`;
        out += `<text class="ax" x="${PL - 6}" y="${yy + 3}" text-anchor="end">${Math.abs(v) >= 1000 ? +(v / 1000).toFixed(1) + "k" : Math.round(v)}</text>`;
      }
      for (let p = 0; p <= 100; p += 25) out += `<text class="ax" x="${PL + PW + 6}" y="${yp(p) + 3}">${p}%</text>`;
      if (lo < 0) out += `<line class="zero" x1="${PL}" x2="${PL + PW}" y1="${yw(0)}" y2="${yw(0)}"/>`;
      // series: area to zero for power, line for all
      for (const [k, , col, ax] of vis) {
        const arr = d.s[k];
        const y = ax === "w" ? yw : yp;
        let segs = [], cur = [];
        arr.forEach((v, b) => {
          if (v === null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([x(b + 0.5), y(v)]);
        });
        if (cur.length) segs.push(cur);
        for (const seg of segs) {
          const line = seg.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
          if (ax === "w") {
            const z = yw(0).toFixed(1);
            out += `<path class="ar" fill="${col}" d="${line}L${seg[seg.length - 1][0].toFixed(1)} ${z}L${seg[0][0].toFixed(1)} ${z}Z"/>`;
          }
          out += `<path class="ln" stroke="${col}" d="${line}"/>`;
        }
      }
      svg.innerHTML = out + "<g data-cur></g>";
      this._geo = { PW, PH };
      this._drawCursor();
      for (const k in this._tot) {
        const v = d.totals[k];
        this._tot[k].textContent = v === null || v === undefined ? "—" : `${v.toFixed(1)} kWh`;
      }
    }

    // Cursor line, time label and legend values only (cursor, else the latest bin): a pointer
    // move must not rebuild the five 288-point series.
    _drawCursor() {
      const d = this._data;
      const g = this._svg && this._svg.querySelector("[data-cur]");
      if (!d || d.empty || !g || !this._geo) return;
      const { PW, PH } = this._geo;
      let at = this._cursor;
      let cur = "";
      if (at === null) {
        at = -1;
        for (const [k] of SERIES) if (d.s[k]) d.s[k].forEach((v, b) => { if (v !== null && b > at) at = b; });
      } else {
        const xx = PL + ((at + 0.5) / BINS) * PW;
        cur += `<line class="cur" x1="${xx}" x2="${xx}" y1="${PT}" y2="${PT + PH}"/>`;
        const mins = at * BIN_MIN;
        cur += `<text class="ax" x="${Math.min(Math.max(xx, PL + 18), PL + PW - 18)}" y="${PT + 9}" text-anchor="middle">${pad(Math.floor(mins / 60))}:${pad(mins % 60)}</text>`;
      }
      g.innerHTML = cur;
      for (const [k] of SERIES) {
        const arr = d.s[k];
        let v = arr && at >= 0 ? arr[at] : null;
        let txt = fmtV(k, v);
        if (v !== null && v !== undefined && (k === "battery" || k === "grid") && Math.abs(v) >= 15) {
          txt += k === "battery" ? (v > 0 ? " out" : " in") : v > 0 ? " in" : " out";
        }
        this._lg[k].querySelector(".v").textContent = txt.replace("-", "");
        this._lg[k].style.display = arr ? "" : "none";
      }
    }
  }

  // The frontend swaps in a scoped custom-element registry while it boots. An element defined
  // before that is invisible to it and the card renders "Configuration error" (a load-order race),
  // so define once the app shell exists. The timeout covers pages without <home-assistant>.
  const define = () => {
    if (customElements.get(TAG)) return;
    customElements.define(TAG, SunsynkOptimizerDayChart);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: TAG,
      name: "Sunsynk Optimizer day chart",
      description: "One day of PV, battery, load, grid and SOC with previous/next day navigation.",
    });
  };
  Promise.race([customElements.whenDefined("home-assistant"), new Promise((r) => setTimeout(r, 10000))]).then(define);
})();
