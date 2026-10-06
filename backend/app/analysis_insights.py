"""What gets sent to a local language model to comment on a set of roasts.

Only summary numbers go in -- per-group averages and spread, and a short list of
recent roasts with their key figures -- never whole curves, so the request stays
small and quick.
"""
from __future__ import annotations

import json
import math
import statistics
from typing import Optional

from .roast_metrics import BT_CURVE_STEP_S, KEY_REPEATABILITY_METRICS, METRICS

_METRIC_BY_KEY = {m["key"]: m for m in METRICS}

# Per group: every key figure, as count / average / spread only.
# Per individual roast: a shorter set.
ROAST_METRICS = [
    "charge_temp_c", "fc_start_time_s", "drop_time_s", "drop_temp_c", "duration_s", "dtr_pct",
    "weight_loss_pct", "color_agtron", "cupping_score", "rating",
]
KEY_METRICS = [
    "charge_temp_c", "tp_time_s", "dry_end_time_s", "fc_start_time_s", "fc_start_temp_c", "drop_time_s",
    "drop_temp_c", "duration_s", "dry_pct", "maillard_pct", "dtr_pct", "development_time_s",
    "max_ror", "weight_loss_pct", "ror_crashes", "ror_flatlines", "ror_flicks",
    "color_agtron", "cupping_score", "rating",
]
# Same threshold as AnalysisView.jsx's Drift tab (MIN_GROUP_FOR_OUTLIERS/OUTLIER_Z_THRESHOLD).
MIN_GROUP_FOR_OUTLIERS = 5
OUTLIER_Z_THRESHOLD = 2
MIN_TREND_POINTS = 4  # below this, an earlier-half/later-half split is too noisy to report
MAX_GROUPS = 10
MAX_ROASTS = 20
# Findings beyond these counts are dropped, so the prompt stays within NUM_CTX.
MIN_PAIRS_FOR_CORRELATION = 10
MIN_CORRELATION = 0.5
MAX_CORRELATIONS = 8
MIN_OUTCOME_SIDE = 4  # roasts on each side of the split before comparing
MIN_CONTRAST = 0.8  # Cohen's d -- a large difference by the usual rule of thumb
MAX_CONTRASTS = 3
MIN_TAG_ROASTS = 3
MAX_TAG_OUTCOMES = 6
MIN_CURVE_POINTS = MIN_GROUP_FOR_OUTLIERS  # roasts sharing a moment of the curve before it's a usable average
MIN_CURVE_DEVIATION_C = 3.0  # mean departure from the bean's typical curve, in Celsius
MAX_CURVE_ROASTS = 5
MAX_NOTE_CHARS = 200
MAX_NOTED_ROASTS = 3  # per end of the rating scale

# Judged outcomes, and the measurements that aren't useful to compare against them.
OUTCOME_KEYS = ["rating", "cupping_score", "color_agtron"]
# Raw weights duplicate weight_loss_pct; bean fields are the same for every roast of a bean, so they can't vary within a bean.
_EXCLUDED_FROM_FINDINGS = {"weight_green_g", "weight_roasted_g", "bean_density_g_l", "bean_moisture_pct", "bean_altitude_m", "drop_time_s"}
FINDING_KEYS = [m["key"] for m in METRICS if m["key"] not in _EXCLUDED_FROM_FINDINGS]

# Ollama's own default window is small; ask for enough room for this prompt.
NUM_CTX = 8192

