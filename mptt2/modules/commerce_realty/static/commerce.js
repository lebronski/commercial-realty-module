/* ============================================================
   Модуль «Коммерческая недвижимость»
   Всё внутри IIFE — ни одного глобального имени, чтобы не пересечься
   со скриптами основного проекта. Адреса API строятся от data-base,
   поэтому префикс URL модуля можно менять без правки скрипта.
   ============================================================ */
(function () {
'use strict';

const BASE = document.getElementById('cm-root').dataset.base.replace(/\/$/, '') + '/';

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

const $ = id => document.getElementById(id);

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

const fmtInt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const fmt1 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function money(v) {
  if (v == null) return '—';
  if (v >= 1e6) return fmt1.format(v / 1e6) + ' млн ₽';
  if (v >= 1e3) return fmtInt.format(v / 1e3) + ' тыс. ₽';
  return fmtInt.format(v) + ' ₽';
}

const METRICS = {
  liquidity: { title: 'Ликвидность, 0–100', get: o => o.liquidity * 100, fmt: v => fmtInt.format(v) },
  cap_rate:  { title: 'Доходность, % годовых', get: o => o.cap_rate * 100, fmt: v => fmt1.format(v) + ' %' },
  payback:   { title: 'Окупаемость, лет', get: o => o.payback, fmt: v => fmt1.format(v) },
  price_m2:  { title: 'Цена покупки, ₽/м²', get: o => o.price_m2, fmt: v => fmtInt.format(v) },
  rent_m2:   { title: 'Аренда, ₽/м² в мес.', get: o => o.rent_m2, fmt: v => fmtInt.format(v) },
  noi_year:  { title: 'Чистый доход, ₽/год', get: o => o.noi_year, fmt: v => money(v) },
  count:     { title: 'Число объектов в ячейке', get: () => 1, fmt: v => fmtInt.format(v) },
};

// Компактная таблица: в ячейке основное значение и под ним — уточнение.
// Клик по заголовку сортирует по основному значению; по любому полю —
// через список «Сортировка».
const sub = t => `<span class="cm-sub">${t}</span>`;

function addressHtml(o) {
  if (!o.address) return '<span class="cm-dim">адрес не найден</span>';
  const a = esc(o.address);
  if (o.address_src === 'street')
    return `${a} <span class="cm-dim" title="Номер дома не найден — показана ближайшая улица">(без номера)</span>`;
  const guessed = (o.address_src || '').endsWith('+street')
    ? ' <span class="cm-dim" title="В OSM у дома указан только номер — улица подставлена ближайшая">(улица по соседству)</span>' : '';
  if ((o.address_src || '').startsWith('nearby'))
    return `<span class="cm-dim" title="В OSM у объекта нет адреса — показан адрес ближайшего дома">рядом с</span> ${a}${guessed}`;
  return a + guessed;
}

function placeLine(o) {
  const parts = [esc(o.settlement || '')];
  if (o.dist_center_km != null) parts.push(`${fmt1.format(o.dist_center_km)} км до центра`);
  return parts.filter(Boolean).join(' · ');
}

const COLUMNS = [
  { key: 'name', title: 'Объект', cls: 'wrap cm-w-obj',
    render: o => (o.name ? `<b>${esc(o.name)}</b>` : '<span class="cm-dim">без названия</span>')
      + (o.vacant ? ' <span class="cm-badge">свободно</span>' : '')
      + sub(`${esc(state.categories[o.category] || o.category)} · ${esc(o.subtype)} · ${esc(o.kind)}`) },
  { key: 'address', title: 'Адрес', cls: 'wrap cm-w-addr',
    render: o => addressHtml(o) + sub(placeLine(o)) },
  { key: 'area', title: 'Площадь', num: true,
    render: o => (o.area_estimated ? '<span title="Типовая площадь: у объекта в OSM нет контура">≈ </span>' : '')
      + fmtInt.format(o.area) + ' м²' + sub(o.levels ? `${o.levels} эт.` : '&nbsp;') },
  { key: 'liquidity', title: 'Ликвидность', num: true, render: o => liqBar(o.liquidity) },
  { key: 'price', title: 'Цена покупки', subtitle: '₽/м²', num: true,
    render: o => money(o.price) + listingBadge(o) + sub(`${fmtInt.format(o.price_m2)} ₽/м²`) },
  { key: 'rent_month', title: 'Аренда в мес.', subtitle: '₽/м² · заполняемость', num: true,
    render: o => money(o.rent_month) + sub(`${fmtInt.format(o.rent_m2)} ₽/м² · ${fmtInt.format(o.occupancy * 100)} %`) },
  { key: 'noi_year', title: 'Доход в год', subtitle: 'доходность', num: true,
    render: o => money(o.noi_year) + sub(`${fmt1.format(o.cap_rate * 100)} % годовых`) },
  { key: 'payback', title: 'Окупаемость', subtitle: 'срок продажи', num: true,
    render: o => (o.payback == null ? '—' : fmt1.format(o.payback) + ' лет') + sub(`продажа ≈ ${o.exposure_months} мес.`) },
];

const SORT_FIELDS = [
  ['cap_rate', 'Доходность'], ['payback', 'Окупаемость'], ['liquidity', 'Ликвидность'],
  ['price', 'Цена покупки'], ['price_m2', 'Цена за м²'], ['rent_month', 'Аренда в месяц'],
  ['rent_m2', 'Аренда за м²'], ['noi_year', 'Чистый доход в год'], ['occupancy', 'Заполняемость'],
  ['exposure_months', 'Срок продажи'], ['area', 'Площадь'], ['levels', 'Этажность'],
  ['dist_center_km', 'Расстояние до центра'], ['name', 'Название'], ['category', 'Категория'],
  ['settlement', 'Населённый пункт'], ['address', 'Адрес'],
];

const PAGE_SIZE = 50;
const CELL_PX = 40;
const POINTS_MIN_ZOOM = 14;
const MAX_POINTS = 2500;
const RAMP = ['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026'];

const state = {
  all: [], filtered: [], categories: {},
  sortKey: 'cap_rate', sortDir: -1, page: 0,
  scale: { min: 0, max: 1 },
};

let map, heatLayer, pointLayer, canvas;


/* ============================================================
   Цвета
   ============================================================ */

function ramp(t) {
  t = Math.max(0, Math.min(1, t));
  const x = t * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const a = parseInt(RAMP[i].slice(1), 16), b = parseInt(RAMP[i + 1].slice(1), 16);
  const ch = s => Math.round(((a >> s) & 255) + (((b >> s) & 255) - ((a >> s) & 255)) * f);
  return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
}

function liqBar(v) {
  const p = Math.round(v * 100);
  return `<span class="cm-bar"><i style="width:${p}%;background:${ramp(v)}"></i></span>${p}`;
}

function norm(v) {
  const { min, max } = state.scale;
  return max > min ? (v - min) / (max - min) : 0.5;
}


/* ============================================================
   Загрузка данных
   ============================================================ */

async function loadObjects() {
  const d = await api(BASE + 'objects');
  state.categories = d.categories;
  state.all = d.rows.map(r => {
    const o = {};
    d.columns.forEach((c, i) => { o[c] = r[i]; });
    o._search = `${o.name} ${o.address} ${o.subtype} ${o.settlement}`.toLowerCase();
    return o;
  });
  fillFilterOptions();
  applyFilters();
  if (state.all.length) {
    const lats = state.all.map(o => o.lat), lons = state.all.map(o => o.lon);
    map.fitBounds([[Math.min(...lats), Math.min(...lons)],
                   [Math.max(...lats), Math.max(...lons)]]);
  } else {
    $('cm-settings-card').open = true;
  }
}

function fillFilterOptions() {
  const cat = $('cm-f-category');
  cat.length = 1;
  for (const [k, v] of Object.entries(state.categories)) cat.add(new Option(v, k));

  const counts = {};
  for (const o of state.all) counts[o.settlement] = (counts[o.settlement] || 0) + 1;
  const sel = $('cm-f-settlement');
  sel.length = 1;
  Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 200)
    .forEach(([name, n]) => { if (name) sel.add(new Option(`${name} (${n})`, name)); });
}


/* ============================================================
   Фильтры, сортировка, сводка
   ============================================================ */

function numVal(id) {
  const v = parseFloat($(id).value);
  return Number.isFinite(v) ? v : null;
}

function applyFilters() {
  const cat = $('cm-f-category').value, kind = $('cm-f-kind').value;
  const st = $('cm-f-settlement').value;
  const q = $('cm-f-search').value.trim().toLowerCase();
  const aMin = numVal('cm-f-area-min'), aMax = numVal('cm-f-area-max');
  const pMin = numVal('cm-f-price-min'), pMax = numVal('cm-f-price-max');
  const liq = numVal('cm-f-liq'), pb = numVal('cm-f-payback');

  state.filtered = state.all.filter(o =>
    (!cat || o.category === cat) &&
    (!kind || o.kind === kind) &&
    (!st || o.settlement === st) &&
    (!q || o._search.includes(q)) &&
    (aMin == null || o.area >= aMin) && (aMax == null || o.area <= aMax) &&
    (pMin == null || o.price >= pMin * 1e6) && (pMax == null || o.price <= pMax * 1e6) &&
    (liq == null || o.liquidity * 100 >= liq) &&
    (pb == null || (o.payback != null && o.payback <= pb)));

  state.page = 0;
  sortFiltered();
  renderSummary();
  renderTable();
  updateScale();
  redrawMap();
}

function sortFiltered() {
  const k = state.sortKey, dir = state.sortDir;
  state.filtered.sort((a, b) => {
    const x = a[k], y = b[k];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === 'string') return dir * x.localeCompare(y, 'ru');
    return dir * (x - y);
  });
}

