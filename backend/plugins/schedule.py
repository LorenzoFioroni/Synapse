"""When a plugin's scheduled work runs: cron rules ("30 2 * * 1-5") or intervals ("6h"), in a time
zone, with catching up on a run missed while the computer slept or the app was closed."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import PluginError

_NAMES = {
    "dow": {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])},
    "month": {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())},
}
_RANGES = {"minute": (0, 59), "hour": (0, 23), "dom": (1, 31), "month": (1, 12), "dow": (0, 7)}
EVERY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$")
UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _field(text: str, kind: str) -> set[int]:
    lo, hi = _RANGES[kind]
    names = _NAMES.get(kind, {})
    out: set[int] = set()
    for part in text.lower().split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise PluginError(f"cron: bad step in {text!r}")
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = _value(x, names), _value(y, names)
        else:
            a = b = _value(part, names)
            if step > 1:
                b = hi
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise PluginError(f"cron: {text!r} is outside {lo}–{hi}")
        out.update(range(a, b + 1, step))
    if kind == "dow" and 7 in out:  # 7 is Sunday too
        out.discard(7)
        out.add(0)
    return out


def _value(v: str, names: dict[str, int]) -> int:
    if v in names:
        return names[v]
    if not v.isdigit():
        raise PluginError(f"cron: {v!r} is not a number")
    return int(v)


@dataclass
class Cron:
    """A five-field cron rule: minute hour day-of-month month day-of-week."""

    text: str
    minute: set[int] = field(default_factory=set)
    hour: set[int] = field(default_factory=set)
    dom: set[int] = field(default_factory=set)
    month: set[int] = field(default_factory=set)
    dow: set[int] = field(default_factory=set)
    dom_any: bool = True
    dow_any: bool = True

    @classmethod
    def parse(cls, text: str) -> Cron:
        parts = text.split()
        if len(parts) != 5:
            raise PluginError(f"cron {text!r}: five fields (minute hour day month weekday)")
        c = cls(text)
        for kind, part in zip(("minute", "hour", "dom", "month", "dow"), parts):
            setattr(c, kind, _field(part, kind))
        c.dom_any, c.dow_any = parts[2] == "*", parts[4] == "*"
        return c

    def _day_ok(self, d: datetime) -> bool:
        if d.month not in self.month:
            return False
        dom_ok = d.day in self.dom
        dow_ok = (d.weekday() + 1) % 7 in self.dow
        if self.dom_any or self.dow_any:  # cron's rule: both restricted means either one
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    def next_after(self, after: datetime) -> datetime:
        """The next wall time after ``after`` (in the rule's zone, or naive for system local time)."""
        t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 2):
            if self._day_ok(t):
                for h in sorted(x for x in self.hour if x >= t.hour):
                    mins = sorted(m for m in self.minute if h > t.hour or m >= t.minute)
                    if mins:
                        return t.replace(hour=h, minute=mins[0])
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
        raise PluginError(f"cron {self.text!r} never fires")


def every_seconds(value) -> float:
    """``30``, ``"90s"``, ``"30m"``, ``"6h"``, ``"1d"`` → seconds (at least one minute)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        secs = float(value)
    else:
        m = EVERY_RE.match(str(value))
        if not m:
            raise PluginError(f"every {value!r}: a number of seconds, or like 30m, 6h, 1d")
        secs = float(m.group(1)) * UNITS[m.group(2)]
    if secs < 60:
        raise PluginError("every: at least one minute")
    return secs


def zone(name: str | None) -> tzinfo | None:
    if not name:
        return None  # resolve system-local rules for the scheduled date, not today's UTC offset
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise PluginError(f"Unknown time zone {name!r}") from e


@dataclass
class Schedule:
    name: str
    run: Callable[[], object]
    cron: Cron | None = None
    every: float | None = None
    tz: tzinfo | None = None
    catch_up: bool = True

    def next_after(self, last: datetime) -> datetime:
        if self.cron:
            if self.tz is not None:
                return self.cron.next_after(last.astimezone(self.tz))
            # astimezone() without an argument returns a fixed offset. Strip it before finding
            # the next wall time, then let the OS resolve that date's local DST rules.
            wall = last.astimezone().replace(tzinfo=None)
            return self.cron.next_after(wall).astimezone(timezone.utc)
        return last + timedelta(seconds=self.every or 3600)

    def due(self, now: datetime, last: datetime | None) -> tuple[bool, datetime | None]:
        """Whether to run now, and what to remember as the last run when not running (a run
        missed while away and not caught up is skipped). ``last`` is None the first time: with
        ``catch_up`` the work runs at once, else it waits for the first planned time."""
        if last is None:
            return (True, None) if self.catch_up else (False, now)
        nxt = self.next_after(last)
        if nxt > now:
            return False, None
        if self.catch_up or now - nxt < timedelta(minutes=2):
            return True, None
        return False, now  # missed and not caught up: start counting from now


def make(name: str, run: Callable, *, cron: str | None, every, tz: str | None, catch_up: bool) -> Schedule:
    if (cron is None) == (every is None):
        raise PluginError(f'schedule "{name}": give cron="…" or every=… (one of them)')
    return Schedule(
        name=name,
        run=run,
        cron=Cron.parse(cron) if cron else None,
        every=every_seconds(every) if every is not None else None,
        tz=zone(tz),
        catch_up=catch_up,
    )