PROMPT_TEMPLATE = """You are an experienced coffee roaster looking across many of one person's drum-roaster \
COFFEE ROASTS to find patterns. Below is data drawn from those roasts: for each group, how many roasts, and the \
average ("mean") and spread (standard deviation, "sd") of each measurement, with "n" roasts having it; then \
FINDINGS that were computed directly from all the roasts in this set (not estimated by you), each with its own \
label and numbers; then a list of the most recent individual roasts.

Units: temperatures in Celsius, times in seconds (milestone times are counted from Charge), percentages in %. \
dtr_pct is development time as a percentage of the roast (Charge to Drop). "ror" is rate of rise in Celsius per \
minute. "ror_crashes"/"ror_flatlines"/"ror_flicks" count rate-of-rise problems. color_agtron, cupping_score and \
rating are the roaster's own after-the-fact judgements when they filled them in.

FINDINGS:
- FLAGGED ROASTS: roasts more than two standard deviations from how their own bean usually roasts (signed "sd").
- TREND SUMMARY: earlier-half vs. later-half averages on the key repeatability numbers, ordered by date.
- CORRELATIONS: pairs of measurements that tend to move together across roasts (Pearson "r" over "n" roasts). \
Correlation is not cause; say so when you mention one.
- OUTCOME CONTRASTS: for each judged outcome, how the higher-scored roasts differ from the lower-scored ones, in \
standard deviations ("d"). Only what's listed here counts as a difference.
- TAG OUTCOMES: the average rating or cupping score of roasts carrying each tag, against the other roasts.
- CURVE DIVERGENCE: roasts whose bean temperature curve departed most from their own bean's typical curve, with \
the moment of largest departure (minutes from Charge).
- TASTING NOTES: the roaster's own tasting notes on the best- and worst-rated roasts.

Write plain text (no markdown, no # headers), short paragraphs and "-" bullets where useful, covering:
1. The strongest patterns in GROUPS and FINDINGS, citing actual numbers and names.
2. Where the roasts are inconsistent (a large sd relative to the average) and what that might mean.
3. What separates the best-judged roasts from the worst, using OUTCOME CONTRASTS, TAG OUTCOMES and TASTING NOTES \
together. If the roaster asked a question, answer it from these findings first.
4. The FLAGGED ROASTS and CURVE DIVERGENCE roasts, by title, with what stood out about each. Only report what's \
listed; don't look for other standouts yourself.
5. Any clear direction in TREND SUMMARY worth mentioning (a real change, not noise).
6. Two or three specific things to try or check next.

Be honest about small samples: with fewer than five roasts in a group, say the pattern is tentative. Do not \
invent causes the data can't show, and do not make up numbers. {question}

{scope}

GROUPS (JSON):
{groups}

FINDINGS -- FLAGGED ROASTS (JSON):
{drift_flags}

FINDINGS -- TREND SUMMARY (JSON):
{trends}

FINDINGS -- CORRELATIONS (JSON):
{correlations}

FINDINGS -- OUTCOME CONTRASTS (JSON):
{outcome_contrasts}

FINDINGS -- TAG OUTCOMES (JSON):
{tag_outcomes}

FINDINGS -- CURVE DIVERGENCE (JSON):
{curve_divergence}

FINDINGS -- TASTING NOTES (JSON):
{tasting_notes}

RECENT ROASTS (JSON, newest first):
{roasts}

Remember: this is coffee roasting in a drum roaster. Write your analysis now."""


def _round(value):
    return round(value, 1) if isinstance(value, float) else value


def _mean_sd(values: list[float]) -> tuple[float, Optional[float]]:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else None
    return mean, sd


def compute_drift_flags(rows: list[dict]) -> list[dict]:
    """Same check as AnalysisView.jsx's Drift tab: any roast >= OUTLIER_Z_THRESHOLD
    standard deviations from its own bean's mean, on any of KEY_REPEATABILITY_METRICS,
    for beans with >= MIN_GROUP_FOR_OUTLIERS finished roasts. Computed here rather
    than left for the model to eyeball from group stats, so "roasts that stand
    out" in the prompt is grounded in the same deterministic check a human sees
    on the Drift tab, not a guess reconstructed from raw numbers."""
    by_bean: dict[str, list[dict]] = {}
    for row in rows:
        by_bean.setdefault(row.get("beans") or "(none)", []).append(row)

    by_roast: dict[str, dict] = {}
    for bean, members in by_bean.items():
        if len(members) < MIN_GROUP_FOR_OUTLIERS:
            continue
        for key in KEY_REPEATABILITY_METRICS:
            pairs = [(r, r["metrics"][key]) for r in members if r["metrics"].get(key) is not None]
            if len(pairs) < MIN_GROUP_FOR_OUTLIERS:
                continue
            mean, sd = _mean_sd([v for _, v in pairs])
            if not sd:
                continue
            for row, value in pairs:
                z = (value - mean) / sd
                if abs(z) < OUTLIER_Z_THRESHOLD:
                    continue
                entry = by_roast.setdefault(
                    row["id"], {"title": row["title"], "beans": bean, "date": row["created_at"][:10], "flags": []}
                )
                entry["flags"].append({"measurement": _METRIC_BY_KEY[key]["label"], "value": _round(value), "sd": round(z, 1)})

    flags = sorted(by_roast.values(), key=lambda f: max(abs(x["sd"]) for x in f["flags"]), reverse=True)
    return flags[:MAX_ROASTS]


