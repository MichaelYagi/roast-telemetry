import {
  MANDATORY_VERTICAL_CONTROL_KEYS,
  OPTIONAL_VERTICAL_CONTROL_KEYS,
  flatToGroups,
  groupsToFlat,
  normalizeLayout,
  verticalControlItem,
} from "../verticalControl.js";

// A controlled editor for AppSettings.vertical_control_layout/
// vertical_control_arrows -- the vertical control panel beside the live
// chart (see VerticalControlPanel.jsx), which fully replaced the old
// always-on horizontal Controls panel. Drum/Air have no show/hide toggle
// here (they're mandatory -- see normalizeLayout's own docstring for why);
// Burner %/Burner SV each get one, with "at least one of the two" enforced
// by disabling the last remaining Remove button rather than allowing a
// save that would leave neither visible.
//
// Works in the flat (ordered-list + joinsPrevious) shape throughout, only
// converting to/from the real string[][] groups shape at the setLayout
// boundary -- see groupsToFlat/flatToGroups's own docstring for why a
// linear list + a per-row toggle is simpler than 2D drag-and-drop grouping
// for the same expressiveness.
export default function VerticalControlSettingsEditor({ layout, setLayout, arrows, setArrows }) {
  // Always both optional keys "available" here regardless of what mode is
  // currently connected (or whether anything's connected at all) -- this
  // is a general app-wide setting, not scoped to one roast's hardware; the
  // *renderer* is what skips Burner SV at runtime on a mode that doesn't
  // support it (see VerticalControlPanel's svRangeC prop).
  const flat = groupsToFlat(normalizeLayout(layout, { svAvailable: true }));
  const includedOptionalCount = flat.filter((item) => OPTIONAL_VERTICAL_CONTROL_KEYS.includes(item.key)).length;

  function commit(nextFlat) {
    setLayout(flatToGroups(nextFlat));
  }

  function move(key, direction) {
    const i = flat.findIndex((item) => item.key === key);
    const j = i + direction;
    if (i < 0 || j < 0 || j >= flat.length) return;
    const next = [...flat];
    [next[i], next[j]] = [next[j], next[i]];
    // The moved pair's own joinsPrevious flags describe their relationship
    // to whatever's now above *them*, which just changed -- clearing both
    // avoids silently grouping this row with a new neighbor it was never
    // deliberately stacked with.
    next[i] = { ...next[i], joinsPrevious: false };
    next[j] = { ...next[j], joinsPrevious: false };
    commit(next);
  }

  function toggleJoinsPrevious(key) {
    commit(flat.map((item) => (item.key === key ? { ...item, joinsPrevious: !item.joinsPrevious } : item)));
  }

  function remove(key) {
    if (includedOptionalCount <= 1) return; // "not none" -- last one standing can't be removed
    commit(flat.filter((item) => item.key !== key));
  }

  function add(key) {
    if (flat.some((item) => item.key === key)) return;
    commit([...flat, { key, joinsPrevious: false }]);
  }

  function toggleArrows(key) {
    setArrows((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  const availableToAdd = OPTIONAL_VERTICAL_CONTROL_KEYS.filter((key) => !flat.some((item) => item.key === key));

  return (
    <>
      <h3>Stack order</h3>
      <p className="hint">
        "Stack with the one above" puts this slider in the same lane as the one above it, splitting that
        lane's height between them (top half / bottom half, and so on) instead of giving it a separate lane
        of its own -- each still stays its own independent slider either way.
      </p>
      <p className="hint">
        Stacking only applies on wider screens. On a phone-width screen, every enabled slider always gets
        its own full-height lane instead, side by side (scrolling sideways if they don't all fit) -- a
        stacked lane's already-limited height splitting further between 2 phone-sized sliders wasn't legible.
      </p>
      <ul className="breakout-order-list">
        {flat.map((item, i) => {
          const meta = verticalControlItem(item.key);
          const mandatory = MANDATORY_VERTICAL_CONTROL_KEYS.includes(item.key);
          return (
            <li key={item.key}>
              <span className="vertical-control-order-label">{meta.label}</span>
              {mandatory && <span className="hint vertical-control-mandatory-tag">always shown</span>}
              {i > 0 && (
                <label className="checkbox-label">
                  <input type="checkbox" checked={item.joinsPrevious} onChange={() => toggleJoinsPrevious(item.key)} />
                  Stack with the one above
                </label>
              )}
              <label className="checkbox-label">
                <input type="checkbox" checked={Boolean(arrows?.[item.key])} onChange={() => toggleArrows(item.key)} />
                +/- buttons
              </label>
              <button type="button" onClick={() => move(item.key, -1)} disabled={i === 0} title="Move up">
                ▲
              </button>
              <button type="button" onClick={() => move(item.key, 1)} disabled={i === flat.length - 1} title="Move down">
                ▼
              </button>
              {!mandatory && (
                <button
                  type="button"
                  className="danger"
                  onClick={() => remove(item.key)}
                  disabled={includedOptionalCount <= 1}
                  title={includedOptionalCount <= 1 ? "At least one of Burner %/Burner SV must stay visible" : "Remove"}
                >
                  Remove
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {availableToAdd.length > 0 && (
        <>
          <h3>Available</h3>
          <div className="breakout-toggle-grid">
            {availableToAdd.map((key) => (
              <button type="button" key={key} className="breakout-add-btn" onClick={() => add(key)}>
                + {verticalControlItem(key).label}
              </button>
            ))}
          </div>
        </>
      )}
    </>
  );
}
