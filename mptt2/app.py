import os
import math
import sqlite3
import jinja2
import folium

from flask import (Flask, request, jsonify, render_template, g,
                   redirect, url_for)

from db import (get_db, close_db, init_db, get_setting, set_setting, DB_PATH)
from modules.loader import ModuleLoader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'graph.db')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
app.config['BASE_DIR'] = BASE_DIR

# Jinja loader с поддержкой модульных шаблонов
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader(os.path.join(BASE_DIR, 'modules', 'templates')),
])

app.teardown_appcontext(close_db)

loader = ModuleLoader(app, BASE_DIR)
app.config['MODULE_LOADER'] = loader


# ------------------------------------------------------------------
#  Инициализация БД + загрузка активных модулей
# ------------------------------------------------------------------

def _bootstrap():
    init_db(DB_FILE)
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    try:
        active = [r['name'] for r in db.execute(
            "SELECT name FROM modules WHERE active=1")]
    finally:
        db.close()
    loader.load_active(active)


_bootstrap()


# ------------------------------------------------------------------
#  Контекст шаблонов
# ------------------------------------------------------------------

@app.context_processor
def inject_menu():
    db = get_db()
    active = [r['name'] for r in db.execute(
        "SELECT name FROM modules WHERE active=1 ORDER BY name")]
    menu = []
    for name in active:
        mod = loader.loaded.get(name)
        if mod is not None and hasattr(mod, 'MENU'):
            menu.append(mod.MENU)
    return {'menu_modules': menu}


# ------------------------------------------------------------------
#  Страницы
# ------------------------------------------------------------------

@app.route('/')
def index():
    m = folium.Map(
        location=[56.6328, 47.8908],
        zoom_start=15,
        tiles='OpenStreetMap',
        control_scale=True,
        prefer_canvas=False,
    )
    map_html = m.get_root().render()
    return render_template('index.html', active='map', map_html=map_html)


@app.route('/characteristics')
def characteristics_page():
    return render_template('characteristics.html', active='characteristics')


@app.route('/building_types')
def building_types_page():
    return render_template('building_types.html', active='buildings')


@app.route('/about')
def about_page():
    return render_template('about.html', active='about')


@app.route('/modules')
def modules_page():
    return render_template('modules.html', active='modules')


# ------------------------------------------------------------------
#  API: граф
# ------------------------------------------------------------------

def _row(r):
    return {k: r[k] for k in r.keys()}


@app.route('/api/graph')
def api_graph():
    db = get_db()
    nodes = [_row(r) for r in db.execute(
        "SELECT id,name,lat,lon,type_id,osm_id FROM nodes")]
    edges = [_row(r) for r in db.execute(
        "SELECT id,from_node,to_node,name,oneway,osm_id FROM edges")]
    buildings = [_row(r) for r in db.execute(
        "SELECT id,name,lat,lon,w,h,rotation,type_id,osm_id FROM buildings")]
    node_types = [_row(r) for r in db.execute(
        "SELECT id,name,color FROM node_types ORDER BY id")]
    building_types = [_row(r) for r in db.execute(
        "SELECT id,name,color FROM building_types ORDER BY id")]
    return jsonify({
        'nodes': nodes, 'edges': edges, 'buildings': buildings,
        'node_types': node_types, 'building_types': building_types,
    })


# ---------------- узлы ----------------

@app.route('/api/nodes', methods=['POST'])
def api_create_node():
    d = request.get_json()
    try:
        lat = float(d['lat']); lon = float(d['lon'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'Неверные координаты'}), 400
    tid = d.get('type_id') or None
    db = get_db()
    cur = db.execute(
        "INSERT INTO nodes(name,lat,lon,type_id) VALUES(?,?,?,?)",
        (d.get('name', ''), lat, lon, tid))
    db.commit()
    return jsonify({'id': cur.lastrowid}), 201


@app.route('/api/nodes/<int:nid>', methods=['PUT'])
def api_update_node(nid):
    d = request.get_json() or {}
    db = get_db()
    row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
    if not row:
        return jsonify({'error': 'Не найдено'}), 404
    try:
        lat = float(d.get('lat', row['lat']))
        lon = float(d.get('lon', row['lon']))
    except (TypeError, ValueError):
        return jsonify({'error': 'Неверные координаты'}), 400
    name = d.get('name', row['name'])
    tid = d.get('type_id', row['type_id'])
    if tid == '': tid = None
    db.execute("UPDATE nodes SET name=?,lat=?,lon=?,type_id=? WHERE id=?",
               (name, lat, lon, tid, nid))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/nodes/<int:nid>', methods=['DELETE'])
