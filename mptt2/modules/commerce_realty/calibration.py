"""
Реальные объявления: разбор CSV, привязка к адресам OSM, калибровка ставок.

Идея калибровки. Модель считает цену за м² как
    базовая_ставка × k_нас.пункта × k_ликвидности × k_площади.
Для объявления цена за м² известна, а коэффициенты можно посчитать по
месту, где оно стоит (берутся факторы ближайшего объекта на карте).
Отсюда «базовая ставка, которую подразумевает объявление». Медиана таких
ставок по группе категорий и есть откалиброванная ставка.

Категории объединяются в группы (профили модели: уличная торговля и
услуги, офисы, склады и производство), потому что объявлений по каждой
категории отдельно мало. Внутри группы ставки категорий сохраняют
исходные соотношения: калибровка даёт общий множитель группы.

Чистый Python, без Flask и базы.
"""
import csv
import io
import re
import statistics

from .model import (DEFAULT_RATES, PROFILE, Grid, _dist_m, liquidity,
                    price_multipliers)

DEALS = {'sale': 'продажа', 'rent': 'аренда'}
MIN_PER_GROUP = 3            # меньше объявлений в группе — ставку не трогаем
FACTOR_RADIUS_M = 500        # ближайший объект, с которого берутся факторы
CITY_RADIUS_M = 20000        # дома ищутся в этом радиусе от нас. пункта

CSV_COLUMNS = ['deal', 'category', 'type', 'address', 'city', 'area_m2',
               'price_rub', 'source', 'url', 'date']

GROUP_TITLES = {'street': 'Торговля, общепит, услуги',
                'office': 'Офисы и гостиницы',
                'logistic': 'Склады, производство, автосервис'}

_TYPE_WORDS = {
    'ул', 'улица', 'пр', 'пр-кт', 'пр-т', 'просп', 'проспект', 'б-р',
    'бульвар', 'пер', 'переулок', 'проезд', 'пр-д', 'ш', 'шоссе', 'тракт',
    'сл', 'слобода', 'пл', 'площадь', 'наб', 'набережная', 'мкр',
    'микрорайон', 'кв-л', 'квартал', 'тер', 'территория', 'аллея',
    'тупик', 'линия',
}
_HOUSE_RE = re.compile(r'^(?:д|дом)?\s*(\d+\s*[а-я]{0,2}(?:\s*/\s*\d+[а-я]?)?'
                       r'(?:\s*(?:к|корп|корпус|стр)\s*\d*[а-я]?)?)$')


# ------------------------------------------------------------------
#  Адреса
# ------------------------------------------------------------------

def normalize_address(text):
    """
    'ул. Луначарского, 52' и 'улица Луначарского, 52' → ('луначарского', '52').
    Возвращает (улица, дом); дом может быть ''. None — если не разобрать.
    """
    t = (text or '').lower().replace('ё', 'е')
    t = re.sub(r'[«»"\'()]', ' ', t).replace('.', ' ')
    parts = [re.sub(r'\s+', ' ', p).strip() for p in t.split(',')]
    parts = [p for p in parts if p]
    house_i = None
    for i in range(len(parts) - 1, -1, -1):
        if _HOUSE_RE.match(parts[i]):
            house_i = i
            break
    if house_i is not None and house_i > 0:
        street_part = parts[house_i - 1]
        m = _HOUSE_RE.match(parts[house_i])
        house = re.sub(r'\s+|корпус|корп|стр', '', m.group(1))
    else:
        street_part = parts[-1] if parts else ''
        house = ''
    words = [w for w in street_part.split() if w not in _TYPE_WORDS]
    if not words:
        return None
    return ' '.join(sorted(words)), house


def address_points(elements):
    """Дома с номером из ответа Overpass → [(улица, дом, текст, lat, lon)]."""
    from .model import _format_address, _rings
    out = []
    for el in elements:
        tags = el.get('tags') or {}
        if not tags.get('addr:housenumber'):
            continue
        text = _format_address(tags)
        key = normalize_address(text)
        if not key or not key[1]:
            continue
        if 'lat' in el:
            lat, lon = el['lat'], el['lon']
        else:
            pts = [p for r in _rings(el) for p in r]
            if not pts:
                continue
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
        out.append((key[0], key[1], text, lat, lon))
    return out


def _bare_house(house):
    m = re.match(r'\d+', house)
    return m.group(0) if m else house


