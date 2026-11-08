"""
Установка модуля в другую копию проекта (например, в версию руководителя).

    python install.py <папка проекта> --check       # только проверить
    python install.py <папка проекта>               # проверить и скопировать
    python install.py <папка проекта> --activate    # + включить модуль в БД

Скрипт ничего не меняет в коде проекта. Он копирует в его modules/
файл commerce_module.py и папку commerce_realty/, а с --activate ещё
добавляет одну строку в таблицу modules. Существующие файлы модуля
перед заменой сохраняются с суффиксом .bak-<время>.
"""
import argparse
import os
import shutil
import sqlite3
import sys
import time

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
# Шаблон файла-точки входа; в modules/ кладётся как commerce_module.py
SHIM = os.path.join(PKG_DIR, 'loader_entry.py')
MODULE_NAME = 'commerce_module'
URL_PREFIX = '/modules/commerce'

OK, WARN, FAIL = '  [ок]  ', '  [!]   ', '  [нет] '


def find_app_dir(root):
    """Папка с app.py и modules/: сам root, его подпапка или подпапка уровнем ниже."""
    cands = [root] + [os.path.join(root, d) for d in sorted(os.listdir(root))
                      if os.path.isdir(os.path.join(root, d))]
    for c in cands:
        if os.path.isfile(os.path.join(c, 'app.py')):
            return c
    for c in cands:
        if os.path.isdir(os.path.join(c, 'modules')):
            return c
    return None


def read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def check(app_dir):
    """Печатает отчёт и возвращает (можно_ставить_в_modules, путь_к_БД)."""
    print(f'Проект: {app_dir}\n')
    modules_dir = os.path.join(app_dir, 'modules')
    loader_ok = False

    # 1. Загрузчик модулей
    loader = read(os.path.join(modules_dir, 'loader.py'))
    if not os.path.isdir(modules_dir):
        print(FAIL + 'Папки modules/ нет — системы модулей в проекте нет.')
    elif not loader:
        print(WARN + 'modules/ есть, но loader.py не найден — как грузятся '
                     'модули, неизвестно.')
    else:
        scans_py = ".py" in loader
        calls_register = 'register' in loader
        uses_db_flag = 'active' in loader or 'active' in read(
            os.path.join(app_dir, 'app.py'))
        if scans_py and calls_register:
            loader_ok = True
            print(OK + 'Загрузчик ищет .py-файлы в modules/ и вызывает '
                       'register(...) — модуль подключится автоматически.')
        else:
            print(WARN + 'Загрузчик устроен иначе (нет поиска .py или вызова '
                         'register). Нужно подключение вручную — см. '
                         'INSTALL.md, вариант Б.')
        if uses_db_flag:
            print(OK + 'Модули включаются флагом active в базе — понадобится '
                       '--activate или кнопка в «Управлении модулями».')

    # 2. Шаблон base.html
    base = ''
    for d in ('templates',):
        base = read(os.path.join(app_dir, d, 'base.html')) or base
    if 'block content' in base:
        print(OK + 'base.html с блоком content найден — страница модуля '
                   'встроится в общее меню и шапку.')
    else:
        print(WARN + 'base.html с блоком content не найден — страница будет '
                     'показана в собственном оформлении модуля (это не '
                     'ошибка).')

    # 3. Конфликт адресов
    clash = []
    for dirpath, dirnames, filenames in os.walk(app_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in ('venv', '.venv', '__pycache__', 'cache',
                                    'commerce_realty', 'node_modules')]
        for fn in filenames:
            if fn.endswith('.py') and fn != 'commerce_module.py':
                if URL_PREFIX in read(os.path.join(dirpath, fn)):
                    clash.append(os.path.relpath(os.path.join(dirpath, fn),
                                                 app_dir))
    if clash:
        print(WARN + f'Адрес {URL_PREFIX} уже встречается в: '
                     f'{", ".join(clash)}. Задайте другой через '
                     f"app.config['COMMERCE_URL_PREFIX'].")
    else:
        print(OK + f'Адрес {URL_PREFIX} свободен.')

    # 4. Зависимости
    req = read(os.path.join(app_dir, 'requirements.txt')).lower()
    if 'flask' in req and 'requests' in req:
        print(OK + 'Flask и requests есть в requirements.txt — новых '
                   'зависимостей не нужно.')
    else:
        print(WARN + 'Модулю нужны flask и requests — проверьте, что они '
                     'установлены (pip install flask requests).')

    # 5. База с таблицей modules
    db_path = None
    for fn in sorted(os.listdir(app_dir)):
        if fn.endswith('.db'):
            p = os.path.join(app_dir, fn)
            try:
                con = sqlite3.connect(p)
                try:
                    if con.execute("SELECT 1 FROM sqlite_master WHERE "
                                   "type='table' AND name='modules'"
                                   ).fetchone():
                        db_path = p
                finally:
                    con.close()
            except sqlite3.Error:
                pass
    if db_path:
        print(OK + f'Таблица modules найдена в {os.path.basename(db_path)}.')
    else:
        print(WARN + 'Базы с таблицей modules не найдено (возможно, она '
                     'создаётся при первом запуске проекта).')

    # 6. Уже установленная версия
    for name in ('commerce_module.py', 'commerce_realty'):
        if os.path.exists(os.path.join(modules_dir, name)):
            print(WARN + f'modules/{name} уже есть — будет сохранён как '
                         f'резервная копия и заменён.')

    print('\nМодулю не нужны ни изменения в app.py и db.py, ни таблицы '
          'в базе проекта:\nсвои данные он хранит в '
          'modules/commerce_realty/data/commerce.db.')
    return loader_ok and os.path.isdir(modules_dir), db_path


