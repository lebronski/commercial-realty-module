"""
Веб-часть: Flask Blueprint со своими шаблонами и статикой, фоновый импорт.

От основного проекта нужен только объект Flask app. Всё остальное
(шаблон base.html, список серверов Overpass) используется, если есть,
и заменяется своим, если нет.
"""
import csv
import io
import threading
import time
import traceback

from flask import Blueprint, Response, jsonify, render_template, request

import os

from . import calibration, model, osm
from .storage import Storage

BP_NAME = 'commerce_realty'
DEFAULT_PREFIX = '/modules/commerce'
DEFAULT_REGION = 'RU-ME'          # ISO3166-2 код региона в OSM
DEFAULT_TIMEOUT = 180
DEFAULT_OPEX = 0.20               # доля операционных расходов от дохода

OWN_LAYOUT = 'commerce_realty/layout.html'


# ------------------------------------------------------------------
#  Фоновая задача
# ------------------------------------------------------------------
_JOB = {
    'running': False, 'progress': 0, 'total': 0, 'message': '',
    'log': [], 'error': None, 'finished_at': None,
}
_JOB_LOCK = threading.Lock()


def _log(msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    with _JOB_LOCK:
        _JOB['log'].append(line)
        if len(_JOB['log']) > 1000:
            _JOB['log'] = _JOB['log'][-1000:]
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
#  Настройки
# ------------------------------------------------------------------

def _rates(store):
    rates = {k: dict(v) for k, v in model.DEFAULT_RATES.items()}
    saved = store.get('rates') or {}
    if isinstance(saved, dict):
        for cat, v in saved.items():
            if cat in rates and isinstance(v, dict):
                for f in ('price', 'rent'):
                    try:
                        rates[cat][f] = float(v.get(f, rates[cat][f]))
                    except (TypeError, ValueError):
                        pass
    return rates


def _opex(store):
    try:
        return max(0.0, min(0.9, float(store.get('opex', DEFAULT_OPEX))))
    except (TypeError, ValueError):
        return DEFAULT_OPEX


def _host_servers():
    """
    Список серверов из настроек модуля Overpass основного проекта, если
    он там есть. Любая ошибка — не наше дело: вернём пустой список.
    """
    try:
        import sqlite3
        import db as host_db
        con = sqlite3.connect(host_db.DB_PATH)
        try:
            row = con.execute("SELECT value FROM settings "
                              "WHERE key='overpass_servers'").fetchone()
        finally:
            con.close()
        return [s.strip() for s in (row[0] if row else '').splitlines()
                if s.strip()]
    except Exception:
        return []


def _servers(store):
    saved = store.get('servers')
    if isinstance(saved, list) and saved:
        return saved
    out = []
    for s in _host_servers() + osm.FALLBACK_SERVERS:
        if s not in out:
            out.append(s)
    return out


# ------------------------------------------------------------------
#  Импорт
# ------------------------------------------------------------------

def _run_import(store, region, timeout, servers):
    total = 6
    with _JOB_LOCK:
        _JOB.update({'running': True, 'progress': 0, 'total': total,
                     'message': 'Инициализация', 'log': [],
                     'error': None, 'finished_at': None})
    try:
        _log(f'Старт: регион {region}, серверы {servers}')
        raw = osm.fetch_all(region, timeout, servers, _log,
                            lambda i, msg: _set_progress(i, total, msg))

        _set_progress(4, total, 'Расчёт факторов и адресов')
        objs = model.parse_objects(raw[0])
        context = raw[1]
        places = model.parse_places(context)
        stops = [e for e in context
                 if e['type'] == 'node' and not e.get('tags')]
        roads = [e for e in context if e['type'] == 'way']
        if not places:
            raise RuntimeError('Не удалось получить населённые пункты — '
                               'без них ликвидность не рассчитать.')
        _log(f'Объектов: {len(objs)}, населённых пунктов: {len(places)}, '
             f'остановок: {len(stops)}, дорог: {len(roads)}, '
             f'жилых зданий: {len(raw[2])}')
        model.compute_factors(objs, places, stops, roads, raw[2])
        st = model.assign_addresses(objs, raw[3])
        store.replace_geo(calibration.address_points(raw[3]), places)
        n_geo = _geocode_listings(store)
        if n_geo is not None:
            _log(f'Объявления: привязано к карте {n_geo}')
        _log(f'Адреса: свои {st["osm"]}, по зданию {st["building"]}, '
             f'ближайший дом {st["nearby"]}, только улица {st["street"]}, '
             f'не найдено {st["none"]}')

        _set_progress(5, total, 'Расчёт цен и запись в базу модуля')
        rates, opex = _rates(store), _opex(store)
        for o in objs:
            model.calc_economics(o, rates, opex)
        store.replace_objects(objs)

        summary = f'Готово: загружено объектов — {len(objs)}'
        _log(summary)
        _set_progress(total, total, summary)
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        with _JOB_LOCK:
            _JOB['error'] = err
        _log(f'ОШИБКА: {err}')
        _log(traceback.format_exc())
    finally:
        with _JOB_LOCK:
            _JOB['running'] = False
            _JOB['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')



# ------------------------------------------------------------------
#  Реальные объявления
# ------------------------------------------------------------------

SEED_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seed',
                        'listings_yoshkar-ola_2026-10.csv')


