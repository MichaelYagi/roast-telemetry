"""roast_heuristics/ror.py -- rate of rise (degrees/minute): a rate over a span
(the older reading averaged with its neighbours), then a weighted average of the
last few rates with the newest weighted most."""
from __future__ import annotations

import pytest

from roast_heuristics.ror import RateOfRise, _resample_uniform, rate_of_rise_series


def _feed(temps, step=1.0, **kw):
    ror = RateOfRise(**kw)
    return [ror.update(i * step, t) for i, t in enumerate(temps)]


def test_a_steady_climb_gives_exactly_that_rate():
    temps = [20 + 0.1 * i for i in range(120)]  # 0.1 C/s = 6 C/min
    out = _feed(temps)
    assert out[-1] == pytest.approx(6.0, abs=0.01)
    warmed = [v for v in out if v is not None]
    assert warmed and all(v == pytest.approx(6.0, abs=0.01) for v in warmed)


def test_a_steady_fall_is_negative():
    assert _feed([200 - 0.05 * i for i in range(60)])[-1] == pytest.approx(-3.0, abs=0.01)


def test_no_rate_until_most_of_a_span_has_been_recorded():
    out = _feed([20 + 0.1 * i for i in range(40)])          # 1 s spacing, 20 s span
    assert all(v is None for v in out[:18])                  # under 18 s (0.9 of the span): noise, so nothing
    assert all(v is not None for v in out[18:])


def test_time_must_move_forward():
    ror = RateOfRise()
    for i in range(30):
        ror.update(float(i), 20 + 0.1 * i)
    last = ror.value
    assert last is not None
    assert ror.update(29.0, 500.0) == last  # same timestamp: ignored, not a jump
    assert ror.update(10.0, 500.0) == last  # time going backwards: ignored


def test_a_missing_reading_repeats_the_last_rate():
    ror = RateOfRise()
    for i in range(30):
        ror.update(float(i), 20 + 0.1 * i)
    last = ror.value
    assert ror.update(30.0, None) == last


def test_a_slope_change_is_followed_smoothly_not_instantly():
    temps = [100 + 0.1 * i for i in range(60)]                     # 6 C/min
    temps += [temps[-1] + 0.3 * (i + 1) for i in range(60)]        # then 18 C/min
    out = _feed(temps)
    after = out[60:]
    assert 5.9 < out[59] < 6.1
    assert all(a <= b + 1e-9 for a, b in zip(after, after[1:]))     # rises steadily to the new rate, never overshoots
    assert after[0] < 12.0 and after[-1] == pytest.approx(18.0, abs=0.3)


def test_one_noisy_reading_is_softened_by_averaging_the_older_point():
    temps = [20 + 0.1 * i for i in range(60)]
    span = 20
    noisy_index = len(temps) - 1 - span  # the reading the rate is measured against
    plain = list(temps)
    noisy = list(temps)
    noisy[noisy_index] += 10.0           # one spiky reading right where it is compared
    a = RateOfRise(smoothing_points=0)  # smoothing off, to isolate the neighbour averaging
    b = RateOfRise(smoothing_points=0)
    ra = [a.update(float(i), t) for i, t in enumerate(plain)][-1]
    rb = [b.update(float(i), t) for i, t in enumerate(noisy)][-1]
    naive_error = abs(10.0 / span * 60.0)            # a plain two-point rate would be thrown off by this much
    assert abs(rb - ra) < naive_error / 2            # averaging with neighbours takes a chunk out of it


def test_matches_a_hand_calculation():
    # span 3 s at 1 s spacing -> the older reading is 3 s back (left = 4), 5-point averaged;
    # smoothing over the last 3 raw rates with weights 1, 2, 3.
    temps = [20, 21, 23, 26, 30, 35, 41, 48, 56, 65]
    times = [float(i) for i in range(len(temps))]
    ror = RateOfRise(span_s=3.0, smoothing_points=3)
    got = [ror.update(t, v) for t, v in zip(times, temps)]

    def raw_at(k):  # raw rate when readings 0..k have arrived
        n = k + 1
        left = min(n, max(2, 3 + 1))
        elapsed = times[k] - times[k - left + 1]
        if left > 2 and n >= left + 2:
            older = sum(temps[k - left + 1 + d] for d in range(-2, 3)) / 5.0
        elif left > 1 and n >= left + 1:
            older = sum(temps[k - left + 1 + d] for d in range(-1, 2)) / 3.0
        else:
            older = temps[k - left + 1]
        return (temps[k] - older) / elapsed * 60.0

    raws = [raw_at(k) for k in range(1, len(temps))]
    expect = (1 * raws[-3] + 2 * raws[-2] + 3 * raws[-1]) / 6.0
    assert got[-1] == pytest.approx(round(expect, 2), abs=0.011)


def test_series_matches_streaming_and_leaves_gaps_as_none():
    temps = [20 + 0.1 * i if i % 7 else None for i in range(50)]
    times = [float(i) for i in range(50)]
    ror = RateOfRise()
    streamed = [ror.update(t, v) for t, v in zip(times, temps)]
    assert rate_of_rise_series(times, temps) == streamed
    assert streamed[0] is None and streamed[-1] is not None


def test_uneven_spacing_is_averaged_on_an_even_grid():
    times = [0.0, 1.0, 3.0, 4.0]
    values = [10.0, 20.0, 40.0, 50.0]  # a straight line: 10 per second
    assert _resample_uniform(times, values, 1.0) == pytest.approx([10.0, 20.0, 30.0, 40.0, 50.0][-4:])
