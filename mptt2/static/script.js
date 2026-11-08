/* =====================================================================
 *  Глобальное состояние
 * ===================================================================== */

const state = {
  nodes: [],
  edges: [],
  buildings: [],
  characteristics: [],
  nodeTypes: [],
  buildingTypes: [],
  mode: 'idle',
  edgeFrom: null,
  weekday: 0,
  hour: 12,
  selected: null,
};

let map = null;
let L = null;
let layers = null;

let drag = null;
let modalDragState = null;

const MAX_NODES_DRAWN     = 3000;
const MAX_EDGES_DRAWN     = 4000;
const MAX_BUILDINGS_DRAWN = 5000;

let _redrawTimer = null;


/* =====================================================================
 *  Утилиты
 * ===================================================================== */

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.status === 204 ? null : res.json();
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, m => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[m]));
}

function darken(hex, factor = 0.65) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return '#7a3a00';
  const n = parseInt(m[1], 16);
  const r = Math.round(((n >> 16) & 255) * factor);
  const g = Math.round(((n >>  8) & 255) * factor);
  const b = Math.round(( n        & 255) * factor);
  return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
}

function metersToLatLon(lat, dxM, dyM) {
  const dLat = dyM / 111320;
  const dLon = dxM / (111320 * Math.cos(lat * Math.PI / 180));
  return { dLat, dLon };
}

function angleBetween(lat1, lon1, lat2, lon2) {
  const dLon = (lon2 - lon1) * Math.cos((lat1 + lat2) / 2 * Math.PI / 180);
  const dLat = (lat2 - lat1);
  return Math.atan2(dLat, dLon) * 180 / Math.PI;
}

function buildingCorners(b) {
  const rot = (b.rotation || 0) * Math.PI / 180;
  const cos = Math.cos(rot), sin = Math.sin(rot);
  const hw = b.w / 2, hh = b.h / 2;
  const local = [
    [-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]
  ];
  return local.map(([x, y]) => {
    const rx = x * cos - y * sin;
    const ry = x * sin + y * cos;
    const { dLat, dLon } = metersToLatLon(b.lat, rx, ry);
    return [b.lat + dLat, b.lon + dLon];
  });
}

// Полностью блокировать событие мыши — и для Leaflet, и для DOM
function blockMouseEvent(ev) {
  L.DomEvent.stopPropagation(ev);
  L.DomEvent.preventDefault(ev);
  if (ev.originalEvent) {
    ev.originalEvent.stopPropagation();
    ev.originalEvent.preventDefault();
  }
}


/* =====================================================================
 *  Загрузка данных
 * ===================================================================== */

async function loadGraph() {
  const data = await api('/api/graph');

  state.nodes = (data.nodes || [])
    .map(n => ({
      id: n.id,
      name: n.name || '',
      lat: +n.lat,
      lon: +n.lon,
      type_id: n.type_id == null ? null : +n.type_id,
      osm_id: n.osm_id || null,
    }))
    .filter(n => Number.isFinite(n.lat) && Number.isFinite(n.lon));

  const nodeById = new Map(state.nodes.map(n => [n.id, n]));

  state.edges = (data.edges || [])
    .filter(e => nodeById.has(e.from_node) && nodeById.has(e.to_node))
    .map(e => ({
      id: e.id,
      from_node: e.from_node,
      to_node: e.to_node,
      name: e.name || '',
      oneway: e.oneway || 0,
      osm_id: e.osm_id || null,
    }));

  state.buildings = (data.buildings || [])
    .map(b => ({
      id: b.id,
      name: b.name || '',
      lat: +b.lat,
      lon: +b.lon,
      w: +b.w || 10,
      h: +b.h || 10,
      rotation: +b.rotation || 0,
      type_id: b.type_id == null ? null : +b.type_id,
      osm_id: b.osm_id || null,
    }))
    .filter(b => Number.isFinite(b.lat) && Number.isFinite(b.lon)
                 && b.w > 0 && b.h > 0);

  state.nodeTypes     = data.node_types || [];
  state.buildingTypes = data.building_types || [];

  console.info('[graph] узлов:', state.nodes.length,
               'рёбер:', state.edges.length,
               'зданий:', state.buildings.length);

  rebuildNodeIndex();
  redrawAll();
}

function rebuildNodeIndex() {
  state._nodeById = new Map(state.nodes.map(n => [n.id, n]));
  state._buildingById = new Map(state.buildings.map(b => [b.id, b]));
  state._edgeById = new Map(state.edges.map(e => [e.id, e]));
  state._nodeTypeById = new Map(state.nodeTypes.map(t => [t.id, t]));
  state._buildingTypeById = new Map(state.buildingTypes.map(t => [t.id, t]));
}