def geocode(listing, addresses, places):
    """
    addresses — {(улица, дом): [(lat, lon, текст)]}, places — [{name, lat, lon, pop}].
    Возвращает (lat, lon, точность) или None. Точность: house — дом найден,
    house_approx — найден дом без буквы/корпуса, street — середина улицы.
    """
    key = normalize_address(listing.get('address'))
    if not key:
        return None
    street, house = key

    city = (listing.get('city') or '').strip().lower().replace('ё', 'е')
    anchor = None
    if city:
        named = [p for p in places
                 if p['name'].lower().replace('ё', 'е') == city]
        if named:
            anchor = max(named, key=lambda p: p['pop'])
    if anchor is None and places:
        anchor = max(places, key=lambda p: p['pop'])   # столица региона

    def pick(cands):
        if not cands:
            return None
        if anchor is None:
            return cands[0]
        best = min(cands, key=lambda c: _dist_m(c[0], c[1],
                                               anchor['lat'], anchor['lon']))
        if _dist_m(best[0], best[1], anchor['lat'],
                   anchor['lon']) > CITY_RADIUS_M:
            return None
        return best

    if house:
        hit = pick(addresses.get((street, house)))
        if hit:
            return hit[0], hit[1], 'house'
        bare = _bare_house(house)
        hit = pick([c for (s, h), cs in addresses.items()
                    if s == street and _bare_house(h) == bare for c in cs])
        if hit:
            return hit[0], hit[1], 'house_approx'
    same_street = [c for (s, _h), cs in addresses.items() if s == street
                   for c in cs]
    near = [c for c in same_street if anchor is None or _dist_m(
        c[0], c[1], anchor['lat'], anchor['lon']) <= CITY_RADIUS_M]
    if near:
        lat = sum(c[0] for c in near) / len(near)
        lon = sum(c[1] for c in near) / len(near)
        return lat, lon, 'street'
    return None


# ------------------------------------------------------------------
#  CSV
# ------------------------------------------------------------------

def _num(v):
    s = str(v or '').replace(' ', '').replace(' ', '').replace(',', '.')
    return float(s) if s else None


def parse_csv(text):
    """
    Разбор CSV с объявлениями (разделитель ; или ,). Возвращает
    (список объявлений, список ошибок по строкам).
    """
    text = text.lstrip('﻿')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=';,')
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ';'
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    out, errors = [], []
    for i, r in enumerate(rows, start=2):
        r = {(k or '').strip().lower(): (v or '').strip()
             for k, v in r.items()}
        try:
            deal = r.get('deal', '').lower()
            deal = {'продажа': 'sale', 'аренда': 'rent'}.get(deal, deal)
            if deal not in DEALS:
                raise ValueError('deal должен быть sale/rent '
                                 '(или продажа/аренда)')
            cat = r.get('category', '').lower()
            if cat not in DEFAULT_RATES:
                raise ValueError(f'неизвестная category «{cat}»')
            area, price = _num(r.get('area_m2')), _num(r.get('price_rub'))
            if not area or area <= 0 or not price or price <= 0:
                raise ValueError('area_m2 и price_rub должны быть больше 0')
            if not r.get('address'):
                raise ValueError('пустой address')
        except ValueError as e:
            errors.append(f'строка {i}: {e}')
            continue
        out.append({
            'deal': deal, 'category': cat, 'type': r.get('type', '')[:100],
            'address': r['address'][:200], 'city': r.get('city', '')[:100],
            'area': area, 'price': price,
            'source': r.get('source', '')[:100], 'url': r.get('url', '')[:500],
            'date': r.get('date', '')[:20],
        })
    return out, errors


# ------------------------------------------------------------------
#  Калибровка
# ------------------------------------------------------------------

def calibrate(listings, objects, current_rates):
    """
    listings — объявления с lat/lon (после geocode), objects — объекты
    модуля с факторами. Возвращает отчёт и новые ставки.
    """
    grid = Grid()
    for o in objects:
        grid.add(o['lat'], o['lon'], o)

    implied = {}     # (группа, сделка) → [отношение к ставке по умолчанию]
    used = []
    for l in listings:
        if l.get('lat') is None:
            continue
        best, best_d = None, None
        for d, o in grid.near(l['lat'], l['lon'], FACTOR_RADIUS_M):
            if best_d is None or d < best_d:
                best, best_d = o, d
        if best is None:
            continue
        cat = l['category']
        liq = liquidity(best, cat)
        k_price, k_rent = price_multipliers(best['f_set'], liq, l['area'])
        per_m2 = l['price'] / l['area']
        field = 'price' if l['deal'] == 'sale' else 'rent'
        base = per_m2 / (k_price if field == 'price' else k_rent)
        ratio = base / DEFAULT_RATES[cat][field]
        implied.setdefault((PROFILE[cat], l['deal']), []).append(ratio)
        used.append({'id': l.get('id'), 'implied_base': round(base),
                     'liquidity': round(liq, 3)})

    groups = []
    new_rates = {k: dict(v) for k, v in current_rates.items()}
    for group, title in GROUP_TITLES.items():
        for deal in DEALS:
            ratios = implied.get((group, deal), [])
            field = 'price' if deal == 'sale' else 'rent'
            row = {'group': group, 'title': title, 'deal': deal,
                   'n': len(ratios), 'applied': False}
            if len(ratios) >= MIN_PER_GROUP:
                ratio = statistics.median(ratios)
                row['ratio'] = round(ratio, 3)
                row['applied'] = True
                for cat, prof in PROFILE.items():
                    if prof == group:
                        new_rates[cat][field] = round(
                            DEFAULT_RATES[cat][field] * ratio,
                            -2 if field == 'price' else 0)
            groups.append(row)
    return {'groups': groups, 'rates': new_rates, 'used': used,
            'min_per_group': MIN_PER_GROUP}
