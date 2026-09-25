import sys; sys.path.insert(0, "/Users/aashish/BEST PROJECT/flash-coder")
n = count_tasks()
assert isinstance(n, int), f"expected int, got {type(n)}"
assert n == 20, f"expected 20 tasks, got {n}"