async function loadCharacteristics() {
  state.characteristics = await api('/api/characteristics');
}

function findNode(id)     { return state._nodeById?.get(id); }
function findBuilding(id) { return state._buildingById?.get(id); }
function findNodeType(id) { return state._nodeTypeById?.get(id); }
function findBuildingType(id) { return state._buildingTypeById?.get(id); }


/* =====================================================================
 *  Инициализация карты
 * ===================================================================== */

function initMap() {
  L = window.L;
  if (!L) {
    console.error('Leaflet не найден в window');
    return;
  }

  let mapName = null;
  for (const k of Object.keys(window)) {
    if (k.startsWith('map_') && window[k] && typeof window[k].setView === 'function') {
      mapName = k;
      break;
    }
  }

  if (!mapName) {
    console.error('Не найдена folium-карта в window');
    return;
  }

  map = window[mapName];

  map.getContainer().addEventListener('contextmenu', e => e.preventDefault());

  layers = {
    edges:     L.layerGroup().addTo(map),
    buildings: L.layerGroup().addTo(map),
    nodes:     L.layerGroup().addTo(map),
    handles:   L.layerGroup().addTo(map),
  };

  map.on('click', onMapClick);

  // Глобальные обработчики мыши на документе — чтобы drag не срывался,
  // если курсор вышел за пределы карты
  document.addEventListener('mousemove', onGlobalMove, true);
  document.addEventListener('mouseup', onGlobalUp, true);

  map.on('moveend', scheduleRedraw);
  map.on('zoomend', scheduleRedraw);

  console.info('[map] Инициализировано, карта:', mapName);
}

function scheduleRedraw() {
  if (_redrawTimer) clearTimeout(_redrawTimer);
  _redrawTimer = setTimeout(() => {
    _redrawTimer = null;
    redrawAll();
  }, 250);
}


/* =====================================================================
 *  Отрисовка (только видимый viewBox)
 * ===================================================================== */

function currentBounds() {
  const b = map.getBounds();
  const sw = b.getSouthWest();
  const ne = b.getNorthEast();
  const pad = 0.0005;
  return {
    minLat: sw.lat - pad,
    maxLat: ne.lat + pad,
    minLon: sw.lng - pad,
    maxLon: ne.lng + pad,
  };
}

function redrawAll() {
  if (!map || !layers) return;
  const b = currentBounds();
  drawEdges(b);
  drawBuildings(b);
  drawNodes(b);
  drawHandles();
}

function inBounds(lat, lon, b) {
  return lat >= b.minLat && lat <= b.maxLat
      && lon >= b.minLon && lon <= b.maxLon;
}

function drawEdges(b) {
  layers.edges.clearLayers();
  let drawn = 0;
  for (const e of state.edges) {
    if (drawn >= MAX_EDGES_DRAWN) break;

    const a = findNode(e.from_node);
    const c = findNode(e.to_node);
    if (!a || !c) continue;
    if (!inBounds(a.lat, a.lon, b) && !inBounds(c.lat, c.lon, b)) continue;
    if (!Number.isFinite(a.lat) || !Number.isFinite(a.lon)) continue;
    if (!Number.isFinite(c.lat) || !Number.isFinite(c.lon)) continue;

    const isSel = state.selected && state.selected.type === 'edge'
                  && state.selected.id === e.id;

    const line = L.polyline(
      [[a.lat, a.lon], [c.lat, c.lon]],
      {
        color: isSel ? '#e74c3c' : '#555',
        weight: 3,
        opacity: 0.9,
        interactive: true,
      }
    );
    line._edge = e;
    line.on('click', ev => {
      L.DomEvent.stopPropagation(ev);
      onEdgeClick(e);
    });
    line.on('dblclick', ev => {
      L.DomEvent.stopPropagation(ev);
      onEdgeClick(e);
    });
    line.addTo(layers.edges);
    drawn++;
  }
}

