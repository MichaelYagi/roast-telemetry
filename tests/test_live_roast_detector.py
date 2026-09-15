"""Direct unit tests for roast_heuristics.LiveRoastDetector -- CHARGE
auto-detection (the BT-drop heuristic other milestones chain off of),
the detect_milestones flag real-hardware engines default to False but
can opt into per roast (see modbus_bridge/ms6514_bridge's own
constructors and RoastCreateRequest.auto_detect_milestones), and the
manual-override bookkeeping (notify_manual_charge/mark_milestone_fired)
that keeps a manual click and auto-detection from ever double-firing
the same milestone."""
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


def test_notify_manual_charge_still_lets_turning_point_auto_fire():
    """Turning Point is a pure observation (the BT minimum right after
    Charge), not a judgment call -- so it stays auto-detected even with
    detect_milestones off, as long as the caller tells the detector CHARGE
    happened (see notify_manual_charge's own docstring). Confirmed against
    a real FZ-94 roast in Artisan, which auto-plots Turning Point despite
    every other milestone being a manual click there too."""
    detector = LiveRoastDetector(detect_milestones=False)
    detector.notify_manual_charge(0.0, 96.0)

    detector.observe(1.0, 90.0, None)  # dips further
    assert detector.get_new_events() == []  # still tracking the minimum, not yet rebounded

    detector.observe(2.0, 91.0, None)  # rebounds by 1.0C >= the 0.5C default threshold
    events = detector.get_new_events()

    assert [e["type"] for e in events] == ["TURNING_POINT"]
    assert events[0]["value"] == 90.0  # the actual minimum, not the rebound reading


def test_notify_manual_charge_does_not_reopen_dry_end_fc_start_detection():
    # detect_milestones=False must still block DRY_END/FC_START even once
    # we're past Turning Point -- only the dip-phase tracking is exempt.
    detector = LiveRoastDetector(detect_milestones=False, dry_end_c=160.0)
    detector.notify_manual_charge(0.0, 96.0)
    detector.observe(1.0, 90.0, None)
    detector.observe(2.0, 91.0, None)  # fires TURNING_POINT, moves to post_tp
    detector.get_new_events()

    detector.observe(3.0, 165.0, None)  # would cross dry_end_c if detection were on

    assert detector.get_new_events() == []


def test_notify_manual_charge_is_a_noop_once_past_pre_charge():
    detector = LiveRoastDetector()
    detector.observe(0.0, 100.0, None)
    detector.observe(1.0, 90.0, None)  # auto-fires CHARGE, moves to "dip"
    detector.get_new_events()

    detector.notify_manual_charge(5.0, 999.0)  # should be ignored -- not pre_charge anymore

    # bt_min tracking is untouched by the ignored call above.
    detector.observe(6.0, 89.0, None)
    detector.observe(7.0, 90.0, None)
    events = [e for e in detector.get_new_events() if e["type"] == "TURNING_POINT"]
    assert events and events[0]["value"] == 89.0  # not 999.0


def test_detect_milestones_true_auto_fires_dry_end_and_fc_start():
    # Opt-in auto-detection (RoastCreateRequest.auto_detect_milestones) --
    # confirms the whole chain works end to end, not just CHARGE/TP.
    detector = LiveRoastDetector(dry_end_c=160.0, fc_start_c=196.0)
    _feed_charge_drop(detector)
    detector.get_new_events()  # drain CHARGE
    detector.observe(2.0, 91.0, None)  # rebounds -- fires TURNING_POINT
    detector.get_new_events()

    detector.observe(3.0, 161.0, None)
    dry_end = [e for e in detector.get_new_events() if e["type"] == "DRY_END"]
    assert dry_end and dry_end[0]["value"] == 161.0

    detector.observe(4.0, 197.0, None)
    fc_start = [e for e in detector.get_new_events() if e["type"] == "FC_START"]
    assert fc_start and fc_start[0]["value"] == 197.0


def test_mark_milestone_fired_prevents_a_later_duplicate_auto_fire():
    """The scenario this exists for: auto-detection is on, but the
    operator clicks DRY_END manually before BT actually crosses the
    threshold. Without mark_milestone_fired, the detector would have no
    idea that happened and would still independently fire its own DRY_END
    once BT crosses 160 -- a real duplicate (see RoastSession.add_event's
    own docstring for why the auto-fired path can't self-dedupe)."""
    detector = LiveRoastDetector(dry_end_c=160.0)
    _feed_charge_drop(detector)
    detector.get_new_events()
    detector.observe(2.0, 91.0, None)  # fires TURNING_POINT, moves to post_tp
    detector.get_new_events()

    detector.mark_milestone_fired("DRY_END")  # operator clicked it manually, early

    detector.observe(3.0, 165.0, None)  # crosses 160 -- would auto-fire if not for the above

    assert detector.get_new_events() == []
