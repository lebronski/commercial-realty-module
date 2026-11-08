"""
Расчётная модель: разбор объектов OSM, факторы ликвидности, экономика.

Чистый Python без Flask и без базы данных — его можно тестировать
и переиспользовать отдельно от веб-части.

ВАЖНО: в OpenStreetMap нет цен. Цена и аренда — оценка по модели
(базовые ставки × коэффициенты), а не данные объявлений.
"""
import math

DEFAULT_REGION = 'RU-ME'          # ISO3166-2 код региона в OSM
DEFAULT_TIMEOUT = 180
DEFAULT_OPEX = 0.20               # доля операционных расходов от дохода

CATEGORIES = {
    'retail':     'Торговое',
    'food':       'Общепит',
    'office':     'Офисное',
    'service':    'Услуги',
    'hotel':      'Гостиница',
    'auto':       'Автосервис / АЗС',
    'warehouse':  'Склад',
    'industrial': 'Производственное',
}

# Базовые ставки для центра столицы региона, ₽ за м².
# price — покупка, rent — аренда в месяц. Это допущения модели.
DEFAULT_RATES = {
    'retail':     {'price': 65000, 'rent': 650},
    'food':       {'price': 70000, 'rent': 750},
    'office':     {'price': 55000, 'rent': 500},
    'service':    {'price': 60000, 'rent': 600},
    'hotel':      {'price': 60000, 'rent': 550},
    'auto':       {'price': 45000, 'rent': 450},
    'warehouse':  {'price': 25000, 'rent': 220},
    'industrial': {'price': 20000, 'rent': 180},
}

# Веса факторов ликвидности по профилям (сумма = 1.0)
WEIGHTS = {
    'street': {'set': 0.25, 'center': 0.15, 'demand': 0.20,
               'transport': 0.12, 'road': 0.13, 'cluster': 0.15},
    'office': {'set': 0.30, 'center': 0.25, 'demand': 0.05,
               'transport': 0.15, 'road': 0.10, 'cluster': 0.15},
    'logistic': {'set': 0.30, 'center': 0.05, 'demand': 0.00,
                 'transport': 0.10, 'road': 0.45, 'cluster': 0.10},
}
PROFILE = {
    'retail': 'street', 'food': 'street', 'service': 'street',
    'hotel': 'office', 'office': 'office',
    'auto': 'logistic', 'warehouse': 'logistic', 'industrial': 'logistic',
}
COMPETITION_PENALTY = 0.05        # макс. штраф за конкурентов того же типа

FOOD_AMENITY = {'restaurant', 'cafe', 'fast_food', 'bar', 'pub',
                'food_court', 'nightclub'}
SERVICE_AMENITY = {'bank', 'pharmacy', 'clinic', 'dentist', 'cinema'}
AUTO_AMENITY = {'fuel', 'car_wash'}
AUTO_SHOP = {'car_repair', 'car', 'car_parts', 'tyres', 'motorcycle'}
SERVICE_SHOP = {'hairdresser', 'beauty', 'laundry', 'dry_cleaning', 'tailor',
                'funeral_directors', 'travel_agency', 'massage', 'tattoo',
                'optician', 'copyshop', 'photo', 'pawnbroker', 'ticket'}
HOTEL_TOURISM = {'hotel', 'hostel', 'guest_house', 'motel'}
BUILDING_CATEGORY = {
    'retail': 'retail', 'supermarket': 'retail', 'kiosk': 'retail',
    'commercial': 'office', 'office': 'office', 'hotel': 'hotel',
    'warehouse': 'warehouse', 'industrial': 'industrial',
}

