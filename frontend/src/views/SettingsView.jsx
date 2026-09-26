import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import BreakoutSettingsEditor from "../components/BreakoutSettingsEditor.jsx";
import VerticalControlSettingsEditor from "../components/VerticalControlSettingsEditor.jsx";
import { SMALL_READOUT_EXCLUDED_KEYS } from "../breakoutPanels.js";

const CHECK_DEBOUNCE_MS = 600;

export default function SettingsView() {
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
        control,
      });
      setSaveFeedback("Saved.");
    } catch (err) {
      setSaveFeedback(`Failed to save: ${err.message}`);
    }
  }

  const connected = status?.connected;
  const modelsAvailable = connected && status.models.length > 0;

  return (
    <div className="settings-view">
      <div className="panel">
        <h2>Roaster safety</h2>
        <p className="hint">
          Applies to everything that changes the roaster: the sliders, alarm rules, repeating a saved roast and
          target control. These are the app's own limits. They are not a substitute for the roaster's own
          safety features, and they only work while the app can still reach the roaster.
        </p>
        <div className="form-row">
          <label>
            Heater never above (%)
            <input type="number" min="0" max="100" value={control.heater_max_pct}
              onChange={(e) => setControl({ ...control, heater_max_pct: Number(e.target.value) })} />
          </label>
          <label>
            Fan never below while heating (%)
            <input type="number" min="0" max="100" value={control.fan_min_pct}
              onChange={(e) => setControl({ ...control, fan_min_pct: Number(e.target.value) })} />
          </label>
          <label>
            Drum never below while heating (%)
            <input type="number" min="0" max="100" value={control.drum_min_pct}
              onChange={(e) => setControl({ ...control, drum_min_pct: Number(e.target.value) })} />
          </label>
        </div>
        <div className="form-row">
          <label>
            Fan level in an emergency (%)
            <input type="number" min="0" max="100" value={control.safe_fan_pct}
              onChange={(e) => setControl({ ...control, safe_fan_pct: Number(e.target.value) })} />
          </label>
          <label>
            Turn the heater off if Roast Telemetry is not open in a browser for (seconds)
            <input type="number" min="0" max="3600" value={control.client_watchdog_s}
              onChange={(e) => setControl({ ...control, client_watchdog_s: Number(e.target.value) })} />
            <span className="hint">0 (the default) never turns it off for this reason.</span>
          </label>
        </div>
        <p className="hint">
          In an emergency (the Emergency stop button, a lost connection or an error during a roast) the heater goes to 0 and the fan to the level above.
          The burner temperature setpoint can't be used while the heater limit is below 100%.
        </p>

        <div className="form-row">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={awayAlarmEnabled}
              onChange={(e) => setAwayAlarmEnabled(e.target.checked)}
            />
            Away alarm while roasting
          </label>
          <span className="hint">
            A gentle, repeating chime if this tab is hidden (minimized, switched away from) while actually roasting
            -- not a substitute for the watchdog above, which only reacts once the connection itself drops. On by
            default.
          </span>
        </div>

        <div className="form-row">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={control.safety_disabled}
              onChange={(e) => setControl({ ...control, safety_disabled: e.target.checked })}
            />
            Disable all of the above
          </label>
        </div>
        {control.safety_disabled && (
          <p className="error">
            Turns off the limits above, Emergency Stop and every fail-safe. Nothing is recorded while this is on.
          </p>
        )}
      </div>

      <div className="panel">
        <h2>Temperature Unit</h2>
        <p className="hint">
          Display only -- storage and the API always stay Celsius; this only changes how live readings
          (readouts, chart, event history) are shown. Threshold/config fields (Dry End, FC Start, SV ranges,
          alarm rule temperatures) stay in Celsius regardless, so what you type there always means the same
          thing. Every download (.alog, JSON, CSV, Excel, PDF) uses this unit too, and most record which
          unit they're in right in the file.
        </p>
        <div className="form-row">
          <label className="checkbox-label">
            <input
              type="radio"
              name="temperature_unit"
              checked={temperatureUnit === "c"}
              onChange={() => setTemperatureUnit("c")}
            />
            Celsius (°C)
          </label>
          <label className="checkbox-label">
            <input
              type="radio"
              name="temperature_unit"
              checked={temperatureUnit === "f"}
              onChange={() => setTemperatureUnit("f")}
            />
            Fahrenheit (°F)
          </label>
        </div>
      </div>

      <div className="panel">
        <h2>Big Readout Panel</h2>
        <p className="hint">
          Pick which live values also show large in a dedicated panel next to the chart on the Live
          Roast screen (only above ~1400px wide), alongside (not instead of) their normal small
          display. Off by default. Changes here apply to an already-open Live Roast tab within a few
          seconds, no refresh needed.
        </p>
        <BreakoutSettingsEditor
          enabledKeys={brokenOutPanels}
          setEnabledKeys={setBrokenOutPanels}
          colors={panelColors}
          setColors={setPanelColors}
        />
      </div>

      <div className="panel">
        <h2>Small Readout</h2>
        <p className="hint">
          Independent from the Big Readout Panel above -- its own set of values, shown as a compact
          scaled column right beside the chart at any screen width (used to be a fixed ET/BT/ΔBT
          legend; now it's whatever you pick here). Which items are enabled can differ freely between
          the two, but colors are shared -- recoloring an item here also changes it in the Big Readout
          Panel, and vice versa.
        </p>
        <BreakoutSettingsEditor
          enabledKeys={smallReadoutPanels}
          setEnabledKeys={setSmallReadoutPanels}
          colors={panelColors}
          setColors={setPanelColors}
          excludeKeys={SMALL_READOUT_EXCLUDED_KEYS}
        />
      </div>

      <div className="panel">
        <h2>Controls</h2>
        <p className="hint">
          The vertical control sliders beside the live chart's Drum/Air/Burner/SV. Drum and Air are always
          shown; Burner % and Burner SV (°C) can each be shown or hidden, but at least one of the two always
          stays visible -- moving one moves the other, they're the same underlying burner setpoint in
          different units. Changes here apply to an already-open Live Roast tab within a few seconds.
        </p>
        <VerticalControlSettingsEditor
          layout={verticalControlLayout}
          setLayout={setVerticalControlLayout}
          arrows={verticalControlArrows}
          setArrows={setVerticalControlArrows}
        />
      </div>

      <div className="panel">
        <h2>History</h2>
        <div className="form-row">
          <label>
            Results per page
            <input
              type="number"
              min="10"
              max="500"
              value={historyPageSize}
              onChange={(e) => setHistoryPageSize(Number(e.target.value) || 100)}
            />
          </label>
          <label>
            Max roasts to compare at once
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
        <h2>AI Roast Review (Ollama)</h2>
        <p className="hint">
          Configures the local Ollama server used for AI roast reviews (Roast detail → Review card).
          Nothing here is required for the rest of the app to work.
        </p>

        <div className="form-row">
          <label>
            Ollama URL
            <input
              placeholder="http://localhost:11434"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </label>
          <label>
            Model
            {modelsAvailable ? (
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">(choose a model)</option>
                {status.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                placeholder="e.g. llama3.1 (not connected -- enter manually)"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            )}
          </label>
        </div>

        <p className={`ollama-status ${connected ? "ollama-status-ok" : url.trim() ? "ollama-status-bad" : ""}`}>
          {checking
            ? "Checking…"
            : !url.trim()
              ? "Enter an Ollama URL above."
              : connected
                ? `Connected — ${status.models.length} model${status.models.length === 1 ? "" : "s"} available.`
                : `Not connected${status?.error ? `: ${status.error}` : ""}`}
        </p>
      </div>

      <div className="panel">
        <button type="button" onClick={handleSave} disabled={!loaded}>
          Save
        </button>
        {saveFeedback && <p className="hint preset-feedback">{saveFeedback}</p>}
      </div>
    </div>
  );
}
