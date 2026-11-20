"""Sandboxed custom Python for user-provided content transformers.

Users write:   def process(context: dict) -> dict: ...

The code runs with a strict builtins whitelist: no imports, no file/network
access, no dunder attributes, no global/nonlocal, no async. On Unix it also
has a 5‑second wall clock timeout via signal.alarm.

NOTE: CPython has no true sandbox. The AST checks + restricted builtins catch
the obvious attacks, but a format-string trick like
    "{0.__class__}".format(x)
can still leak object internals. This module is appropriate for a *small,
logged-in user base* (your own dashboard users), not for executing arbitrary
untrusted code at internet scale. For that, use `RestrictedPython`.
"""
from __future__ import annotations

import ast
import signal
from typing import Callable

_TIMEOUT_SECONDS = 5

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len,
    "list": list, "max": max, "min": min, "print": print,
    "range": range, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "True": True, "False": False, "None": None,
}

_COMPILE_CACHE: dict[str, Callable | None] = {}


class _CustomCodeTimeout(Exception):
    pass


def _timeout_handler(signum, frame):  # noqa: ARG001
    raise _CustomCodeTimeout(f"Custom Python exceeded {_TIMEOUT_SECONDS}s")


def _validate_ast(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("import statements are not allowed")
        if isinstance(node, (ast.Attribute, ast.Name)):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name.startswith("__") and name.endswith("__"):
                raise ValueError(f"dunder access ({name}) is not allowed")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError("global/nonlocal declarations are not allowed")
        if isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.Await)):
            raise ValueError("async code is not allowed")


def compile_custom_python(code: str):
    """Parse + validate user code, return the process() function (or None)."""
    code = (code or "").strip()
    if not code:
        return None
    if code in _COMPILE_CACHE:
        return _COMPILE_CACHE[code]

    try:
        tree = ast.parse(code, filename="<custom_python>", mode="exec")
    except SyntaxError as e:
        raise ValueError(f"Python syntax error: {e.msg} (line {e.lineno})")
    _validate_ast(tree)

    namespace = {"__builtins__": _SAFE_BUILTINS}
    try:
        exec(compile(tree, "<custom_python>", "exec"), namespace)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Python failed to load: {e}")

    fn = namespace.get("process")
    if not callable(fn):
        raise ValueError("Your Python code must define: def process(context): ...")
    _COMPILE_CACHE[code] = fn
    return fn


def apply_custom_python(context: dict, code: str) -> dict:
    """Run the user's process() on the context; return the (possibly new) dict."""
    fn = compile_custom_python(code)
    if fn is None:
        return context

    def _run():
        result = fn(dict(context))
        if not isinstance(result, dict):
            raise ValueError("process() must return a dict")
        return result

    if hasattr(signal, "SIGALRM"):  # Unix / macOS only
        old = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(_TIMEOUT_SECONDS)
        try:
            return _run()
        except _CustomCodeTimeout as e:
            raise ValueError(str(e)) from e
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
    return _run()