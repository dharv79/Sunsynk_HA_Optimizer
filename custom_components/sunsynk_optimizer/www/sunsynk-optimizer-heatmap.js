/* Sunsynk Optimizer heat map — served by the integration, no HACS install needed.
 * Usage: type: custom:sunsynk-optimizer-heatmap (the dashboard generator writes the config).
 * Days down, 48 half-hours across, coloured by grid import (or export, or house load). Shows the cheap
 * window, the daytime gaps and the peak window at a glance. Tap a cell to open that day in the day chart.
 * Data: the websocket command sunsynk_optimizer/heatmap (the logged half-hourly meter data), not entity
 * attributes. Redraws only when the data changed. */
(() => {
  const TAG = "sunsynk-optimizer-heatmap";
  const REFRESH_MS = 15 * 60 * 1000;
  const PL = 56, PR = 8, PT = 20, PB = 8, CELL_H = 14;
  const KINDS = [
    ["import", "Import", "#f5b800"],
    ["export", "Export", "#3ecf8e"],
    ["load", "Load", "#ff7a7a"],
  ];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const CSS = `
  :host { display:block; }
  ha-card { display:block; padding:16px; overflow:hidden; }
  .head { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; }
  .title { font-size:16px; font-weight:600; color:var(--primary-text-color); }
  .tabs { display:flex; gap:4px; }
  .tabs button { border:0; background:color-mix(in srgb, var(--primary-text-color) 7%, transparent);
    color:var(--primary-text-color); border-radius:10px; height:34px; padding:0 12px; font:inherit; font-size:14px; cursor:pointer; }
  .tabs button[aria-pressed="true"] { background:var(--primary-color); color:var(--text-primary-color, #fff); }
  .note { font-size:12px; color:var(--secondary-text-color); min-height:16px; margin:8px 0 4px; }
  svg { width:100%; display:block; }
  .ax { font-size:10px; fill:var(--secondary-text-color); font-family:inherit; }
  .edge { stroke:var(--primary-text-color); stroke-opacity:.55; stroke-width:1; stroke-dasharray:3 3; }
  .none { fill:none; stroke:var(--divider-color, rgba(127,127,127,.35)); stroke-width:.5; }
  .cell { cursor:pointer; }
  .msg { font-size:13px; fill:var(--secondary-text-color); text-anchor:middle; font-family:inherit; }
  `;

  const pad = (n) => String(n).padStart(2, "0");
  const dayLabel = (iso) => {
    const [, m, d] = iso.split("-").map(Number);
    return `${d} ${MONTHS[m - 1]}`;
  };
  const slotLabel = (i) => `${pad(Math.floor(i / 2))}:${i % 2 ? "30" : "00"}`;

  class SunsynkOptimizerHeatmap extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._kind = "import";
      this._data = null;
      this._sig = null;
      this._w = 720;
      this._built = false;
    }

    static getStubConfig() {
      return {};
    }

    setConfig(config) {
      if (!config || typeof config !== "object") throw new Error("Invalid configuration");
      this._cfg = { title: "Import by half-hour", days: 28, day_chart_path: "day-chart", ...config };
      this._kind = KINDS.some((k) => k[0] === this._cfg.kind) ? this._cfg.kind : "import";
      this._built = false;
      this._sig = null;
      if (this._hass) this._start();
    }

    getCardSize() {
      return 8;
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first && this._cfg) this._start();
    }

    connectedCallback() {
      this._timer = setInterval(() => this._load(), REFRESH_MS);
    }

    disconnectedCallback() {
      clearInterval(this._timer);
      if (this._ro) this._ro.disconnect();
    }

    _start() {
      this._build();
      this._load();
    }

    _build() {
      const r = this.shadowRoot;
      r.innerHTML = `<style>${CSS}</style><ha-card>
        <div class="head"><div class="title"></div><div class="tabs"></div></div>
        <div class="note"></div>
        <svg role="img"></svg>
      </ha-card>`;
      r.querySelector(".title").textContent = this._cfg.title;
      this._note = r.querySelector(".note");
      this._svg = r.querySelector("svg");
      const tabs = r.querySelector(".tabs");
      this._tabs = {};
      for (const [key, label] of KINDS) {
        const b = document.createElement("button");
        b.textContent = label;
        b.setAttribute("aria-pressed", String(key === this._kind));
        b.onclick = () => {
          this._kind = key;
          for (const [k, el] of Object.entries(this._tabs)) el.setAttribute("aria-pressed", String(k === key));
          this._sig = null;
          this._load();
        };
        tabs.appendChild(b);
        this._tabs[key] = b;
      }
      if (typeof ResizeObserver !== "undefined") {
        this._ro = new ResizeObserver(() => {
          const w = Math.round(this._svg.getBoundingClientRect().width);
          if (w > 100 && Math.abs(w - this._w) > 2) {
            this._w = w;
            this._draw();
          }
        });
        this._ro.observe(this._svg);
      }
      this._built = true;
    }

    async _load() {
      if (!this._hass || !this._built) return;
      const kind = this._kind;
      let data;
      try {
        data = await this._hass.callWS({ type: "sunsynk_optimizer/heatmap", kind, days: this._cfg.days });
      } catch (err) {
        this._note.textContent = "Could not load the heat map data.";
        return;
      }
      if (kind !== this._kind) return; // the user switched while this was loading
      const sig = JSON.stringify(data);
      if (sig === this._sig) return; // nothing changed: no redraw
      this._sig = sig;
      this._data = data;
      this._draw();
    }

    _colour() {
      return (KINDS.find((k) => k[0] === this._kind) || KINDS[0])[2];
    }

    _draw() {
      const d = this._data;
      if (!d || !this._svg) return;
      const rows = d.days.length;
      const cw = (this._w - PL - PR) / 48;
      const h = PT + rows * CELL_H + PB;
      const have = d.days.filter((x) => x.kwh).length;
      this._svg.setAttribute("viewBox", `0 0 ${this._w} ${h}`);
      this._svg.setAttribute("height", h);
      this._svg.setAttribute("aria-label", `${this._cfg.title}: ${have} of ${rows} days with data`);
      if (!have) {
        this._svg.innerHTML = `<text class="msg" x="${this._w / 2}" y="${h / 2}">No half-hourly ${this._kind} data logged yet</text>`;
        this._note.textContent = "";
        return;
      }
      const col = this._colour();
      const top = d.max_kwh > 0 ? d.max_kwh : 1;
      let out = "";
      for (let hr = 0; hr < 24; hr += 3) {
        out += `<text class="ax" x="${PL + hr * 2 * cw}" y="12">${pad(hr)}:00</text>`;
      }
      d.days.forEach((day, r) => {
        const y = PT + r * CELL_H;
        out += `<text class="ax" x="${PL - 6}" y="${y + CELL_H - 4}" text-anchor="end">${dayLabel(day.date)}</text>`;
        if (!day.kwh) {
          out += `<rect class="none" x="${PL}" y="${y}" width="${cw * 48}" height="${CELL_H - 1}"><title>${dayLabel(day.date)}: no data</title></rect>`;
          return;
        }
        day.kwh.forEach((v, i) => {
          const a = (0.06 + 0.94 * Math.min(v / top, 1)).toFixed(2);
          out += `<rect class="cell" data-d="${day.date}" x="${(PL + i * cw).toFixed(1)}" y="${y}" width="${(cw - 0.6).toFixed(1)}" height="${CELL_H - 1}" fill="${col}" fill-opacity="${a}"><title>${dayLabel(day.date)} ${slotLabel(i)}: ${v.toFixed(2)} kWh</title></rect>`;
        });
      });
      const edges = new Set();
      for (const [s, e] of d.bands || []) {
        edges.add(s);
        edges.add(e % 1440);
      }
      for (const m of edges) {
        const x = (PL + (m / 30) * cw).toFixed(1);
        out += `<line class="edge" x1="${x}" x2="${x}" y1="${PT}" y2="${h - PB}"/>`;
      }
      this._svg.innerHTML = out;
      this._svg.onclick = (ev) => {
        const day = ev.target && ev.target.dataset && ev.target.dataset.d;
        if (day) this._open(day);
      };
      const bandNote = edges.size ? " Dashed lines mark your tariff price-band edges." : "";
      this._note.textContent = `Darkest = ${top.toFixed(2)} kWh or more in a half-hour (95th percentile of the days shown). Tap a cell to open that day.${bandNote}`;
    }

    _open(day) {
      const base = (location.pathname.split("/")[1] || "lovelace");
      history.pushState(null, "", `/${base}/${this._cfg.day_chart_path}?date=${day}`);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    }
  }

  if (!customElements.get(TAG)) customElements.define(TAG, SunsynkOptimizerHeatmap);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: TAG, name: "Sunsynk Optimizer heat map", description: "Import by half-hour over the last weeks." });
})();