function drawBuildings(b) {
  layers.buildings.clearLayers();
  let drawn = 0;
  for (const bl of state.buildings) {
    if (drawn >= MAX_BUILDINGS_DRAWN) break;
    if (!inBounds(bl.lat, bl.lon, b)) continue;
    if (!Number.isFinite(bl.lat) || !Number.isFinite(bl.lon)) continue;

    const corners = buildingCorners(bl);
    const t = findBuildingType(bl.type_id);
    const fill = t ? t.color : '#e67e22';
    const isSel = state.selected && state.selected.type === 'building'
                  && state.selected.id === bl.id;

    const poly = L.polygon(corners, {
      color: isSel ? '#922b21' : darken(fill),
      fillColor: isSel ? '#e74c3c' : fill,
      fillOpacity: 0.75,
      weight: 2,
      interactive: true,
    });
    poly._building = bl;

    poly.on('mousedown', ev => {
      if (state.mode !== 'idle') return;
      blockMouseEvent(ev);
      // Отключаем панорамирование карты, пока тянем здание
      map.dragging.disable();
      startItemDrag(ev, 'building', bl);
    });
    poly.on('click', ev => {
      L.DomEvent.stopPropagation(ev);
      if (drag && drag.moved) return;
      onBuildingClick(bl);
    });
    poly.on('dblclick', ev => {
      L.DomEvent.stopPropagation(ev);
      onBuildingClick(bl);
    });
    poly.addTo(layers.buildings);
    drawn++;
  }
}

function drawNodes(b) {
  layers.nodes.clearLayers();
  let drawn = 0;
  for (const n of state.nodes) {
    if (drawn >= MAX_NODES_DRAWN) break;
    if (!inBounds(n.lat, n.lon, b)) continue;
    if (!Number.isFinite(n.lat) || !Number.isFinite(n.lon)) continue;

    const t = findNodeType(n.type_id);
    const fill = t ? t.color : '#3498db';
    const isSel = state.selected && state.selected.type === 'node'
                  && state.selected.id === n.id;
    const isSrc = state.edgeFrom === n.id;

    const marker = L.circleMarker([n.lat, n.lon], {
      radius: 6,
      color: isSel ? '#922b21' : darken(fill),
      fillColor: isSrc ? '#27ae60' : (isSel ? '#e74c3c' : fill),
      fillOpacity: 0.95,
      weight: 2,
    });
    marker._node = n;

    marker.on('mousedown', ev => {
      if (state.mode !== 'idle') return;
      blockMouseEvent(ev);
      map.dragging.disable();
      startItemDrag(ev, 'node', n);
    });
    marker.on('click', ev => {
      L.DomEvent.stopPropagation(ev);
      if (drag && drag.moved) return;
      onNodeClick(n);
    });
    marker.on('dblclick', ev => {
      L.DomEvent.stopPropagation(ev);
      onNodeClick(n);
    });
    marker.bindTooltip(n.name || ('#' + n.id), { direction: 'top', offset: [0, -8] });
    marker.addTo(layers.nodes);
    drawn++;
  }
}

function drawHandles() {
  layers.handles.clearLayers();
  if (state.mode !== 'idle') return;
  if (!state.selected || state.selected.type !== 'building') return;
  const bl = findBuilding(state.selected.id);
  if (!bl) return;
  if (!Number.isFinite(bl.lat) || !Number.isFinite(bl.lon)) return;

  const rot = (bl.rotation || 0) * Math.PI / 180;
  const cos = Math.cos(rot), sin = Math.sin(rot);
  const hw = bl.w / 2, hh = bl.h / 2;

  const defs = [
    ['nw', -hw, -hh, 'rotate'],
    ['n',   0,   -hh, 'resize'],
    ['ne',  hw,  -hh, 'rotate'],
    ['e',   hw,   0,  'resize'],
    ['se',  hw,   hh, 'rotate'],
    ['s',   0,    hh, 'resize'],
    ['sw', -hw,   hh, 'rotate'],
    ['w',  -hw,   0,  'resize'],
  ];

  for (const [dir, lx, ly, kind] of defs) {
    const rx = lx * cos - ly * sin;
    const ry = lx * sin + ly * cos;
    const { dLat, dLon } = metersToLatLon(bl.lat, rx, ry);
    const lat = bl.lat + dLat;
    const lon = bl.lon + dLon;

    const handle = L.circleMarker([lat, lon], {
      radius: 8,
      color: '#2c3e50',
      fillColor: '#ffffff',
      fillOpacity: 1,
      weight: 2,
      interactive: true,
    });
    handle.on('mousedown', ev => {
      blockMouseEvent(ev);
      map.dragging.disable();
      if (kind === 'rotate') startRotate(ev, bl);
      else startResize(ev, bl, dir);
    });
    handle.on('click', ev => L.DomEvent.stopPropagation(ev));
    handle.on('dblclick', ev => L.DomEvent.stopPropagation(ev));
    handle.addTo(layers.handles);
  }
}


/* =====================================================================
 *  Перетаскивание
 * ===================================================================== */

function startItemDrag(ev, kind, item) {
  drag = {
    kind,
    item,
    startLat: ev.latlng.lat,
    startLon: ev.latlng.lng,
    origLat: item.lat,
    origLon: item.lon,
    moved: false,
    screenStart: { x: ev.originalEvent.clientX, y: ev.originalEvent.clientY },
  };
}