def compute_trend_summary(rows: list[dict]) -> list[dict]:
    """Earlier-half vs. later-half average for each of KEY_REPEATABILITY_METRICS,
    across the whole matching set ordered by date -- a compact stand-in for
    what the Trends tab's rolling-average line shows visually, so the model
    doesn't have to reconstruct direction from a flat list of recent roasts."""
    trends = []
    for key in KEY_REPEATABILITY_METRICS:
        points = sorted(
            ((r["created_at"], r["metrics"][key]) for r in rows if r["metrics"].get(key) is not None),
            key=lambda p: p[0],
        )
        if len(points) < MIN_TREND_POINTS:
            continue
        mid = len(points) // 2
        earlier_avg = sum(v for _, v in points[:mid]) / mid
        later_avg = sum(v for _, v in points[mid:]) / (len(points) - mid)
        info = _METRIC_BY_KEY[key]
        trends.append({
            "measurement": info["label"], "unit": info["unit"], "n": len(points),
            "earlier_avg": _round(earlier_avg), "later_avg": _round(later_avg), "change": _round(later_avg - earlier_avg),
        })
    return trends


def compute_bean_comparison(this_metrics: dict, other_metrics: list[dict]) -> Optional[list[dict]]:
    """This roast's own value vs. the mean/SD of `other_metrics` (its bean's
    other finished roasts -- the caller excludes this roast itself before
    calling this, so the baseline isn't partly measured against its own
    value), for each of KEY_REPEATABILITY_METRICS. Same numbers and
    MIN_GROUP_FOR_OUTLIERS threshold as compute_drift_flags above, just
    scoped to one roast instead of flagging across a whole set -- used by
    the per-roast AI review (roast_review.py) to compare a roast to its own
    bean's history instead of judging it in isolation. None if there isn't
    enough history for this bean yet."""
    if len(other_metrics) < MIN_GROUP_FOR_OUTLIERS:
        return None
    comparison = []
    for key in KEY_REPEATABILITY_METRICS:
        this_value = this_metrics.get(key)
        if this_value is None:
            continue
        values = [m[key] for m in other_metrics if m.get(key) is not None]
        if len(values) < MIN_GROUP_FOR_OUTLIERS:
            continue
        mean, sd = _mean_sd(values)
        info = _METRIC_BY_KEY[key]
        entry = {
            "measurement": info["label"], "unit": info["unit"], "this_roast": _round(this_value),
            "usual_mean": _round(mean), "roasts_compared": len(values),
        }
        if sd:
            entry["usual_sd"] = _round(sd)
            entry["sd_from_usual"] = round((this_value - mean) / sd, 1)
        comparison.append(entry)
    return comparison or None


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def compute_correlations(rows: list[dict]) -> list[dict]:
    """Pairs of measurements that move together across the roasts, strongest first.
    Near-identical pairs (r above 0.97) are dropped -- they're one number counted
    twice, like a time and the same time's phase length -- and the correlation
    says nothing about cause."""
    found = []
    for i, a in enumerate(FINDING_KEYS):
        for b in FINDING_KEYS[i + 1:]:
            pairs = [(r["metrics"][a], r["metrics"][b]) for r in rows if r["metrics"].get(a) is not None and r["metrics"].get(b) is not None]
            if len(pairs) < MIN_PAIRS_FOR_CORRELATION:
                continue
            r = _pearson([x for x, _ in pairs], [y for _, y in pairs])
            if r is None or abs(r) < MIN_CORRELATION or abs(r) > 0.97:
                continue
            found.append({
                "measurement": _METRIC_BY_KEY[a]["label"], "with": _METRIC_BY_KEY[b]["label"],
                "r": round(r, 2), "n": len(pairs),
            })
    found.sort(key=lambda c: abs(c["r"]), reverse=True)
    return found[:MAX_CORRELATIONS]


