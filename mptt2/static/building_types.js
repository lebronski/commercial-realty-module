async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts
  });
  if (!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : res.json();
}

function darken(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const r = Math.round(((n >> 16) & 255) * 0.65);
  const g = Math.round(((n >>  8) & 255) * 0.65);
  const b = Math.round(( n        & 255) * 0.65);
  return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
}

async function loadList() {
  const list = await api('/api/building_types');
  const tbody = document.querySelector('#bt-table tbody');
  tbody.innerHTML = '';
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">Список пуст</td></tr>';
    return;
  }
  for (const t of list) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${t.id}</td>
      <td><input type="text" value="${esc(t.name)}" data-role="name" data-id="${t.id}"></td>
      <td><input type="color" value="${t.color}" data-role="color" data-id="${t.id}"></td>
      <td><span class="color-preview" data-role="preview" data-id="${t.id}"
                style="background:${t.color}; border-color:${darken(t.color)};"></span></td>
      <td>
        <button data-action="save" data-id="${t.id}">💾</button>
        <button data-action="delete" data-id="${t.id}">🗑</button>
      </td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('input[data-role="color"]').forEach(inp => {
    inp.addEventListener('input', () => {
      const id = inp.dataset.id;
      const pv = tbody.querySelector(`span[data-role=preview][data-id="${id}"]`);
      if (pv) { pv.style.background = inp.value; pv.style.borderColor = darken(inp.value); }
    });
  });
  tbody.querySelectorAll('button[data-action="save"]').forEach(b => {
    b.addEventListener('click', async () => {
      const id = b.dataset.id;
      const name = tbody.querySelector(`input[data-id="${id}"][data-role=name]`).value;
      const color = tbody.querySelector(`input[data-id="${id}"][data-role=color]`).value;
      await api(`/api/building_types/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ name, color })
      });
      loadList();
    });
  });
  tbody.querySelectorAll('button[data-action="delete"]').forEach(b => {
    b.addEventListener('click', async () => {
      if (!confirm('Удалить тип? Дома останутся без типа.')) return;
      await api(`/api/building_types/${b.dataset.id}`, { method: 'DELETE' });
      loadList();
    });
  });
}

document.getElementById('bt-form').addEventListener('submit', async e => {
  e.preventDefault();
  const name = document.getElementById('bt-name').value.trim();
  const color = document.getElementById('bt-color').value;
  if (!name) return;
  await api('/api/building_types', {
    method: 'POST',
    body: JSON.stringify({ name, color })
  });
  document.getElementById('bt-name').value = '';
  loadList();
});

function esc(s) {
  return String(s).replace(/[&<>"']/g, m => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[m]));
}

loadList();