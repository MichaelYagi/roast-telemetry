import { useEffect, useRef, useState } from "react";
import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";

// Quick-pick swatches shown in a popover next to each item's native color
// input -- the native input alone (see .breakout-order-swatch below) still
// covers "any color at all", this is just a faster path for a reasonable
// spread of visually distinct options. A plain Tailwind-ish 500-weight
// sweep around the wheel, not tied to any of BREAKOUT_PANEL_ITEMS' own
// built-in defaults.
const PRESET_COLORS = [
  "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16", "#22c55e",
  "#10b981", "#06b6d4", "#0ea5e9", "#3b82f6", "#6366f1", "#8b5cf6",
  "#a855f7", "#d946ef", "#ec4899", "#64748b",
];

// A controlled editor for one ordered, colorable set of BREAKOUT_PANEL_ITEMS
// keys -- used twice on the Settings page (Big Readout Panel, Small
// Readout), each with its own independent enabledKeys/colors state living
// in the parent (SettingsView), not here. Each instance owns its own
// palette-open state internally, so two instances on the same page never
// collide over which row's popover is open.
export default function BreakoutSettingsEditor({ enabledKeys, setEnabledKeys, colors, setColors, excludeKeys }) {
  const [paletteOpenFor, setPaletteOpenFor] = useState(null);
  const paletteRef = useRef(null);
  // Some items don't make sense for every instance of this editor (e.g.
  // "Elapsed time" is redundant next to the Small Readout column, which
  // always sits beside a chart that already has its own elapsed-time
  // axis) -- excluded from both the "Available" add-list and, if present
  // from an older save, the enabled list itself, rather than only hiding
  // the add button and leaving a stale entry the user can't remove.
  const excluded = excludeKeys || [];
  const visibleItems = BREAKOUT_PANEL_ITEMS.filter((item) => !excluded.includes(item.key));
  const visibleEnabledKeys = enabledKeys.filter((key) => !excluded.includes(key));

  useEffect(() => {
    if (!paletteOpenFor) return undefined;
    function onDocClick(e) {
      // Skip entirely for a click on *any* row's toggle button (not just
      // this one's) -- that button's own onClick is the sole authority
      // for what paletteOpenFor becomes next (open this row / close if
      // already open). Without this bailout, clicking a *different* row's
      // toggle while one is open raced two independent state updates
      // against each other for the same click -- this handler's
      // unconditional close, and that button's open -- and they canceled
      // out instead of switching rows, needing a second click to recover.
      if (e.target.closest(".breakout-order-palette-toggle")) return;
      if (paletteRef.current && !paletteRef.current.contains(e.target)) setPaletteOpenFor(null);
    }
    function onKeyDown(e) {
      if (e.key === "Escape") setPaletteOpenFor(null);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [paletteOpenFor]);

  // Actually purge any excluded key from the real enabledKeys state (not
  // just hide it from view) -- otherwise a stale "time" entry left over
  // from before this exclusion existed keeps sitting in the saved array
  // forever, quietly shifting the indices `move()` swaps between.
  useEffect(() => {
    if (excluded.length === 0) return;
    setEnabledKeys((prev) => {
      const next = prev.filter((k) => !excluded.includes(k));
      return next.length === prev.length ? prev : next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [excludeKeys]);

  function setColor(key, hex) {
    setColors((prev) => ({ ...prev, [key]: hex }));
  }

  function resetColor(key) {
    setColors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  function enable(key) {
    setEnabledKeys((prev) => (prev.includes(key) ? prev : [...prev, key]));
  }

  function disable(key) {
    setEnabledKeys((prev) => prev.filter((k) => k !== key));
  }

  function move(key, direction) {
    setEnabledKeys((prev) => {
      const i = prev.indexOf(key);
      const j = i + direction;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  return (
    <>
      {visibleEnabledKeys.length > 0 && (
        <>
          <h3>Enabled, in display order</h3>
          <ul className="breakout-order-list">
            {visibleEnabledKeys.map((key, i) => {
              const item = BREAKOUT_PANEL_ITEMS.find((it) => it.key === key);
              if (!item) return null;
              const color = colors[key] || item.color;
              const isCustom = Boolean(colors[key]);
              const paletteOpen = paletteOpenFor === key;
              return (
                <li key={key}>
                  <div className="breakout-order-color" ref={paletteOpen ? paletteRef : null}>
                    <input
                      type="color"
                      className="breakout-order-swatch"
                      value={color}
                      title={`${item.label} color -- opens the full color picker`}
                      onChange={(e) => setColor(key, e.target.value)}
                    />
                    <button
                      type="button"
                      className="breakout-order-palette-toggle"
                      onClick={() => setPaletteOpenFor((k) => (k === key ? null : key))}
                      title="Choose from preset colors"
                      aria-expanded={paletteOpen}
                    >
                      ▾
                    </button>
                    {paletteOpen && (
                      <div className="breakout-order-palette" role="menu">
                        {PRESET_COLORS.map((c) => (
                          <button
                            type="button"
                            key={c}
                            role="menuitemradio"
                            aria-checked={color.toLowerCase() === c}
                            className={`breakout-order-palette-swatch${color.toLowerCase() === c ? " selected" : ""}`}
                            style={{ background: c }}
                            title={c}
                            onClick={() => {
                              setColor(key, c);
                              setPaletteOpenFor(null);
                            }}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                  {isCustom && (
                    <button
                      type="button"
                      className="breakout-order-swatch-reset"
                      onClick={() => resetColor(key)}
                      title="Reset to default color"
                    >
                      ↺
                    </button>
                  )}
                  <span className="breakout-order-label">{item.label}</span>
                  <button type="button" onClick={() => move(key, -1)} disabled={i === 0} title="Move up">
                    ▲
                  </button>
                  <button
                    type="button"
                    onClick={() => move(key, 1)}
                    disabled={i === visibleEnabledKeys.length - 1}
                    title="Move down"
                  >
                    ▼
                  </button>
                  <button type="button" className="danger" onClick={() => disable(key)} title="Remove">
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <h3>Available</h3>
      <div className="breakout-toggle-grid">
        {visibleItems
          .filter((item) => !enabledKeys.includes(item.key))
          .map((item) => (
            <button type="button" key={item.key} className="breakout-add-btn" onClick={() => enable(item.key)}>
              + {item.label}
            </button>
          ))}
        {visibleItems.every((item) => enabledKeys.includes(item.key)) && (
          <p className="hint">All items are enabled.</p>
        )}
      </div>
    </>
  );
}
