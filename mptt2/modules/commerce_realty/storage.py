"""
Собственное хранилище модуля — отдельный файл SQLite.

Модуль не создаёт таблиц и не пишет настроек в базу основного проекта,
поэтому не зависит от её схемы и от db.py. Удалить модуль = удалить
папку, в основной базе ничего не остаётся.
"""
import contextlib
import json
import os
import sqlite3

from .model import ALL_COLS, ECON_COLS, FACTOR_COLS

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    osm_type        TEXT NOT NULL,
    osm_id          INTEGER NOT NULL,
    name            TEXT DEFAULT '',
    category        TEXT NOT NULL,
    subtype         TEXT DEFAULT '',
    kind            TEXT NOT NULL,
    vacant          INTEGER NOT NULL DEFAULT 0,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    area            REAL NOT NULL,
    area_estimated  INTEGER NOT NULL DEFAULT 0,
    levels          INTEGER,
    year            TEXT DEFAULT '',
    address         TEXT DEFAULT '',
    address_src     TEXT DEFAULT '',
    settlement      TEXT DEFAULT '',
    settlement_pop  INTEGER,
    dist_center_km  REAL,
    f_set           REAL, f_center REAL, f_demand REAL,
    f_transport     REAL, f_road REAL, f_cluster REAL, f_comp REAL,
    liquidity       REAL,
    price           REAL, price_m2 REAL,
    rent_month      REAL, rent_m2 REAL,
    occupancy       REAL,
    noi_year        REAL,
    cap_rate        REAL,
    payback         REAL,
    exposure_months INTEGER,
    UNIQUE(osm_type, osm_id)
);
CREATE INDEX IF NOT EXISTS idx_objects_cat ON objects(category);

-- Реальные объявления (импорт CSV) и их привязка к карте
CREATE TABLE IF NOT EXISTS listings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    deal      TEXT NOT NULL,          -- sale | rent
    category  TEXT NOT NULL,
    type      TEXT DEFAULT '',
    address   TEXT NOT NULL,
    city      TEXT DEFAULT '',
    area      REAL NOT NULL,
    price     REAL NOT NULL,          -- продажа: вся цена; аренда: в месяц
    source    TEXT DEFAULT '',
    url       TEXT DEFAULT '',
    date      TEXT DEFAULT '',
    lat       REAL,
    lon       REAL,
    geo       TEXT DEFAULT ''         -- house | house_approx | street | ''
);

-- Адреса домов из OSM (для привязки объявлений) и населённые пункты.
-- Заполняются при загрузке из Overpass.
CREATE TABLE IF NOT EXISTS addresses (
    street TEXT NOT NULL,
    house  TEXT NOT NULL,
    text   TEXT NOT NULL,
    lat    REAL NOT NULL,
    lon    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_addresses_key ON addresses(street, house);

CREATE TABLE IF NOT EXISTS places (
    name TEXT NOT NULL,
    lat  REAL NOT NULL,
    lon  REAL NOT NULL,
    pop  INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

ADDED_COLUMNS = {
    'address_src': "TEXT DEFAULT ''",     # v2.1: откуда взят адрес
}

DEFAULT_PATH =os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'data', 'commerce.db')


class Storage:
    def __init__(self, path=None):
        self.path = path or DEFAULT_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._conn() as con:
            con.executescript(SCHEMA)
            # Столбцы, добавленные в новых версиях: старая база дополняется
            # сама, без удаления данных.
            have = {r['name'] for r in con.execute('PRAGMA table_info(objects)')}
            for col, ddl in ADDED_COLUMNS.items():
                if col not in have:
                    con.execute(f'ALTER TABLE objects ADD COLUMN {col} {ddl}')

    @contextlib.contextmanager
    def _conn(self):
        # Новое соединение на каждый вызов: безопасно для потоков Flask
        # и фонового импорта. Коммит при успехе, откат при ошибке.
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    # ---------- настройки ----------

    def get(self, key, default=None):
        with self._conn() as con:
            row = con.execute('SELECT value FROM settings WHERE key=?',
                              (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row['value'])
        except ValueError:
            return default

    def set_many(self, values):
        with self._conn() as con:
            con.executemany(
                'INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                [(k, json.dumps(v, ensure_ascii=False))
                 for k, v in values.items()])

    # ---------- объекты ----------

    def replace_objects(self, objs):
        sql = (f'INSERT OR REPLACE INTO objects({",".join(ALL_COLS)}) '
               f'VALUES({",".join("?" * len(ALL_COLS))})')
        with self._conn() as con:      # одна транзакция: всё или ничего
            con.execute('DELETE FROM objects')
            con.executemany(sql, [[o.get(c) for c in ALL_COLS]
                                  for o in objs])

    def clear(self):
        with self._conn() as con:
            con.execute('DELETE FROM objects')

    def rows(self, cols, order=None):
        sql = f'SELECT {",".join(cols)} FROM objects'
        if order:
            sql += f' ORDER BY {order}'
        with self._conn() as con:
            return con.execute(sql).fetchall()

    def factor_rows(self):
        return self.rows(['id', 'category', 'area'] + FACTOR_COLS)

    def update_economics(self, objs):
        with self._conn() as con:
            con.executemany(
                f'UPDATE objects SET '
                f'{",".join(c + "=?" for c in ECON_COLS)} WHERE id=?',
                [[o[c] for c in ECON_COLS] + [o['id']] for o in objs])

    # ---------- адреса и населённые пункты ----------

    def replace_geo(self, addresses, places):
        with self._conn() as con:
            con.execute('DELETE FROM addresses')
            con.executemany('INSERT INTO addresses(street,house,text,lat,lon) '
                            'VALUES(?,?,?,?,?)', addresses)
            con.execute('DELETE FROM places')
            con.executemany('INSERT INTO places(name,lat,lon,pop) '
                            'VALUES(?,?,?,?)',
                            [(p['name'], p['lat'], p['lon'], p['pop'])
                             for p in places])

    def address_index(self):
        """{(улица, дом): [(lat, lon, текст)]}"""
        idx = {}
        with self._conn() as con:
            for r in con.execute('SELECT street,house,text,lat,lon '
                                 'FROM addresses'):
                idx.setdefault((r['street'], r['house']), []).append(
                    (r['lat'], r['lon'], r['text']))
        return idx

    def places(self):
        with self._conn() as con:
            return [dict(r) for r in con.execute(
                'SELECT name,lat,lon,pop FROM places')]

    # ---------- объявления ----------

    LISTING_COLS = ['deal', 'category', 'type', 'address', 'city', 'area',
                    'price', 'source', 'url', 'date', 'lat', 'lon', 'geo']

    def add_listings(self, items, replace=False):
        cols = self.LISTING_COLS
        with self._conn() as con:
            if replace:
                con.execute('DELETE FROM listings')
            con.executemany(
                f'INSERT INTO listings({",".join(cols)}) '
                f'VALUES({",".join("?" * len(cols))})',
                [[it.get(c) for c in cols] for it in items])

    def listings(self):
        with self._conn() as con:
            return [dict(r) for r in con.execute(
                'SELECT * FROM listings ORDER BY id')]

    def update_listing_geo(self, items):
        with self._conn() as con:
            con.executemany('UPDATE listings SET lat=?,lon=?,geo=? WHERE id=?',
                            [(it.get('lat'), it.get('lon'), it.get('geo', ''),
                              it['id']) for it in items])

    def clear_listings(self):
        with self._conn() as con:
            con.execute('DELETE FROM listings')
