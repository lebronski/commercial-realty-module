import os
import sqlite3

DB_PATH = None


def set_db_path(path):
    global DB_PATH
    DB_PATH = path


def get_db():
    from flask import g
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    from flask import g
    db = g.pop('db', None)
    if db is not None:
        db.close()


def get_setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row['value'] if row else default


def set_setting(db, key, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


SCHEMA = """
CREATE TABLE IF NOT EXISTS node_types (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#3498db'
);

CREATE TABLE IF NOT EXISTS nodes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT DEFAULT '',
    lat     REAL NOT NULL,
    lon     REAL NOT NULL,
    type_id INTEGER REFERENCES node_types(id) ON DELETE SET NULL,
    osm_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nodes_osm ON nodes(osm_id);

CREATE TABLE IF NOT EXISTS edges (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_node   INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    name      TEXT DEFAULT '',
    oneway    INTEGER DEFAULT 0,
    osm_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_edges_osm ON edges(osm_id);

CREATE TABLE IF NOT EXISTS building_types (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#e67e22'
);

CREATE TABLE IF NOT EXISTS buildings (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT DEFAULT '',
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    w        REAL NOT NULL DEFAULT 10,
    h        REAL NOT NULL DEFAULT 10,
    rotation REAL NOT NULL DEFAULT 0,
    type_id  INTEGER REFERENCES building_types(id) ON DELETE SET NULL,
    osm_id   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_buildings_osm ON buildings(osm_id);

CREATE TABLE IF NOT EXISTS characteristics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    applies_to TEXT NOT NULL DEFAULT 'both'
               CHECK(applies_to IN ('node','edge','building','both','all'))
);

CREATE TABLE IF NOT EXISTS data (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id         INTEGER NOT NULL,
    type              TEXT NOT NULL CHECK(type IN ('node','edge','building')),
    characteristic_id INTEGER NOT NULL
                      REFERENCES characteristics(id) ON DELETE CASCADE,
    weekday           INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
    hour              INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 24),
    value             REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_data_lookup ON data(type, target_id, weekday, hour);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS modules (
    name         TEXT PRIMARY KEY,
    active       INTEGER NOT NULL DEFAULT 0,
    installed_at TEXT
);
"""


DEFAULT_NODE_TYPES = [
    ('Перекрёсток',        '#3498db'),
    ('Остановка',          '#27ae60'),
    ('Пешеходный переход', '#f1c40f'),
    ('Светофор',           '#e74c3c'),
]

DEFAULT_BUILDING_TYPES = [
    ('Жилое (МКС)',  '#3498db'),
    ('Жилое (ИЖС)',  '#27ae60'),
    ('Коммерческое', '#e67e22'),
    ('Промышленное', '#95a5a6'),
    ('Социальное',   '#9b59b6'),
]

DEFAULT_CHARACTERISTICS = [
    ('Пропускная способность', 'both'),
    ('Загруженность',          'both'),
    ('Скорость',               'edge'),
    ('Кол-во полос',           'edge'),
    ('Статус',                 'building'),
    ('Количество жильцов',     'building'),
    ('Этажность',              'building'),
    ('Количество квартир',     'building'),
]


def init_db(path):
    set_db_path(path)
    fresh = not os.path.exists(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)

    if fresh or db.execute("SELECT COUNT(*) FROM node_types").fetchone()[0] == 0:
        for name, color in DEFAULT_NODE_TYPES:
            db.execute("INSERT INTO node_types(name,color) VALUES(?,?)", (name, color))

    if fresh or db.execute("SELECT COUNT(*) FROM building_types").fetchone()[0] == 0:
        for name, color in DEFAULT_BUILDING_TYPES:
            db.execute("INSERT INTO building_types(name,color) VALUES(?,?)", (name, color))

    if fresh or db.execute("SELECT COUNT(*) FROM characteristics").fetchone()[0] == 0:
        for name, applies in DEFAULT_CHARACTERISTICS:
            db.execute("INSERT INTO characteristics(name,applies_to) VALUES(?,?)",
                       (name, applies))

    # демо-данные только на пустой базе
    if db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0:
        db.execute("INSERT INTO nodes(name,lat,lon,type_id) VALUES(?,?,?,?)",
                   ('Демо-перекрёсток', 56.6328, 47.8908, 1))

    # модуль overpass активен по умолчанию, если файл есть
    row = db.execute("SELECT name FROM modules WHERE name='overpass_module'").fetchone()
    if not row:
        db.execute("INSERT INTO modules(name,active,installed_at) VALUES(?,?,datetime('now'))",
                   ('overpass_module', 1))

    db.commit()
    db.close()