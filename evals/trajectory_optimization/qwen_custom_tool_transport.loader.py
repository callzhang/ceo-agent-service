import sys
import json
import types
import importlib.abc
import importlib.util

payload = json.load(sys.stdin)


class SourceLoader(importlib.abc.Loader):
    def __init__(self, name, source):
        self.name = name
        self.source = source

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = "anonymous-offline-source:" + self.name
        exec(compile(self.source, module.__file__, "exec"), module.__dict__)


class SourceFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in payload["sources"]:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            SourceLoader(fullname, payload["sources"][fullname]),
            origin="anonymous-offline-source:" + fullname,
        )


sys.meta_path.insert(0, SourceFinder())
main = types.ModuleType("__main__")
main.__dict__["__name__"] = "__main__"
sys.modules["__main__"] = main
exec(compile(payload["test"], "anonymous-check-transport", "exec"), main.__dict__)