function startRotate(ev, bl) {
  drag = {
    kind: 'rotate',
    item: bl,
    centerLat: bl.lat,
    centerLon: bl.lon,
    startAngle: angleBetween(bl.lat, bl.lon, ev.latlng.lat, ev.latlng.lng),
    startRotation: bl.rotation || 0,
    moved: false,
    screenStart: { x: ev.originalEvent.clientX, y: ev.originalEvent.clientY },
  };
  console.info('[rotate] старт, угол:', drag.startAngle.toFixed(1));
}

function startResize(ev, bl, dir) {
  drag = {
    kind: 'resize',
    item: bl,
    dir,
    startLat: ev.latlng.lat,
    startLon: ev.latlng.lng,
    origLat: bl.lat,
    origLon: bl.lon,
    origW: bl.w,
    origH: bl.h,
    startRotation: bl.rotation || 0,
    moved: false,
    screenStart: { x: ev.originalEvent.clientX, y: ev.originalEvent.clientY },
  };
}

function onGlobalMove(ev) {
  if (!drag) return;

  // Приводим событие к единому виду: у нас может быть либо Leaflet-событие,
  // либо обычное DOM-событие (если стрельнул document-listener)
  let latlng = null;
  if (ev.latlng) {
    latlng = ev.latlng;
  } else if (map) {
    // Преобразуем screen-координаты в lat/lon
    const rect = map.getContainer().getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    if (x >= 0 && y >= 0 && x <= rect.width && y <= rect.height) {
      const p = L.point(x, y);
      latlng = map.containerPointToLatLng(p);
    }
  }
  if (!latlng) return;

  const clientX = ev.originalEvent ? ev.originalEvent.clientX : ev.clientX;
  const clientY = ev.originalEvent ? ev.originalEvent.clientY : ev.clientY;
  const ctrlKey = ev.originalEvent ? ev.originalEvent.ctrlKey : ev.ctrlKey;

  const dx = clientX - drag.screenStart.x;
  const dy = clientY - drag.screenStart.y;
  if (!drag.moved && Math.hypot(dx, dy) < 3) return;

  if (!drag.moved) {
    drag.moved = true;
    document.body.style.cursor = 'grabbing';
  }

  if (drag.kind === 'node' || drag.kind === 'building') {
    const dLat = latlng.lat - drag.startLat;
    const dLon = latlng.lng - drag.startLon;
    drag.item.lat = drag.origLat + dLat;
    drag.item.lon = drag.origLon + dLon;
    redrawAll();
    return;
  }

  if (drag.kind === 'rotate') {
    const cur = angleBetween(drag.centerLat, drag.centerLon,
                             latlng.lat, latlng.lng);
    let newRot = drag.startRotation + (cur - drag.startAngle);
    if (ctrlKey) newRot = Math.round(newRot / 15) * 15;
    newRot = ((newRot % 360) + 360) % 360;
    drag.item.rotation = newRot;
    redrawAll();
    return;
  }

  if (drag.kind === 'resize') {
    const bl = drag.item;
    const dyMeters = (latlng.lat - drag.startLat) * 111320;
    const dxMeters = (latlng.lng - drag.startLon) * 111320 *
                     Math.cos(bl.lat * Math.PI / 180);

    const a = -drag.startRotation * Math.PI / 180;
    const cosA = Math.cos(a), sinA = Math.sin(a);
    const ldx = dxMeters * cosA - dyMeters * sinA;
    const ldy = dxMeters * sinA + dyMeters * cosA;

    let dw = 0, dh = 0;
    if (drag.dir === 'e') dw = ldx;
    else if (drag.dir === 'w') dw = -ldx;
    if (drag.dir === 's') dh = ldy;
    else if (drag.dir === 'n') dh = -ldy;

    const newW = Math.max(3, drag.origW + dw);
    const newH = Math.max(3, drag.origH + dh);
    const actualDw = newW - drag.origW;
    const actualDh = newH - drag.origH;

    let cxL = 0, cyL = 0;
    if (drag.dir === 'e') cxL =  actualDw / 2;
    else if (drag.dir === 'w') cxL = -actualDw / 2;
    if (drag.dir === 's') cyL =  actualDh / 2;
    else if (drag.dir === 'n') cyL = -actualDh / 2;

    const rad = drag.startRotation * Math.PI / 180;
    const cosR = Math.cos(rad), sinR = Math.sin(rad);
    const wx = cxL * cosR - cyL * sinR;
    const wy = cxL * sinR + cyL * cosR;
    const { dLat, dLon } = metersToLatLon(drag.origLat, wx, wy);

    bl.lat = drag.origLat + dLat;
    bl.lon = drag.origLon + dLon;
    bl.w = newW;
    bl.h = newH;
    redrawAll();
    return;
  }
}

