"""Select tests from a per-test coverage and fixture map of the unchanged checkout."""
import ast
import gzip
import json
import os
import re
from functools import lru_cache
from pathlib import Path

APP = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("SQLGLOT_SOURCE", "/opt/sqlglot"))
FILE_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def changes(patch):
    files = {}
    current = None
    edits = None
    old_remaining = new_remaining = 0
    for line in patch.split("\n"):
        if old_remaining or new_remaining:
            if line == r"\ No newline at end of file":
                continue
            tag = line[:1]
            if tag not in ("+", "-", " "):
                raise ValueError("incomplete hunk")
            old_remaining -= tag != "+"
            new_remaining -= tag != "-"
            if old_remaining < 0 or new_remaining < 0:
                raise ValueError("invalid hunk range")
            edits.append((tag, line[1:]))
            continue
        if line.startswith("diff --git "):
            header = FILE_HEADER.fullmatch(line)
            if not header or header[1] != header[2]:
                raise ValueError("unsupported file change")
            current = header[2]
            if (
                current in files
                or "\\" in current
                or ":" in current
                or any(part in ("", ".", "..") for part in current.split("/"))
            ):
                raise ValueError("invalid file path")
            files[current] = []
        elif m := HUNK.match(line):
            if current is None:
                raise ValueError("hunk before file header")
            old_remaining = int(m[2]) if m[2] is not None else 1
            new_remaining = int(m[3]) if m[3] is not None else 1
            offset = int(m[1]) - bool(old_remaining)
            if offset < 0:
                raise ValueError("invalid hunk offset")
            edits = []
            files[current].append((offset, edits))
        elif line.startswith("@@"):
            raise ValueError("invalid hunk header")
    if old_remaining or new_remaining or not files:
        raise ValueError("incomplete patch")
    return files


@lru_cache(maxsize=128)
def baseline(path):
    content = (SOURCE / path).read_text(encoding="utf-8").split("\n")
    if content[-1] == "":
        content.pop()
    return content, ast.parse("\n".join(content) + "\n")


def equivalent(before, after):
    if type(before) is not type(after):
        return False
    if isinstance(before, ast.AST):
        return all(
            equivalent(getattr(before, field, None), getattr(after, field, None))
            for field in before._fields
        )
    if isinstance(before, list):
        return len(before) == len(after) and all(
            equivalent(old, new) for old, new in zip(before, after)
        )
    return before == after


def compare(path, hunks):
    lines, tree = baseline(path)
    revised = list(lines)
    touched = set()
    for start, edits in reversed(hunks):
        old = [s for tag, s in edits if tag != "+"]
        if revised[start:start + len(old)] != old:
            raise ValueError("hunk context mismatch")
        revised[start:start + len(old)] = [s for tag, s in edits if tag != "-"]
        pos = start + 1
        insert_at = None
        block_deletes = False
        for tag, _ in [*edits, (" ", "")]:
            if tag == "-":
                touched.add(pos)
                block_deletes = True
            elif tag == "+" and insert_at is None:
                insert_at = pos
            elif tag == " ":
                if insert_at is not None and not block_deletes:
                    touched.update((insert_at - 1, insert_at))
                insert_at = None
                block_deletes = False
            if tag != "+":
                pos += 1
    updated = ast.parse("\n".join(revised) + "\n")
    same = equivalent(tree, updated)
    if not same:
        compile(updated, path, "exec", dont_inherit=True)
    return same, touched, tree