def api_delete_node(nid):
    db = get_db()
    db.execute("DELETE FROM edges WHERE from_node=? OR to_node=?", (nid, nid))
    db.execute("DELETE FROM nodes WHERE id=?", (nid,))
    db.execute("DELETE FROM data WHERE type='node' AND target_id=?", (nid,))
    db.commit()
    return jsonify({'ok': True})


# ---------------- рёбра ----------------

@app.route('/api/edges', methods=['POST'])
def api_create_edge():
    d = request.get_json()
    try:
        a = int(d['from_node']); b = int(d['to_node'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'Неверные вершины'}), 400
    if a == b:
        return jsonify({'error': 'Петли не поддерживаются'}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO edges(from_node,to_node,name,oneway) VALUES(?,?,?,?)",
        (a, b, d.get('name', ''), 1 if d.get('oneway') else 0))
    db.commit()
    return jsonify({'id': cur.lastrowid}), 201


@app.route('/api/edges/<int:eid>', methods=['PUT'])
def api_update_edge(eid):
    d = request.get_json() or {}
    db = get_db()
    row = db.execute("SELECT * FROM edges WHERE id=?", (eid,)).fetchone()
    if not row:
        return jsonify({'error': 'Не найдено'}), 404
    db.execute("UPDATE edges SET name=?, oneway=? WHERE id=?",
               (d.get('name', row['name']),
                1 if d.get('oneway', row['oneway']) else 0, eid))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/edges/<int:eid>', methods=['DELETE'])
def api_delete_edge(eid):
    db = get_db()
    db.execute("DELETE FROM edges WHERE id=?", (eid,))
    db.execute("DELETE FROM data WHERE type='edge' AND target_id=?", (eid,))
    db.commit()
    return jsonify({'ok': True})


# ---------------- постройки ----------------

@app.route('/api/buildings', methods=['POST'])
def api_create_building():
    d = request.get_json()
    try:
        lat = float(d['lat']); lon = float(d['lon'])
        w = max(1.0, float(d.get('w', 10)))
        h = max(1.0, float(d.get('h', 10)))
        rot = float(d.get('rotation', 0)) % 360
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'Неверные данные'}), 400
    tid = d.get('type_id') or None
    db = get_db()
    cur = db.execute(
        "INSERT INTO buildings(name,lat,lon,w,h,rotation,type_id) "
        "VALUES(?,?,?,?,?,?,?)",
        (d.get('name', ''), lat, lon, w, h, rot, tid))
    db.commit()
    return jsonify({'id': cur.lastrowid}), 201


@app.route('/api/buildings/<int:bid>', methods=['PUT'])
def api_update_building(bid):
    d = request.get_json() or {}
    db = get_db()
    row = db.execute("SELECT * FROM buildings WHERE id=?", (bid,)).fetchone()
    if not row:
        return jsonify({'error': 'Не найдено'}), 404
    try:
        lat = float(d.get('lat', row['lat']))
        lon = float(d.get('lon', row['lon']))
        w = max(1.0, float(d.get('w', row['w'])))
        h = max(1.0, float(d.get('h', row['h'])))
        rot = float(d.get('rotation', row['rotation'])) % 360
    except (TypeError, ValueError):
        return jsonify({'error': 'Неверные данные'}), 400
    name = d.get('name', row['name'])
    tid = d.get('type_id', row['type_id'])
    if tid == '': tid = None
    db.execute("UPDATE buildings SET name=?,lat=?,lon=?,w=?,h=?,rotation=?,type_id=? "
               "WHERE id=?",
               (name, lat, lon, w, h, rot, tid, bid))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/buildings/<int:bid>', methods=['DELETE'])
def api_delete_building(bid):
    db = get_db()
    db.execute("DELETE FROM buildings WHERE id=?", (bid,))
    db.execute("DELETE FROM data WHERE type='building' AND target_id=?", (bid,))
    db.commit()
    return jsonify({'ok': True})


