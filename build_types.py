"""Observe operand types on baseline comparisons, without applying mutations."""
import ast
import builtins
from collections import defaultdict
import concurrent.futures
import enum
import gzip
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import sys
import pytest
from build_map import TracedPool


class CompareTypes(ast.NodeTransformer):
    def __init__(self, file):
        self.file = file

    def visit_Compare(self, node):
        self.generic_visit(node)
        if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)):
            return node
        # Each operand is evaluated once in its original position.
        for side, expr in enumerate([node.left, node.comparators[0]]):
            key = f"{self.file}:{node.lineno}:{node.col_offset}:{side}"
            traced = ast.copy_location(ast.Call(ast.Name("_blast_observe_type", ast.Load()),
                                                 [ast.Constant(key), expr], []), expr)
            if side:
                node.comparators[0] = traced
            else:
                node.left = traced
        return node


class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        tree = ast.parse(self.get_data(self.path), self.path)
        tree = CompareTypes(Path(self.path).relative_to(ROOT).as_posix()).visit(tree)
        return compile(ast.fix_missing_locations(tree), self.path, "exec", dont_inherit=True)


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "sqlglot" or fullname.startswith("sqlglot."):
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec and spec.origin and spec.origin.endswith(".py"):
                spec.loader = Loader(fullname, spec.origin)
            return spec


if __name__ == "__main__":
    ROOT, output = [Path(p).resolve() for p in sys.argv[1:]]
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.meta_path.insert(0, Finder())
    types = defaultdict(set)

    def observe(key, value):
        types[key].add(type(value))
        return value

    builtins._blast_observe_type = observe
    os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", SKIP_INTEGRATION="0")
    concurrent.futures.ProcessPoolExecutor = TracedPool
    result = pytest.main(["-q", "tests"])
    if result != 0:
        raise SystemExit(result)
    names = {t: t.__module__ + "." + t.__qualname__ for seen in types.values() for t in seen}
    definitions = {name: [issubclass(t, enum.Enum), t.__eq__.__qualname__ if hasattr(t.__eq__, "__qualname__") else ""]
                   for t, name in names.items()}
    with gzip.open(output, "wt") as f:
        json.dump(dict(observed={k: sorted(names[t] for t in ts) for k, ts in types.items()},
                       definitions=definitions), f)
