"""
Модуль интеграции с Overpass API v2.0.
Без OSMnx — прямые запросы через requests, граф через networkx.
"""
import math
import threading
import time
import traceback

MODULE_INFO = {
    'name': 'overpass_module',
    'description': 'Интеграция с Overpass API — импорт дорог и зданий из OSM',
    'version': '2.0',
}

MENU = {'url': '/modules/overpass', 'title': 'Модуль Overpass API'}


DEFAULT_SERVERS = [
    'http://localhost:12345/api',
    'https://overpass.openstreetmap.ru/api',
    'https://overpass-api.de/api',
]

DEFAULT_TIMEOUT = 90
MAX_ATTEMPTS_PER_TILE = 3
RETRY_PAUSE = 3
PREFLIGHT_TIMEOUT = 20


# ------------------------------------------------------------------
#  Состояние
# ------------------------------------------------------------------
_JOB = {
    'running': False,
    'progress': 0,
    'total': 0,
    'message': '',
    'log': [],
    'error': None,
    'finished_at': None,
    'servers_ok': [],
}
_JOB_LOCK = threading.Lock()


def _log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    with _JOB_LOCK:
        _JOB['log'].append(line)
        if len(_JOB['log']) > 2000:
            _JOB['log'] = _JOB['log'][-2000:]
    try:
        print(line, flush=True)
    except Exception:
        pass


def _set_progress(step, total, message=None):
    with _JOB_LOCK:
        _JOB['progress'] = step
        _JOB['total'] = total
        if message is not None:
            _JOB['message'] = message


# ------------------------------------------------------------------
#  URL helpers
# ------------------------------------------------------------------

def _normalize_base(url):
    url = (url or '').strip().rstrip('/')
    if url.endswith('/interpreter'):
        url = url[:-len('/interpreter')]
    return url


def _interpreter_url(base):
    base = _normalize_base(base)
    if base.endswith('/api'):
        return base + '/interpreter'
    return base + '/api/interpreter'


# ------------------------------------------------------------------
#  Проверка серверов
# ------------------------------------------------------------------

def check_servers(timeout=PREFLIGHT_TIMEOUT, servers=None, logger=_log):
    import requests

    servers = servers or DEFAULT_SERVERS
    ok = []
    partial = []
    query = '[out:json][timeout:10];node(1);out;'

    for url in servers:
        probe = _interpreter_url(url)
        t0 = time.time()
        try:
            r = requests.post(probe, data={'data': query}, timeout=timeout)
            dt = time.time() - t0
            if r.status_code == 200:
                ok.append(url)
                logger(f'  ✓ {url} — {dt:.1f} с')
            elif r.status_code == 429:
                partial.append(url)
                logger(f'  ~ {url} — HTTP 429 (rate-limited, но живой, {dt:.1f} с)')
            else:
                logger(f'  ✗ {url} — HTTP {r.status_code} ({dt:.1f} с)')
        except Exception as e:
            dt = time.time() - t0
            logger(f'  ✗ {url} — {type(e).__name__} ({dt:.1f} с)')
    return ok, partial


# ------------------------------------------------------------------
#  Запрос к Overpass
# ------------------------------------------------------------------

def _overpass_request(url, query, timeout):
    """
    POST запрос к Overpass API. Возвращает dict (JSON) или строку-маркер:
      'empty' — пустой ответ,
      ('error', exc) — ошибка.
    """
    import requests
    endpoint = _interpreter_url(url)
    try:
        r = requests.post(endpoint, data={'data': query},
                          timeout=timeout,
                          headers={'User-Agent': 'map_app/2.0'})
        if r.status_code == 429:
            return ('rate_limited', None)
        if r.status_code != 200:
            return ('error', Exception(f'HTTP {r.status_code}'))
        data = r.json()
        if not data.get('elements'):
            return 'empty'
        return data
    except Exception as e:
        return ('error', e)


# ------------------------------------------------------------------
#  Парсинг данных из Overpass в структуры для импорта
# ------------------------------------------------------------------

