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

function showRestart() {
  document.getElementById('restart-note').classList.remove('hidden');
}

async function loadList() {
  const list = await api('/api/modules');
  const tbody = document.querySelector('#modules-table tbody');
  tbody.innerHTML = '';
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">Модулей нет</td></tr>';
    return;
  }
  for (const m of list) {
    const info = m.info || {};
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${esc(m.filename)}</code></td>
      <td>${esc(info.name || m.name)}</td>
      <td>${esc(info.description || '')}</td>
      <td>${esc(info.version || '?')}</td>
      <td>${m.active ? '✅' : '—'}</td>
      <td>${m.loaded ? '✅' : '—'}${m.error ? ' ⚠ ' + esc(m.error) : ''}</td>
      <td>
        ${m.active
          ? `<button data-action="deactivate" data-name="${esc(m.name)}">Деактивировать</button>`
          : `<button data-action="activate"   data-name="${esc(m.name)}">Активировать</button>`}
        <button data-action="delete" data-name="${esc(m.name)}" class="danger">Удалить</button>
      </td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('button[data-action="activate"]').forEach(b => {
    b.addEventListener('click', async () => {
      await api(`/api/modules/${b.dataset.name}/activate`, { method: 'PUT' });
      showRestart();
      loadList();
    });
  });
  tbody.querySelectorAll('button[data-action="deactivate"]').forEach(b => {
    b.addEventListener('click', async () => {
      await api(`/api/modules/${b.dataset.name}/deactivate`, { method: 'PUT' });
      showRestart();
      loadList();
    });
  });
  tbody.querySelectorAll('button[data-action="delete"]').forEach(b => {
    b.addEventListener('click', async () => {
      if (!confirm('Удалить файл модуля? Действие необратимо.')) return;
      await api(`/api/modules/${b.dataset.name}`, { method: 'DELETE' });
      showRestart();
      loadList();
    });
  });
}

document.getElementById('upload-form').addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = document.getElementById('module-file').files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  try {
    const res = await fetch('/api/modules/upload', { method: 'POST', body: fd });
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = j.error || msg; } catch (_) {}
      throw new Error(msg);
    }
    document.getElementById('module-file').value = '';
    showRestart();
    loadList();
  } catch (e) { alert(e.message); }
});

function esc(s) {
  return String(s).replace(/[&<>"']/g, m => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[m]));
}

loadList();