def _geocode(store, items):
    """Проставляет lat/lon/geo объявлениям. None — нет адресов OSM."""
    index = store.address_index()
    if not index:
        return None
    places = store.places()
    n = 0
    for it in items:
        g = calibration.geocode(it, index, places)
        if g:
            it['lat'], it['lon'], it['geo'] = g
            n += 1
        else:
            it['lat'] = it['lon'] = None
            it['geo'] = ''
    return n


def _geocode_listings(store):
    items = store.listings()
    if not items:
        return None
    n = _geocode(store, items)
    if n is not None:
        store.update_listing_geo(items)
    return n


def _load_listings(store, text, replace):
    items, errors = calibration.parse_csv(text)
    n_geo = _geocode(store, items)
    store.add_listings(items, replace=replace)
    return {'ok': True, 'added': len(items), 'errors': errors[:50],
            'geocoded': n_geo,
            'warning': None if n_geo is not None else
            'Адреса OSM ещё не загружены — объявления сохранены, но на карту '
            'попадут после «Загрузить данные из Overpass».'}


def _object_rows(store):
    cols = ['id'] + model.ALL_COLS
    return [dict(r) for r in store.rows(cols)]


# ------------------------------------------------------------------
#  Blueprint
# ------------------------------------------------------------------

def _pick_layout(app):
    """
    base.html основного проекта, если в нём есть блок content;
    иначе — собственный каркас страницы.
    """
    try:
        src, _f, _u = app.jinja_loader.get_source(app.jinja_env, 'base.html')
        if 'block content' in src:
            return 'base.html'
    except Exception:
        pass
    return OWN_LAYOUT