def _build_road_graph(elements, network_type='drive'):
    """
    Возвращает (nodes, edges):
      nodes — {osm_node_id: {'lat': ..., 'lon': ..., 'tags': {...}}}
      edges — [(from_osm_id, to_osm_id, {'name': ..., 'oneway': bool, 'osm_id': ...})]
    """
    nodes = {}
    ways = []
    for el in elements:
        if el['type'] == 'node':
            nodes[el['id']] = {
                'lat': el['lat'],
                'lon': el['lon'],
                'tags': el.get('tags', {}) or {},
            }
        elif el['type'] == 'way':
            ways.append(el)

    edges = []
    for w in ways:
        tags = w.get('tags', {}) or {}
        # берём только дороги
        if 'highway' not in tags:
            continue
        name = tags.get('name', '')
        if isinstance(name, list):
            name = name[0] if name else ''
        oneway_raw = tags.get('oneway', '')
        oneway = str(oneway_raw).lower() in ('yes', 'true', '1', '-1')
        reverse_oneway = str(oneway_raw) == '-1'

        n_ids = w.get('nodes', [])
        for i in range(len(n_ids) - 1):
            a = n_ids[i]
            b = n_ids[i + 1]
            if a not in nodes or b not in nodes:
                continue
            if reverse_oneway:
                edges.append((b, a, {'name': name, 'oneway': True, 'osm_id': w['id']}))
            else:
                edges.append((a, b, {'name': name, 'oneway': oneway, 'osm_id': w['id']}))
                if not oneway:
                    edges.append((b, a, {'name': name, 'oneway': False, 'osm_id': w['id']}))
    return nodes, edges

def _build_buildings(elements):
    """
    Возвращает список зданий с оптимальным повернутым прямоугольником
    (minimum rotated rectangle) вокруг контура way.
    [{'name': ..., 'lat': ..., 'lon': ..., 'w': м, 'h': м, 'rotation': град, 'osm_id': ...}]
    """
    nodes = {}
    ways = []
    for el in elements:
        if el['type'] == 'node':
            nodes[el['id']] = (el['lat'], el['lon'])
        elif el['type'] == 'way':
            ways.append(el)

    buildings = []
    for w in ways:
        tags = w.get('tags', {}) or {}
        if 'building' not in tags and 'building:part' not in tags:
            continue

        n_ids = w.get('nodes', [])
        coords = [nodes[nid] for nid in n_ids if nid in nodes]
        if len(coords) < 3:
            continue

        try:
            import numpy as np
            from shapely.geometry import Polygon

            # переводим в локальные метры относительно первого узла
            lat0, lon0 = coords[0]
            m_per_deg_lat = 111320.0
            m_per_deg_lon = 111320.0 * abs(math.cos(math.radians(lat0)))

            xs = [(c[1] - lon0) * m_per_deg_lon for c in coords]
            ys = [(c[0] - lat0) * m_per_deg_lat for c in coords]

            poly = Polygon(zip(xs, ys))
            if poly.is_empty or not poly.is_valid:
                poly = poly.buffer(0)
                if poly.is_empty:
                    continue

            # minimum rotated rectangle
            mrr = poly.minimum_rotated_rectangle
            if mrr.is_empty:
                continue

            # координаты 4 углов mrr
            xs_r, ys_r = mrr.exterior.coords.xy
            pts = list(zip(xs_r, ys_r))[:4]  # 4 угла
            if len(pts) < 4:
                continue

            # центр прямоугольника
            cx_m = sum(p[0] for p in pts) / 4.0
            cy_m = sum(p[1] for p in pts) / 4.0

            # длины сторон
            def dist(a, b):
                return math.hypot(b[0] - a[0], b[1] - a[1])
            d1 = dist(pts[0], pts[1])
            d2 = dist(pts[1], pts[2])
            w_m = max(d1, d2)
            h_m = min(d1, d2)

            if w_m < 2: w_m = 2
            if h_m < 2: h_m = 2

            # угол поворота (в градусах от востока против часовой стрелки)
            # если d1 — ширина, то сторона pts[0]→pts[1]
            if d1 >= d2:
                ang = math.atan2(pts[1][1] - pts[0][1],
                                 pts[1][0] - pts[0][0])
            else:
                ang = math.atan2(pts[2][1] - pts[1][1],
                                 pts[2][0] - pts[1][0])
            rotation_deg = math.degrees(ang) % 360.0

            # возвращаем в lat/lon
            lon_c = lon0 + cx_m / m_per_deg_lon
            lat_c = lat0 + cy_m / m_per_deg_lat

        except Exception:
            # если что-то пошло не так — сваливаемся в старую логику bbox
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            lat_c = sum(lats) / len(lats)
            lon_c = sum(lons) / len(lons)
            dlat = max(lats) - min(lats)
            dlon = max(lons) - min(lons)
            w_m = max(2.0, dlon * 111320 * abs(math.cos(math.radians(lat_c))))
            h_m = max(2.0, dlat * 111320)
            rotation_deg = 0.0

        name = tags.get('name', '') or tags.get('addr:street', '') or ''
        if isinstance(name, list):
            name = name[0] if name else ''

        buildings.append({
            'name': name,
            'lat': lat_c,
            'lon': lon_c,
            'w': w_m,
            'h': h_m,
            'rotation': rotation_deg,
            'osm_id': w['id'],
        })
    return buildings