# ---------------- типы узлов ----------------

@app.route('/api/node_types')
def api_node_types():
    db = get_db()
    return jsonify([_row(r) for r in db.execute(
        "SELECT id,name,color FROM node_types ORDER BY id")])


# ---------------- типы построек ----------------

@app.route('/api/building_types')
def api_building_types():
    db = get_db()
    return jsonify([_row(r) for r in db.execute(
        "SELECT id,name,color FROM building_types ORDER BY id")])


@app.route('/api/building_types', methods=['POST'])
def api_create_building_type():
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Укажите название'}), 400
    color = (d.get('color') or '#e67e22').strip()
    db = get_db()
    cur = db.execute("INSERT INTO building_types(name,color) VALUES(?,?)",
                     (name, color))
    db.commit()
    return jsonify({'id': cur.lastrowid, 'name': name, 'color': color}), 201


@app.route('/api/building_types/<int:tid>', methods=['PUT'])
def api_update_building_type(tid):
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Укажите название'}), 400
    color = (d.get('color') or '#e67e22').strip()
    db = get_db()
    db.execute("UPDATE building_types SET name=?, color=? WHERE id=?",
               (name, color, tid))
    db.commit()
    return jsonify({'ok': True, 'name': name, 'color': color})


@app.route('/api/building_types/<int:tid>', methods=['DELETE'])
def api_delete_building_type(tid):
    db = get_db()
    db.execute("UPDATE buildings SET type_id=NULL WHERE type_id=?", (tid,))
    db.execute("DELETE FROM building_types WHERE id=?", (tid,))
    db.commit()
    return jsonify({'ok': True})


# ---------------- характеристики ----------------

@app.route('/api/characteristics')
def api_characteristics():
    db = get_db()
    return jsonify([_row(r) for r in db.execute(
        "SELECT id,name,applies_to FROM characteristics ORDER BY id")])


@app.route('/api/characteristics', methods=['POST'])
def api_create_characteristic():
    d = request.get_json() or {}
    applies = d.get('applies_to', 'both')
    if applies not in ('node', 'edge', 'building', 'both', 'all'):
        return jsonify({'error': 'Недопустимое applies_to'}), 400
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Укажите название'}), 400
    db = get_db()
    cur = db.execute("INSERT INTO characteristics(name,applies_to) VALUES(?,?)",
                     (name, applies))
    db.commit()
    return jsonify({'id': cur.lastrowid}), 201


@app.route('/api/characteristics/<int:cid>', methods=['PUT'])
def api_update_characteristic(cid):
    d = request.get_json() or {}
    applies = d.get('applies_to', 'both')
    if applies not in ('node', 'edge', 'building', 'both', 'all'):
        return jsonify({'error': 'Недопустимое applies_to'}), 400
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Укажите название'}), 400
    db = get_db()
    db.execute("UPDATE characteristics SET name=?, applies_to=? WHERE id=?",
               (name, applies, cid))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/characteristics/<int:cid>', methods=['DELETE'])
def api_delete_characteristic(cid):
    db = get_db()
    db.execute("DELETE FROM characteristics WHERE id=?", (cid,))
    db.execute("DELETE FROM data WHERE characteristic_id=?", (cid,))
    db.commit()
    return jsonify({'ok': True})


# ---------------- данные характеристик ----------------

@app.route('/api/data')
def api_data():
    t = request.args.get('type')
    tid = request.args.get('target_id', type=int)
    weekday = request.args.get('weekday', type=int)
    hour = request.args.get('hour', type=int)
    if t not in ('node', 'edge', 'building'):
        return jsonify({'error': 'Неверный тип'}), 400
    db = get_db()
    rows = db.execute("""
        SELECT d.id, d.value, d.weekday, d.hour, d.type, d.target_id,
               c.id AS characteristic_id, c.name AS characteristic_name
        FROM data d JOIN characteristics c ON c.id=d.characteristic_id
        WHERE d.type=? AND d.target_id=? AND d.weekday=? AND d.hour=?
        ORDER BY c.name""", (t, tid, weekday, hour))
    return jsonify([_row(r) for r in rows])


