import { useTranslation } from "react-i18next";
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
// always-on horizontal Controls panel. Drum/Fan have no show/hide toggle
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
  const { t } = useTranslation();
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

  const ARROW_STEP_MAX = 100;
  const DEFAULT_ARROW_STEP = 1;

  function toggleArrows(key) {
    setArrows((prev) => {
      const next = { ...prev };
      if (next[key]) delete next[key];
      else next[key] = DEFAULT_ARROW_STEP;
      return next;
    });
  }

  function setArrowStep(key, rawValue) {
    const step = Math.max(1, Math.min(ARROW_STEP_MAX, Number(rawValue) || DEFAULT_ARROW_STEP));
    setArrows((prev) => ({ ...prev, [key]: step }));
  }

  const availableToAdd = OPTIONAL_VERTICAL_CONTROL_KEYS.filter((key) => !flat.some((item) => item.key === key));

  return (
    <>
      <h3>{t("common.verticalControlSettingsEditor.stackOrderHeading")}</h3>
      <p className="hint">{t("common.verticalControlSettingsEditor.stackHint1")}</p>
      <p className="hint">{t("common.verticalControlSettingsEditor.stackHint2")}</p>
      <ul className="breakout-order-list vertical-control-order-list">
        {flat.map((item, i) => {
          const meta = verticalControlItem(item.key);
          const mandatory = MANDATORY_VERTICAL_CONTROL_KEYS.includes(item.key);
          return (
            <li key={item.key}>
              <div className="vertical-control-order-row">
                <span className="vertical-control-order-label">{meta.label}</span>
                {mandatory && <span className="hint vertical-control-mandatory-tag">{t("common.verticalControlSettingsEditor.alwaysShown")}</span>}
                <span className="vertical-control-order-row-actions">
                  <button type="button" onClick={() => move(item.key, -1)} disabled={i === 0} title={t("common.verticalControlSettingsEditor.moveUp")}>
                    ▲
                  </button>
                  <button type="button" onClick={() => move(item.key, 1)} disabled={i === flat.length - 1} title={t("common.verticalControlSettingsEditor.moveDown")}>
                    ▼
                  </button>
                  {!mandatory && (
                    <button
                      type="button"
                      className="danger"
                      onClick={() => remove(item.key)}
                      disabled={includedOptionalCount <= 1}
                      title={includedOptionalCount <= 1 ? t("common.verticalControlSettingsEditor.mustStayVisible") : t("common.verticalControlSettingsEditor.remove")}
                    >
                      {t("common.verticalControlSettingsEditor.remove")}
                    </button>
                  )}
                </span>
              </div>
              {i > 0 && (
                <div className="vertical-control-order-row">
                  <label className="checkbox-label">
                    <input type="checkbox" checked={item.joinsPrevious} onChange={() => toggleJoinsPrevious(item.key)} />
                    {t("common.verticalControlSettingsEditor.stackWithAbove")}
                  </label>
                </div>
              )}
              <div className="vertical-control-order-row">
                <label className="checkbox-label">
                  <input type="checkbox" checked={Boolean(arrows?.[item.key])} onChange={() => toggleArrows(item.key)} />
                  {t("common.verticalControlSettingsEditor.arrowButtons")}
                </label>
                {Boolean(arrows?.[item.key]) && (
                  <label className="checkbox-label">
                    {t("common.verticalControlSettingsEditor.stepsBy")}
                    <input
                      type="number"
                      min={1}
                      max={ARROW_STEP_MAX}
                      value={arrows[item.key]}
                      onChange={(e) => setArrowStep(item.key, e.target.value)}
                    />
                  </label>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {availableToAdd.length > 0 && (
        <>
          <h3>{t("common.verticalControlSettingsEditor.availableHeading")}</h3>
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
