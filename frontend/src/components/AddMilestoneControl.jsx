import { useState } from "react";
import { useTranslation } from "react-i18next";
import { EVENT_BUTTONS } from "./EventButtonRow.jsx";
import { formatTime } from "../chartDefaults.js";

// Mirrors backend/app/models.py's MILESTONE_SEQUENCE exactly, including
// Turning Point -- unlike EVENT_BUTTONS (which omits it, since there's
// no manual-mark button for it), the *range* this control offers for a
// chosen milestone has to walk the real sequence, or a marked Turning
// Point would be silently skipped as a neighbor and this would offer a
// wider range than the server actually accepts (see _check_milestone_order).
const FULL_SEQUENCE = ["CHARGE", "TURNING_POINT", "DRY_END", "FC_START", "FC_END", "SC_START", "SC_END", "DROP", "COOL_END"];

// Adds a milestone that was never marked to a finished roast (see
// RoastDetailView.jsx's handleAddMilestone -> POST /roasts/{id}/events
// with time_s). Only offers milestones not already on this roast, in
// MILESTONE_SEQUENCE order (EVENT_BUTTONS, the existing client-side
// mirror of that order used by the live button row), and only within
// the range the server will actually accept: strictly between whichever
// already-marked milestones sit immediately before/after it.
export default function AddMilestoneControl({ roast, onAdd }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [type, setType] = useState("");
  const [timeS, setTimeS] = useState(0);
  const [busy, setBusy] = useState(false);

  const fired = new Set(roast.events.filter((e) => e.type !== "CUSTOM").map((e) => e.type));
  const available = EVENT_BUTTONS.filter((b) => !fired.has(b.type));
  const lastTimeS = roast.profile.length ? roast.profile[roast.profile.length - 1].time_s : 0;

  function range(chosenType) {
    const idx = FULL_SEQUENCE.indexOf(chosenType);
    const byType = new Map(roast.events.filter((e) => e.type !== "CUSTOM").map((e) => [e.type, e]));
    let min = 0;
    for (let i = idx - 1; i >= 0; i--) {
      const ev = byType.get(FULL_SEQUENCE[i]);
      if (ev) {
        min = ev.time_s;
        break;
      }
    }
    let max = lastTimeS;
    for (let i = idx + 1; i < FULL_SEQUENCE.length; i++) {
      const ev = byType.get(FULL_SEQUENCE[i]);
      if (ev) {
        max = ev.time_s;
        break;
      }
    }
    return { min, max };
  }

  function startAdding() {
    if (!available.length) return;
    const first = available[0].type;
    setType(first);
    const { min, max } = range(first);
    setTimeS((min + max) / 2);
    setOpen(true);
  }

  function handleTypeChange(nextType) {
    setType(nextType);
    const { min, max } = range(nextType);
    setTimeS((min + max) / 2);
  }

  async function handleSave() {
    setBusy(true);
    const ok = await onAdd(type, timeS);
    setBusy(false);
    if (ok) setOpen(false);
  }

  if (!available.length) return null;

  if (!open) {
    return (
      <button type="button" className="link-like no-print" onClick={startAdding}>
        {t("roastDetail.addMilestone.open")}
      </button>
    );
  }

  const { min, max } = range(type);
  return (
    <div className="add-milestone-control no-print">
      <label>
        {t("roastDetail.addMilestone.milestone")}
        <select value={type} onChange={(e) => handleTypeChange(e.target.value)} disabled={busy}>
          {available.map((b) => (
            <option key={b.type} value={b.type}>
              {t(`roastDetail.addMilestone.names.${b.type}`)}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("roastDetail.addMilestone.time")}
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={timeS}
          onChange={(e) => setTimeS(Number(e.target.value))}
          disabled={busy || min >= max}
        />
        <span className="add-milestone-time-readout">{formatTime(timeS)}</span>
      </label>
      <div className="dialog-actions">
        <button type="button" onClick={handleSave} disabled={busy || min >= max}>
          {t("roastDetail.addMilestone.add")}
        </button>
        <button type="button" className="link-like" onClick={() => setOpen(false)} disabled={busy}>
          {t("roastDetail.addMilestone.cancel")}
        </button>
      </div>
    </div>
  );
}
