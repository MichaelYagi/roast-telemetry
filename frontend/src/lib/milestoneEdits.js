import { EVENT_BUTTONS } from "../components/EventButtonRow.jsx";

// Every milestone type the backend will let delete_event/retime_event/
// add_milestone_at touch -- the same set EVENT_BUTTONS already mirrors
// (everything except TURNING_POINT, which auto-fires and can't be
// edited, and CUSTOM, which isn't a milestone at all -- see
// backend/app/roast_session/session.py's _find_editable_milestone).
export const EDITABLE_MILESTONE_TYPES = new Set(EVENT_BUTTONS.map((b) => b.type));

function editableByType(events) {
  const byType = new Map();
  for (const e of events) if (EDITABLE_MILESTONE_TYPES.has(e.type)) byType.set(e.type, e);
  return byType;
}

// Compares two milestone lists purely by type + time_s (never by id --
// a staged add has no server id yet, and a staged retime keeps its
// original one) and returns the ops needed to turn `before` into
// `after`: {kind: "delete", id} | {kind: "add", type, time_s} |
// {kind: "retime", id, time_s}. A milestone present in both with the
// *same* time_s produces no op at all -- dragging one back to exactly
// where it started (or re-adding it at its own original time) is a
// no-op, not a pending change, so Save/Cancel never shows for it.
export function diffMilestoneEvents(before, after) {
  const beforeByType = editableByType(before);
  const afterByType = editableByType(after);
  const ops = [];
  for (const [type, ev] of beforeByType) {
    if (!afterByType.has(type)) ops.push({ kind: "delete", type, id: ev.id });
  }
  for (const [type, ev] of afterByType) {
    const prev = beforeByType.get(type);
    if (!prev) ops.push({ kind: "add", type, time_s: ev.time_s });
    else if (prev.time_s !== ev.time_s) ops.push({ kind: "retime", type, id: prev.id, time_s: ev.time_s });
  }
  return ops;
}

// Mirrors the backend's own nearest-sample snap (_add_milestone_at /
// _retime_milestone in backend/app/roast_session/session.py) purely so a
// staged (not-yet-saved) milestone has *some* BT reading to plot against
// -- without one, RoastChart's markerPosition has no temperature to
// place the dot at and falls back to the bottom of the chart, well off
// the BT line. The real, authoritative value always comes back from the
// server once the edit is actually saved; this is only a client-side
// preview of what it'll snap to.
export function nearestProfileBt(profile, timeS) {
  if (!profile?.length) return null;
  let nearest = profile[0];
  let bestDiff = Math.abs(profile[0].time_s - timeS);
  for (const p of profile) {
    const diff = Math.abs(p.time_s - timeS);
    if (diff < bestDiff) {
      nearest = p;
      bestDiff = diff;
    }
  }
  return nearest.bt ?? null;
}
