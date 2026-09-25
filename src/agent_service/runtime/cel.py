"""CEL evaluation shared by the CEL evaluator (MCP) and the Built-in step library.

Every rule a builder makes with the designer's pickers is CEL. Besides the standard library,
rules can call the service's named functions below: each one is written and tested once here
and offered in the pickers, which is how the service handles logic a plain comparison gets
wrong (travel-sync's "is this traveler me?" is the first).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import celpy
from celpy import celtypes

# Record fields typed "date & time with time zone" arrive as ISO 8601 text and become CEL
# timestamps, so rules can compare them and subtract them (b.start - a.end <= duration('24h')).
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:\d{2}|Z)$")


class RuleError(Exception):
    """A rule failed to parse or to evaluate. The message names the rule."""


def _name_tokens(text: str) -> set[str]:
    return {p.lower() for p in re.split(r"[\s/,]+", text) if p}


def is_me(traveler: celtypes.StringType, my_name: celtypes.StringType) -> celtypes.BoolType:
    """True if a traveler name, as written in an email, is the person the agent acts for.
    Airline records write names as 'RIVERA/ALEX'; compare on name tokens (from travel-sync)."""
    wanted, got = _name_tokens(str(my_name)), _name_tokens(str(traveler))
    return celtypes.BoolType(len(wanted & got) >= 2 or (len(wanted) == 1 and bool(wanted & got)))


def norm(text: celtypes.StringType) -> celtypes.StringType:
    """Ignore spaces, dashes and capitals: UA 1244, ua1244 and UA-1244 are the same."""
    return celtypes.StringType(re.sub(r"[\s\-]", "", str(text)).upper())


def date_of(ts: celtypes.TimestampType) -> celtypes.StringType:
    """The calendar date of a timestamp, in its own time zone, as YYYY-MM-DD."""
    return celtypes.StringType(str(ts)[:10])


FUNCTIONS = {"is_me": is_me, "norm": norm, "date_of": date_of}
_env = celpy.Environment(annotations={name: celtypes.FunctionType for name in FUNCTIONS})


def to_cel(value: Any, timestamps: bool = False) -> Any:
    """JSON-like data to CEL values. With `timestamps`, ISO 8601 strings become timestamps."""
    if isinstance(value, dict):
        return celtypes.MapType({celtypes.StringType(k): to_cel(v, timestamps) for k, v in value.items()})
    if isinstance(value, list):
        return celtypes.ListType([to_cel(v, timestamps) for v in value])
    if timestamps and isinstance(value, str) and _TIMESTAMP.match(value):
        return celtypes.TimestampType(value)
    if isinstance(value, datetime):
        return celtypes.TimestampType(value.isoformat())
    return celpy.json_to_cel(value)


def to_python(value: Any) -> Any:
    """CEL values back to JSON-safe Python."""
    if isinstance(value, celtypes.BoolType):
        return bool(value)
    if isinstance(value, (celtypes.IntType, celtypes.UintType)):
        return int(value)
    if isinstance(value, celtypes.DoubleType):
        return float(value)
    if isinstance(value, celtypes.StringType):
        return str(value)
    if isinstance(value, celtypes.TimestampType):
        return value.isoformat()
    if isinstance(value, celtypes.DurationType):
        return str(value)
    if isinstance(value, (celtypes.ListType, list, tuple)):
        return [to_python(v) for v in value]
    if isinstance(value, (celtypes.MapType, dict)):
        return {str(k): to_python(v) for k, v in value.items()}
    return value


class Rule:
    """One compiled CEL expression, named so errors and run logs can point at it."""

    def __init__(self, name: str, source: str):
        self.name, self.source = name, source
        try:
            self._program = _env.program(_env.compile(source), functions=FUNCTIONS)
        except Exception as exc:  # celpy raises CELParseError and friends
            raise RuleError(f"Rule {name!r} is not valid CEL: {exc}") from None

    def evaluate_cel(self, activation: dict[str, Any]) -> Any:
        try:
            result = self._program.evaluate(activation)
        except Exception as exc:
            raise RuleError(f"Rule {name_of(self)} failed: {exc}") from None
        if isinstance(result, Exception):   # celpy can return errors as values
            raise RuleError(f"Rule {name_of(self)} failed: {result}")
        return result

    def evaluate(self, data: dict[str, Any], timestamps: bool = False) -> Any:
        return to_python(self.evaluate_cel({k: to_cel(v, timestamps) for k, v in data.items()}))


def name_of(rule: Rule) -> str:
    return f"{rule.name!r} ({rule.source})"
