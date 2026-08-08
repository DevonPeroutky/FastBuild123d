"""Shared error value for `Value | Error` return unions (PYTHON_CODE_STANDARDS §3)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Error:
    message: str