async function onGlobalUp() {
  // Возвращаем перетаскивание карты
  if (map && map.dragging && !map.dragging.enabled()) {
    map.dragging.enable();
  }
  document.body.style.cursor = '';

  if (!drag) return;
  const wasMoved = drag.moved;
  const kind = drag.kind;
  const item = drag.item;
  drag = null;

  if (!wasMoved) return;

  try {
    if (kind === 'node') {
      await api(`/api/nodes/${item.id}`, {
        method: 'PUT',
        body: JSON.stringify({ name: item.name || '', lat: item.lat, lon: item.lon,
                              type_id: item.type_id }),
      });
    } else if (kind === 'building' || kind === 'rotate' || kind === 'resize') {
      await api(`/api/buildings/${item.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: item.name || '', lat: item.lat, lon: item.lon,
          w: item.w, h: item.h, rotation: item.rotation,
          type_id: item.type_id,
        }),
      });
    }
  } catch (e) {
    alert('Не удалось сохранить: ' + e.message);
  }
}


/* =====================================================================
 *  Клики
 * ===================================================================== */

async function onMapClick(ev) {
  if (state.mode === 'add-node') {
    const name = prompt('Название вершины:', 'Перекрёсток');
    if (name === null) { setMode('idle'); return; }
    try {
      await api('/api/nodes', {
        method: 'POST',
        body: JSON.stringify({
          name, lat: ev.latlng.lat, lon: ev.latlng.lng,
          type_id: state.nodeTypes[0] ? state.nodeTypes[0].id : null,
        }),
      });
      setMode('idle');
      await loadGraph();
    } catch (e) { alert('Ошибка: ' + e.message); setMode('idle'); }
    return;
  }

  if (state.mode === 'add-building') {
    const name = prompt('Название постройки:', 'Постройка');
    if (name === null) { setMode('idle'); return; }
    try {
      await api('/api/buildings', {
        method: 'POST',
        body: JSON.stringify({
          name, lat: ev.latlng.lat, lon: ev.latlng.lng,
          w: 20, h: 20, rotation: 0,
          type_id: state.buildingTypes[0] ? state.buildingTypes[0].id : null,
        }),
      });
      setMode('idle');
      await loadGraph();
    } catch (e) { alert('Ошибка: ' + e.message); setMode('idle'); }
    return;
  }

  if (state.mode === 'add-edge') {
    state.edgeFrom = null;
    updateHint();
    redrawAll();
    return;
  }
}

function onNodeClick(n) {
  if (state.mode === 'add-edge') {
    if (state.edgeFrom === null) {
      state.edgeFrom = n.id;
      updateHint();
      redrawAll();
    } else if (state.edgeFrom === n.id) {
      state.edgeFrom = null;
      updateHint();
      redrawAll();
    } else {
      const fromId = state.edgeFrom;
      api('/api/edges', {
        method: 'POST',
        body: JSON.stringify({ from_node: fromId, to_node: n.id, name: '' })
      }).then(async () => {
        try {
          await api('/api/edges', {
            method: 'POST',
            body: JSON.stringify({ from_node: n.id, to_node: fromId, name: '' })
          });
        } catch (_) {}
        setMode('idle');
        loadGraph();
      }).catch(e => { alert('Ошибка: ' + e.message); setMode('idle'); });
    }
    return;
  }

  if (state.mode === 'delete') {
    if (confirm(`Удалить вершину #${n.id}?`)) {
      api(`/api/nodes/${n.id}`, { method: 'DELETE' })
        .then(() => { setMode('idle'); loadGraph(); })
        .catch(e => { alert(e.message); setMode('idle'); });
    } else setMode('idle');
    return;
  }

  if (state.mode !== 'idle') return;
  if (state.selected && state.selected.type === 'node'
      && state.selected.id === n.id && !isModalHidden()) return;
  openModal('node', n.id);
}

function onBuildingClick(bl) {
  if (state.mode === 'delete') {
    if (confirm(`Удалить постройку #${bl.id}?`)) {
      api(`/api/buildings/${bl.id}`, { method: 'DELETE' })
        .then(() => { setMode('idle'); loadGraph(); })
        .catch(e => { alert(e.message); setMode('idle'); });
    } else setMode('idle');
    return;
  }
  if (state.mode !== 'idle') return;
  if (state.selected && state.selected.type === 'building'
      && state.selected.id === bl.id && !isModalHidden()) return;
  openModal('building', bl.id);
}

function onEdgeClick(e) {
  if (state.mode === 'delete') {
    if (confirm(`Удалить ребро #${e.id}?`)) {
      api(`/api/edges/${e.id}`, { method: 'DELETE' })
        .then(() => { setMode('idle'); loadGraph(); })
        .catch(err => { alert(err.message); setMode('idle'); });
    } else setMode('idle');
    return;
  }
  if (state.mode !== 'idle') return;
  if (state.selected && state.selected.type === 'edge'
      && state.selected.id === e.id && !isModalHidden()) return;
  openModal('edge', e.id);
}


/* =====================================================================
 *  Режимы
 * ===================================================================== */

function setMode(mode) {
  state.mode = mode;
  if (mode === 'idle') state.edgeFrom = null;
  document.getElementById('btn-add-node').classList.toggle('active', mode === 'add-node');
  document.getElementById('btn-add-edge').classList.toggle('active', mode === 'add-edge');
  document.getElementById('btn-add-building').classList.toggle('active', mode === 'add-building');
  document.getElementById('btn-delete').classList.toggle('active', mode === 'delete');
  document.getElementById('btn-cancel').classList.toggle('hidden', mode === 'idle');
  updateHint();
  redrawAll();
}

function updateHint() {
  const el = document.getElementById('mode-hint');
  const m = state.mode;
  if (m === 'idle') { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  if (m === 'add-node') el.textContent = 'Кликните по карте, чтобы поставить вершину';
  else if (m === 'add-building') el.textContent = 'Кликните по карте, чтобы поставить постройку';
  else if (m === 'add-edge') el.textContent = state.edgeFrom
    ? `Источник #${state.edgeFrom}. Выберите конечную вершину`
    : 'Выберите начальную вершину';
  else if (m === 'delete') el.textContent = 'Кликните по элементу для удаления';
}


/* =====================================================================
 *  Модальное окно
 * ===================================================================== */

function isModalHidden() {
  return document.getElementById('modal').classList.contains('hidden');
}

function positionModalNear(item) {
  const modal = document.getElementById('modal');
  const w = modal.offsetWidth  || 560;
  const h = modal.offsetHeight || 400;

  let left = 100;
  let top  = 100;

  if (item && map && Number.isFinite(item.lat) && Number.isFinite(item.lon)) {
    try {
      const pt = map.latLngToContainerPoint([item.lat, item.lon]);
      const mapRect = map.getContainer().getBoundingClientRect();
      const screenX = mapRect.left + pt.x;
      const screenY = mapRect.top  + pt.y;

      left = screenX + 60;
      top  = screenY - h / 3;

      if (left + w > window.innerWidth - 20) {
        left = screenX - w - 60;
      }
      if (left < 20) {
        left = Math.max(20, (window.innerWidth - w) / 2);
      }
      if (top < 20) top = 20;
      if (top + h > window.innerHeight - 20) {
        top = Math.max(20, window.innerHeight - h - 20);
      }
    } catch (e) {
      console.warn('positionModalNear error:', e);
      left = Math.max(20, (window.innerWidth - w) / 2);
      top  = 100;
    }
  } else {
    left = Math.max(20, (window.innerWidth - w) / 2);
    top  = 100;
  }

  modal.style.left = Math.round(left) + 'px';
  modal.style.top  = Math.round(top)  + 'px';
  modal.style.zIndex = '999999';
}

function openModal(type, id) {
  state.selected = { type, id };
  redrawAll();

  const editor = document.getElementById('editor');
  const typeRow = document.getElementById('type-row');
  const titleEl = document.getElementById('modal-title');

  let itemForPos = null;

  if (type === 'node') {
    const n = findNode(id);
    titleEl.textContent = `Вершина #${n.id}: ${n.name || '(без названия)'}`;
    document.getElementById('obj-name').value = n.name || '';
    editor.classList.remove('hidden');
    typeRow.classList.remove('hidden');
    renderTypeSelect('node', n.type_id);
    itemForPos = n;
  } else if (type === 'building') {
    const bl = findBuilding(id);
    titleEl.textContent = `Постройка #${bl.id}: ${bl.name || '(без названия)'}`;
    document.getElementById('obj-name').value = bl.name || '';
    editor.classList.remove('hidden');
    typeRow.classList.remove('hidden');
    renderTypeSelect('building', bl.type_id);
    itemForPos = bl;
  } else {
    const e = state.edges.find(x => x.id === id);
    titleEl.textContent = `Ребро #${e.id}: узел ${e.from_node} → узел ${e.to_node}`;
    editor.classList.add('hidden');
    typeRow.classList.add('hidden');
    const a = findNode(e.from_node), b2 = findNode(e.to_node);
    if (a && b2) itemForPos = { lat: (a.lat + b2.lat) / 2, lon: (a.lon + b2.lon) / 2 };
  }

  updateModalTime();
  renderCharSelect(type);

  const modal = document.getElementById('modal');
  modal.classList.remove('hidden');
  // Форсируем показ (некоторые CSS-фреймворки мешают)
  modal.style.display = 'block';

  positionModalNear(itemForPos);
  refreshModalData();
}

function renderTypeSelect(kind, currentId) {
  const sel = document.getElementById('obj-type-select');
  sel.innerHTML = '';
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = '— без типа —';
  sel.appendChild(empty);

  const list = kind === 'node' ? state.nodeTypes : state.buildingTypes;
  for (const t of list) {
    const opt = document.createElement('option');
    opt.value = t.id;
    opt.textContent = t.name;
    if (currentId != null && +currentId === t.id) opt.selected = true;
    sel.appendChild(opt);
  }
  if (currentId == null) empty.selected = true;
}

function closeModal() {
  const modal = document.getElementById('modal');
  modal.classList.add('hidden');
  modal.style.display = '';
  state.selected = null;
  redrawAll();
}

document.getElementById('modal-close').addEventListener('click', closeModal);

document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && !isModalHidden()) closeModal();
});

