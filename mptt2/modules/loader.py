import ast
import importlib.util
import os
import sys
import traceback


SKIP = {'__init__', 'loader'}


class ModuleLoader:
    def __init__(self, app, base_dir):
        self.app = app
        self.base_dir = base_dir
        self.modules_dir = os.path.join(base_dir, 'modules')
        self.loaded = {}     # name -> module object
        self.errors = {}     # name -> error string

    # ---------- сканирование каталога ----------

    def scan(self):
        if not os.path.isdir(self.modules_dir):
            return []
        out = []
        for fn in sorted(os.listdir(self.modules_dir)):
            if not fn.endswith('.py'):
                continue
            stem = fn[:-3]
            if stem in SKIP or stem.startswith('_'):
                continue
            path = os.path.join(self.modules_dir, fn)
            info = self._read_info(path, stem)
            out.append({'filename': fn, 'name': stem, 'info': info})
        return out

    def exists(self, name):
        return os.path.isfile(self.module_path(name))

    def module_path(self, name):
        return os.path.join(self.modules_dir, f'{name}.py')

    # ---------- чтение MODULE_INFO без импорта модуля ----------

    def _read_info(self, path, fallback_name):
        info = {'name': fallback_name, 'description': '', 'version': '?'}
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                tree = ast.parse(fh.read())
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == 'MODULE_INFO':
                            try:
                                info = ast.literal_eval(node.value)
                            except Exception:
                                pass
        except Exception as e:
            info['description'] = f'(parse error: {e})'
        return info

    # ---------- загрузка активных модулей ----------

    def load_active(self, active_names):
        if not os.path.isdir(self.modules_dir):
            return
        if self.modules_dir not in sys.path:
            sys.path.insert(0, self.modules_dir)
        for name in active_names:
            path = self.module_path(name)
            if not os.path.isfile(path):
                self.errors[name] = 'Файл не найден'
                continue
            try:
                mod_name = f'mod_{name}'
                spec = importlib.util.spec_from_file_location(mod_name, path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                register = getattr(mod, 'register', None)
                if callable(register):
                    register(self.app, self)
                self.loaded[name] = mod
            except Exception as e:
                self.errors[name] = f'{type(e).__name__}: {e}'
                traceback.print_exc()