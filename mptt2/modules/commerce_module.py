"""
(Эталон файла modules/commerce_module.py — его копирует install.py.)

Точка подключения модуля «Коммерческая недвижимость» к загрузчику модулей.

Вся логика — в пакете commerce_realty/ рядом с этим файлом. Этот файл
нужен только потому, что загрузчик ищет в modules/ одиночные .py-файлы
с MODULE_INFO, MENU и register(app, loader).
"""
import os
import sys

# Литерал, а не ссылка на пакет: загрузчик читает его через ast без импорта.
MODULE_INFO = {
    'name': 'commerce_module',
    'description': 'Коммерческая недвижимость: тепловая карта ликвидности, '
                   'цены покупки и дохода от аренды (данные Overpass API)',
    'version': '2.2',
}

MENU = {'url': '/modules/commerce', 'title': 'Коммерческая недвижимость'}

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

import commerce_realty  # noqa: E402


def register(app, loader=None):
    return commerce_realty.register(app, loader)


init_app = register
setup = register