function median(arr) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

function renderSummary() {
  const f = state.filtered;
  const tiles = [
    [fmtInt.format(f.length), 'объектов в выборке'],
    [f.length ? fmtInt.format(median(f.map(o => o.liquidity * 100))) : '—', 'медианная ликвидность'],
    [f.length ? money(median(f.map(o => o.price))) : '—', 'медианная цена покупки'],
    [f.length ? money(median(f.map(o => o.noi_year))) : '—', 'медианный чистый доход в год'],
    [f.length ? fmt1.format(median(f.map(o => o.cap_rate * 100))) + ' %' : '—', 'медианная доходность'],
    [f.length ? fmt1.format(median(f.filter(o => o.payback != null).map(o => o.payback))) : '—', 'медианная окупаемость, лет'],
  ];
  $('cm-summary').innerHTML = tiles.map(([v, l]) =>
    `<div class="cm-tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
}


/* ============================================================
   Таблица
   ============================================================ */

function renderHead() {
  $('cm-head').innerHTML = COLUMNS.map(c => {
    const sorted = c.key === state.sortKey;
    const arrow = sorted ? (state.sortDir > 0 ? ' ▲' : ' ▼') : '';
    return `<th data-key="${c.key}" class="${c.num ? 'num' : ''} ${sorted ? 'sorted' : ''}">`
      + `${c.title}${arrow}${c.subtitle ? sub(c.subtitle) : ''}</th>`;
  }).join('');
  $('cm-sort').value = state.sortKey;
  $('cm-sort-dir').textContent = state.sortDir > 0 ? '▲ по возрастанию' : '▼ по убыванию';
}

function setSort(key, dir) {
  state.sortKey = key;
  state.sortDir = dir ?? (typeof (state.all[0] || {})[key] === 'string' ? 1 : -1);
  sortFiltered();
  state.page = 0;
  renderTable();
}

function renderTable() {
  renderHead();
  const total = state.filtered.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  state.page = Math.min(state.page, pages - 1);
  const rows = state.filtered.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);

  $('cm-body').innerHTML = rows.length
    ? rows.map(o => `<tr data-id="${o.id}">` + COLUMNS.map(c =>
        `<td class="${c.num ? 'num' : ''} ${c.cls || ''}">${c.render(o)}</td>`).join('') + '</tr>').join('')
    : `<tr><td colspan="${COLUMNS.length}" class="empty">${state.all.length
        ? 'Нет объектов под выбранные фильтры'
        : 'Данные не загружены — откройте «Данные и параметры модели» и нажмите «Загрузить данные из Overpass»'}</td></tr>`;

  $('cm-count').textContent = `— ${fmtInt.format(total)} из ${fmtInt.format(state.all.length)}`;
  $('cm-page').textContent = `стр. ${state.page + 1} из ${pages}`;
}

$('cm-head').addEventListener('click', e => {
  const th = e.target.closest('th');
  if (!th) return;
  const key = th.dataset.key;
  setSort(key, state.sortKey === key ? -state.sortDir : undefined);
});

SORT_FIELDS.forEach(([k, t]) => $('cm-sort').add(new Option(t, k)));
$('cm-sort').addEventListener('change', e => setSort(e.target.value));
$('cm-sort-dir').addEventListener('click', () => setSort(state.sortKey, -state.sortDir));

$('cm-body').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-id]');
  if (!tr) return;
  const o = state.all.find(x => x.id === +tr.dataset.id);
  if (!o) return;
  map.setView([o.lat, o.lon], Math.max(map.getZoom(), 17));
  L.popup().setLatLng([o.lat, o.lon]).setContent(popupHtml(o)).openOn(map);
  $('cm-map').scrollIntoView({ behavior: 'smooth', block: 'center' });
});

$('cm-prev').addEventListener('click', () => { if (state.page > 0) { state.page--; renderTable(); } });
$('cm-next').addEventListener('click', () => {
  if ((state.page + 1) * PAGE_SIZE < state.filtered.length) { state.page++; renderTable(); }
});


/* ============================================================
   Карта
   ============================================================ */

function initMap() {
  map = L.map('cm-map').setView([56.6328, 47.8908], 8);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OpenStreetMap'
  }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  canvas = L.canvas({ padding: 0.2 });
  heatLayer = L.layerGroup().addTo(map);
  pointLayer = L.layerGroup().addTo(map);
  listingLayer = L.layerGroup().addTo(map);
  map.on('moveend', redrawMap);
}

function updateScale() {
  const key = $('cm-metric').value, m = METRICS[key];
  $('cm-legend-title').textContent = m.title;
  if (key === 'count') return;   // шкала считается по ячейкам при отрисовке
  const vals = state.filtered.map(m.get).filter(v => v != null).sort((a, b) => a - b);
  if (!vals.length) { state.scale = { min: 0, max: 1 }; setLegend(m); return; }
  // 5-й и 95-й процентили, чтобы выбросы не съедали шкалу
  state.scale = {
    min: vals[Math.floor(vals.length * 0.05)],
    max: vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.95))],
  };
  setLegend(m);
}

function setLegend(m) {
  $('cm-legend-min').textContent = m.fmt(state.scale.min);
  $('cm-legend-max').textContent = m.fmt(state.scale.max);
}

function popupHtml(o) {
  const row = (k, v) => `<tr><td>${k}</td><td><b>${v}</b></td></tr>`;
  const pct = v => fmtInt.format(v * 100);
  return `<div class="cm-popup">
    <h4>${esc(o.name || state.categories[o.category])}</h4>
    <table>
      ${row('Категория', esc(state.categories[o.category]) + ' · ' + esc(o.subtype))}
      ${row('Вид', esc(o.kind) + (o.vacant ? ' · свободно' : ''))}
      ${row('Адрес', [esc(o.settlement), addressHtml(o)].filter(Boolean).join(', '))}
      ${row('Площадь', (o.area_estimated ? '≈ ' : '') + fmtInt.format(o.area) + ' м²' + (o.levels ? `, этажей: ${o.levels}` : ''))}
      ${row('Цена покупки', money(o.price) + ` (${fmtInt.format(o.price_m2)} ₽/м²)`)}
      ${row('Аренда', money(o.rent_month) + ` в мес. (${fmtInt.format(o.rent_m2)} ₽/м²)`)}
      ${row('Заполняемость', pct(o.occupancy) + ' %')}
      ${row('Чистый доход', money(o.noi_year) + ' в год')}
      ${row('Доходность', fmt1.format(o.cap_rate * 100) + ' % годовых')}
      ${row('Окупаемость', o.payback == null ? '—' : fmt1.format(o.payback) + ' лет')}
      ${row('Срок продажи', '≈ ' + o.exposure_months + ' мес.')}
      ${row('Ликвидность', pct(o.liquidity) + ' из 100')}
    </table>
    <div style="font-size:11px;color:#666;margin-top:4px;">
      Факторы: нас. пункт ${pct(o.f_set)} · центр ${pct(o.f_center)} · спрос ${pct(o.f_demand)} ·
      транспорт ${pct(o.f_transport)} · дорога ${pct(o.f_road)} · коммерция ${pct(o.f_cluster)} ·
      конкуренция ${pct(o.f_comp)}
    </div>
    ${objectListingsHtml(o)}
    <a href="https://www.openstreetmap.org/${esc(o.osm_type)}/${o.osm_id}" target="_blank" rel="noopener">объект в OSM</a>
  </div>`;
}

function redrawMap() {
  if (!map) return;
  heatLayer.clearLayers();
  pointLayer.clearLayers();
  const key = $('cm-metric').value, m = METRICS[key];
  const zoom = map.getZoom();
  const b = map.getBounds().pad(0.1);
  const visible = state.filtered.filter(o => b.contains([o.lat, o.lon]));

  if ($('cm-show-heat').checked) {
    // Ячейки привязаны к мировым пикселям, поэтому не «плывут» при сдвиге карты
    const cells = new Map();
    for (const o of visible) {
      const v = m.get(o);
      if (v == null) continue;
      const p = map.project([o.lat, o.lon], zoom);
      const cx = Math.floor(p.x / CELL_PX), cy = Math.floor(p.y / CELL_PX);
      const id = cx + ':' + cy;
      let c = cells.get(id);
      if (!c) { c = { cx, cy, sum: 0, n: 0 }; cells.set(id, c); }
      c.sum += v; c.n++;
    }
    const isCount = key === 'count';
    if (isCount) {
      const mx = Math.max(1, ...[...cells.values()].map(c => c.n));
      state.scale = { min: 1, max: mx };
      setLegend(m);
    }
    const showPoints = $('cm-show-points').checked && zoom >= POINTS_MIN_ZOOM;
    for (const c of cells.values()) {
      const val = isCount ? c.n : c.sum / c.n;
      // для числа объектов шкала логарифмическая — иначе всё, кроме центра, бледное
      const t = isCount
        ? Math.log(val) / Math.log(Math.max(2, state.scale.max))
        : norm(val);
      const sw = map.unproject([c.cx * CELL_PX, (c.cy + 1) * CELL_PX], zoom);
      const ne = map.unproject([(c.cx + 1) * CELL_PX, c.cy * CELL_PX], zoom);
      L.rectangle([sw, ne], {
        renderer: canvas, stroke: false, fillColor: ramp(t),
        fillOpacity: showPoints ? 0.35 : 0.65, interactive: !showPoints,
      }).bindTooltip(`${m.title}: ${m.fmt(val)}<br>объектов: ${c.n}`, { sticky: true })
        .addTo(heatLayer);
    }
  }

  if ($('cm-show-points').checked && zoom >= POINTS_MIN_ZOOM) {
    const metric = key === 'count' ? METRICS.liquidity : m;
    for (const o of visible.slice(0, MAX_POINTS)) {
      const v = metric.get(o);
      const t = key === 'count' ? o.liquidity : (v == null ? 0 : norm(v));
      L.circleMarker([o.lat, o.lon], {
        renderer: canvas, radius: 6, color: '#2c3e50', weight: 1,
        fillColor: ramp(t), fillOpacity: 0.95,
      }).bindPopup(() => popupHtml(o), { maxWidth: 380 }).addTo(pointLayer);
    }
  }
  drawListings();
}


/* ============================================================
   Реальные объявления и калибровка
   ============================================================ */

const DEAL_TITLE = { sale: 'продажа', rent: 'аренда' };
const GEO_TITLE = {
  house: 'дом найден по адресу',
  house_approx: 'найден дом без буквы/корпуса',
  street: 'точный дом не найден — середина улицы',
};
let listingLayer = null;
const listingState = { items: [], byObject: new Map() };

async function loadListings() {
  const d = await api(BASE + 'listings');
  listingState.items = d.listings;
  listingState.byObject = new Map();
  for (const l of d.listings)
    for (const id of l.object_ids) {
      if (!listingState.byObject.has(id)) listingState.byObject.set(id, []);
      listingState.byObject.get(id).push(l);
    }
  const geo = d.listings.filter(l => l.lat != null).length;
  $('cm-l-count').textContent = d.listings.length
    ? `— ${d.listings.length}, на карте ${geo}` : '';
  $('cm-l-seed').style.display = d.has_seed ? '' : 'none';
  renderTable();
  redrawMap();
  await loadCalibration();
}

function listingPerM2(l) {
  return l.price / l.area;
}

function listingHtml(l) {
  const per = fmtInt.format(listingPerM2(l));
  const price = l.deal === 'sale'
    ? `${money(l.price)} (${per} ₽/м²)`
    : `${money(l.price)} в мес. (${per} ₽/м²)`;
  return `<div class="cm-l-item"><b>${DEAL_TITLE[l.deal]}</b> · ${esc(l.type || state.categories[l.category])}<br>`
    + `${esc(l.address)}${l.city ? ', ' + esc(l.city) : ''} · ${fmtInt.format(l.area)} м²<br>`
    + `${price}<br>`
    + `<span class="cm-dim">${esc(l.source)}${l.date ? ', ' + esc(l.date) : ''}`
    + `${l.geo ? ' · ' + GEO_TITLE[l.geo] : ''}</span>`
    + (/^https?:\/\//.test(l.url) ? ` · <a href="${esc(l.url)}" target="_blank" rel="noopener">источник</a>` : '')
    + '</div>';
}

function listingBadge(o) {
  const ls = listingState.byObject.get(o.id);
  if (!ls) return '';
  const tip = ls.map(l => `${DEAL_TITLE[l.deal]}: ${money(l.price)}, ${fmtInt.format(l.area)} м²`).join('; ');
  return ` <span class="cm-badge cm-badge-l" title="Реальное объявление по этому адресу — ${esc(tip)}">объявл.</span>`;
}

function objectListingsHtml(o) {
  const ls = listingState.byObject.get(o.id);
  if (!ls) return '';
  return `<div class="cm-l-box"><b>Реальные объявления по этому адресу</b>${ls.map(listingHtml).join('')}</div>`;
}

function drawListings() {
  if (!listingLayer) return;
  listingLayer.clearLayers();
  if (!$('cm-show-listings').checked) return;
  for (const l of listingState.items) {
    if (l.lat == null) continue;
    L.marker([l.lat, l.lon], {
      icon: L.divIcon({ className: 'cm-l-marker cm-l-' + l.deal, iconSize: [14, 14] }),
      title: `${DEAL_TITLE[l.deal]}: ${l.address}`,
    }).bindPopup(`<div class="cm-popup"><h4>Объявление</h4>${listingHtml(l)}</div>`, { maxWidth: 360 })
      .addTo(listingLayer);
  }
}

async function loadCalibration() {
  const box = $('cm-calib');
  if (!listingState.items.length) { box.innerHTML = ''; $('cm-l-apply').disabled = true; return; }
  const r = await api(BASE + 'calibration');
  const rows = [];
  for (const g of r.groups) {
    const field = g.deal === 'sale' ? 'price' : 'rent';
    const cats = Object.keys(r.categories).filter(c => PROFILE_OF[c] === g.group);
    const change = cats.map(c => {
      const before = r.current[c][field], after = r.rates[c][field];
      return `${esc(r.categories[c])}: ${fmtInt.format(before)} → <b>${fmtInt.format(after)}</b>`;
    }).join('<br>');
    rows.push(`<tr><td>${esc(g.title)}</td><td>${DEAL_TITLE[g.deal]}</td>`
      + `<td class="num">${g.n}</td>`
      + `<td class="num">${g.applied ? '×' + fmt1.format(g.ratio) : '<span class="cm-dim">мало данных</span>'}</td>`
      + `<td>${g.applied ? change : '<span class="cm-dim">ставки не меняются</span>'}</td></tr>`);
  }
  box.innerHTML = `<thead><tr><th>Группа</th><th>Сделка</th><th class="num">Объявлений</th>`
    + `<th class="num">Множитель к ставке<span class="cm-sub">по умолчанию</span></th>`
    + `<th>Ставка, ₽/м² (продажа) или ₽/м² в мес. (аренда)<span class="cm-sub">сейчас → по объявлениям</span></th></tr></thead>`
    + `<tbody>${rows.join('')}</tbody>`;
  $('cm-l-msg').textContent = `Привязано к карте ${r.n_geo} из ${r.n_total} объявлений; `
    + `в расчёт вошли ${r.used.length} (рядом есть объект с факторами). `
    + `Группа калибруется, если в ней не меньше ${r.min_per_group} объявлений.`;
  $('cm-l-apply').disabled = !r.groups.some(g => g.applied);
}

// Профиль категории — как в model.PROFILE
const PROFILE_OF = {
  retail: 'street', food: 'street', service: 'street', hotel: 'office', office: 'office',
  auto: 'logistic', warehouse: 'logistic', industrial: 'logistic',
};

function showLoadResult(r) {
  const parts = [`Добавлено объявлений: ${r.added}`];
  if (r.geocoded != null) parts.push(`привязано к карте: ${r.geocoded}`);
  if (r.warning) parts.push(r.warning);
  if (r.errors && r.errors.length) parts.push('Пропущены строки: ' + r.errors.join('; '));
  $('cm-l-msg').textContent = parts.join('. ');
}

$('cm-l-seed').addEventListener('click', async () => {
  if (listingState.items.length && !confirm('Заменить текущие объявления собранными?')) return;
  try { const r = await api(BASE + 'listings/seed', { method: 'POST' }); await loadListings(); showLoadResult(r); }
  catch (e) { alert('Ошибка: ' + e.message); }
});

$('cm-l-upload').addEventListener('click', async () => {
  const f = $('cm-l-file').files[0];
  if (!f) { alert('Выберите файл CSV'); return; }
  const fd = new FormData();
  fd.append('file', f);
  fd.append('replace', $('cm-l-replace').checked ? '1' : '0');
  try {
    const res = await fetch(BASE + 'listings/upload', { method: 'POST', body: fd });
    const r = await res.json();
    if (!res.ok) throw new Error(r.error || res.statusText);
    await loadListings();
    showLoadResult(r);
  } catch (e) { alert('Ошибка: ' + e.message); }
});

$('cm-l-clear').addEventListener('click', async () => {
  if (!confirm('Удалить все объявления? Ставки модели не изменятся.')) return;
  try { await api(BASE + 'listings/clear', { method: 'POST' }); await loadListings(); $('cm-l-msg').textContent = ''; }
  catch (e) { alert('Ошибка: ' + e.message); }
});

$('cm-l-reset').addEventListener('click', async () => {
  if (!confirm('Вернуть базовые ставки, заложенные в модуль, и пересчитать объекты?')) return;
  try {
    const r = await api(BASE + 'calibration');
    document.querySelectorAll('#cm-rates-body input').forEach(inp => {
      inp.value = r.defaults[inp.dataset.cat][inp.dataset.f];
    });
    await saveSettings();
    await api(BASE + 'recalc', { method: 'POST' });
    await loadSettings();
    await loadObjects();
    await loadCalibration();
  } catch (e) { alert('Ошибка: ' + e.message); }
});

// Ссылка вида …/modules/commerce/#listings сразу открывает этот раздел
if (location.hash === '#listings') $('cm-listings-card').open = true;

$('cm-l-apply').addEventListener('click', async () => {
  if (!confirm('Заменить базовые ставки откалиброванными и пересчитать все объекты?')) return;
  try {
    const r = await api(BASE + 'calibration/apply', { method: 'POST' });
    await loadSettings();
    await loadObjects();
    await loadCalibration();
    alert(`Ставки обновлены, пересчитано объектов: ${r.count}`);
  } catch (e) { alert('Ошибка: ' + e.message); }
});


/* ============================================================
   Настройки и загрузка
   ============================================================ */

let settings = null;

async function loadSettings() {
  settings = await api(BASE + 'settings');
  $('cm-region').value = settings.region;
  $('cm-timeout').value = settings.timeout;
  $('cm-opex').value = Math.round(settings.opex * 100);
  $('cm-servers').value = settings.servers.join('\n');
  $('cm-rates-body').innerHTML = Object.entries(settings.categories).map(([k, title]) => `
    <tr><td>${esc(title)}</td>
      <td><input type="number" min="1" data-cat="${k}" data-f="price" value="${settings.rates[k].price}"></td>
      <td><input type="number" min="1" data-cat="${k}" data-f="rent" value="${settings.rates[k].rent}"></td>
    </tr>`).join('');
}

async function saveSettings() {
  const rates = {};
  document.querySelectorAll('#cm-rates-body input').forEach(inp => {
    (rates[inp.dataset.cat] ||= {})[inp.dataset.f] = parseFloat(inp.value);
  });
  await api(BASE + 'settings', {
    method: 'POST',
    body: JSON.stringify({
      region: $('cm-region').value,
      timeout: parseInt($('cm-timeout').value) || 180,
      opex: (parseFloat($('cm-opex').value) || 0) / 100,
      rates,
      servers: $('cm-servers').value.split(/\r?\n/).map(x => x.trim()).filter(Boolean),
    })
  });
}

$('cm-save').addEventListener('click', async () => {
  try {
    await saveSettings();
    const r = await api(BASE + 'recalc', { method: 'POST' });
    await loadObjects();
    alert(`Настройки сохранены, пересчитано объектов: ${r.count}`);
  } catch (e) { alert('Ошибка: ' + e.message); }
});

$('cm-import').addEventListener('click', async () => {
  if (!confirm('Загрузить данные из Overpass? Текущие объекты будут заменены.')) return;
  try {
    await saveSettings();
    await api(BASE + 'import', { method: 'POST' });
    wasRunning = true;
    startPolling();
  } catch (e) { alert('Не удалось запустить: ' + e.message); }
});

$('cm-clear').addEventListener('click', async () => {
  if (!confirm('Удалить все загруженные коммерческие объекты?')) return;
  try {
    await api(BASE + 'clear', { method: 'POST' });
    await loadObjects();
  } catch (e) { alert('Ошибка: ' + e.message); }
});

let pollTimer = null, wasRunning = false;

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(poll, 1000);
  poll();
}

async function poll() {
  try {
    const s = await api(BASE + 'status');
    $('cm-bar').style.width = (s.total ? Math.round(100 * s.progress / s.total) : 0) + '%';
    $('cm-message').textContent = s.error ? 'Ошибка: ' + s.error
      : (s.message || (s.running ? 'работает…' : '—'));
    $('cm-counter').textContent = s.total ? `${s.progress} / ${s.total}` : '';
    const log = $('cm-log');
    log.textContent = (s.log || []).join('\n');
    log.scrollTop = log.scrollHeight;
    if (s.running) { wasRunning = true; $('cm-settings-card').open = true; }
    else {
      clearInterval(pollTimer); pollTimer = null;
      if (wasRunning) { wasRunning = false; if (!s.error) { await loadObjects(); await loadListings(); } }
    }
  } catch (_) { /* сервер недоступен — попробуем на следующем тике */ }
}


/* ============================================================
   Инициализация
   ============================================================ */

['cm-f-category', 'cm-f-kind', 'cm-f-settlement'].forEach(id =>
  $(id).addEventListener('change', applyFilters));

let filterTimer = null;
['cm-f-search', 'cm-f-area-min', 'cm-f-area-max', 'cm-f-price-min',
 'cm-f-price-max', 'cm-f-liq', 'cm-f-payback'].forEach(id =>
  $(id).addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(applyFilters, 250);
  }));

$('cm-f-reset').addEventListener('click', () => {
  document.querySelectorAll('[id^="cm-f-"]').forEach(n => { if ('value' in n) n.value = ''; });
  applyFilters();
});

$('cm-metric').addEventListener('change', () => { updateScale(); redrawMap(); });
$('cm-show-heat').addEventListener('change', redrawMap);
$('cm-show-points').addEventListener('change', redrawMap);

$('cm-show-listings').addEventListener('change', drawListings);

$('cm-export').href = BASE + 'export.csv';
$('cm-l-template').href = BASE + 'listings/template.csv';
initMap();
loadSettings().catch(e => console.error('loadSettings:', e));
loadObjects()
  .then(loadListings)
  .catch(e => console.error('loadObjects/loadListings:', e));
startPolling();

})();
