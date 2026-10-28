/*
 * TBM Live card — prochains passages, dans l'esprit du thème Fluvy.
 *
 * Intégration communautaire non officielle (https://github.com/itsNathanB/ha-tbm-live).
 * Sans dépendance ni étape de build : servie telle quelle par l'intégration.
 *
 * Utilise les variables --fluvy-* quand le thème Fluvy est actif, sinon les
 * variables standard de Home Assistant : la carte suit donc n'importe quel thème.
 */

const CARD_VERSION = "0.3.0";
const DOMAIN = "tbm_live";
const GRACE_MS = 30 * 1000;
const MAX_COUNT = 5;

const I18N = {
  fr: {
    name: "TBM Live",
    description: "Prochains passages des trams et bus de Bordeaux (intégration non officielle).",
    min: "min",
    due: "À l'approche",
    realtime: "Temps réel",
    scheduled: "Horaire théorique",
    late: (m) => `+${m} min`,
    early: (m) => `−${m} min`,
    none: "Aucun passage prévu",
    unavailable: "Indisponible",
    missing: (e) => `Entité introuvable : ${e}`,
    then: "Puis",
    title: "Titre (facultatif)",
    entities: "Arrêts suivis (capteur « Prochain passage »)",
    count: "Passages affichés (vide = réglage de l'intégration)",
    show_status: "Afficher « Temps réel / théorique »",
    compact: "Mode compact",
  },
  en: {
    name: "TBM Live",
    description: "Upcoming Bordeaux tram and bus departures (unofficial integration).",
    min: "min",
    due: "Due",
    realtime: "Real time",
    scheduled: "Scheduled",
    late: (m) => `+${m} min`,
    early: (m) => `−${m} min`,
    none: "No upcoming departure",
    unavailable: "Unavailable",
    missing: (e) => `Entity not found: ${e}`,
    then: "Then",
    title: "Title (optional)",
    entities: "Followed stops (\"Next departure\" sensor)",
    count: "Departures shown (empty = integration setting)",
    show_status: "Show \"Real time / scheduled\"",
    compact: "Compact mode",
  },
};

const t = (hass) => {
  const lang = (hass?.locale?.language || hass?.language || "fr").slice(0, 2);
  return I18N[lang] || I18N.en;
};

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/** Normalise `entities` : chaînes ou objets { entity, name, color }. */
const normalizeRows = (entities) =>
  (entities || [])
    .map((item) => (typeof item === "string" ? { entity: item } : { ...item }))
    .filter((row) => row && row.entity);

/** Trouve le capteur qui porte la liste des passages (même appareil). */
function resolveMainEntity(hass, entityId) {
  const state = hass.states[entityId];
  if (state?.attributes?.departures) return entityId;
  const deviceId = hass.entities?.[entityId]?.device_id;
  if (!deviceId) return entityId;
  const sibling = Object.values(hass.entities).find(
    (e) => e.device_id === deviceId && hass.states[e.entity_id]?.attributes?.departures,
  );
  return sibling ? sibling.entity_id : entityId;
}

function findTbmEntities(hass) {
  return Object.keys(hass.states).filter((id) => {
    if (!id.startsWith("sensor.")) return false;
    const attrs = hass.states[id].attributes || {};
    const platform = hass.entities?.[id]?.platform;
    return Array.isArray(attrs.departures) && (platform === DOMAIN || attrs.line_ref);
  });
}