document.querySelector('.modal-header').addEventListener('mousedown', ev => {
  if (ev.button !== 0) return;
  if (ev.target.closest('#modal-close')) return;
  ev.preventDefault();
  const modal = document.getElementById('modal');
  const rect = modal.getBoundingClientRect();
  modalDragState = { offsetX: ev.clientX - rect.left, offsetY: ev.clientY - rect.top };
  document.addEventListener('mousemove', onModalMove);
  document.addEventListener('mouseup', onModalUp);
});

function onModalMove(ev) {
  if (!modalDragState) return;
  const modal = document.getElementById('modal');
  const w = modal.offsetWidth, h = modal.offsetHeight;
  const x = Math.min(Math.max(0, ev.clientX - modalDragState.offsetX),
                     window.innerWidth - w);
  const y = Math.min(Math.max(0, ev.clientY - modalDragState.offsetY),
                     window.innerHeight - h);
  modal.style.left = x + 'px';
  modal.style.top  = y + 'px';
}

function onModalUp() {
  modalDragState = null;
  document.removeEventListener('mousemove', onModalMove);
  document.removeEventListener('mouseup', onModalUp);
}

function updateModalTime() {
  const W = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'];
  document.getElementById('modal-time').textContent =
    `${W[state.weekday]}, ${String(state.hour).padStart(2, '0')}:00`;
}

