"""
Запросы к Overpass API. Без Flask; журнал пишется через переданную
функцию log(msg).
"""
import time

from .model import (FOOD_AMENITY, SERVICE_AMENITY, AUTO_AMENITY,
                    HOTEL_TOURISM, BUILDING_CATEGORY)

FALLBACK_SERVERS = [
    'http://localhost:12345/api',
    'https://overpass-api.de/api',
    'https://maps.mail.ru/osm/tools/overpass/api',
]
RETRY_ROUNDS = 4
RETRY_PAUSE = 20
STEP_PAUSE = 15                   # пауза между запросами к публичным серверам


def _interpreter_url(base):
    base = (base or '').strip().rstrip('/')
    if base.endswith('/interpreter'):
        return base
    if base.endswith('/api'):
        return base + '/interpreter'
    return base + '/api/interpreter'


def request(servers, query, timeout, dead, log=print):
    """
    Пробует серверы по очереди, возвращает (elements, адрес сервера).
    Занятый сервер (429/504) опрашивается повторно после паузы;
    недоступный попадает в dead и в следующих запросах пропускается.
    """
    import requests
    last = None
    for attempt in range(RETRY_ROUNDS):
        for url in servers:
            if url in dead:
                continue
            t0 = time.time()
            try:
                r = requests.post(
                    _interpreter_url(url), data={'data': query},
                    timeout=(10, timeout + 30),
                    headers={'User-Agent': 'map_app/commerce-1.0'})
                if r.status_code != 200:
                    raise RuntimeError(f'HTTP {r.status_code}')
                data = r.json()
                remark = data.get('remark')
                if remark and not data.get('elements'):
                    raise RuntimeError(remark[:200])
                els = data.get('elements', [])
                log(f'    {url} → {len(els)} элементов '
                     f'({time.time() - t0:.1f} с)')
                if not els:
                    # пусто бывает, когда на сервере нет областей (areas)
                    last = RuntimeError('пустой ответ')
                    continue
                return els, url
            except requests.exceptions.ConnectionError as e:
                last = e
                dead.add(url)
                log(f'    {url} → недоступен, больше не опрашивается')
            except Exception as e:
                last = e
                log(f'    {url} → ошибка: {type(e).__name__}: {e}')
        if attempt < RETRY_ROUNDS - 1:
            log(f'    пауза {RETRY_PAUSE} с и повтор')
            time.sleep(RETRY_PAUSE)
    raise RuntimeError(f'Ни один Overpass-сервер не ответил ({last})')


def _wrap(region, timeout, body, out):
    return (f'[out:json][timeout:{timeout}];\n'
            f'area["ISO3166-2"="{region}"]->.a;\n'
            f'{body}\n{out}')


def query_commerce(region, timeout):
    am = '|'.join(sorted(FOOD_AMENITY | SERVICE_AMENITY | AUTO_AMENITY
                         | {'marketplace'}))
    tour = '|'.join(sorted(HOTEL_TOURISM))
    bld = '|'.join(sorted(BUILDING_CATEGORY))
    body = f'''(
  nwr["shop"](area.a);
  nwr["office"](area.a);
  nwr["craft"](area.a);
  nwr["amenity"~"^({am})$"](area.a);
  nwr["tourism"~"^({tour})$"](area.a);
  way["building"~"^({bld})$"](area.a);
);'''
    return _wrap(region, timeout, body, 'out tags geom;')


def query_context(region, timeout):
    """
    Окружение одним запросом (публичные серверы ограничивают частоту):
    населённые пункты — узлы с тегами, остановки — узлы без тегов,
    магистральные дороги — линии.
    """
    return _wrap(region, timeout,
              'node["place"~"^(city|town|village|hamlet)$"](area.a);',
              'out body;\n'
              '(node["highway"="bus_stop"](area.a);'
              'node["public_transport"="platform"](area.a);'
              'node["railway"~"^(station|halt|tram_stop)$"](area.a););\n'
              'out skel;\n'
              'way["highway"~"^(trunk|primary|secondary|tertiary)$"](area.a);\n'
              'out tags geom;')


def query_housing(region, timeout):
    return _wrap(region, timeout,
              'way["building"~"^(apartments|residential|dormitory|house|'
              'detached|semidetached_house|terrace|yes)$"](area.a);',
              'out tags center;')


def query_addresses(region, timeout):
    """Дома с номером (контуры) и улицы с названием — для подстановки адресов."""
    return _wrap(region, timeout,
                 '(nwr["addr:housenumber"](area.a);'
                 'way["highway"]["name"](area.a););',
                 'out tags geom;')


def is_local(url):
    return 'localhost' in url or '127.0.0.1' in url


def fetch_all(region, timeout, servers, log=print, progress=None):
    """
    Четыре запроса: коммерческие объекты, окружение (населённые пункты,
    остановки, дороги), жилая застройка, адреса. Возвращает четыре
    списка elements.
    """
    steps = [
        ('коммерческие объекты', query_commerce),
        ('населённые пункты, остановки, дороги', query_context),
        ('жилая застройка', query_housing),
        ('адреса домов и улицы', query_addresses),
    ]
    raw = []
    dead = set()
    for i, (title, builder) in enumerate(steps):
        if progress:
            progress(i, f'Загрузка: {title}')
        log(f'Загрузка: {title}')
        els, used = request(servers, builder(region, timeout), timeout,
                            dead, log)
        raw.append(els)
        if i < len(steps) - 1 and not is_local(used):
            time.sleep(STEP_PAUSE)
    return raw
