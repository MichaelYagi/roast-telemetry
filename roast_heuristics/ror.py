"""Rate of rise (RoR) -- how fast a temperature is climbing, in degrees per
minute -- worked out the way roasting software conventionally does it, so the
numbers can be compared with other tools' readings of the same roast.

Two steps, both standard:

1. **Raw rate** over a span (default 20 s): the newest reading minus the
   reading about one span earlier, divided by the time between them, times 60.
   The older reading is averaged with its neighbours (3, or 5 when there are
   enough) so a single noisy sample doesn't swing the result -- this adds no
   delay, because the newest reading is used as it is.
2. **Smoothing:** a weighted average of the last few raw rates (default 4),
   the newest weighted most (weights 1, 2, 3, 4).

No rate is given until most of a span of readings exists, since a rate over a
few seconds is mostly noise. Written for this project from that description. ``RateOfRise`` works on a
stream of readings (live hardware, the simulator); ``rate_of_rise_series``
runs the same calculation over a finished curve (imported ``.alog`` files).
"""
from __future__ import annotations

from collections import deque
from statistics import median
from typing import Optional, Sequence

DEFAULT_SPAN_S = 20.0
DEFAULT_SMOOTHING_POINTS = 4

WARMUP_FRACTION = 0.9  # no rate until this fraction of a span has been recorded

_KEEP = 2000  # readings remembered; far more than a span needs at any sampling rate


def _resample_uniform(times: Sequence[float], values: Sequence[float], step: float) -> list[float]:
    """The values on an evenly spaced grid (spacing ``step``) that ends at the newest time --
    the same numbers back for evenly sampled data, and a sensible average for uneven data."""
    n = len(values)
    if n < 2 or step <= 0:
        return list(values)
    end = times[-1]
    out: list[float] = []
    j = 0
    for i in range(n):
        x = end - (n - 1 - i) * step
        while j + 1 < n - 1 and times[j + 1] < x:
            j += 1
        t0, t1 = times[j], times[j + 1]
        if x <= t0 or t1 == t0:
            out.append(values[j])
        elif x >= t1:
            out.append(values[j + 1])
        else:
            out.append(values[j] + (values[j + 1] - values[j]) * (x - t0) / (t1 - t0))
    return out


class RateOfRise:
    """Feed it readings one at a time; ``update()`` returns the current RoR in °/min
    (or None until there are two readings)."""

    def __init__(self, span_s: float = DEFAULT_SPAN_S, smoothing_points: int = DEFAULT_SMOOTHING_POINTS):
        self.span_s = span_s
        self.smoothing_points = smoothing_points
        self._times: deque[float] = deque(maxlen=_KEEP)
        self._temps: deque[float] = deque(maxlen=_KEEP)
        # one more raw rate than the smoothing uses: smoothing starts once there are more than that many
        self._raw: deque[tuple[float, float]] = deque(maxlen=smoothing_points + 1)
        self._first_time: Optional[float] = None
        self.value: Optional[float] = None  # the latest result

    def reset(self) -> None:
        self._times.clear()
        self._temps.clear()
        self._raw.clear()
        self._first_time = None
        self.value = None

    def _interval(self) -> float:
        times = list(self._times)[-11:]
        gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
        return median(gaps) if gaps else 1.0

    def update(self, time_s: float, temp: Optional[float]) -> Optional[float]:
        # A missing reading, or time not moving forward, repeats the last value.
        if temp is None or (self._times and time_s <= self._times[-1]):
            return self.value
        self._times.append(time_s)
        self._temps.append(temp)
        if self._first_time is None:
            self._first_time = time_s
        count = len(self._times)
        if count < 2:
            return self.value

        interval = self._interval()
        steps_back = max(1, round(self.span_s / interval))
        left = min(count, max(2, steps_back + 1))  # how far back the older reading is (1 = newest)
        times, temps = self._times, self._temps
        elapsed = times[-1] - times[-left]
        if elapsed <= 0:
            return self.value

        # The older reading, averaged with its neighbours: 5 readings when there are
        # two on each side, else 3, else just the one.
        if left > 2 and count >= left + 2:
            older = sum(temps[-left + k] for k in range(-2, 3)) / 5.0
        elif left > 1 and count >= left + 1:
            older = sum(temps[-left + k] for k in range(-1, 2)) / 3.0
        else:
            older = temps[-left]
        raw = (temps[-1] - older) / elapsed * 60.0

        # Until most of a span of readings exists, the rate is taken over a much
        # shorter interval, so a small change divided by a small time scaled to a
        # minute is mostly noise (RoR in the hundreds right at Charge). Like other
        # roasting software, give no rate yet.
        if time_s - self._first_time < WARMUP_FRACTION * self.span_s:
            return self.value

        self._raw.append((time_s, raw))
        k = self.smoothing_points
        if k >= 2 and len(self._raw) > k:
            trail = list(self._raw)[-k:]
            evened = _resample_uniform([t for t, _ in trail], [v for _, v in trail], interval)
            weights = range(1, len(evened) + 1)  # oldest 1 ... newest k
            smoothed = sum(w * v for w, v in zip(weights, evened)) / sum(weights)
        else:
            smoothed = raw
        self.value = round(smoothed, 2)
        return self.value


def rate_of_rise_series(
    times: Sequence[float],
    temps: Sequence[Optional[float]],
    span_s: float = DEFAULT_SPAN_S,
    smoothing_points: int = DEFAULT_SMOOTHING_POINTS,
) -> list[Optional[float]]:
    """The RoR at every point of a finished curve (None where there isn't one yet)."""
    ror = RateOfRise(span_s, smoothing_points)
    return [ror.update(t, v) for t, v in zip(times, temps)]