# Типовая площадь помещения (м²), когда в OSM объект задан точкой
DEFAULT_AREA = {
    'supermarket': 400, 'mall': 3000, 'department_store': 1500,
    'convenience': 80, 'kiosk': 10, 'furniture': 300, 'doityourself': 500,
    'hardware': 150, 'electronics': 150, 'clothes': 90, 'car': 600,
    'car_repair': 150, 'restaurant': 180, 'cafe': 90, 'fast_food': 70,
    'bar': 100, 'pub': 120, 'nightclub': 300, 'food_court': 250,
    'bank': 150, 'pharmacy': 70, 'clinic': 250, 'dentist': 90, 'cinema': 800,
    'fuel': 100, 'car_wash': 120, 'marketplace': 1000,
    'hotel': 800, 'hostel': 250, 'guest_house': 200, 'motel': 400,
    'warehouse': 800, 'industrial': 1000,
}
DEFAULT_AREA_BY_CATEGORY = {
    'retail': 60, 'food': 90, 'office': 60, 'service': 50, 'hotel': 500,
    'auto': 120, 'warehouse': 800, 'industrial': 1000,
}
USABLE_SHARE = 0.85               # полезная площадь от площади по контуру

PLACE_DEFAULT_POP = {'city': 100000, 'town': 10000, 'village': 500,
                     'hamlet': 50}
ROAD_CLASS_WEIGHT = {'trunk': 1.0, 'primary': 1.0, 'secondary': 0.85,
                     'tertiary': 0.7}

M_PER_DEG = 111320.0

FACTOR_COLS = ['f_set', 'f_center', 'f_demand', 'f_transport', 'f_road',
               'f_cluster', 'f_comp']
ECON_COLS = ['liquidity', 'price', 'price_m2', 'rent_month', 'rent_m2',
             'occupancy', 'noi_year', 'cap_rate', 'payback',
             'exposure_months']
BASE_COLS = ['osm_type', 'osm_id', 'name', 'category', 'subtype', 'kind',
             'vacant', 'lat', 'lon', 'area', 'area_estimated', 'levels',
             'year', 'address', 'address_src', 'settlement',
             'settlement_pop', 'dist_center_km']
ALL_COLS = BASE_COLS + FACTOR_COLS + ECON_COLS


# ------------------------------------------------------------------
#  Геометрия
# ------------------------------------------------------------------

def _dist_m(lat1, lon1, lat2, lon2):
    kx = M_PER_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * M_PER_DEG)


def _ring_area_center(geom):
    """Площадь (м²) и центр замкнутого контура [{'lat','lon'}, ...]."""
    pts = [(p['lat'], p['lon']) for p in geom if p]
    if len(pts) < 3:
        return None, None
    lat0 = sum(p[0] for p in pts) / len(pts)
    lon0 = sum(p[1] for p in pts) / len(pts)
    closed = len(pts) >= 4 and pts[0] == pts[-1]
    if not closed:
        return None, (lat0, lon0)
    kx = M_PER_DEG * math.cos(math.radians(lat0))
    xy = [((p[1] - lon0) * kx, (p[0] - lat0) * M_PER_DEG) for p in pts]
    s = 0.0
    for i in range(len(xy) - 1):
        s += xy[i][0] * xy[i + 1][1] - xy[i + 1][0] * xy[i][1]
    return abs(s) / 2.0, (lat0, lon0)


class Grid:
    """Простейший пространственный индекс по сетке ~550 м."""

    def __init__(self, cell_lat=0.005, cell_lon=0.009):
        self.cl = cell_lat
        self.co = cell_lon
        self.cells = {}

    def add(self, lat, lon, payload=None):
        key = (int(lat / self.cl), int(lon / self.co))
        self.cells.setdefault(key, []).append((lat, lon, payload))

    def near(self, lat, lon, radius_m):
        """Итератор (расстояние, payload) в радиусе."""
        ri = int(radius_m / (self.cl * M_PER_DEG)) + 1
        rj = int(radius_m / (self.co * M_PER_DEG
                             * math.cos(math.radians(lat)))) + 1
        ci, cj = int(lat / self.cl), int(lon / self.co)
        for i in range(ci - ri, ci + ri + 1):
            for j in range(cj - rj, cj + rj + 1):
                for plat, plon, payload in self.cells.get((i, j), ()):
                    d = _dist_m(lat, lon, plat, plon)
                    if d <= radius_m:
                        yield d, payload


# ------------------------------------------------------------------
#  Разбор объектов OSM
# ------------------------------------------------------------------