const STYLE = `
  :host {
    --tl-text: var(--fluvy-text, var(--primary-text-color));
    --tl-text-2: var(--fluvy-text-secondary, var(--secondary-text-color));
    --tl-accent: var(--fluvy-accent, var(--primary-color));
    --tl-accent-fill: var(--fluvy-accent-fill, color-mix(in srgb, var(--primary-color) 15%, transparent));
    --tl-on-accent-fill: var(--fluvy-accent-on-fill, var(--primary-color));
    --tl-chip: var(--fluvy-page-alt, color-mix(in srgb, var(--primary-text-color) 7%, transparent));
    --tl-chip-text: var(--fluvy-text, var(--primary-text-color));
    --tl-border: var(--fluvy-border, var(--divider-color, rgba(127, 127, 127, 0.2)));
    --tl-success: var(--fluvy-success, var(--success-color, #2e7d32));
    --tl-warning: var(--fluvy-warning, var(--warning-color, #b26a00));
    --tl-warning-fill: var(--fluvy-warning-fill, color-mix(in srgb, var(--warning-color, #b26a00) 16%, transparent));
    --tl-radius-control: var(--fluvy-radius-control, 12px);
    --tl-pad: var(--fluvy-pad-comfortable, 16px);
    --tl-gap: var(--fluvy-gap-comfortable, 12px);
    --tl-font: var(--fluvy-font-sans, var(--ha-font-family-body, var(--primary-font-family, inherit)));
    display: block;
  }
  ha-card { overflow: hidden; }
  .card {
    font-family: var(--tl-font);
    color: var(--tl-text);
    padding: var(--tl-pad);
    display: flex;
    flex-direction: column;
    gap: var(--tl-gap);
  }
  .title {
    font-size: var(--fluvy-text-l, 16px);
    font-weight: var(--fluvy-weight-semibold, 600);
    line-height: 1.2;
  }
  .row {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr) auto;
    column-gap: var(--tl-gap);
    row-gap: 10px;
    align-items: center;
    cursor: pointer;
    border-radius: var(--tl-radius-control);
    -webkit-tap-highlight-color: transparent;
  }
  .row + .row {
    border-top: 1px solid var(--tl-border);
    padding-top: var(--tl-gap);
  }
  .row:focus-visible { outline: 2px solid var(--tl-accent); outline-offset: 4px; }
  .badge {
    width: 40px; height: 40px;
    border-radius: var(--tl-radius-control);
    display: grid; place-items: center;
    background: var(--tl-chip);
    color: var(--tl-chip-text);
    font-weight: var(--fluvy-weight-bold, 700);
    font-size: 17px;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }
  .badge.tram { background: var(--tl-accent-fill); color: var(--tl-on-accent-fill); }
  .badge.custom { background: var(--line-color); color: var(--line-text, #fff); }
  .badge.long { font-size: 13px; }
  .info { min-width: 0; }
  .stop {
    font-size: var(--fluvy-text-m, 14px);
    font-weight: var(--fluvy-weight-semibold, 600);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .dir {
    margin-top: 2px;
    font-size: var(--fluvy-text-s, 12px);
    color: var(--tl-text-2);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .eta { text-align: right; }
  .eta-main { white-space: nowrap; line-height: 1; }
  .num {
    font-size: var(--fluvy-text-3xl, 28px);
    font-weight: var(--fluvy-weight-semibold, 600);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.03em;
  }
  .unit { font-size: var(--fluvy-text-s, 12px); color: var(--tl-text-2); margin-left: 3px; }
  .due { font-size: var(--fluvy-text-m, 14px); font-weight: var(--fluvy-weight-semibold, 600); color: var(--tl-accent); }
  .eta-sub {
    margin-top: 5px;
    font-size: var(--fluvy-text-xs, 11px);
    color: var(--tl-text-2);
    display: inline-flex; align-items: center; gap: 5px;
    white-space: nowrap;
  }
  .delay {
    margin-left: 2px; padding: 1px 6px;
    border-radius: var(--fluvy-radius-pill, 999px);
    background: var(--tl-warning-fill); color: var(--tl-warning);
    font-weight: var(--fluvy-weight-semibold, 600);
  }
  .dot {
    width: 7px; height: 7px; border-radius: 50%;
    flex: none;
    box-sizing: border-box;
    border: 1.5px solid var(--tl-text-2);
  }
  .dot.rt { border: none; background: var(--tl-success); animation: pulse 2.4s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
  @media (prefers-reduced-motion: reduce) { .dot.rt { animation: none; } }
  .next {
    grid-column: 2 / -1;
    display: flex; flex-wrap: wrap; gap: 6px;
  }
  .chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 10px;
    border-radius: var(--fluvy-radius-pill, 999px);
    background: var(--tl-chip);
    color: var(--tl-chip-text);
    font-size: var(--fluvy-text-s, 12px);
    font-weight: var(--fluvy-weight-medium, 500);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .list {
    grid-column: 2 / -1;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    column-gap: 10px; row-gap: 4px;
    font-size: var(--fluvy-text-s, 12px);
    font-variant-numeric: tabular-nums;
  }
  .list .when {
    display: inline-flex; align-items: center; gap: 6px;
    font-weight: var(--fluvy-weight-medium, 500);
    white-space: nowrap;
  }
  .list .when .dot { width: 6px; height: 6px; }
  .list .dest { color: var(--tl-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip .dot { width: 6px; height: 6px; }
  .empty { font-size: var(--fluvy-text-s, 12px); color: var(--tl-text-2); text-align: right; }
  .warning { font-size: var(--fluvy-text-s, 12px); color: var(--tl-warning); }

  /* Mode compact : une ligne par arrêt, les passages suivants en petit. */
  .compact .row { grid-template-columns: 32px minmax(0, 1fr) auto; }
  .compact .badge { width: 32px; height: 32px; font-size: 14px; border-radius: 10px; }
  .compact .badge.long { font-size: 11px; }
  .compact .num { font-size: var(--fluvy-text-xl, 20px); }
  .compact .then { margin-top: 3px; font-size: var(--fluvy-text-xs, 11px); color: var(--tl-text-2); white-space: nowrap; font-variant-numeric: tabular-nums; }
`;

class TbmLiveCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("tbm-live-card-editor");
  }

  static getStubConfig(hass) {
    return { entities: findTbmEntities(hass).slice(0, 2) };
  }

  setConfig(config) {
    if (!config || !Array.isArray(config.entities) || !normalizeRows(config.entities).length) {
      throw new Error("tbm-live-card : renseignez au moins une entité dans « entities ».");
    }
    if (config.count !== undefined && config.count !== null && config.count !== "") {
      const count = Number(config.count);
      if (!Number.isInteger(count) || count < 1 || count > MAX_COUNT) {
        throw new Error(`tbm-live-card : « count » doit être compris entre 1 et ${MAX_COUNT}.`);
      }
    }
    this._config = { show_status: true, ...config };
    this._rows = normalizeRows(config.entities);
    this._lastKey = undefined;
    this._render(true);
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // Le temps restant est recalculé localement, sans attendre l'API.
    this._timer = window.setInterval(() => this._render(true), 15000);
    this._render(true);
  }

  disconnectedCallback() {
    window.clearInterval(this._timer);
  }

  getCardSize() {
    const rows = this._rows?.length || 1;
    return (this._config?.title ? 1 : 0) + rows * (this._config?.compact ? 1 : 2);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _states() {
    return this._rows.map((row) => {
      const main = resolveMainEntity(this._hass, row.entity);
      return { row, main, state: this._hass.states[main] };
    });
  }

  _render(force = false) {
    if (!this._hass || !this._config) return;
    const items = this._states();
    // Ne redessine que si une entité suivie a changé (ou au tic d'horloge).
    const key = items.map((i) => i.state?.last_updated).join("|") + JSON.stringify(this._config);
    if (!force && key === this._lastKey) return;
    this._lastKey = key;

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
      this.shadowRoot.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") this._onClick(ev);
      });
    }

    const i18n = t(this._hass);
    const now = Date.now();
    const title = this._config.title
      ? `<div class="title">${esc(this._config.title)}</div>`
      : "";
    const rows = items.map((item) => this._renderRow(item, i18n, now)).join("");

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="card ${this._config.compact ? "compact" : ""}">${title}${rows}</div>
      </ha-card>`;
  }

  _renderRow({ row, main, state }, i18n, now) {
    if (!state) {
      return `<div class="row"><div class="badge">?</div><div class="info warning">${esc(i18n.missing(row.entity))}</div></div>`;
    }
    const a = state.attributes || {};
    const code = String(row.code || a.line_code || "").slice(0, 4) || "•";
    const mode = a.mode === "tram" ? "tram" : "bus";
    const badgeStyle = row.color
      ? ` style="--line-color:${esc(row.color)};${row.text_color ? `--line-text:${esc(row.text_color)}` : ""}"`
      : "";
    const badgeClass = `badge ${row.color ? "custom" : mode}${code.length > 2 ? " long" : ""}`;
    const name = row.name || a.stop || state.attributes.friendly_name || main;

    const limit = Math.min(Number(this._config.count) || a.count || MAX_COUNT, MAX_COUNT);
    const departures = (Array.isArray(a.departures) ? a.departures : [])
      .filter((d) => !d.cancelled && Date.parse(d.time) >= now - GRACE_MS)
      .sort((x, y) => Date.parse(x.time) - Date.parse(y.time))
      .slice(0, limit);

    const unavailable = state.state === "unavailable";
    const first = departures[0];
    const direction = first?.destination || a.direction || "";
    const branches = new Set(departures.map((d) => d.destination).filter(Boolean)).size > 1;

    let eta;
    if (unavailable) {
      eta = `<div class="eta empty">${esc(i18n.unavailable)}</div>`;
    } else if (!first) {
      eta = `<div class="eta empty">${esc(i18n.none)}</div>`;
    } else {
      const sub = this._config.compact
        ? this._then(departures.slice(1), i18n, now)
        : this._status(first, i18n);
      eta = `<div class="eta">${this._etaMain(first, i18n, now)}${sub}</div>`;
    }

    const rest = departures.slice(1);
    let next = "";
    if (rest.length && branches) {
      // Lignes à branches : liste alignée heure + terminus.
      next = `<div class="list">${rest
        .map(
          (d) => `<span class="when"><span class="dot ${d.realtime ? "rt" : ""}"></span>${this._etaText(d, i18n, now)}</span><span class="dest">${esc(d.destination || "")}</span>`,
        )
        .join("")}</div>`;
    } else if (rest.length) {
      next = `<div class="next">${rest
        .map(
          (d) => `<span class="chip"><span class="dot ${d.realtime ? "rt" : ""}"></span>${this._etaText(d, i18n, now)}</span>`,
        )
        .join("")}</div>`;
    }

    return `
      <div class="row" role="button" tabindex="0" data-entity="${esc(main)}"
           aria-label="${esc(`${a.line || code} ${name} ${direction}`)}">
        <div class="${badgeClass}"${badgeStyle}>${esc(code)}</div>
        <div class="info">
          <div class="stop">${esc(name)}</div>
          <div class="dir">→ ${esc(direction)}</div>
        </div>
        ${eta}
        ${this._config.compact ? "" : next}
      </div>`;
  }

  _minutes(d, now) {
    return Math.floor((Date.parse(d.time) - now) / 60000);
  }

  _clock(d) {
    const lang = this._hass?.locale?.language || "fr";
    return new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(new Date(d.time));
  }

  _etaMain(d, i18n, now) {
    const m = this._minutes(d, now);
    if (m < 1) return `<div class="eta-main"><span class="due">${esc(i18n.due)}</span></div>`;
    if (m >= 60) return `<div class="eta-main"><span class="num">${esc(this._clock(d))}</span></div>`;
    return `<div class="eta-main"><span class="num">${m}</span><span class="unit">${esc(i18n.min)}</span></div>`;
  }

  _etaText(d, i18n, now) {
    const m = this._minutes(d, now);
    if (m < 1) return esc(i18n.due);
    if (m >= 60) return esc(this._clock(d));
    return `${m} ${esc(i18n.min)}`;
  }

  _then(rest, i18n, now) {
    if (!rest.length) return "";
    const parts = rest.map((d) => {
      const m = this._minutes(d, now);
      return m >= 60 ? this._clock(d) : String(Math.max(m, 0));
    });
    const unit = rest.every((d) => this._minutes(d, now) >= 60) ? "" : ` ${i18n.min}`;
    return `<div class="then">${esc(i18n.then)} ${esc(parts.join(" · "))}${esc(unit)}</div>`;
  }

  _status(d, i18n) {
    if (this._config.show_status === false) return "";
    const delay = Number(d.delay_minutes);
    const delayChip =
      d.realtime && delay >= 2
        ? `<span class="delay">${esc(i18n.late(delay))}</span>`
        : d.realtime && delay <= -2
          ? `<span class="delay">${esc(i18n.early(-delay))}</span>`
          : "";
    return `<div class="eta-sub"><span class="dot ${d.realtime ? "rt" : ""}"></span>${esc(
      d.realtime ? i18n.realtime : i18n.scheduled,
    )}${delayChip}</div>`;
  }

  _onClick(ev) {
    const row = ev.composedPath().find((el) => el.classList?.contains("row"));
    const entityId = row?.dataset?.entity;
    if (!entityId) return;
    ev.preventDefault();
    this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }),
    );
  }
}

class TbmLiveCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._render();
  }

  _schema() {
    return [
      { name: "title", selector: { text: {} } },
      {
        name: "entities",
        selector: { entity: { multiple: true, filter: { integration: DOMAIN, domain: "sensor" } } },
      },
      {
        type: "grid",
        name: "",
        schema: [
          { name: "count", selector: { number: { min: 1, max: MAX_COUNT, mode: "box" } } },
          { name: "show_status", selector: { boolean: {} } },
          { name: "compact", selector: { boolean: {} } },
        ],
      },
    ];
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => t(this._hass)[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => this._changed(ev));
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema();
    this._form.data = {
      show_status: true,
      ...this._config,
      entities: normalizeRows(this._config.entities).map((r) => r.entity),
    };
  }

  _changed(ev) {
    ev.stopPropagation();
    const value = { ...ev.detail.value };
    // Conserve les réglages par entité (name, color...) saisis en YAML.
    const previous = Object.fromEntries(
      normalizeRows(this._config.entities).map((r) => [r.entity, r]),
    );
    value.entities = (value.entities || []).map((id) => {
      const row = previous[id];
      return row && Object.keys(row).length > 1 ? row : id;
    });
    for (const key of ["title", "count"]) {
      if (value[key] === "" || value[key] === undefined || value[key] === null) delete value[key];
    }
    this._config = value;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: value }, bubbles: true, composed: true }));
  }
}

if (!customElements.get("tbm-live-card")) {
  customElements.define("tbm-live-card", TbmLiveCard);
  customElements.define("tbm-live-card-editor", TbmLiveCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "tbm-live-card",
    name: "TBM Live",
    description: I18N.fr.description,
    preview: true,
    documentationURL: "https://github.com/itsNathanB/ha-tbm-live",
  });
  console.info(`%c TBM-LIVE-CARD %c ${CARD_VERSION} `, "background:#7E1A3B;color:#fff;border-radius:3px 0 0 3px", "background:#F4C95D;color:#33270b;border-radius:0 3px 3px 0");
}
