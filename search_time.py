"""Per-pass monotonic search accounting and a process-local tool deadline.

Only completed segments can be resumed. A crash leaves an active marker; its
unrecorded duration is unknown, so it requires an explicit archived retry pass.
Legacy elapsed/started fields never establish cumulative time provenance.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
import subprocess
import time


VERSION = 1
UNKNOWN = 'Suchzeit nicht zuverlässig belegt; ausdrücklich neuen Suchpass starten. Belege und Reservierung bleiben erhalten.'
_DEADLINE = ContextVar('ps2decomp_search_deadline', default=None)


class Exhausted(TimeoutError):
    pass


def input_identity(pins):
    try:
        identity = sorted((path, pin['sha256']) for path, pin in pins.items())
        return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise ValueError(UNKNOWN) from exc


def initial(settings, pins=None):
    """No elapsed field: an untouched queued job has not started a segment."""
    record = dict(version=VERSION, spent=0.0, attempts=0, segments=0, active=False,
                  seconds=settings['seconds'], maxAttempts=settings['maxAttempts'])
    if pins is not None:
        record['inputs'] = input_identity(pins)
    return record


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


class Budget:
    def __init__(self, job, directory, settings, pins=None):
        record = job.get('timeAccounting')
        if 'timeAccounting' not in job:
            # A manual seed can exist before the first execution. Every other
            # job artifact/marker is evidence that zero time is not established.
            pristine = (job.get('status') == 'queued' and type(job.get('attempts')) is int
                        and job['attempts'] == 0 and job.get('best') is None
                        and not job.get('deferred', False)
                        and set(job) <= {'name', 'status', 'attempts', 'best', 'created', 'inputMode', 'deferred'}
                        and all(p.name in ('job.json', 'seed.c') for p in directory.iterdir()))
            if not pristine:
                raise ValueError(UNKNOWN)
            record = initial(settings, pins)
        if (not isinstance(record, dict) or type(record.get('version')) is not int
                or record['version'] != VERSION or record.get('active') is not False
                or not _number(record.get('spent'))
                or type(record.get('segments')) is not int or record['segments'] < 0
                or type(job.get('attempts')) is not int or job['attempts'] < 0
                or type(record.get('attempts')) is not int or record['attempts'] != job['attempts']
                or record.get('seconds') != settings['seconds']
                or record.get('maxAttempts') != settings['maxAttempts']
                or pins is not None and record.get('inputs') != input_identity(pins)
                or record['segments'] == 0 and (record['spent'] != 0 or job['attempts'] != 0
                    or any(key in job for key in ('started', 'segmentStarted', 'elapsed', 'workerPid', 'lastAttempt', 'finished')))
                or record['segments'] > 0 and (not _number(job.get('elapsed')) or job['elapsed'] != round(record['spent'], 2))):
            raise ValueError(UNKNOWN)
        self.job, self.record = job, dict(record)
        self.base, self.limit = float(record['spent']), settings['seconds']
        self.started = None

    def spent(self):
        return self.base + (max(0.0, time.monotonic() - self.started) if self.started is not None else 0.0)

    def remaining(self):
        return max(0.0, self.limit - self.spent())

    def timeout(self, maximum):
        remaining = self.remaining()
        if remaining <= 0:
            raise Exhausted('Zeitlimit erreicht')
        return min(maximum, remaining)

    def start(self):
        self.started = time.monotonic()
        self.record.update(active=True, segments=self.record['segments'] + 1)

    def checkpoint(self, *, finished=False):
        spent = self.spent()
        self.record.update(spent=spent, attempts=self.job['attempts'], active=not finished)
        self.job['timeAccounting'] = dict(self.record)
        self.job['elapsed'] = round(spent, 2)  # Presentation only, never a budget input.

    @contextmanager
    def tools(self):
        """All nested local tool calls share this segment's remaining time."""
        deadline = self.started + max(0.0, self.limit - self.base)
        outer = _DEADLINE.get()
        token = _DEADLINE.set(min(deadline, outer) if outer is not None else deadline)
        try:
            yield
        finally:
            _DEADLINE.reset(token)


def tool_timeout(command, requested):
    """Bound at actual process launch, after caller setup and earlier probes."""
    deadline = _DEADLINE.get()
    if deadline is None:
        return requested
    remaining = deadline - time.monotonic()
    if remaining <= 0 or requested <= 0:
        raise subprocess.TimeoutExpired(command, 0)
    return min(requested, remaining)
