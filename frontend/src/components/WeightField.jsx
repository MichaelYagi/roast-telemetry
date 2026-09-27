import { useState } from "react";
import { useTranslation } from "react-i18next";

// Shared by Green weight/Roasted weight in both LiveRoastView.jsx and
// RoastDetailView.jsx -- pulled out after those four instances drifted
// out of sync with each other (one had an edit/add toggle, the other a
// plain always-open input, then a fix to one wasn't mirrored to the
// other). One component now, so all four behave identically by
// construction instead of by careful copy-pasting.
//
// State machine, driven by two things: `value` (the parent's current
// known weight, or null/undefined) and `userWantsToEdit` (set true by
// clicking "edit", cleared by a successful Save/Cancel/Delete) --
// `showInput = value == null || userWantsToEdit` is *derived* fresh
// every render rather than tracked as its own separately-synced piece
// of state, so it can never get stuck out of sync with an async-loaded
// `value` (e.g. LiveRoastView.jsx's roast object arriving after mount)
// the way an independently-tracked "editing" boolean did before.
export default function WeightField({ value, onSave, onDelete, noPrint = false }) {
  const { t } = useTranslation();
  const [userWantsToEdit, setUserWantsToEdit] = useState(false);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const showInput = value == null || userWantsToEdit;

  function startEditing() {
    setInput(value != null ? String(value) : "");
    setError(null);
    setUserWantsToEdit(true);
  }

  async function handleSave() {
    const grams = Number(input);
    if (!input.trim() || Number.isNaN(grams)) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(grams);
      setUserWantsToEdit(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setError(null);
    try {
      await onDelete();
      setUserWantsToEdit(false);
    } catch (err) {
      setError(err.message);
    }
  }

  if (showInput) {
    return (
      <>
        <span className={`input-suffix-group${noPrint ? " no-print" : ""}`}>
          <input
            type="number" min="0" step="0.1"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t("common.weightField.gramsPlaceholder")}
            // Only when the user explicitly clicked "edit" -- not just
            // because the field happens to start empty (value == null),
            // which used to steal focus and scroll the whole page down
            // to this field on every page load.
            autoFocus={userWantsToEdit}
          />
          <span className="input-suffix">g</span>
          <button type="button" onClick={handleSave} disabled={saving || !input.trim()}>
            {saving ? t("common.weightField.saving") : t("common.weightField.save")}
          </button>
          {value != null && (
            <button type="button" className="link-like" onClick={() => { setUserWantsToEdit(false); setError(null); }}>
              {t("common.weightField.cancel")}
            </button>
          )}
        </span>
        {error && <span className="error"> {error}</span>}
      </>
    );
  }

  return (
    <>
      {value} g{" "}
      <button type="button" className={`link-like${noPrint ? " no-print" : ""}`} onClick={startEditing}>
        {t("common.weightField.edit")}
      </button>{" "}
      <button type="button" className={`danger link-like${noPrint ? " no-print" : ""}`} onClick={handleDelete}>
        {t("common.weightField.delete")}
      </button>
      {error && <span className="error"> {error}</span>}
    </>
  );
}