@app.route('/api/data', methods=['POST'])
def api_create_data():
    d = request.get_json()
    if d.get('type') not in ('node', 'edge', 'building'):
        return jsonify({'error': 'Неверный тип'}), 400
    db = get_db()
    existing = db.execute("""SELECT id FROM data
        WHERE type=? AND target_id=? AND characteristic_id=?
          AND weekday=? AND hour=?""",
        (d['type'], d['target_id'], d['characteristic_id'],
         d['weekday'], d['hour'])).fetchone()
    if existing:
        db.execute("UPDATE data SET value=? WHERE id=?",
                   (float(d['value']), existing['id']))
        db.commit()
        return jsonify({'id': existing['id'], 'updated': True})
    cur = db.execute("""INSERT INTO data(target_id,type,characteristic_id,
                                          weekday,hour,value)
                        VALUES(?,?,?,?,?,?)""",
                     (d['target_id'], d['type'], d['characteristic_id'],
                      d['weekday'], d['hour'], float(d['value'])))
    db.commit()
    return jsonify({'id': cur.lastrowid}), 201


@app.route('/api/data/<int:did>', methods=['PUT'])
def api_update_data(did):
    d = request.get_json()
    db = get_db()
    db.execute("UPDATE data SET value=? WHERE id=?", (float(d['value']), did))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/data/<int:did>', methods=['DELETE'])
def api_delete_data(did):
    db = get_db()
    db.execute("DELETE FROM data WHERE id=?", (did,))
    db.commit()
    return jsonify({'ok': True})


# ------------------------------------------------------------------
#  API: управление модулями
# ------------------------------------------------------------------

@app.route('/api/modules')
def api_modules_list():
    db = get_db()
    found = loader.scan()
    registered = {r['name']: r for r in db.execute(
        "SELECT name,active FROM modules")}
    result = []
    for m in found:
        name = m['name']
        active = bool(registered.get(name, {}).get('active')) \
            if isinstance(registered.get(name), dict) \
            else bool(registered.get(name)['active']) if name in registered else False
        result.append({
            'name': name,
            'filename': m['filename'],
            'info': m['info'],
            'active': active,
            'loaded': name in loader.loaded,
            'error': loader.errors.get(name),
        })
    return jsonify(result)


@app.route('/api/modules/upload', methods=['POST'])
def api_modules_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не передан'}), 400
    f = request.files['file']
    if not f.filename.endswith('.py'):
        return jsonify({'error': 'Нужен .py файл'}), 400
    safe = os.path.basename(f.filename)
    if not safe or safe.startswith('_') or safe in ('loader.py',):
        return jsonify({'error': 'Недопустимое имя файла'}), 400
    path = os.path.join(loader.modules_dir, safe)
    f.save(path)
    name = safe[:-3]
    db = get_db()
    db.execute("INSERT OR IGNORE INTO modules(name,active,installed_at) "
               "VALUES(?,0,datetime('now'))", (name,))
    db.commit()
    return jsonify({'ok': True, 'name': name})


@app.route('/api/modules/<name>/activate', methods=['PUT'])
def api_modules_activate(name):
    if not loader.exists(name):
        return jsonify({'error': 'Модуль не найден'}), 404
    db = get_db()
    db.execute("INSERT OR REPLACE INTO modules(name,active,installed_at) "
               "VALUES(?,1,COALESCE((SELECT installed_at FROM modules WHERE name=?), datetime('now')))",
               (name, name))
    db.commit()
    return jsonify({'ok': True, 'restart_required': True})


@app.route('/api/modules/<name>/deactivate', methods=['PUT'])
def api_modules_deactivate(name):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO modules(name,active,installed_at) "
               "VALUES(?,0,COALESCE((SELECT installed_at FROM modules WHERE name=?), datetime('now')))",
               (name, name))
    db.commit()
    return jsonify({'ok': True, 'restart_required': True})


@app.route('/api/modules/<name>', methods=['DELETE'])
def api_modules_delete(name):
    if not loader.exists(name):
        return jsonify({'error': 'Модуль не найден'}), 404
    try:
        os.remove(loader.module_path(name))
    except OSError as e:
        return jsonify({'error': f'Не удалось удалить: {e}'}), 500
    db = get_db()
    db.execute("DELETE FROM modules WHERE name=?", (name,))
    db.commit()
    return jsonify({'ok': True, 'restart_required': True})


app.config['loader'] = loader


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)