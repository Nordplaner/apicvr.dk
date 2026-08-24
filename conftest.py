# Present so pytest puts the repository root on sys.path, which is what lets
# `tests/` do `from apis import searchcvr`. Without it, a bare `pytest`
# invocation inserts only `tests/` and the import fails -- `python -m pytest`
# happens to work because it adds the working directory itself.