def compute_outcome_contrasts(rows: list[dict]) -> list[dict]:
    """For each judged outcome, split the roasts that have it at the median and
    say how the higher-scored half differs from the lower half on each other
    measurement, in standard deviations (Cohen's d). Ties at the median are left
    out of both halves."""
    out = []
    for outcome in OUTCOME_KEYS:
        judged = [r for r in rows if r["metrics"].get(outcome) is not None]
        if len(judged) < 2 * MIN_OUTCOME_SIDE:
            continue
        median = statistics.median(r["metrics"][outcome] for r in judged)
        high = [r for r in judged if r["metrics"][outcome] > median]
        low = [r for r in judged if r["metrics"][outcome] < median]
        if len(high) < MIN_OUTCOME_SIDE or len(low) < MIN_OUTCOME_SIDE:
            continue
        contrasts = []
        for key in FINDING_KEYS:
            if key in OUTCOME_KEYS:
                continue
            hi = [r["metrics"][key] for r in high if r["metrics"].get(key) is not None]
            lo = [r["metrics"][key] for r in low if r["metrics"].get(key) is not None]
            if len(hi) < 3 or len(lo) < 3:
                continue
            mh, sh = _mean_sd(hi)
            ml, sl = _mean_sd(lo)
            pooled = math.sqrt(((sh ** 2) * (len(hi) - 1) + (sl ** 2) * (len(lo) - 1)) / (len(hi) + len(lo) - 2))
            if not pooled:
                continue
            d = (mh - ml) / pooled
            if abs(d) < MIN_CONTRAST:
                continue
            contrasts.append({
                "measurement": _METRIC_BY_KEY[key]["label"], "unit": _METRIC_BY_KEY[key]["unit"],
                "higher_group_mean": _round(mh), "lower_group_mean": _round(ml), "d": round(d, 1),
            })
        contrasts.sort(key=lambda c: abs(c["d"]), reverse=True)
        if contrasts:
            out.append({
                "outcome": _METRIC_BY_KEY[outcome]["label"],
                "higher_group": f"above {_round(median)}", "lower_group": f"below {_round(median)}",
                "roasts_higher": len(high), "roasts_lower": len(low), "differences": contrasts[:MAX_CONTRASTS],
            })
    return out


def compute_tag_outcomes(rows: list[dict]) -> list[dict]:
    """Average rating and cupping score of the roasts carrying each tag, against
    the other judged roasts. Only tags on at least MIN_TAG_ROASTS roasts both ways."""
    found = []
    for outcome in ("rating", "cupping_score"):
        judged = [r for r in rows if r["metrics"].get(outcome) is not None]
        tags = {t for r in judged for t in r.get("tags", []) if t != "simulated"}
        for tag in tags:
            with_tag = [r["metrics"][outcome] for r in judged if tag in r.get("tags", [])]
            without = [r["metrics"][outcome] for r in judged if tag not in r.get("tags", [])]
            if len(with_tag) < MIN_TAG_ROASTS or len(without) < MIN_TAG_ROASTS:
                continue
            mw, mo = sum(with_tag) / len(with_tag), sum(without) / len(without)
            found.append({
                "outcome": _METRIC_BY_KEY[outcome]["label"], "tag": tag, "roasts_with_tag": len(with_tag),
                "mean_with_tag": _round(mw), "mean_without_tag": _round(mo), "difference": _round(mw - mo),
            })
    found.sort(key=lambda f: abs(f["difference"]), reverse=True)
    return found[:MAX_TAG_OUTCOMES]


