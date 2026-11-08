"""
Запуск модуля как отдельного приложения — без основного проекта.

Запасной вариант на случай, если подключить модуль к проекту не удалось:
страница открывается на своём порту и работает со своей базой.

    python modules/commerce_realty/standalone.py            # порт 5001
    python modules/commerce_realty/standalone.py --port 5050
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, redirect  # noqa: E402

import commerce_realty  # noqa: E402


def create_app():
    app = Flask(__name__)
    commerce_realty.register(app)

    @app.route('/')
    def root():
        return redirect(commerce_realty.MENU['url'] + '/')

    return app


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--port', type=int, default=5001)
    args = ap.parse_args()
    print(f'Коммерческая недвижимость: http://127.0.0.1:{args.port}/')
    create_app().run(host='127.0.0.1', port=args.port, debug=False)