def copy(app_dir):
    modules_dir = os.path.join(app_dir, 'modules')
    os.makedirs(modules_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')

    dst_pkg = os.path.join(modules_dir, 'commerce_realty')
    dst_shim = os.path.join(modules_dir, 'commerce_module.py')
    if os.path.abspath(dst_pkg) == PKG_DIR:
        print('Это та же папка, копировать некуда.')
        return
    for p in (dst_pkg, dst_shim):
        if os.path.exists(p):
            os.rename(p, f'{p}.bak-{stamp}')

    shutil.copytree(PKG_DIR, dst_pkg, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', 'data', '*.bak-*'))
    shutil.copy2(SHIM, dst_shim)
    print(f'\nСкопировано в {modules_dir}:\n  commerce_module.py\n'
          f'  commerce_realty/')


def activate(db_path):
    con = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in con.execute('PRAGMA table_info(modules)')]
        data = {'name': MODULE_NAME, 'active': 1}
        if 'installed_at' in cols:
            data['installed_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        data = {k: v for k, v in data.items() if k in cols}
        if 'name' not in data:
            print(WARN + 'В таблице modules нет столбца name — включите '
                         'модуль через интерфейс проекта.')
            return
        con.execute(f'INSERT OR REPLACE INTO modules({",".join(data)}) '
                    f'VALUES({",".join("?" * len(data))})',
                    list(data.values()))
        con.commit()
        print(f'Модуль включён в {os.path.basename(db_path)}.')
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description='Установка модуля '
                                 '«Коммерческая недвижимость».')
    ap.add_argument('project', help='папка проекта (с app.py или с mptt2/)')
    ap.add_argument('--check', action='store_true',
                    help='только проверить, ничего не копировать')
    ap.add_argument('--activate', action='store_true',
                    help='включить модуль в таблице modules')
    args = ap.parse_args()

    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        sys.exit(f'Папка не найдена: {root}')
    app_dir = find_app_dir(root)
    if not app_dir:
        sys.exit('Не нашёл app.py или modules/ — укажите папку проекта.')

    auto, db_path = check(app_dir)
    if args.check:
        return
    copy(app_dir)
    if args.activate:
        if db_path:
            activate(db_path)
        else:
            print(WARN + 'Включить не удалось: нет базы с таблицей modules. '
                         'Запустите проект один раз и повторите --activate.')
    print('\nДальше: перезапустите проект и откройте '
          f'http://127.0.0.1:5000{URL_PREFIX}/')
    if not auto:
        print('Загрузчик не подошёл — подключите вручную (INSTALL.md, '
              'вариант Б) или запустите отдельно (вариант В).')


if __name__ == '__main__':
    main()