# ------------------------------------------------------------------
#  Импорт в БД
# ------------------------------------------------------------------

def _import_roads(db, nodes, edges):
    """
    Импортирует узлы и рёбра в БД. Дедуп по osm_id.
    Возвращает (nodes_added, edges_added, nodes_skipped, edges_skipped).
    """
    node_map = {}     # osm_id -> db_id
    n_added = n_skipped = 0

    for osm_id, node in nodes.items():
        row = db.execute("SELECT id FROM nodes WHERE osm_id=?",
                         (osm_id,)).fetchone()
        if row:
            node_map[osm_id] = row['id']
            n_skipped += 1
            continue
        name = ''
        tags = node.get('tags', {})
        # тип узла: 1 — перекрёсток по умолчанию; 2 — остановка; 3 — переход; 4 — светофор
        type_id = 1
        if tags.get('highway') == 'bus_stop' or tags.get('public_transport') == 'platform':
            type_id = 2
        elif tags.get('highway') == 'crossing' or tags.get('crossing'):
            type_id = 3
        elif tags.get('highway') == 'traffic_signals':
            type_id = 4

        cur = db.execute(
            "INSERT INTO nodes(name,lat,lon,type_id,osm_id) VALUES(?,?,?,?,?)",
            (name, node['lat'], node['lon'], type_id, osm_id))
        node_map[osm_id] = cur.lastrowid
        n_added += 1

    e_added = e_skipped = 0
    for a_osm, b_osm, edata in edges:
        a_db = node_map.get(a_osm)
        b_db = node_map.get(b_osm)
        if not a_db or not b_db:
            continue
        # Дедуп по паре (from,to) + name — у нас osm_id одинаковый для обоих направлений,
        # поэтому проверяем ещё и направление
        existing = db.execute(
            "SELECT id FROM edges WHERE from_node=? AND to_node=? AND name=?",
            (a_db, b_db, edata.get('name', ''))).fetchone()
        if existing:
            e_skipped += 1
            continue
        db.execute(
            "INSERT INTO edges(from_node,to_node,name,oneway,osm_id) VALUES(?,?,?,?,?)",
            (a_db, b_db, edata.get('name', ''),
             1 if edata.get('oneway') else 0, edata.get('osm_id')))
        e_added += 1

    return n_added, e_added, n_skipped, e_skipped


def _import_buildings(db, buildings):
    b_added = b_skipped = 0
    for b in buildings:
        osm_id = b['osm_id']
        row = db.execute("SELECT id FROM buildings WHERE osm_id=?",
                         (osm_id,)).fetchone()
        if row:
            b_skipped += 1
            continue
        db.execute(
            "INSERT INTO buildings(name,lat,lon,w,h,rotation,type_id,osm_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (b['name'], b['lat'], b['lon'], b['w'], b['h'],
             b.get('rotation', 0.0), None, osm_id))
        b_added += 1
    return b_added, b_skipped


# ------------------------------------------------------------------
#  Основной поток импорта
# ------------------------------------------------------------------

def _tile_size_km(tile):
    try:
        minx, miny, maxx, maxy = tile.bounds
        w_km = (maxx - minx) * 111.32 * math.cos(math.radians((miny + maxy) / 2))
        h_km = (maxy - miny) * 111.32
        return w_km, h_km
    except Exception:
        return 0, 0


def _roads_query(minx, miny, maxx, maxy, network_type):
    """Overpass QL запрос для дорог."""
    # network_type drive → фильтр только автомобильных дорог
    if network_type == 'drive':
        highway_filter = '["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|service|living_street|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"]'
    elif network_type == 'walk':
        highway_filter = '["highway"~"^(footway|path|pedestrian|steps|cycleway|track)$"]'
    elif network_type == 'bike':
        highway_filter = '["highway"~"^(cycleway|path|track)$"]'
    else:
        highway_filter = '["highway"]'

    return f'''[out:json][timeout:60];
way{highway_filter}({miny},{minx},{maxy},{maxx});
(._;>;);
out body;'''


