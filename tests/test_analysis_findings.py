"""The findings the Analysis page's AI comment is built from -- computed in code
over synthetic roasts with planted patterns, so each one has a known answer."""
import math

from backend.app.analysis_insights import (
    compute_correlations,
    compute_curve_divergence,
    compute_outcome_contrasts,
    compute_tag_outcomes,
    compute_tasting_notes,
)


def roast(i, *, metrics=None, tags=(), beans="Bean A", curve=None, notes=None):
    return {
        "id": f"r{i}", "title": f"Roast {i}", "beans": beans, "created_at": f"2026-03-{(i % 28) + 1:02d}T10:00:00",
        "tags": list(tags), "tasting_notes": notes, "bt_curve": curve, "metrics": dict(metrics or {}),
    }


def wobble(i):
    # Deterministic, zero-mean-ish spread so the planted patterns aren't perfect lines.
    return math.sin(i * 1.7) * 0.5


def test_correlations_find_a_planted_relationship_and_skip_identities():
    rows = [roast(i, metrics={"dtr_pct": 15 + i * 0.5 + wobble(i), "max_ror": 10 - i * 0.2 + wobble(i + 1),
                              "duration_s": 600 + i, "drop_time_s": 600 + i}) for i in range(20)]
    found = compute_correlations(rows)
    pairs = {frozenset((c["measurement"], c["with"])) for c in found}
    assert frozenset(("Development % (DTR)", "Peak rate of rise")) in pairs
    assert all(abs(c["r"]) >= 0.5 for c in found)
    # duration and drop time are the same number, so that pair is dropped rather than reported as a finding.
    assert frozenset(("Roast duration (Charge to Drop)", "Drop time")) not in pairs


def test_outcome_contrasts_say_how_top_rated_roasts_differ():
    rows = []
    for i in range(12):
        high = i % 2 == 0
        rows.append(roast(i, metrics={
            "rating": 5.0 if high else 3.0,
            "dtr_pct": (20 if high else 14) + wobble(i),
            "max_ror": 9.0 + wobble(i + 3) * 0.1,
        }))
    contrasts = compute_outcome_contrasts(rows)
    rating = next(c for c in contrasts if c["outcome"] == "Rating")
    top = rating["differences"][0]
    assert top["measurement"] == "Development % (DTR)"
    assert top["d"] > 0.8
    assert all(c["measurement"] != "Rating" for c in rating["differences"])


def test_tag_outcomes_compare_a_tag_with_the_rest():
    rows = [roast(i, metrics={"rating": 2.0 if i < 5 else 4.0}, tags=["washed"] if i < 5 else []) for i in range(10)]
    found = compute_tag_outcomes(rows)
    washed = next(f for f in found if f["tag"] == "washed" and f["outcome"] == "Rating")
    assert washed["roasts_with_tag"] == 5
    assert washed["difference"] == -2.0


def test_curve_divergence_finds_the_roast_that_ran_hot_mid_roast():
    base = [150.0 + i * 0.5 for i in range(30)]
    rows = [roast(i, curve=[round(v + wobble(i + k) * 0.2, 1) for k, v in enumerate(base)]) for i in range(7)]
    hot = [v + (12 if 8 <= k <= 16 else 0) for k, v in enumerate(base)]
    rows.append(roast(99, curve=hot, beans="Bean A"))
    found = compute_curve_divergence(rows)
    assert found and found[0]["title"] == "Roast 99"
    assert found[0]["direction"] == "above"
    assert found[0]["mean_departure_c"] >= 3.0


def test_curve_divergence_needs_enough_roasts_of_the_same_bean():
    rows = [roast(i, curve=[150.0, 160.0, 170.0], beans=f"Bean {i}") for i in range(10)]
    assert compute_curve_divergence(rows) == []


def test_tasting_notes_cover_best_and_worst_without_repeating_a_roast():
    rows = [roast(i, metrics={"rating": float(i % 5 + 1)}, notes=f"note {i}") for i in range(4)]
    notes = compute_tasting_notes(rows)
    # Four rated roasts, three shown per end: the worst end gets only the one left over.
    assert [n["end"] for n in notes] == ["best", "best", "best", "worst"]
    ids = [n["title"] for n in notes]
    assert len(ids) == len(set(ids))
    best = next(n for n in notes if n["end"] == "best")
    assert best["rating"] == 4.0
