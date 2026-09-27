import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
n = count_tasks()
assert isinstance(n, int), f"expected int, got {type(n)}"
assert n == 20, f"expected 20 tasks, got {n}"
