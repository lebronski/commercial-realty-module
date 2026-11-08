/* ============================================================
   Обёртки
   ============================================================ */

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
  return res.status === 204 ? null : res.json();
}

function el(id, required = true) {
  const node = document.getElementById(id);
  if (!node && required) {
    console.warn(`[overpass.js] Нет элемента #${id}`);
  }
  return node;
}

function on(id, event, handler) {
  const node = document.getElementById(id);
  if (!node) { console.warn(`[overpass.js] Пропущена привязка: #${id}`); return; }
  node.addEventListener(event, handler);
}

function diagnose() {
  const ids = ['ov-place', 'ov-network', 'ov-grid', 'ov-timeout',
               'ov-roads', 'ov-buildings', 'ov-servers-text',
               'ov-save', 'ov-test', 'ov-run', 'ov-clear',
               'ov-bar', 'ov-message', 'ov-counter', 'ov-log'];
  const missing = ids.filter(x => !document.getElementById(x));
  if (missing.length) {
    console.warn('[overpass.js] Отсутствуют элементы:', missing.join(', '));
    console.warn('[overpass.js] Замените modules/templates/overpass.html.');
  } else {
    console.info('[overpass.js] Все элементы на месте.');
  }
}

function parseServers(text) {
  return (text || '').split(/\r?\n/)
    .map(s => s.trim())
    .filter(Boolean);
}


/* ============================================================
   Настройки
   ============================================================ */

async function loadSettings() {
  try {
    const s = await api('/modules/overpass/settings');
    const p = el('ov-place');        if (p) p.value = s.place || '';
    const n = el('ov-network');      if (n) n.value = s.network || 'drive';
    const g = el('ov-grid');         if (g) g.value = s.grid || 2;
    const t = el('ov-timeout');      if (t) t.value = s.timeout || 90;
    const r = el('ov-roads');        if (r) r.checked = !!s.do_roads;
    const b = el('ov-buildings');    if (b) b.checked = !!s.do_buildings;
    const sv = el('ov-servers-text');
    if (sv) sv.value = s.servers_text || s.servers_default || '';
  } catch (e) {
    console.error('loadSettings:', e);
  }
}

on('ov-save', 'click', async () => {
  try {
    await api('/modules/overpass/settings', {
      method: 'POST',
      body: JSON.stringify({
        place:        el('ov-place')?.value   ?? '',
        network:      el('ov-network')?.value ?? 'drive',
        grid:         parseInt(el('ov-grid')?.value)    || 2,
        timeout:      parseInt(el('ov-timeout')?.value) || 90,
        do_roads:     !!el('ov-roads')?.checked,
        do_buildings: !!el('ov-buildings')?.checked,
        servers_text: el('ov-servers-text')?.value ?? '',
      })
    });
    alert('Настройки сохранены');
  } catch (err) {
    alert('Ошибка: ' + err.message);
  }
});


/* ============================================================
   Проверка серверов
   ============================================================ */

on('ov-test', 'click', async () => {
  const log = el('ov-log', false);
  if (log) log.textContent = 'Проверка серверов…\n';

  const servers = parseServers(el('ov-servers-text')?.value);

  try {
    const r = await api('/modules/overpass/test_servers', {
      method: 'POST',
      body: JSON.stringify({ timeout: 20, servers })
    });
    if (log) {
      log.textContent += (r.log || []).join('\n');
      log.textContent += `\n\nOK: ${r.ok.length}, ` +
                         `rate-limited (429): ${(r.partial || []).length}`;
      log.scrollTop = log.scrollHeight;
    }
    console.info('[overpass.js] test_servers →', r);
  } catch (err) {
    if (log) log.textContent += 'Ошибка: ' + err.message;
    console.error(err);
  }
});


/* ============================================================
   Импорт
   ============================================================ */

on('ov-run', 'click', async () => {
  if (!confirm('Запустить импорт данных из Overpass API?\n\n' +
               'Это может занять 10–30 минут. Прогресс и лог — ниже.')) return;
  try {
    await api('/modules/overpass/import', { method: 'POST' });
    startPolling();
  } catch (err) {
    alert('Не удалось запустить: ' + err.message);
    console.error(err);
  }
});

on('ov-clear', 'click', async () => {
  if (!confirm('Удалить все данные, импортированные из OSM? ' +
               'Собственные объекты останутся.')) return;
  try {
    await api('/modules/overpass/clear', { method: 'POST' });
    alert('Импортированные данные удалены');
  } catch (err) {
    alert('Ошибка: ' + err.message);
  }
});


/* ============================================================
   Поллинг статуса
   ============================================================ */

let pollTimer = null;

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(poll, 1000);
  poll();
}

async function poll() {
  try {
    const s = await api('/modules/overpass/status');
    const bar = el('ov-bar', false);
    const msg = el('ov-message', false);
    const counter = el('ov-counter', false);
    const log = el('ov-log', false);

    const pct = s.total ? Math.round(100 * s.progress / s.total) : 0;
    if (bar) bar.style.width = pct + '%';
    if (msg) msg.textContent = s.message || (s.running ? 'работает…' : 'ожидание');
    if (counter) counter.textContent = s.total ? `${s.progress} / ${s.total}` : '';

    if (log) {
      const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.textContent = (s.log || []).join('\n');
      if (nearBottom) log.scrollTop = log.scrollHeight;
    }

    if (!s.running) {
      clearInterval(pollTimer);
      pollTimer = null;
      if (s.error && msg) msg.textContent = 'Ошибка: ' + s.error;
    }
  } catch (err) {
    // молча
  }
}


/* ============================================================
   Инициализация
   ============================================================ */

diagnose();
loadSettings();
startPolling();