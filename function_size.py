"""Workspace selection range, separate from native control-flow safety gates."""
from candidate_preflight import MAX_WORDS
from collections.abc import Mapping

DEFAULTS = dict(minBytes=8, maxBytes=640)
MAX_BYTES = MAX_WORDS * 4


def settings(value=None):
    value = {} if value is None else value
    result = {key: value.get(key, default) for key, default in DEFAULTS.items()} if isinstance(value, Mapping) else {}
    if (not result or any(type(v) is not int for v in result.values()) or
            not 0 <= result['minBytes'] <= result['maxBytes'] <= MAX_BYTES or
            result['maxBytes'] < 8):
        raise ValueError('Ungültiger Byte-Bereich: ganze Zahlen von 0 bis 32768, bis mindestens 8 und von nicht größer als bis.')
    return result


def contains(size, value=None):
    limits = settings(value)
    return 8 <= size and limits['minBytes'] <= size <= limits['maxBytes']
