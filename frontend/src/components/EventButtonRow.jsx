// The row of manual event-marker buttons Artisan shows under the scope
// during a roast, for logging milestones by hand.
const EVENT_BUTTONS = [
  { type: "CHARGE", label: "CHARGE" },
  { type: "DRY_END", label: "DRY END" },
  { type: "FC_START", label: "FC START" },
  { type: "FC_END", label: "FC END" },
  { type: "SC_START", label: "SC START" },
  { type: "DROP", label: "DROP" },
];

export default function EventButtonRow({ disabled, onFire }) {
  return (
    <div className="event-button-row">
      {EVENT_BUTTONS.map((btn) => (
        <button
          key={btn.type}
          type="button"
          className="event-btn"
          disabled={disabled}
          onClick={() => onFire(btn.type, btn.label)}
        >
          {btn.label}
        </button>
      ))}
    </div>
  );
}
