import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";

// The web's own preset swatches -- same list BreakoutSettingsEditor used
// to carry before color-picking moved out of it and into this dedicated
// section (see that component's own comment for why: one color per item,
// shared by the chart line, the Big Readout Panel and the Small Readout
// Panel, so picking it from two different per-panel editors was
// duplicated UI for what's really one setting).
const PRESET_COLORS = [
  "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16", "#22c55e",
  "#10b981", "#06b6d4", "#0ea5e9", "#3b82f6", "#6366f1", "#8b5cf6",
  "#a855f7", "#d946ef", "#ec4899", "#64748b",
];

// Every colorable channel/stat, regardless of whether it's currently
// enabled in the Big or Small Readout Panel (or on the chart, for the
// subset that's a real series) -- color is independent of visibility now,
// so this always lists the full BREAKOUT_PANEL_ITEMS registry, not just
// whatever's currently toggled on somewhere.
export default function ColorsSettingsEditor({ colors, setColors }) {
  const { t } = useTranslation();
  const [paletteOpenFor, setPaletteOpenFor] = useState(null);
  const paletteRef = useRef(null);

  useEffect(() => {
    if (!paletteOpenFor) return undefined;
    function onDocClick(e) {
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

  return (
    <ul className="breakout-order-list">
      {BREAKOUT_PANEL_ITEMS.map((item) => {
        const key = item.key;
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
                title={t("common.breakoutSettingsEditor.colorPickerTitle", { label: item.label })}
                onChange={(e) => setColor(key, e.target.value)}
              />
              <button
                type="button"
                className="breakout-order-palette-toggle"
                onClick={() => setPaletteOpenFor((k) => (k === key ? null : key))}
                title={t("common.breakoutSettingsEditor.choosePreset")}
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
                title={t("common.breakoutSettingsEditor.resetColor")}
              >
                ↺
              </button>
            )}
            <span className="breakout-order-label">{item.label}</span>
          </li>
        );
      })}
    </ul>
  );
}
