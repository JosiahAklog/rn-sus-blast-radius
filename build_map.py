"""Collect baseline line coverage and fixture reads at the pinned checkout."""
import gzip
import json
import os
from pathlib import Path
import sys
from collections import defaultdict
import pytest
import concurrent.futures
import pickle


class TracedPool(concurrent.futures.Executor):
    """Run pool tasks in the tracing process, preserving the serialization boundary."""
    def __init__(self, initializer=None, initargs=(), **kwargs):
        if initializer:
            initializer(*initargs)

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            fn, args, kwargs = pickle.loads(pickle.dumps((fn, args, kwargs)))
            future.set_result(pickle.loads(pickle.dumps(fn(*args, **kwargs))))
        except Exception as err:
            future.set_exception(err)
        return future



class MapBuilder:
    def __init__(self, root, tests):
        self.prefix = str(root) + "/"
        self.ids = [t["id"] for t in tests]
        self.index = {name: i for i, name in enumerate(self.ids)}
        self.bits = defaultdict(dict)
        self.active = 0

        self.bad = set()
        self.seen = set()

    def record(self, path, line):
        rows = self.bits[path]
        rows[line] = rows.get(line, 0) | self.active

    def trace(self, code, line):
        path = code.co_filename
        if self.active and path.startswith(self.prefix):
            self.record(path[len(self.prefix):], line)
        return sys.monitoring.DISABLE

    def audit(self, event, args):
        if self.active and event == "open" and isinstance(args[0], str):
            path = os.path.abspath(args[0])
            if "/tests/fixtures/" in path and path.startswith(self.prefix):
                self.record(path[len(self.prefix):], 0)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        index = self.index.get(item.nodeid)
        if index is not None:
            self.seen.add(index)
            self.active = 1 << index
            sys.monitoring.restart_events()
        yield
        self.active = 0

    def pytest_runtest_logreport(self, report):
        if report.nodeid in self.index and (report.failed or report.skipped):
            self.bad.add(self.index[report.nodeid])


def main():
    root, catalog, output = map(Path, sys.argv[1:])
    root, output = root.resolve(), output.resolve()
    tests = json.loads(catalog.read_text())["tests"]
    builder = MapBuilder(root, tests)
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["SKIP_INTEGRATION"] = "0"
    concurrent.futures.ProcessPoolExecutor = TracedPool
    monitor = sys.monitoring
    monitor.use_tool_id(4, "coverage map")
    monitor.register_callback(4, monitor.events.LINE, builder.trace)
    monitor.set_events(4, monitor.events.LINE)
    sys.addaudithook(builder.audit)
    try:
        pytest.main(["-q", "--continue-on-collection-errors", "tests"], plugins=[builder])
    finally:
        monitor.set_events(4, 0)
        missing = set(range(len(tests))) - builder.seen | builder.bad
        rows = {p: {n: hex(v) for n, v in ns.items()} for p, ns in builder.bits.items()}
        with gzip.open(output, "wt") as f:
            json.dump(dict(ids=builder.ids, rows=rows, incomplete=sorted(missing)), f)
        print(f"{len(missing)} tests require conservative fallback", file=sys.stderr)


if __name__ == "__main__":
    main()
