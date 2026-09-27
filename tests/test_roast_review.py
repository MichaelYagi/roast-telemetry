"""backend/app/roast_review.py's build_summary -- the data handed to the
AI review prompt."""
from __future__ import annotations

from backend.app.models import Roast, RoastMode, RoastStatus
from backend.app.roast_review import build_summary


def _make_roast(**overrides) -> Roast:
    defaults = dict(
        id="r1", title="Test Roast", mode=RoastMode.SIMULATOR, status=RoastStatus.STOPPED,
        created_at="2026-01-01T00:00:00", profile=[], events=[], notes=[],
    )
    return Roast(**{**defaults, **overrides})


def test_weight_loss_pct_computed_normally():
    roast = _make_roast(weight_green_g=200.0, weight_roasted_g=170.0)
    summary = build_summary(roast)
    assert summary["roast_weight_loss_pct"] == 15.0


def test_weight_loss_pct_none_when_roasted_weight_never_recorded():
    roast = _make_roast(weight_green_g=200.0, weight_roasted_g=None)
    summary = build_summary(roast)
    assert summary["roast_weight_loss_pct"] is None


def test_weight_loss_pct_handles_a_genuine_total_loss_batch():
    # Real bug: a roasted weight of exactly 0 (beans destroyed/lost
    # entirely) used to be treated identically to "never measured" by a
    # truthy check -- it's a real, legitimate measurement, and the loss
    # is the most notable thing about that roast.
    roast = _make_roast(weight_green_g=200.0, weight_roasted_g=0.0)
    summary = build_summary(roast)
    assert summary["roast_weight_loss_pct"] == 100.0


def test_bean_comparison_included_when_given():
    comparison = [{"measurement": "Drop temperature", "unit": "°C", "this_roast": 200.0, "usual_mean": 195.0}]
    summary = build_summary(_make_roast(), bean_comparison=comparison)
    assert summary["compared_to_this_beans_other_roasts"] == comparison


def test_bean_comparison_omitted_without_one():
    summary = build_summary(_make_roast())
    assert "compared_to_this_beans_other_roasts" not in summary