def _classify(tags):
    """→ (category, subtype, vacant) или None, если объект не подходит."""
    shop = tags.get('shop')
    amenity = tags.get('amenity')
    office = tags.get('office')
    tourism = tags.get('tourism')
    craft = tags.get('craft')
    building = tags.get('building')

    if shop and shop != 'no':
        if shop == 'vacant':
            return 'retail', 'vacant', 1
        if shop in AUTO_SHOP:
            return 'auto', shop, 0
        if shop in SERVICE_SHOP:
            return 'service', shop, 0
        return 'retail', shop, 0
    if amenity in FOOD_AMENITY:
        return 'food', amenity, 0
    if amenity in SERVICE_AMENITY:
        return 'service', amenity, 0
    if amenity in AUTO_AMENITY:
        return 'auto', amenity, 0
    if amenity == 'marketplace':
        return 'retail', amenity, 0
    if office and office not in ('no', 'government'):
        return 'office', office, 0
    if tourism in HOTEL_TOURISM:
        return 'hotel', tourism, 0
    if craft and craft != 'no':
        return 'service', craft, 0
    if building in BUILDING_CATEGORY:
        return BUILDING_CATEGORY[building], building, 0
    return None


def _levels(tags):
    raw = str(tags.get('building:levels', '')).replace(',', '.').strip()
    try:
        v = int(float(raw))
        return v if 1 <= v <= 60 else None
    except ValueError:
        return None


def parse_objects(elements):
    out = []
    for el in elements:
        tags = el.get('tags') or {}
        cls = _classify(tags)
        if not cls:
            continue
        category, subtype, vacant = cls
        etype = el['type']
        footprint = None

        if etype == 'node':
            lat, lon = el.get('lat'), el.get('lon')
        elif etype == 'way':
            footprint, center = _ring_area_center(el.get('geometry') or [])
            if not center:
                continue
            lat, lon = center
        else:  # relation — сумма внешних замкнутых контуров
            b = el.get('bounds')
            if not b:
                continue
            lat = (b['minlat'] + b['maxlat']) / 2
            lon = (b['minlon'] + b['maxlon']) / 2
            total = 0.0
            for m in el.get('members', []):
                if m.get('type') == 'way' and m.get('role') in ('outer', ''):
                    a, _c = _ring_area_center(m.get('geometry') or [])
                    if a:
                        total += a
            footprint = total or None
        if lat is None or lon is None:
            continue

        levels = _levels(tags)
        building = tags.get('building')
        own_building = bool(footprint) and bool(building)
        # Контур без тега building — это территория (база отдыха, АЗС, рынок),
        # а не помещение: её площадь в расчёт не берём.
        if footprint and footprint >= 4 and building:
            # Жилой дом с магазином — коммерческий только первый этаж
            if building in BUILDING_CATEGORY or building in ('yes', 'roof',
                                                             'service',
                                                             'garages'):
                floors = levels or 1
            else:
                floors = 1
            area = footprint * floors * USABLE_SHARE
            estimated = 0
        else:
            area = DEFAULT_AREA.get(subtype,
                                    DEFAULT_AREA_BY_CATEGORY[category])
            estimated = 1

        address = _format_address(tags)

        out.append({
            'osm_type': etype, 'osm_id': el['id'],
            'name': (tags.get('name') or tags.get('brand')
                     or tags.get('operator') or '')[:200],
            'category': category, 'subtype': subtype,
            'kind': 'здание' if own_building else 'помещение',
            'vacant': vacant,
            'lat': lat, 'lon': lon,
            'area': round(max(area, 4.0), 1),
            'area_estimated': estimated,
            'levels': levels,
            'year': str(tags.get('start_date', ''))[:10],
            'address': address[:200],
            # osm — адрес в тегах самого объекта; иначе заполнит
            # assign_addresses()
            'address_src': 'osm' if tags.get('addr:housenumber') else '',
        })
    return out


# ------------------------------------------------------------------
#  Адреса
# ------------------------------------------------------------------

ADDRESS_NEARBY_M = 40      # «рядом»: ближайший дом с адресом не дальше
STREET_NEARBY_M = 80       # иначе — ближайшая улица с названием не дальше
CONTAIN_SEARCH_M = 300     # радиус поиска зданий, внутри которых точка


