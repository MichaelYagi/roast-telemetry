import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import BreakoutSettingsEditor from "../components/BreakoutSettingsEditor.jsx";
import VerticalControlSettingsEditor from "../components/VerticalControlSettingsEditor.jsx";
import { SMALL_READOUT_EXCLUDED_KEYS } from "../breakoutPanels.js";

const CHECK_DEBOUNCE_MS = 600;

export default function SettingsView() {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("");
  const [brokenOutPanels, setBrokenOutPanels] = useState([]); // ordered -- display order == array order
  // Shared by both editors below -- colors are a property of the item
  // (e.g. "bt" is the same blue everywhere it's shown), not something
  // that should drift between the Big and Small readouts. Only holds
  // items overridden from their built-in default.
  const [panelColors, setPanelColors] = useState({});
  // Independent from the Big Readout Panel above -- its own ordered
  // enabled list, rendered beside the chart at any width (not gated to
  // the Big Readout Panel's >=1400px split-layout threshold). Both
  // editors pull from the same BREAKOUT_PANEL_ITEMS registry (and the
  // same panelColors above), so the same value (e.g. "bt") can be
  // enabled in one, both, or neither independently, always the same color.
  const [smallReadoutPanels, setSmallReadoutPanels] = useState([]);
  const [temperatureUnit, setTemperatureUnit] = useState("c");
  const [verticalControlLayout, setVerticalControlLayout] = useState([]);
  const [verticalControlArrows, setVerticalControlArrows] = useState({});
  const [historyPageSize, setHistoryPageSize] = useState(100);
  const [maxCompare, setMaxCompare] = useState(20);
  const [awayAlarmEnabled, setAwayAlarmEnabled] = useState(true);
  const [language, setLanguage] = useState("en");
  const [control, setControl] = useState({
    heater_max_pct: 100, fan_min_pct: 0, drum_min_pct: 0, safe_fan_pct: 100, client_watchdog_s: 0, safety_disabled: false,
  });
  const [loaded, setLoaded] = useState(false);
  const [checking, setChecking] = useState(false);
  const [status, setStatus] = useState(null); // { connected, models, error } | null
  const [saveFeedback, setSaveFeedback] = useState(null);
  const debounceRef = useRef(null);

  useEffect(() => {
    api.getSettings().then((s) => {
      setUrl(s.ollama_url || "");
      setModel(s.ollama_model || "");
      setBrokenOutPanels(s.broken_out_panels || []);
      setPanelColors(s.breakout_panel_colors || {});
      setSmallReadoutPanels(s.small_readout_panels || []);
      setTemperatureUnit(s.temperature_unit || "c");
      setVerticalControlLayout(s.vertical_control_layout || []);
      setVerticalControlArrows(s.vertical_control_arrows || {});
      setHistoryPageSize(s.history_page_size || 100);
      setMaxCompare(s.max_compare || 20);
      // Not `?? true`/`|| true` -- those would force it back on whenever
      // the saved value is false, since false is falsy too.
      setAwayAlarmEnabled(s.away_alarm_enabled !== false);
      setLanguage(s.language || "en");
      if (s.control) setControl(s.control);
      setLoaded(true);
    });
  }, []);

  // Auto-checks connectivity whenever the URL changes (debounced) -- no
  // manual "Test connection" button, per how this was scoped: the status
  // just reflects reality as you type, and a successful check is what
  // lets the model field become a dropdown of what's actually pulled.
  useEffect(() => {
    if (!loaded) return;
    setStatus(null);
    if (!url.trim()) return;
    setChecking(true);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      api
        .checkOllama(url.trim())
        .then(setStatus)
        .catch((err) => setStatus({ connected: false, models: [], error: err.message }))
        .finally(() => setChecking(false));
    }, CHECK_DEBOUNCE_MS);
    return () => clearTimeout(debounceRef.current);
  }, [url, loaded]);

  async function handleSave() {
    setSaveFeedback(null);
    try {
      await api.saveSettings({
        ollama_url: url.trim() || null,
        ollama_model: model.trim() || null,
        broken_out_panels: brokenOutPanels,
        breakout_panel_colors: panelColors,
        small_readout_panels: smallReadoutPanels,
        temperature_unit: temperatureUnit,
        vertical_control_layout: verticalControlLayout,
        vertical_control_arrows: verticalControlArrows,
        history_page_size: historyPageSize,
        max_compare: maxCompare,
        away_alarm_enabled: awayAlarmEnabled,
        language,
        control,
      });
      setSaveFeedback(t("settings.saved"));
    } catch (err) {
      setSaveFeedback(t("settings.failedToSave", { error: err.message }));
    }
  }

  const connected = status?.connected;
  const modelsAvailable = connected && status.models.length > 0;

  return (
    <div className="settings-view">
      <div className="panel">
        <h2>{t("settings.roasterSafety.heading")}</h2>
        <p className="hint">{t("settings.roasterSafety.hint")}</p>
        <div className="form-row">
          <label>
            {t("settings.roasterSafety.heaterMaxPct")}
            <input type="number" min="0" max="100" value={control.heater_max_pct}
              onChange={(e) => setControl({ ...control, heater_max_pct: Number(e.target.value) })} />
          </label>
          <label>
            {t("settings.roasterSafety.fanMinPct")}
            <input type="number" min="0" max="100" value={control.fan_min_pct}
              onChange={(e) => setControl({ ...control, fan_min_pct: Number(e.target.value) })} />
          </label>
          <label>
            {t("settings.roasterSafety.drumMinPct")}
            <input type="number" min="0" max="100" value={control.drum_min_pct}
              onChange={(e) => setControl({ ...control, drum_min_pct: Number(e.target.value) })} />
          </label>
        </div>
        <div className="form-row">
          <label>
            {t("settings.roasterSafety.safeFanPct")}
            <input type="number" min="0" max="100" value={control.safe_fan_pct}
              onChange={(e) => setControl({ ...control, safe_fan_pct: Number(e.target.value) })} />
          </label>
          <label>
            {t("settings.roasterSafety.watchdogLabel")}
            <input type="number" min="0" max="3600" value={control.client_watchdog_s}
              onChange={(e) => setControl({ ...control, client_watchdog_s: Number(e.target.value) })} />
            <span className="hint">{t("settings.roasterSafety.watchdogHint")}</span>
          </label>
        </div>
        <p className="hint">{t("settings.roasterSafety.emergencyHint")}</p>

        <div className="form-row">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={control.safety_disabled}
              onChange={(e) => setControl({ ...control, safety_disabled: e.target.checked })}
            />
            {t("settings.roasterSafety.disableAll")}
          </label>
        </div>
        {control.safety_disabled && <p className="error">{t("settings.roasterSafety.disabledWarning")}</p>}

        <div className="form-row">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={awayAlarmEnabled}
              onChange={(e) => setAwayAlarmEnabled(e.target.checked)}
            />
            {t("settings.roasterSafety.awayAlarm")}
          </label>
          <span className="hint">{t("settings.roasterSafety.awayAlarmHint")}</span>
        </div>
      </div>

      <div className="panel">
        <h2>{t("settings.temperatureUnit.heading")}</h2>
        <p className="hint">{t("settings.temperatureUnit.hint")}</p>
        <div className="form-row">
          <label className="checkbox-label">
            <input
              type="radio"
              name="temperature_unit"
              checked={temperatureUnit === "c"}
              onChange={() => setTemperatureUnit("c")}
            />
            {t("settings.temperatureUnit.celsius")}
          </label>
          <label className="checkbox-label">
            <input
              type="radio"
              name="temperature_unit"
              checked={temperatureUnit === "f"}
              onChange={() => setTemperatureUnit("f")}
            />
            {t("settings.temperatureUnit.fahrenheit")}
          </label>
        </div>
      </div>

      <div className="panel">
        <h2>{t("settings.language.label")}</h2>
        <p className="hint">{t("settings.language.hint")}</p>
        <div className="form-row">
          <label className="checkbox-label">
            <input type="radio" name="language" checked={language === "en"} onChange={() => setLanguage("en")} />
            English
          </label>
          <label className="checkbox-label">
            <input type="radio" name="language" checked={language === "ja"} onChange={() => setLanguage("ja")} />
            日本語
          </label>
        </div>
      </div>

      <div className="panel">
        <h2>{t("settings.bigReadoutPanel.heading")}</h2>
        <p className="hint">{t("settings.bigReadoutPanel.hint")}</p>
        <BreakoutSettingsEditor
          enabledKeys={brokenOutPanels}
          setEnabledKeys={setBrokenOutPanels}
          colors={panelColors}
          setColors={setPanelColors}
        />
      </div>

      <div className="panel">
        <h2>{t("settings.smallReadout.heading")}</h2>
        <p className="hint">{t("settings.smallReadout.hint")}</p>
        <BreakoutSettingsEditor
          enabledKeys={smallReadoutPanels}
          setEnabledKeys={setSmallReadoutPanels}
          colors={panelColors}
          setColors={setPanelColors}
          excludeKeys={SMALL_READOUT_EXCLUDED_KEYS}
        />
      </div>

      <div className="panel">
        <h2>{t("settings.controls.heading")}</h2>
        <p className="hint">{t("settings.controls.hint")}</p>
        <VerticalControlSettingsEditor
          layout={verticalControlLayout}
          setLayout={setVerticalControlLayout}
          arrows={verticalControlArrows}
          setArrows={setVerticalControlArrows}
        />
      </div>

      <div className="panel">
        <h2>{t("settings.history.heading")}</h2>
        <div className="form-row">
          <label>
            {t("settings.history.resultsPerPage")}
            <input
              type="number"
              min="10"
              max="500"
              value={historyPageSize}
              onChange={(e) => setHistoryPageSize(Number(e.target.value) || 100)}
            />
          </label>
          <label>
            {t("settings.history.maxCompare")}
            <input
              type="number"
              min="3"
              max="100000"
              value={maxCompare}
              onChange={(e) => setMaxCompare(Number(e.target.value) || 20)}
            />
          </label>
        </div>
      </div>

      <div className="panel">
        <h2>{t("settings.aiRoastReview.heading")}</h2>
        <p className="hint">{t("settings.aiRoastReview.hint")}</p>

        <div className="form-row">
          <label>
            {t("settings.aiRoastReview.ollamaUrl")}
            <input
              placeholder="http://localhost:11434"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </label>
          <label>
            {t("settings.aiRoastReview.model")}
            {modelsAvailable ? (
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">{t("settings.aiRoastReview.chooseModel")}</option>
                {status.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                placeholder={t("settings.aiRoastReview.modelPlaceholder")}
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            )}
          </label>
        </div>

        <p className={`ollama-status ${connected ? "ollama-status-ok" : url.trim() ? "ollama-status-bad" : ""}`}>
          {checking
            ? t("settings.aiRoastReview.checking")
            : !url.trim()
              ? t("settings.aiRoastReview.enterUrl")
              : connected
                ? t("settings.aiRoastReview.connected", { count: status.models.length })
                : t("settings.aiRoastReview.notConnected") + (status?.error ? t("settings.aiRoastReview.notConnectedError", { error: status.error }) : "")}
        </p>
      </div>

      <div className="panel">
        <button type="button" onClick={handleSave} disabled={!loaded}>
          {t("settings.save")}
        </button>
        {saveFeedback && <p className="hint preset-feedback">{saveFeedback}</p>}
      </div>
    </div>
  );
}