def compute_curve_divergence(rows: list[dict]) -> list[dict]:
    """Roasts whose bean temperature curve sat furthest from their own bean's
    typical curve, sampled every BT_CURVE_STEP_S seconds from Charge. A roast is
    compared with the other roasts of its bean only (leave-one-out, so it doesn't
    pull its own average), and only where enough of them reached that moment."""
    by_bean: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("bt_curve"):
            by_bean.setdefault(row.get("beans") or "(none)", []).append(row)

    found = []
    for bean, members in by_bean.items():
        if len(members) < MIN_GROUP_FOR_OUTLIERS + 1:
            continue
        length = max(len(r["bt_curve"]) for r in members)
        sums = [0.0] * length
        counts = [0] * length
        for r in members:
            for i, v in enumerate(r["bt_curve"]):
                if v is not None:
                    sums[i] += v
                    counts[i] += 1
        for r in members:
            deviations = []
            for i, v in enumerate(r["bt_curve"]):
                if v is None:
                    continue
                others = counts[i] - 1
                if others < MIN_CURVE_POINTS:
                    continue
                deviations.append((i, v - (sums[i] - v) / others))
            if len(deviations) < MIN_CURVE_POINTS:
                continue
            mean_departure = sum(abs(d) for _, d in deviations) / len(deviations)
            if mean_departure < MIN_CURVE_DEVIATION_C:
                continue
            i, worst = max(deviations, key=lambda p: abs(p[1]))
            found.append({
                "title": r["title"], "beans": bean, "date": r["created_at"][:10],
                "mean_departure_c": _round(mean_departure),
                "largest_departure_c": _round(worst), "at_minutes": _round(i * BT_CURVE_STEP_S / 60),
                "direction": "above" if worst > 0 else "below",
            })
    found.sort(key=lambda f: f["mean_departure_c"], reverse=True)
    return found[:MAX_CURVE_ROASTS]


def compute_tasting_notes(rows: list[dict]) -> list[dict]:
    """The roaster's own tasting notes on the best- and worst-rated roasts. A roast
    never lands in both lists, so a short set of ratings isn't counted twice."""
    rated = [r for r in rows if r["metrics"].get("rating") is not None and r.get("tasting_notes")]
    rated.sort(key=lambda r: r["metrics"]["rating"], reverse=True)
    best = rated[:MAX_NOTED_ROASTS]
    best_ids = {r["id"] for r in best}
    worst = [r for r in reversed(rated) if r["id"] not in best_ids][:MAX_NOTED_ROASTS]
    return [
        {
            "end": end, "title": r["title"], "beans": r["beans"], "rating": r["metrics"]["rating"],
            "notes": r["tasting_notes"][:MAX_NOTE_CHARS],
        }
        for end, group in (("best", best), ("worst", worst))
        for r in group
    ]


def build_prompt(*, groups: list[dict], rows: list[dict], group_by: str, total: int, truncated: bool,
                 filters_text: str, question: Optional[str]) -> str:
    compact_groups = []
    for g in groups[:MAX_GROUPS]:
        metrics = {
            k: {s: _round(v) for s, v in g["metrics"][k].items() if s in ("n", "mean", "sd") and v is not None}
            for k in KEY_METRICS
            if k in g["metrics"]
        }
        compact_groups.append({"group": g["key"], "roasts": g["count"], "with_rate_of_rise_flags": g["flagged_ror"], "measurements": metrics})

    recent = []
    for r in rows[:MAX_ROASTS]:
        item = {
            "title": r["title"],
            "date": r["created_at"][:10],
            "beans": r["beans"],
            "data_from": r["source"],
            **{k: _round(r["metrics"][k]) for k in ROAST_METRICS if r["metrics"].get(k) is not None},
        }
        if r.get("tasting_notes"):
            item["tasting_notes"] = r["tasting_notes"][:200]
        recent.append(item)

    scope = f"SCOPE: {total} roasts" + (" (the newest 2000 of a larger set)" if truncated else "")
    scope += f", grouped by {group_by}." if group_by != "none" else ", all in one group."
    if filters_text:
        scope += f" Filters: {filters_text}."
    if len(groups) > MAX_GROUPS:
        scope += f" Only the {MAX_GROUPS} largest of {len(groups)} groups are shown."
    if total > MAX_ROASTS:
        scope += f" Only the {MAX_ROASTS} newest roasts are listed individually."

    asked = f"\n\nThe roaster also asks: {question.strip()}\nAnswer that specifically, first." if question and question.strip() else ""
    return PROMPT_TEMPLATE.format(
        question=asked, scope=scope,
        groups=_dump(compact_groups),
        drift_flags=_dump(compute_drift_flags(rows)),
        trends=_dump(compute_trend_summary(rows)),
        correlations=_dump(compute_correlations(rows)),
        outcome_contrasts=_dump(compute_outcome_contrasts(rows)),
        tag_outcomes=_dump(compute_tag_outcomes(rows)),
        curve_divergence=_dump(compute_curve_divergence(rows)),
        tasting_notes=_dump(compute_tasting_notes(rows)),
        roasts=_dump(recent),
    )


def _dump(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