def _format_address(tags):
    street = tags.get('addr:street') or tags.get('addr:place') or ''
    house = tags.get('addr:housenumber', '')
    return ', '.join(x for x in (street, house) if x)


def _rings(el):
    """Замкнутые контуры элемента как списки (lat, lon)."""
    if el['type'] == 'way':
        geoms = [el.get('geometry') or []]
    elif el['type'] == 'relation':
        geoms = [m.get('geometry') or [] for m in el.get('members', [])
                 if m.get('type') == 'way' and m.get('role') in ('outer', '')]
    else:
        return []
    out = []
    for g in geoms:
        pts = [(p['lat'], p['lon']) for p in g if p]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            out.append(pts)
    return out


def _in_ring(lat, lon, ring):
    """Точка внутри многоугольника (метод лучей)."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat) and \
                lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def assign_addresses(objs, elements):
    """
    Заполняет адрес объектам, у которых в OSM нет своего номера дома.

    elements — дома с addr:housenumber и улицы с названием (osm.query_addresses).
    Порядок поиска и значение address_src:
      building — адрес здания, внутри контура которого стоит объект;
      nearby   — ближайший дом с адресом в пределах ADDRESS_NEARBY_M;
      street   — только улица: ближайшая в пределах STREET_NEARBY_M.
    Если у найденного дома есть номер, но нет улицы, к источнику
    добавляется «+street»: улица подставлена ближайшая.
    Возвращает счётчик по источникам.
    """
    houses = Grid()
    streets = Grid()
    for el in elements:
        tags = el.get('tags') or {}
        if tags.get('addr:housenumber'):
            text = _format_address(tags)
            rings = _rings(el)
            if rings:
                pts = [p for r in rings for p in r]
                lats = [p[0] for p in pts]
                lons = [p[1] for p in pts]
                bbox = (min(lats), min(lons), max(lats), max(lons))
                c_lat = (bbox[0] + bbox[2]) / 2
                c_lon = (bbox[1] + bbox[3]) / 2
                houses.add(c_lat, c_lon, (text, rings, bbox))
            elif 'lat' in el:
                houses.add(el['lat'], el['lon'], (text, None, None))
        elif tags.get('highway') and tags.get('name'):
            geom = el.get('geometry') or []
            for i in range(len(geom) - 1):
                a, b = geom[i], geom[i + 1]
                if not a or not b:
                    continue
                n = max(1, int(_dist_m(a['lat'], a['lon'],
                                       b['lat'], b['lon']) / 25))
                for k in range(n):
                    t = k / n
                    streets.add(a['lat'] + (b['lat'] - a['lat']) * t,
                                a['lon'] + (b['lon'] - a['lon']) * t,
                                tags['name'])

    def nearest_street(lat, lon):
        best, best_d = None, None
        for d, name in streets.near(lat, lon, STREET_NEARBY_M):
            if best_d is None or d < best_d:
                best, best_d = name, d
        return best

    def complete(o, text):
        # Номер дома без улицы (в OSM нет addr:street) — дополняем
        # ближайшей улицей и помечаем это в address_src.
        if text and ',' not in text:
            name = nearest_street(o['lat'], o['lon'])
            if name:
                o['address_src'] += '+street'
                return f'{name}, {text}'
        return text

    stats = {'osm': 0, 'building': 0, 'nearby': 0, 'street': 0, 'none': 0}
    for o in objs:
        if o.get('address_src') == 'osm':
            stats['osm'] += 1
            o['address'] = complete(o, o['address'])[:200]
            continue
        lat, lon = o['lat'], o['lon']
        found, src = None, None

        # 1. Здание с адресом, внутри которого стоит объект
        best_size = None
        for _d, (text, rings, bbox) in houses.near(lat, lon,
                                                   CONTAIN_SEARCH_M):
            if not rings or not (bbox[0] <= lat <= bbox[2]
                                 and bbox[1] <= lon <= bbox[3]):
                continue
            if any(_in_ring(lat, lon, r) for r in rings):
                size = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                if best_size is None or size < best_size:
                    found, src, best_size = text, 'building', size

        # 2. Ближайший дом с адресом
        if not found:
            best_d = None
            for d, (text, _r, _b) in houses.near(lat, lon, ADDRESS_NEARBY_M):
                if best_d is None or d < best_d:
                    found, src, best_d = text, 'nearby', d

        # 3. Ближайшая улица
        if not found:
            found = nearest_street(lat, lon)
            src = 'street' if found else None

        if found:
            o['address_src'] = src
            if src != 'street':
                found = complete(o, found)
            o['address'] = found[:200]
            stats[src] += 1
        else:
            stats['none'] += 1
    return stats


def parse_places(elements):
    places = []
    for el in elements:
        tags = el.get('tags') or {}
        ptype = tags.get('place')
        if ptype not in PLACE_DEFAULT_POP or 'lat' not in el:
            continue
        try:
            pop = int(float(str(tags.get('population', ''))
                            .replace(' ', '').replace(',', '.')))
        except ValueError:
            pop = 0
        if pop <= 0:
            pop = PLACE_DEFAULT_POP[ptype]
        places.append({
            'name': tags.get('name', ''), 'type': ptype, 'pop': pop,
            'lat': el['lat'], 'lon': el['lon'],
            # условный радиус застройки, км
            'r_km': max(0.4, 0.6 * math.sqrt(pop / 1000.0)),
        })
    return places


def _housing_weight(tags):
    """Оценка числа жителей в здании."""
    b = tags.get('building')
    if b in ('apartments', 'residential', 'dormitory'):
        return (_levels(tags) or 5) * 35
    if b == 'yes':
        lv = _levels(tags)
        return lv * 35 if lv and lv >= 3 else 2
    return 3


# ------------------------------------------------------------------
#  Факторы ликвидности
# ------------------------------------------------------------------

def _sat(x, x0):
    """Насыщающаяся шкала 0..1."""
    return 1.0 - math.exp(-x / x0)


def compute_factors(objs, places, stops_el, roads_el, housing_el):
    max_pop = max((p['pop'] for p in places), default=100000)
    log_max = math.log10(max(max_pop, 1000))

    big = [p for p in places if p['type'] in ('city', 'town')]
    small_grid = Grid(0.03, 0.05)
    for p in places:
        if p['type'] not in ('city', 'town'):
            small_grid.add(p['lat'], p['lon'], p)

    stops = Grid()
    for el in stops_el:
        if 'lat' in el:
            stops.add(el['lat'], el['lon'])

    roads = Grid()
    for el in roads_el:
        w = ROAD_CLASS_WEIGHT.get((el.get('tags') or {}).get('highway'), 0.7)
        geom = el.get('geometry') or []
        for i in range(len(geom) - 1):
            a, b = geom[i], geom[i + 1]
            d = _dist_m(a['lat'], a['lon'], b['lat'], b['lon'])
            n = max(1, int(d / 50))          # точка каждые ~50 м
            for k in range(n):
                t = k / n
                roads.add(a['lat'] + (b['lat'] - a['lat']) * t,
                          a['lon'] + (b['lon'] - a['lon']) * t, w)

    housing = Grid()
    for el in housing_el:
        c = el.get('center')
        if c:
            housing.add(c['lat'], c['lon'],
                        _housing_weight(el.get('tags') or {}))

    commerce = Grid()
    for o in objs:
        commerce.add(o['lat'], o['lon'], o['subtype'])

    for o in objs:
        lat, lon = o['lat'], o['lon']

        # --- населённый пункт и центральность
        best, best_ratio, best_d = None, 1e9, 0.0
        cands = list(big) + [p for _d, p in small_grid.near(lat, lon, 6000)]
        for p in cands:
            d_km = _dist_m(lat, lon, p['lat'], p['lon']) / 1000.0
            ratio = d_km / p['r_km']
            if ratio < best_ratio:
                best, best_ratio, best_d = p, ratio, d_km
        if best:
            f_set = (math.log10(best['pop']) - 2) / (log_max - 2)
            f_set = max(0.0, min(1.0, f_set))
            f_set *= math.exp(-0.5 * max(0.0, best_ratio - 1.0))
            f_center = math.exp(-1.5 * best_ratio)
            o['settlement'] = (best['name'] if best_ratio <= 2.5
                               else 'вне населённых пунктов')
            o['settlement_pop'] = best['pop']
            o['dist_center_km'] = round(best_d, 2)
        else:
            f_set = f_center = 0.0
            o['settlement'] = ''
            o['settlement_pop'] = None
            o['dist_center_km'] = None

        # --- спрос: жители в радиусе 500 м
        residents = sum(w for _d, w in housing.near(lat, lon, 500))
        f_demand = _sat(residents, 4000)

        # --- транспорт: остановки в радиусе 400 м
        n_stops = sum(1 for _ in stops.near(lat, lon, 400))
        f_transport = _sat(n_stops, 3)

        # --- трафик: близость к магистральной дороге
        f_road = 0.0
        for d, w in roads.near(lat, lon, 600):
            v = w * math.exp(-d / 150.0)
            if v > f_road:
                f_road = v

        # --- коммерческая активность и конкуренция в радиусе 300 м
        n_all = n_same = 0
        for _d, sub in commerce.near(lat, lon, 300):
            n_all += 1
            if sub == o['subtype']:
                n_same += 1
        f_cluster = _sat(max(0, n_all - 1), 15)
        f_comp = min(1.0, max(0, n_same - 1) / 8.0)

        o['f_set'] = round(f_set, 3)
        o['f_center'] = round(f_center, 3)
        o['f_demand'] = round(f_demand, 3)
        o['f_transport'] = round(f_transport, 3)
        o['f_road'] = round(f_road, 3)
        o['f_cluster'] = round(f_cluster, 3)
        o['f_comp'] = round(f_comp, 3)


# ------------------------------------------------------------------
#  Экономика
# ------------------------------------------------------------------

def liquidity(f, category):
    """Индекс ликвидности 0..1 по факторам места f и профилю категории."""
    w = WEIGHTS[PROFILE[category]]
    liq = (w['set'] * f['f_set'] + w['center'] * f['f_center']
           + w['demand'] * f['f_demand'] + w['transport'] * f['f_transport']
           + w['road'] * f['f_road'] + w['cluster'] * f['f_cluster']
           - COMPETITION_PENALTY * f['f_comp'])
    return max(0.0, min(1.0, liq))


def price_multipliers(f_set, liq, area):
    """
    Во сколько раз цена и аренда за м² в этом месте отличаются от базовой
    ставки категории. Используется и в расчёте, и в калибровке.
    """
    k_set = 0.45 + 0.55 * f_set                        # размер нас. пункта
    k_size = max(0.7, min(1.25, (area / 100.0) ** -0.08))  # скидка на объём
    return (k_set * (0.70 + 0.60 * liq) * k_size,
            k_set * (0.60 + 0.80 * liq) * k_size)


def calc_economics(o, rates, opex):
    """Заполняет ликвидность, цену, аренду и доходность объекта."""
    liq = liquidity(o, o['category'])
    rate = rates.get(o['category']) or DEFAULT_RATES[o['category']]
    area = o['area']
    k_price, k_rent = price_multipliers(o['f_set'], liq, area)

    price_m2 = rate['price'] * k_price
    rent_m2 = rate['rent'] * k_rent
    occupancy = 0.60 + 0.35 * liq                    # доля времени в аренде

    price = price_m2 * area
    rent_month = rent_m2 * area
    noi = rent_month * 12 * occupancy * (1.0 - opex)

    o['liquidity'] = round(liq, 3)
    o['price_m2'] = round(price_m2)
    o['price'] = round(price, -3)
    o['rent_m2'] = round(rent_m2)
    o['rent_month'] = round(rent_month, -2)
    o['occupancy'] = round(occupancy, 3)
    o['noi_year'] = round(noi, -2)
    o['cap_rate'] = round(noi / price, 4) if price else 0
    o['payback'] = round(price / noi, 1) if noi else None
    o['exposure_months'] = int(round(2 + 14 * (1.0 - liq)))
    return o