def _buildings_query(minx, miny, maxx, maxy):
    return f'''[out:json][timeout:60];
way["building"]({miny},{minx},{maxy},{maxx});
(._;>;);
out body;'''


def _run_import(app, place, network, grid, do_roads, do_buildings, timeout,
                custom_servers=None):
    with _JOB_LOCK:
        _JOB.update({
            'running': True, 'progress': 0, 'total': 0,
            'message': 'Инициализация', 'log': [],
            'error': None, 'finished_at': None,
            'servers_ok': [],
        })

    stats = {'roads_ok': 0, 'roads_empty': 0, 'roads_err': 0,
             'bld_ok': 0,   'bld_empty': 0,   'bld_err': 0,
             'nodes_added': 0, 'edges_added': 0,
             'buildings_added': 0,
             'nodes_skipped': 0, 'edges_skipped': 0,
             'buildings_skipped': 0}

    try:
        _log(f'Импорт стартовал (timeout={timeout} с, сетка={grid}×{grid})')

        candidate_servers = custom_servers or DEFAULT_SERVERS

        _log('Preflight-проверка серверов Overpass:')
        ok, partial = check_servers(
            timeout=PREFLIGHT_TIMEOUT, servers=candidate_servers)

        live = ok + partial
        if not live:
            raise RuntimeError(
                'Ни один Overpass-сервер не отвечает. Проверьте '
                'доступность сервера в настройках.'
            )

        with _JOB_LOCK:
            _JOB['servers_ok'] = list(live)
        _log(f'Используем серверы: {live}')

        # Получаем bbox города через Nominatim
        import requests
        _log(f'Загрузка границ: {place}')
        nom = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': place, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'map_app/2.0'},
            timeout=30,
        ).json()
        if not nom:
            raise RuntimeError(f'Не удалось найти место: {place}')
        bbox = nom[0]['boundingbox']  # [minlat, maxlat, minlon, maxlon] (строки)
        miny, maxy = float(bbox[0]), float(bbox[1])
        minx, maxx = float(bbox[2]), float(bbox[3])
        _log(f'BBox: {minx:.4f}, {miny:.4f}, {maxx:.4f}, {maxy:.4f}')

        # Делим bbox на сетку N×N
        grid = max(1, min(10, int(grid)))
        tiles = []
        for i in range(grid):
            for j in range(grid):
                tiles.append({
                    'minx': minx + (maxx - minx) * i / grid,
                    'maxx': minx + (maxx - minx) * (i + 1) / grid,
                    'miny': miny + (maxy - miny) * j / grid,
                    'maxy': miny + (maxy - miny) * (j + 1) / grid,
                })

        total_steps = (len(tiles) if do_roads else 0) + \
                      (len(tiles) if do_buildings else 0)
        _set_progress(0, total_steps)

        with app.app_context():
            from db import get_db
            db = get_db()
            step = 0

            if do_roads:
                for i, t in enumerate(tiles):
                    step += 1
                    w_km = (t['maxx'] - t['minx']) * 111.32 * abs(math.cos(math.radians((t['miny'] + t['maxy']) / 2)))
                    h_km = (t['maxy'] - t['miny']) * 111.32
                    msg = (f'Дороги: тайл {i + 1}/{len(tiles)} '
                           f'({w_km:.1f}×{h_km:.1f} км)')
                    _set_progress(step, total_steps, msg)
                    _log(msg)

                    query = _roads_query(t['minx'], t['miny'], t['maxx'], t['maxy'], network)
                    data = None
                    for attempt in range(MAX_ATTEMPTS_PER_TILE):
                        server = live[attempt % len(live)]
                        _log(f'    попытка {attempt + 1}/{MAX_ATTEMPTS_PER_TILE} → {server}')
                        t0 = time.time()
                        res = _overpass_request(server, query, timeout)
                        dt = time.time() - t0
                        if res == 'empty':
                            _log(f'    → пусто ({dt:.1f} с)')
                            data = 'empty'
                            break
                        if isinstance(res, tuple):
                            kind, err = res
                            if kind == 'rate_limited':
                                _log(f'    → сервер занят (429), пробуем другой')
                            else:
                                _log(f'    → ошибка: {err} ({dt:.1f} с)')
                            time.sleep(RETRY_PAUSE * (attempt + 1))
                            continue
                        _log(f'    → OK ({dt:.1f} с, элементов {len(res["elements"])})')
                        data = res
                        break

                    if data == 'empty':
                        stats['roads_empty'] += 1
                        continue
                    if not isinstance(data, dict):
                        stats['roads_err'] += 1
                        continue

                    try:
                        nodes, edges = _build_road_graph(data['elements'], network)
                        n_added, e_added, n_skip, e_skip = _import_roads(db, nodes, edges)
                        db.commit()
                        stats['roads_ok'] += 1
                        stats['nodes_added'] += n_added
                        stats['edges_added'] += e_added
                        stats['nodes_skipped'] += n_skip
                        stats['edges_skipped'] += e_skip
                        _log(f'    → +узлов {n_added} (skip {n_skip}), '
                             f'+рёбер {e_added} (skip {e_skip})')
                    except Exception as e:
                        db.rollback()
                        stats['roads_err'] += 1
                        _log(f'    → ошибка вставки: {type(e).__name__}: {e}')

            if do_buildings:
                for i, t in enumerate(tiles):
                    step += 1
                    w_km = (t['maxx'] - t['minx']) * 111.32 * abs(math.cos(math.radians((t['miny'] + t['maxy']) / 2)))
                    h_km = (t['maxy'] - t['miny']) * 111.32
                    msg = (f'Здания: тайл {i + 1}/{len(tiles)} '
                           f'({w_km:.1f}×{h_km:.1f} км)')
                    _set_progress(step, total_steps, msg)
                    _log(msg)

                    query = _buildings_query(t['minx'], t['miny'], t['maxx'], t['maxy'])
                    data = None
                    for attempt in range(MAX_ATTEMPTS_PER_TILE):
                        server = live[attempt % len(live)]
                        _log(f'    попытка {attempt + 1}/{MAX_ATTEMPTS_PER_TILE} → {server}')
                        t0 = time.time()
                        res = _overpass_request(server, query, timeout)
                        dt = time.time() - t0
                        if res == 'empty':
                            _log(f'    → пусто ({dt:.1f} с)')
                            data = 'empty'
                            break
                        if isinstance(res, tuple):
                            kind, err = res
                            if kind == 'rate_limited':
                                _log(f'    → сервер занят (429), пробуем другой')
                            else:
                                _log(f'    → ошибка: {err} ({dt:.1f} с)')
                            time.sleep(RETRY_PAUSE * (attempt + 1))
                            continue
                        _log(f'    → OK ({dt:.1f} с, элементов {len(res["elements"])})')
                        data = res
                        break

                    if data == 'empty':
                        stats['bld_empty'] += 1
                        continue
                    if not isinstance(data, dict):
                        stats['bld_err'] += 1
                        continue

                    try:
                        buildings = _build_buildings(data['elements'])
                        b_add, b_skip = _import_buildings(db, buildings)
                        db.commit()
                        stats['bld_ok'] += 1
                        stats['buildings_added'] += b_add
                        stats['buildings_skipped'] += b_skip
                        _log(f'    → +зданий {b_add} (skip {b_skip})')
                    except Exception as e:
                        db.rollback()
                        stats['bld_err'] += 1
                        _log(f'    → ошибка вставки: {type(e).__name__}: {e}')

            summary = (
                f'Готово. '
                f'Дороги: {stats["roads_ok"]} ok / '
                f'{stats["roads_empty"]} пусто / '
                f'{stats["roads_err"]} сбой. '
                f'Здания: {stats["bld_ok"]} ok / '
                f'{stats["bld_empty"]} пусто / '
                f'{stats["bld_err"]} сбой. '
                f'ИТОГО: +узлов {stats["nodes_added"]}, '
                f'+рёбер {stats["edges_added"]}, '
                f'+зданий {stats["buildings_added"]}.'
            )
            _log(summary)
            with _JOB_LOCK:
                _JOB['message'] = summary

    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        with _JOB_LOCK:
            _JOB['error'] = err
        _log(f'КРИТИЧЕСКАЯ ОШИБКА: {err}')
        _log(traceback.format_exc())
    finally:
        with _JOB_LOCK:
            _JOB['running'] = False
            _JOB['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')


