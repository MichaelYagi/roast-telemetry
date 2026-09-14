// The row of manual event-marker buttons Artisan shows under the scope,
// for logging milestones by hand. CHARGE is always auto-detected
// (roast_heuristics.LiveRoastDetector / SimulatorEngine -- there's no
// live mode where the operator marks it themselves, since it's a sharp
// BT-drop signature that needs no human judgment), so its button stays
// visible for a complete/consistent row but permanently disabled.
// TURNING_POINT is auto-detected the same way (the BT minimum right
// after CHARGE) but deliberately has no button here at all -- omitted
// by request to match a reference setup that doesn't surface it either;
// it's still recorded as a real event server-side (see detector.py),
// just not given a row entry. Everything else stays clickable --
// DRY_END/FC_START auto-fire in some modes but can be disabled (blank
// threshold) or unavailable (alog events missing one), and
// FC_END/SC_START/SC_END/DROP/COOL_END are judgment calls never
// auto-detected live at all.
const EVENT_BUTTONS = [
  { type: "CHARGE", label: "CHARGE", alwaysAuto: true },
  { type: "DRY_END", label: "DRY END" },
  { type: "FC_START", label: "FC START" },
  { type: "FC_END", label: "FC END" },
  { type: "SC_START", label: "SC START" },
  { type: "SC_END", label: "SC END" },
  { type: "DROP", label: "DROP" },
  { type: "COOL_END", label: "COOL END" },
];

// Order here mirrors backend/app/models.py's MILESTONE_SEQUENCE minus
// TURNING_POINT (omitted above) -- still the same relative order for
// every type that does appear, so the "a later milestone already fired"
// lock logic below stays correct. That backend sequence is the real
// source of truth (see session.py's add_event); this is a client-side
// mirror of it so buttons are disabled *before* a doomed request round
// trips, not instead of the backend check.
export default function EventButtonRow({ disabled, events = [], onFire }) {
  const fired = new Set(events.filter((e) => e.type !== "CUSTOM").map((e) => e.type));

  return (
    <div className="event-button-row">
      {EVENT_BUTTONS.map((btn, i) => {
        const alreadyFired = fired.has(btn.type);
        const laterFired = EVENT_BUTTONS.slice(i + 1).some((b) => fired.has(b.type));
        const locked = btn.alwaysAuto || alreadyFired || laterFired;
        const title = btn.alwaysAuto
          ? "Always auto-detected -- not manually markable"
          : alreadyFired
            ? "Already marked for this roast"
            : laterFired
              ? "Can't mark -- a later milestone is already recorded"
              : undefined;
        return (
          <button
            key={btn.type}
            type="button"
            className="event-btn"
            disabled={disabled || locked}
            title={title}
            onClick={() => onFire(btn.type, btn.label)}
          >
            {btn.label}
          </button>
        );
      })}
    </div>
  );
}
