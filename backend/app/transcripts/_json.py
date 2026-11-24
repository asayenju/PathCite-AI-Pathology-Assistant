"""Defensive accessors for untrusted vendor JSON: wrong types become empty, never crash."""

from typing import Any


def as_dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def as_list(x: Any) -> list:
    return x if isinstance(x, list) else []


def dicts(x: Any) -> list[dict]:
    return [i for i in as_list(x) if isinstance(i, dict)]


def num(x: Any) -> float | None:
    """Number or numeric string (AWS sends "1.23") -> float; anything else -> None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return None
    return None


def text(x: Any) -> str:
    return x if isinstance(x, str) else ""
