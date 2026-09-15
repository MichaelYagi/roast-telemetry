import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import BreakoutSettingsEditor from "../components/BreakoutSettingsEditor.jsx";
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
        <h2>Temperature Unit</h2>
        <p className="hint">
          Display only, same idea as Artisan's own Celsius/Fahrenheit Mode toggle -- everything is still
          stored and sent as Celsius; this only changes how live readings (readouts, chart, event history)
          are shown. Threshold/config fields (Dry End, FC Start, SV ranges, alarm rule temperatures) stay
          in Celsius regardless, so what you type there always means the same thing.
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
        <h2>Settings</h2>
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
        <button type="button" onClick={handleSave} disabled={!loaded}>
          Save
        </button>
        {saveFeedback && <p className="hint preset-feedback">{saveFeedback}</p>}
      </div>
    </div>
  );
}
