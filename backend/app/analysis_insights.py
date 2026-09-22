"""What gets sent to a local language model to comment on a set of roasts.

Only summary numbers go in -- per-group averages and spread, and a short list of
recent roasts with their key figures -- never whole curves, so the request stays
small and quick.
"""
from __future__ import annotations

import json
from typing import Optional

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
MAX_GROUPS = 10
MAX_ROASTS = 20

# Ollama's own default window is small; ask for enough room for this prompt.
NUM_CTX = 8192

PROMPT_TEMPLATE = """You are an experienced coffee roaster looking across many of one person's drum-roaster \
COFFEE ROASTS to find patterns. Below is data drawn from those roasts: for each group, how many roasts, and the \
average ("mean") and spread (standard deviation, "sd") of each measurement, with "n" roasts having it; then a list \
of the most recent individual roasts.

Units: temperatures in Celsius, times in seconds (milestone times are counted from Charge), percentages in %. \
dtr_pct is development time as a percentage of the roast (Charge to Drop). "ror" is rate of rise in Celsius per \
minute. "ror_crashes"/"ror_flatlines"/"ror_flicks" count rate-of-rise problems. color_agtron, cupping_score and \
rating are the roaster's own after-the-fact judgements when they filled them in.

Write plain text (no markdown, no # headers), short paragraphs and "-" bullets where useful, covering:
1. The main patterns you can see, citing actual numbers and group names.
2. Where the roasts are inconsistent (a large sd relative to the average) and what that might mean.
3. Any roasts that stand out, by title.
4. Two or three specific things to try or check next.

Be honest about small samples: with fewer than five roasts in a group, say the pattern is tentative. Do not \
invent causes the data can't show, and do not make up numbers. {question}

{scope}

GROUPS (JSON):
{groups}

RECENT ROASTS (JSON, newest first):
{roasts}

Remember: this is coffee roasting in a drum roaster. Write your analysis now."""


def _round(value):
    return round(value, 1) if isinstance(value, float) else value


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
        groups=json.dumps(compact_groups, separators=(",", ":"), ensure_ascii=False),
        roasts=json.dumps(recent, separators=(",", ":"), ensure_ascii=False),
    )
