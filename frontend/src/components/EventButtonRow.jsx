// The row of manual event-marker buttons Artisan shows under the scope,
// for logging milestones by hand. CHARGE and TURNING_POINT are always
// auto-detected (roast_heuristics.LiveRoastDetector / SimulatorEngine --
// there's no live mode where the operator marks these themselves, since
// neither needs human judgment: CHARGE is a sharp BT-drop signature,
// Turning Point is just the BT minimum right after), so their buttons
// stay visible for a complete/consistent row but permanently disabled.
// Everything else stays clickable -- DRY_END/FC_START auto-fire in some
// modes but can be disabled (blank threshold) or unavailable (alog
// events missing one), and FC_END/SC_START/SC_END/DROP/COOL_END are
// judgment calls never auto-detected live at all.
const EVENT_BUTTONS = [
  { type: "CHARGE", label: "CHARGE", alwaysAuto: true },
  { type: "TURNING_POINT", label: "TURNING POINT", alwaysAuto: true },
  { type: "DRY_END", label: "DRY END" },
  { type: "FC_START", label: "FC START" },
  { type: "FC_END", label: "FC END" },
  { type: "SC_START", label: "SC START" },
  { type: "SC_END", label: "SC END" },
  { type: "DROP", label: "DROP" },
  { type: "COOL_END", label: "COOL END" },
];

// Mirrors backend/app/models.py's MILESTONE_SEQUENCE + ALWAYS_AUTO_EVENT_TYPES
// exactly (same array order = same sequence) -- this is a client-side
// mirror of the same rule the backend enforces (see session.py's
// add_event), so buttons are disabled *before* a doomed request round
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