async function refreshModalData() {
  if (!state.selected) return;
  const { type, id } = state.selected;
  const rows = await api(`/api/data?type=${type}&target_id=${id}`
                        +`&weekday=${state.weekday}&hour=${state.hour}`);
  const tbody = document.getElementById('data-body');
  tbody.innerHTML = '';
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="3" class="empty">Нет данных</td></tr>';
    return;
  }
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(r.characteristic_name)}</td>
      <td><input type="number" step="any" value="${r.value}" data-id="${r.id}"></td>
      <td>
        <button data-action="save" data-id="${r.id}">💾</button>
        <button data-action="delete" data-id="${r.id}">🗑</button>
      </td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('button[data-action="save"]').forEach(b => {
    b.addEventListener('click', async () => {
      const did = b.dataset.id;
      const inp = tbody.querySelector(`input[data-id="${did}"]`);
      await api(`/api/data/${did}`, {
        method: 'PUT',
        body: JSON.stringify({ value: parseFloat(inp.value) })
      });
      refreshModalData();
    });
  });
  tbody.querySelectorAll('button[data-action="delete"]').forEach(b => {
    b.addEventListener('click', async () => {
      await api(`/api/data/${b.dataset.id}`, { method: 'DELETE' });
      refreshModalData();
    });
  });
}

function charApplies(c, type) {
  if (c.applies_to === 'all') return true;
  if (c.applies_to === 'both') return type === 'node' || type === 'edge';
  return c.applies_to === type;
}

function renderCharSelect(type) {
  const sel = document.getElementById('char-select');
  sel.innerHTML = '';
  const list = state.characteristics.filter(c => charApplies(c, type));
  for (const c of list) {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = c.name;
    sel.appendChild(opt);
  }
  if (!list.length) sel.innerHTML = '<option value="">— нет —</option>';
}

