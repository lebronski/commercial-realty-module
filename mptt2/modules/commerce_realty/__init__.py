"""
Модуль «Коммерческая недвижимость» — самостоятельный пакет.

Тепловая карта коммерческих помещений и зданий региона (данные Overpass
API), индекс ликвидности по местоположению и спросу, модельная цена
покупки и доход от сдачи в аренду.

Устройство пакета:
    model.py    — расчёты (чистый Python)
    calibration.py — реальные объявления: CSV, привязка к адресам, калибровка
    osm.py      — запросы к Overpass API
    storage.py  — собственная база SQLite модуля (data/commerce.db)
    web.py      — Flask Blueprint: страница, API, фоновый импорт
    templates/, static/ — шаблоны и скрипты, раздаются самим Blueprint

Подключение — см. INSTALL.md. Коротко: register(app).
"""
__version__ = '2.3'

MODULE_INFO = {
    'name': 'commerce_module',
    'description': 'Коммерческая недвижимость: тепловая карта ликвидности, '
                   'цены покупки и дохода от аренды (данные Overpass API)',
    'version': __version__,
}

MENU = {'url': '/modules/commerce', 'title': 'Коммерческая недвижимость'}


def register(app, loader=None, url_prefix=None, db_path=None):
    """
    Точка входа. Сигнатура (app, loader) совпадает с контрактом загрузчика
    модулей основного проекта; loader не используется.
    """
    from .web import register as _register
    bp = _register(app, url_prefix=url_prefix, db_path=db_path)
    MENU['url'] = (url_prefix or app.config.get('COMMERCE_URL_PREFIX')
                   or MENU['url'])
    return bp


# Другие распространённые имена точки входа — на случай, если загрузчик
# в версии проекта руководителя вызывает модуль иначе.
init_app = register
setup = register
