import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { formatSeconds } from "../lib/metricFormat.js";

// Automatic control: repeat a saved roast's heater/fan/drum settings, or hold a
// target by adjusting the heater. Moving any slider by hand takes over again.
export default function AutoControlPanel({ roastId }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [past, setPast] = useState([]);
  const [pastId, setPastId] = useState("");
  const [variable, setVariable] = useState("ror_bt");
  const [target, setTarget] = useState("");
  const [maxHeater, setMaxHeater] = useState(100);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!roastId) return;
    try {
      setStatus(await api.getControl(roastId));
    } catch {
      /* the roast may have just ended */
    }
  }, [roastId]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    api
      .listRoasts({ status: "complete", limit: 50 })
      .then((rows) => setPast(rows.filter((r) => r.id !== roastId)))
      .catch(() => {});
  }, [roastId]);

  async function run(action) {
    setBusy(true);
    setError(null);
    try {
      setStatus(await action());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!status || !status.can_write) return null;

  const { program, feedback, tripped_reason: tripped } = status;
  const unit = variable === "bt" ? "°C" : "°C/min";

  return (
    <div className="panel auto-control-panel">
      <h3>{t("common.autoControlPanel.heading")}</h3>
      <p className="hint">{t("common.autoControlPanel.intro")}</p>

      {status.limits?.safety_disabled && (
        <p className="error" role="alert">
          {t("common.autoControlPanel.safetyDisabled")}
        </p>
      )}

      {tripped && (
        <p className="error" role="alert">
          {t("common.autoControlPanel.strippedForSafety", { reason: tripped })}
        </p>
      )}

      {program && (
        <p className="auto-status">
          {t("common.autoControlPanel.repeating")} <strong>{program.label}</strong> —{" "}
          {program.waiting_for_charge
            ? t("common.autoControlPanel.waitingForCharge")
            : program.next_step_in_s != null
              ? t("common.autoControlPanel.nextChangeIn", { time: formatSeconds(program.next_step_in_s) })
              : t("common.autoControlPanel.finishedSteps")}
        </p>
      )}
      {feedback && (
        <p className="auto-status">
          {t("common.autoControlPanel.holding")}{" "}
          {feedback.variable === "bt" ? t("common.autoControlPanel.beanTemperature") : t("common.autoControlPanel.rateOfRise")} at{" "}
          <strong>{feedback.setpoint ?? "—"}</strong>
          {t("common.autoControlPanel.heaterPct", { pct: feedback.output_pct })}
          {feedback.waiting_for_charge ? t("common.autoControlPanel.waitingForChargeParen") : ""}
        </p>
      )}
      {(program || feedback) && (
        <button type="button" onClick={() => run(() => api.stopAutomation(roastId))} disabled={busy}>
          {t("common.autoControlPanel.stopAutomaticControl")}
        </button>
      )}

      <h4>{t("common.autoControlPanel.repeatSavedRoast")}</h4>
      <div className="form-row">
        <select className="past-roast-select" value={pastId} onChange={(e) => setPastId(e.target.value)}>
          <option value="">{t("common.autoControlPanel.chooseRoast")}</option>
          {past.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title} ({new Date(r.created_at).toLocaleDateString()})
            </option>
          ))}
        </select>
        <button type="button" disabled={!pastId || busy} onClick={() => run(() => api.startProgramFromRoast(roastId, pastId))}>
          {t("common.autoControlPanel.repeatIt")}
        </button>
      </div>

      <h4>{t("common.autoControlPanel.holdTarget")}</h4>
      <div className="form-row">
        <select value={variable} onChange={(e) => setVariable(e.target.value)}>
          <option value="ror_bt">{t("common.autoControlPanel.rateOfRiseOption")}</option>
          <option value="bt">{t("common.autoControlPanel.beanTemperatureOption")}</option>
        </select>
        <label className="inline-field">
          {t("common.autoControlPanel.target", { unit })}
          <input type="number" step="0.1" value={target} onChange={(e) => setTarget(e.target.value)} />
        </label>
        <label className="inline-field">
          {t("common.autoControlPanel.heaterMax")}
          <input type="number" min="0" max="100" value={maxHeater} onChange={(e) => setMaxHeater(Number(e.target.value))} />
        </label>
        <button
          type="button"
          disabled={target === "" || busy}
          onClick={() => run(() => api.startFeedback(roastId, { variable, setpoint: Number(target), output_max_pct: maxHeater }))}
        >
          {t("common.autoControlPanel.holdIt")}
        </button>
      </div>
      <p className="hint">{t("common.autoControlPanel.heaterChangeHint")}</p>

      {error && <p className="error">{error}</p>}
    </div>
  );
}