def schedule(tests):
    ordered = sorted(
        ((round(test["seconds"] * 1000), test["number"]) for test in tests), reverse=True
    )
    if not ordered:
        return []
    total = sum(duration for duration, _ in ordered)
    best_cost, best = float("inf"), []
    for count in range(1, min(8, len(ordered)) + 1):
        if max(ordered[0][0], (total + count - 1) // count) + count * 2000 >= best_cost:
            continue
        loads, workers = [0] * count, [[] for _ in range(count)]
        for duration, number in ordered:
            i = loads.index(min(loads))
            workers[i].append(number)
            loads[i] += duration
        cost = max(loads) + count * 2000
        if cost < best_cost:
            best_cost, best = cost, workers
    return best


def combine(values):
    mask = 0
    for value in values:
        mask |= value
    return mask


def coverage_between(hits, start, end):
    return combine(hits.get(line, 0) for line in range(start, end + 1))


def main():
    data = Path(os.environ.get("BLAST_DATA", "/data"))
    tests = json.loads((data / "tests.json").read_text(encoding="utf-8"))["tests"]
    with gzip.open(APP / "map.json.gz", "rt") as f:
        model = json.load(f)
    index = {name: i for i, name in enumerate(model["ids"])}
    incomplete = set(model.get("incomplete", ()))
    bits = {
        t["number"]: 1 << index[t["id"]] if t["id"] in index and index[t["id"]] not in incomplete else 0
        for t in tests
    }
    all_tests = combine(bits.values())
    tests_by_file = {}
    for test in tests:
        file = test["file"]
        tests_by_file[file] = tests_by_file.get(file, 0) | bits[test["number"]]
    rows = {p: {int(n): int(v, 16) for n, v in ns.items()} for p, ns in model["rows"].items()}
    with gzip.open(APP / "classes.json.gz", "rt") as f:
        classes = {key: int(value, 16) for key, value in json.load(f).items()}
    for patch in sorted((data / "commits").glob("*.patch")):
        mask = 0
        try:
            changed_files = changes(patch.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            changed_files = {}
            mask = all_tests
        for path, hunks in changed_files.items():
            hits = rows.get(path, {})
            if path.startswith("tests/fixtures/"):
                mask |= hits.get(0, all_tests)
                continue
            if not path.endswith(".py") or not hunks:
                mask |= all_tests
                continue
            try:
                same, touched, tree = compare(path, hunks)
            except (SyntaxError, OSError, ValueError, RecursionError):
                mask |= all_tests
                continue
            if same:
                continue
            module_hits = combine(hits.values())
            if path.startswith("tests/"):
                mask |= (tests_by_file.get(path, 0) | module_hits) or all_tests
                continue
            funcs = [
                (node.body[0].lineno, node.end_lineno)
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            for line in touched:
                recorded = hits.get(line, 0)
                functions = [(start, end) for start, end in funcs if start <= line <= end]
                if functions:
                    start, end = min(functions, key=lambda span: span[1] - span[0])
                    # A changed branch can affect tests that previously took another path.
                    function_hits = coverage_between(hits, start, end)
                    mask |= (recorded | function_hits) or module_hits or all_tests
                    continue
                owners = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.lineno < line <= n.end_lineno]
                if path.startswith("sqlglot/expressions/") and owners:
                    owner = min(owners, key=lambda n: n.end_lineno - n.lineno)
                    key = path[:-3].replace("/", ".") + "." + owner.name
                    if key in classes:
                        mask |= recorded | classes[key] | coverage_between(hits, owner.lineno, owner.end_lineno)
                        continue
                name = Path(path).stem
                mask |= recorded | module_hits
                if path.startswith(("sqlglot/dialects/", "sqlglot/parsers/", "sqlglot/generators/", "sqlglot/typing/")) and name != "dialect":
                    for peer in ("dialects", "parsers", "generators", "typing"):
                        mask |= combine(rows.get(f"sqlglot/{peer}/{name}.py", {}).values())
                    mask |= combine(bits[t["number"]] for t in tests if t["file"].endswith(f"/test_{name}.py"))
                elif path.startswith("sqlglot/"):
                    mask |= all_tests
        selected = [t for t in tests if not bits[t["number"]] or bits[t["number"]] & mask]
        print(json.dumps({"commit": patch.stem, "workers": schedule(selected)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