# ------------------------------------------------------------------
#  register()
# ------------------------------------------------------------------

def register(app, loader):
    from flask import request, jsonify, render_template
    from db import get_db, get_setting, set_setting

    def _servers_from_settings(db):
        raw = get_setting(db, 'overpass_servers', '')
        if raw:
            return [s.strip() for s in raw.splitlines() if s.strip()]
        return DEFAULT_SERVERS

    @app.route('/modules/overpass')
    def overpass_page():
        return render_template('overpass.html',
                               info=MODULE_INFO, active='modules')

    @app.route('/modules/overpass/settings', methods=['GET', 'POST'])
    def overpass_settings():
        db = get_db()
        if request.method == 'GET':
            servers = _servers_from_settings(db)
            return jsonify({
                'place':        get_setting(db, 'overpass_place',
                                            'Йошкар-Ола, Россия'),
                'network':      get_setting(db, 'overpass_network', 'drive'),
                'grid':         int(get_setting(db, 'overpass_grid', '3')),
                'timeout':      int(get_setting(db, 'overpass_timeout',
                                                str(DEFAULT_TIMEOUT))),
                'do_roads':     get_setting(db, 'overpass_roads', '1') == '1',
                'do_buildings': get_setting(db, 'overpass_buildings', '1') == '1',
                'servers_text': '\n'.join(servers),
                'servers_default': '\n'.join(DEFAULT_SERVERS),
            })
        d = request.get_json() or {}
        set_setting(db, 'overpass_place',
                    (d.get('place') or 'Йошкар-Ола, Россия').strip())
        set_setting(db, 'overpass_network', d.get('network', 'drive'))
        set_setting(db, 'overpass_grid', str(int(d.get('grid', 3))))
        set_setting(db, 'overpass_timeout',
                    str(int(d.get('timeout', DEFAULT_TIMEOUT))))
        set_setting(db, 'overpass_roads', '1' if d.get('do_roads') else '0')
        set_setting(db, 'overpass_buildings',
                    '1' if d.get('do_buildings') else '0')
        servers_text = (d.get('servers_text') or '').strip()
        if servers_text and servers_text != '\n'.join(DEFAULT_SERVERS):
            set_setting(db, 'overpass_servers', servers_text)
        elif not servers_text:
            set_setting(db, 'overpass_servers', '')
        db.commit()
        return jsonify({'ok': True})

    @app.route('/modules/overpass/test_servers', methods=['POST'])
    def overpass_test_servers():
        d = request.get_json(silent=True) or {}
        timeout = int(d.get('timeout', PREFLIGHT_TIMEOUT))
        custom = d.get('servers')
        if custom is None:
            db = get_db()
            custom = _servers_from_settings(db)
        servers = custom if custom else DEFAULT_SERVERS

        logs = []

        def logger(msg):
            logs.append(msg)

        ok, partial = check_servers(timeout=timeout, servers=servers,
                                    logger=logger)
        return jsonify({'ok': ok, 'partial': partial, 'log': logs})

    @app.route('/modules/overpass/import', methods=['POST'])
    def overpass_import():
        with _JOB_LOCK:
            if _JOB['running']:
                return jsonify({'error': 'Импорт уже запущен'}), 409
        db = get_db()
        place = get_setting(db, 'overpass_place', 'Йошкар-Ола, Россия')
        network = get_setting(db, 'overpass_network', 'drive')
        grid = int(get_setting(db, 'overpass_grid', '3'))
        timeout = int(get_setting(db, 'overpass_timeout', str(DEFAULT_TIMEOUT)))
        do_roads = get_setting(db, 'overpass_roads', '1') == '1'
        do_buildings = get_setting(db, 'overpass_buildings', '1') == '1'
        servers = _servers_from_settings(db)

        t = threading.Thread(
            target=_run_import,
            args=(app, place, network, grid, do_roads, do_buildings, timeout,
                  servers),
            daemon=True)
        t.start()
        return jsonify({'ok': True})

    @app.route('/modules/overpass/status')
    def overpass_status():
        with _JOB_LOCK:
            return jsonify(dict(_JOB))

    @app.route('/modules/overpass/clear', methods=['POST'])
    def overpass_clear():
        db = get_db()
        db.execute("DELETE FROM edges WHERE osm_id IS NOT NULL")
        db.execute("DELETE FROM data WHERE type='node' AND target_id IN "
                   "(SELECT id FROM nodes WHERE osm_id IS NOT NULL)")
        db.execute("DELETE FROM nodes WHERE osm_id IS NOT NULL")
        db.execute("DELETE FROM data WHERE type='building' AND target_id IN "
                   "(SELECT id FROM buildings WHERE osm_id IS NOT NULL)")
        db.execute("DELETE FROM buildings WHERE osm_id IS NOT NULL")
        db.commit()
        return jsonify({'ok': True})