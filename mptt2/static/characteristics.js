async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const t = await res.text(); const j = JSON.parse(t); msg = j.error || t; }
    catch (_) {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

const LABEL = {
  node: 'Только узлы',
  edge: 'Только рёбра',
  building: 'Только постройки',
  both: 'Узлы и рёбра',
  all: 'Все элементы',
};

function optsHtml(selected) {
  return Object.entries(LABEL).map(([v, t]) =>
    `<option value="${v}"${v === selected ? ' selected' : ''}>${t}</option>`
  ).join('');
}

async function loadList() {
  const list = await api('/api/characteristics');
  const tbody = document.querySelector('#char-table tbody');
  tbody.innerHTML = '';
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">Список пуст</td></tr>';
    return;
  }
  for (const c of list) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${c.id}</td>
      <td><input type="text" value="${esc(c.name)}" data-role="name" data-id="${c.id}"></td>
      <td><select data-role="applies" data-id="${c.id}">${optsHtml(c.applies_to)}</select></td>
      <td>
        <button data-action="save" data-id="${c.id}">💾</button>
        <button data-action="delete" data-id="${c.id}">🗑</button>
      </td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('button[data-action="save"]').forEach(b => {
    b.addEventListener('click', async () => {
      const id = b.dataset.id;
      const name = tbody.querySelector(`input[data-id="${id}"][data-role=name]`).value;
      const applies = tbody.querySelector(`select[data-id="${id}"][data-role=applies]`).value;
      try {
        await api(`/api/characteristics/${id}`, {
          method: 'PUT',
          body: JSON.stringify({ name, applies_to: applies })
        });
        loadList();
      } catch (e) { alert(e.message); }
    });
  });
  tbody.querySelectorAll('button[data-action="delete"]').forEach(b => {
    b.addEventListener('click', async () => {
      if (!confirm('Удалить характеристику?')) return;
      try {
        await api(`/api/characteristics/${b.dataset.id}`, { method: 'DELETE' });
        loadList();
      } catch (e) { alert(e.message); }
    });
  });
}

document.getElementById('char-form').addEventListener('submit', async e => {
  e.preventDefault();
  const name = document.getElementById('char-name').value.trim();
  const applies = document.getElementById('char-applies').value;
  if (!name) return;
  try {
    await api('/api/characteristics', {
      method: 'POST',
      body: JSON.stringify({ name, applies_to: applies })
    });
    document.getElementById('char-name').value = '';
    loadList();
  } catch (err) { alert(err.message); }
});

function esc(s) {
  return String(s).replace(/[&<>"']/g, m => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[m]));
}

loadList();