import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";
import { EVENT_ITEMS, OTHER_LINE_ITEMS, PHASE_ITEMS, extraChannelDefaultColor } from "../chartColors.js";

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

// Every color there is, in one place: each channel/stat (regardless of
// whether it's currently enabled in the Big or Small Readout Panel, or on
// the chart -- color is independent of visibility), then everything else
// the roast chart draws -- its other lines, every extra channel any roast
// has (`extraChannels`, from the server's settings.extra_channels), the
// milestone markers and the phase bands. See chartColors.js.
export default function ColorsSettingsEditor({ colors, setColors, extraChannels = [] }) {
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

  const groups = [
    { heading: null, items: BREAKOUT_PANEL_ITEMS },
    { heading: t("settings.colors.otherLines"), items: OTHER_LINE_ITEMS },
    {
      heading: t("settings.colors.extraChannels"),
      hint: t("settings.colors.extraChannelsHint"),
      items: extraChannels.map((label, i) => ({ key: `extra:${label}`, label, color: extraChannelDefaultColor(colors, label, i) })),
    },
    { heading: t("settings.colors.milestones"), items: EVENT_ITEMS },
    { heading: t("settings.colors.phases"), items: PHASE_ITEMS },
  ].filter((g) => g.items.length);

  return (
    <>
      {groups.map((group) => (
        <div key={group.heading || "channels"}>
          {group.heading && <h3 className="colors-group-heading">{group.heading}</h3>}
          {group.hint && <p className="hint">{group.hint}</p>}
          {renderList(group.items)}
        </div>
      ))}
    </>
  );

  function renderList(items) {
    return (
    <ul className="breakout-order-list">
      {items.map((item) => {
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
}
