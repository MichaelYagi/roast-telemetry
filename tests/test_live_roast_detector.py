"""Direct unit tests for roast_heuristics.LiveRoastDetector -- CHARGE
auto-detection (the BT-drop heuristic other milestones chain off of) and
the detect_milestones off switch real-hardware engines now use by
default (see modbus_bridge/ms6514_bridge's own constructors)."""
from __future__ import annotations

from roast_heuristics import LiveRoastDetector


def _feed_charge_drop(detector: LiveRoastDetector) -> None:
    """BT 100.0 -> 90.0 within the default 20s charge window -- the
    default 8C-drop-triggers-CHARGE heuristic."""
    detector.observe(0.0, 100.0, None)
    detector.observe(1.0, 90.0, None)


def test_detect_milestones_true_fires_charge_on_a_sharp_bt_drop():
    detector = LiveRoastDetector()
    _feed_charge_drop(detector)

    types = [e["type"] for e in detector.get_new_events()]

    assert types == ["CHARGE"]


def test_detect_milestones_false_never_fires_charge():
    detector = LiveRoastDetector(detect_milestones=False)
    _feed_charge_drop(detector)

    assert detector.get_new_events() == []


def test_detect_milestones_false_still_computes_ror():
    # RoR is independent of milestone detection -- only the phase machine
    # (_detect_events) is skipped when the flag is off, not the rolling
    # BT/ET history it's computed from.
    detector = LiveRoastDetector(detect_milestones=False)
    detector.observe(0.0, 100.0, 50.0)

    sample = detector.observe(30.0, 130.0, 60.0)

    assert sample["ror_bt"] == 60.0  # 30C over 30s -> 60C/min


def test_charge_is_one_shot_but_a_fresh_detector_can_fire_it_again():
    """CHARGE detection is one-shot by design (the _phase machine moves
    past "pre_charge" and never returns) -- reset_detection() on the
    engines that use this (see modbus_bridge/ms6514_bridge) works by
    discarding and rebuilding a fresh LiveRoastDetector entirely, not by
    pausing/resuming this one, which is what this test actually verifies
    at the detector level."""
    detector = LiveRoastDetector()
    _feed_charge_drop(detector)
    assert [e["type"] for e in detector.get_new_events()] == ["CHARGE"]

    # Detector has moved past "pre_charge" -- further BT movement (even
    # another apparent sharp drop) never re-fires CHARGE, one-shot by
    # design. (Not asserting get_new_events() == [] here: the same
    # movement can legitimately trigger TURNING_POINT next, which isn't
    # what this test is about.)
    detector.observe(2.0, 80.0, None)
    types = [e["type"] for e in detector.get_new_events()]
    assert "CHARGE" not in types

    # A brand new detector can fire it again.
    fresh = LiveRoastDetector()
    _feed_charge_drop(fresh)
    assert [e["type"] for e in fresh.get_new_events()] == ["CHARGE"]