document.getElementById('btn-add-data').addEventListener('click', async () => {
  if (!state.selected) return;
  const charId = parseInt(document.getElementById('char-select').value);
  const v = parseFloat(document.getElementById('new-value').value);
  if (!charId || isNaN(v)) { alert('Выберите характеристику и введите число'); return; }
  await api('/api/data', {
    method: 'POST',
    body: JSON.stringify({
      type: state.selected.type,
      target_id: state.selected.id,
      characteristic_id: charId,
      weekday: state.weekday,
      hour: state.hour,
      value: v,
    }),
  });
  document.getElementById('new-value').value = '';
  refreshModalData();
});

document.getElementById('btn-new-char').addEventListener('click', async () => {
  if (!state.selected) return;
  const type = state.selected.type;
  const name = prompt('Название новой характеристики:');
  if (!name || !name.trim()) return;
  try {
    await api('/api/characteristics', {
      method: 'POST',
      body: JSON.stringify({ name: name.trim(), applies_to: type })
    });
    await loadCharacteristics();
    renderCharSelect(type);
  } catch (e) { alert(e.message); }
});

document.getElementById('obj-save').addEventListener('click', async () => {
  if (!state.selected) return;
  const t = state.selected.type;
  if (t !== 'node' && t !== 'building') return;
  const item = t === 'node' ? findNode(state.selected.id) : findBuilding(state.selected.id);
  const name = document.getElementById('obj-name').value;
  item.name = name;
  const url = t === 'node' ? `/api/nodes/${item.id}` : `/api/buildings/${item.id}`;
  const body = t === 'node'
    ? { name, lat: item.lat, lon: item.lon, type_id: item.type_id }
    : { name, lat: item.lat, lon: item.lon, w: item.w, h: item.h,
        rotation: item.rotation, type_id: item.type_id };
  await api(url, { method: 'PUT', body: JSON.stringify(body) });
  document.getElementById('modal-title').textContent =
    t === 'node' ? `Вершина #${item.id}: ${name}` : `Постройка #${item.id}: ${name}`;
  redrawAll();
});

document.getElementById('obj-type-select').addEventListener('change', async ev => {
  if (!state.selected) return;
  const t = state.selected.type;
  if (t !== 'node' && t !== 'building') return;
  const raw = ev.target.value;
  const newType = (raw === '' || raw === 'null') ? null : +raw;
  const item = t === 'node' ? findNode(state.selected.id) : findBuilding(state.selected.id);
  item.type_id = newType;
  redrawAll();
  const url = t === 'node' ? `/api/nodes/${item.id}` : `/api/buildings/${item.id}`;
  const body = t === 'node'
    ? { name: item.name || '', lat: item.lat, lon: item.lon, type_id: newType }
    : { name: item.name || '', lat: item.lat, lon: item.lon, w: item.w, h: item.h,
        rotation: item.rotation, type_id: newType };
  try {
    await api(url, { method: 'PUT', body: JSON.stringify(body) });
  } catch (e) { alert('Не удалось сохранить тип: ' + e.message); }
});


/* =====================================================================
 *  Тулбар
 * ===================================================================== */

document.getElementById('btn-add-node').addEventListener('click', () => {
  setMode(state.mode === 'add-node' ? 'idle' : 'add-node');
});
document.getElementById('btn-add-edge').addEventListener('click', () => {
  if (state.mode === 'add-edge') setMode('idle');
  else { state.edgeFrom = null; setMode('add-edge'); }
});
document.getElementById('btn-add-building').addEventListener('click', () => {
  setMode(state.mode === 'add-building' ? 'idle' : 'add-building');
});
document.getElementById('btn-delete').addEventListener('click', () => {
  setMode(state.mode === 'delete' ? 'idle' : 'delete');
});
document.getElementById('btn-cancel').addEventListener('click', () => setMode('idle'));


/* =====================================================================
 *  Переключатели времени
 * ===================================================================== */

document.getElementById('weekday').addEventListener('change', ev => {
  state.weekday = parseInt(ev.target.value);
  updateModalTime();
  if (!isModalHidden()) refreshModalData();
});
document.getElementById('hour').addEventListener('input', ev => {
  state.hour = parseInt(ev.target.value);
  document.getElementById('hour-label').textContent =
    String(state.hour).padStart(2, '0') + ':00';
  updateModalTime();
  if (!isModalHidden()) refreshModalData();
});


/* =====================================================================
 *  Инициализация
 * ===================================================================== */

(async function init() {
  try {
    initMap();
    await loadCharacteristics();
    await loadGraph();
    document.getElementById('hour-label').textContent = '12:00';
    updateHint();
  } catch (e) {
    console.error('Ошибка инициализации:', e);
  }
})();