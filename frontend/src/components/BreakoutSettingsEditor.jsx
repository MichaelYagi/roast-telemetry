import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";

// A controlled editor for one ordered set of BREAKOUT_PANEL_ITEMS keys --
// used twice on the Settings page (Big Readout Panel, Small Readout), each
// with its own independent enabledKeys state living in the parent
// (SettingsView), not here. Visibility/order only -- color used to live
// here too (duplicated between both instances, since color is really one
// shared property of the item, not per-panel), now picked once in its own
// dedicated Colors section instead (see ColorsSettingsEditor.jsx).
export default function BreakoutSettingsEditor({ enabledKeys, setEnabledKeys, excludeKeys }) {
  const { t } = useTranslation();
  // Some items don't make sense for every instance of this editor (e.g.
  // "Elapsed time" is redundant next to the Small Readout column, which
  // always sits beside a chart that already has its own elapsed-time
  // axis) -- excluded from both the "Available" add-list and, if present
  // from an older save, the enabled list itself, rather than only hiding
  // the add button and leaving a stale entry the user can't remove.
  const excluded = excludeKeys || [];
  const visibleItems = BREAKOUT_PANEL_ITEMS.filter((item) => !excluded.includes(item.key));
  const visibleEnabledKeys = enabledKeys.filter((key) => !excluded.includes(key));

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
          <h3>{t("common.breakoutSettingsEditor.enabledHeading")}</h3>
          <ul className="breakout-order-list">
            {visibleEnabledKeys.map((key, i) => {
              const item = BREAKOUT_PANEL_ITEMS.find((it) => it.key === key);
              if (!item) return null;
              return (
                <li key={key}>
                  <span className="breakout-order-label">{item.label}</span>
                  <button type="button" onClick={() => move(key, -1)} disabled={i === 0} title={t("common.breakoutSettingsEditor.moveUp")}>
                    ▲
                  </button>
                  <button
                    type="button"
                    onClick={() => move(key, 1)}
                    disabled={i === visibleEnabledKeys.length - 1}
                    title={t("common.breakoutSettingsEditor.moveDown")}
                  >
                    ▼
                  </button>
                  <button type="button" className="danger" onClick={() => disable(key)} title={t("common.breakoutSettingsEditor.remove")}>
                    {t("common.breakoutSettingsEditor.remove")}
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <h3>{t("common.breakoutSettingsEditor.availableHeading")}</h3>
      <div className="breakout-toggle-grid">
        {visibleItems
          .filter((item) => !enabledKeys.includes(item.key))
          .map((item) => (
            <button type="button" key={item.key} className="breakout-add-btn" onClick={() => enable(item.key)}>
              + {item.label}
            </button>
          ))}
        {visibleItems.every((item) => enabledKeys.includes(item.key)) && (
          <p className="hint">{t("common.breakoutSettingsEditor.allEnabled")}</p>
        )}
      </div>
    </>
  );
}
