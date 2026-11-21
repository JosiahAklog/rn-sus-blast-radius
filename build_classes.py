"""Index expression types constructed by each baseline test."""
import gzip
import json
import os
import sys
from pathlib import Path
from collections import defaultdict
import concurrent.futures
import pytest
from build_map import TracedPool


class ClassMap:
    def __init__(self, ids):
        self.numbers = {name: i for i, name in enumerate(ids)}
        self.active = 0
        self.seen = defaultdict(int)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        i = self.numbers.get(item.nodeid)
        self.active = 0 if i is None else 1 << i
        yield
        self.active = 0


if __name__ == "__main__":
    root, catalog, out = [Path(p).resolve() for p in sys.argv[1:]]
    tracker = ClassMap([t["id"] for t in json.loads(catalog.read_text())["tests"]])
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", SKIP_INTEGRATION="0")
    from sqlglot.expressions.core import Expr
    init = Expr.__init__
    seen = set()

    def init_traced(self, **kwargs):
        identity = (type(self), tracker.active)
        if identity not in seen:
            seen.add(identity)
            for cls in type(self).__mro__:
                tracker.seen[cls.__module__ + "." + cls.__name__] |= tracker.active
        init(self, **kwargs)

    Expr.__init__ = init_traced
    concurrent.futures.ProcessPoolExecutor = TracedPool
    pytest.main(["-q", "--continue-on-collection-errors", "tests"], plugins=[tracker])
    with gzip.open(out, "wt") as f:
        json.dump({k: hex(v) for k, v in tracker.seen.items()}, f)