def create_blueprint(store, layout_resolver):
    bp = Blueprint(BP_NAME, __name__,
                   template_folder='templates',
                   static_folder='static',
                   static_url_path='/static')

    @bp.route('/')
    def page():
        from . import MODULE_INFO
        return render_template('commerce_realty/page.html',
                               layout=layout_resolver(),
                               info=MODULE_INFO, active='modules')

    @bp.route('/settings', methods=['GET', 'POST'])
    def settings():
        if request.method == 'GET':
            return jsonify({
                'region': store.get('region', DEFAULT_REGION),
                'timeout': store.get('timeout', DEFAULT_TIMEOUT),
                'opex': _opex(store),
                'rates': _rates(store),
                'categories': model.CATEGORIES,
                'servers': _servers(store),
            })
        d = request.get_json(silent=True) or {}
        try:
            region = str(d.get('region') or DEFAULT_REGION).strip().upper()
            if not region.replace('-', '').isalnum() or len(region) > 10:
                return jsonify({'error': 'Неверный код региона'}), 400
            timeout = max(30, min(900, int(d.get('timeout',
                                                 DEFAULT_TIMEOUT))))
            opex = max(0.0, min(0.9, float(d.get('opex', DEFAULT_OPEX))))
            rates = {}
            for cat in model.DEFAULT_RATES:
                v = (d.get('rates') or {}).get(cat) or {}
                price = float(v.get('price',
                                    model.DEFAULT_RATES[cat]['price']))
                rent = float(v.get('rent', model.DEFAULT_RATES[cat]['rent']))
                if price <= 0 or rent <= 0:
                    return jsonify({'error':
                                    'Ставки должны быть больше 0'}), 400
                rates[cat] = {'price': price, 'rent': rent}
            servers = [s.strip() for s in d.get('servers') or []
                       if isinstance(s, str) and s.strip()]
        except (TypeError, ValueError, AttributeError):
            return jsonify({'error': 'Неверные значения настроек'}), 400
        values = {'region': region, 'timeout': timeout, 'opex': opex,
                  'rates': rates}
        if servers:
            values['servers'] = servers
        store.set_many(values)
        return jsonify({'ok': True})

    @bp.route('/import', methods=['POST'])
    def start_import():
        with _JOB_LOCK:
            if _JOB['running']:
                return jsonify({'error': 'Загрузка уже идёт'}), 409
            _JOB['running'] = True
        threading.Thread(
            target=_run_import,
            args=(store, store.get('region', DEFAULT_REGION),
                  int(store.get('timeout', DEFAULT_TIMEOUT)),
                  _servers(store)),
            daemon=True).start()
        return jsonify({'ok': True})

    @bp.route('/status')
    def status():
        with _JOB_LOCK:
            return jsonify(dict(_JOB))

    @bp.route('/recalc', methods=['POST'])
    def recalc():
        """Пересчёт цен по текущим ставкам без повторной загрузки."""
        rates, opex = _rates(store), _opex(store)
        objs = [model.calc_economics(dict(r), rates, opex)
                for r in store.factor_rows()]
        store.update_economics(objs)
        return jsonify({'ok': True, 'count': len(objs)})

    @bp.route('/objects')
    def objects():
        cols = ['id'] + model.ALL_COLS
        return jsonify({'columns': cols,
                        'rows': [list(r) for r in store.rows(cols)],
                        'categories': model.CATEGORIES})

    @bp.route('/export.csv')
    def export():
        cols = ['id'] + model.ALL_COLS
        buf = io.StringIO()
        wr = csv.writer(buf, delimiter=';')
        wr.writerow(cols)
        for r in store.rows(cols, order='cap_rate DESC'):
            wr.writerow(list(r))
        return Response(
            '﻿' + buf.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition':
                     'attachment; filename=commerce_objects.csv'})

    @bp.route('/listings')
    def listings():
        items = store.listings()
        # объекты карты с тем же адресом (дом найден точно)
        by_addr = {}
        for o in store.rows(['id', 'address', 'address_src']):
            if o['address_src'] in ('osm', 'building'):
                key = calibration.normalize_address(o['address'])
                if key and key[1]:
                    by_addr.setdefault(key, []).append(o['id'])
        for it in items:
            key = calibration.normalize_address(it['address'])
            it['object_ids'] = by_addr.get(key, []) if key else []
        return jsonify({'listings': items, 'deals': calibration.DEALS,
                        'has_seed': os.path.isfile(SEED_CSV)})

    @bp.route('/listings/upload', methods=['POST'])
    def listings_upload():
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'Файл не передан'}), 400
        raw = f.read()
        for enc in ('utf-8-sig', 'cp1251'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return jsonify({'error': 'Не удалось прочитать файл: нужна '
                            'кодировка UTF-8 или Windows-1251'}), 400
        return jsonify(_load_listings(store, text,
                                      request.form.get('replace') == '1'))

    @bp.route('/listings/seed', methods=['POST'])
    def listings_seed():
        if not os.path.isfile(SEED_CSV):
            return jsonify({'error': 'Файл с примером не найден'}), 404
        with open(SEED_CSV, encoding='utf-8-sig') as fh:
            return jsonify(_load_listings(store, fh.read(), replace=True))

    @bp.route('/listings/clear', methods=['POST'])
    def listings_clear():
        store.clear_listings()
        return jsonify({'ok': True})

    @bp.route('/listings/template.csv')
    def listings_template():
        rows = [
            calibration.CSV_COLUMNS,
            ['sale', 'retail', 'Помещение свободного назначения',
             'ул. Луначарского, 52', 'Йошкар-Ола', '288', '18000000',
             'Авито', 'https://...', '2026-10-17'],
            ['rent', 'office', 'Офисное помещение', 'Ленинский проспект, 68',
             'Йошкар-Ола', '24', '22000', 'ЦИАН', 'https://...',
             '2026-10-17'],
        ]
        buf = io.StringIO()
        csv.writer(buf, delimiter=';').writerows(rows)
        return Response('\ufeff' + buf.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition':
                                 'attachment; filename=listings_template.csv'})

    @bp.route('/calibration')
    def calibration_report():
        items = [l for l in store.listings() if l['lat'] is not None]
        rep = calibration.calibrate(items, _object_rows(store),
                                    _rates(store))
        rep['current'] = _rates(store)
        rep['defaults'] = model.DEFAULT_RATES
        rep['categories'] = model.CATEGORIES
        rep['n_total'] = len(store.listings())
        rep['n_geo'] = len(items)
        return jsonify(rep)

    @bp.route('/calibration/apply', methods=['POST'])
    def calibration_apply():
        items = [l for l in store.listings() if l['lat'] is not None]
        rep = calibration.calibrate(items, _object_rows(store),
                                    _rates(store))
        if not any(g['applied'] for g in rep['groups']):
            return jsonify({'error': 'Недостаточно привязанных объявлений '
                            'для калибровки'}), 400
        store.set_many({'rates': rep['rates']})
        rates, opex = _rates(store), _opex(store)
        objs = [model.calc_economics(dict(r), rates, opex)
                for r in store.factor_rows()]
        store.update_economics(objs)
        return jsonify({'ok': True, 'rates': rates, 'count': len(objs)})

    @bp.route('/clear', methods=['POST'])
    def clear():
        store.clear()
        return jsonify({'ok': True})

    return bp


def register(app, url_prefix=None, db_path=None):
    """
    Подключает модуль к Flask-приложению. Повторный вызов ничего не делает.
    Параметры можно задать и через app.config: COMMERCE_URL_PREFIX,
    COMMERCE_DB_PATH.
    """
    if BP_NAME in app.blueprints:
        return app.blueprints[BP_NAME]
    url_prefix = (url_prefix or app.config.get('COMMERCE_URL_PREFIX')
                  or DEFAULT_PREFIX)
    store = Storage(db_path or app.config.get('COMMERCE_DB_PATH'))

    layout = {}

    def resolve_layout():
        # Определяется при первом показе страницы, когда у приложения
        # уже настроены все загрузчики шаблонов.
        if 'name' not in layout:
            layout['name'] = _pick_layout(app)
        return layout['name']

    bp = create_blueprint(store, resolve_layout)
    app.register_blueprint(bp, url_prefix=url_prefix)
    app.config.setdefault('COMMERCE_STORAGE', store)
    return bp
