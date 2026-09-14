import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";

const CHECK_DEBOUNCE_MS = 600;

export default function SettingsView() {
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("");
  const [brokenOutPanels, setBrokenOutPanels] = useState([]); // ordered -- display order == array order
  const [panelColors, setPanelColors] = useState({}); // key -> hex, only for items overridden from their built-in default
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
      setLoaded(true);
    });
  }, []);

  function setPanelColor(key, hex) {
    setPanelColors((prev) => ({ ...prev, [key]: hex }));
  }

  function resetPanelColor(key) {
    setPanelColors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  function enablePanel(key) {
    setBrokenOutPanels((prev) => (prev.includes(key) ? prev : [...prev, key]));
  }

  function disablePanel(key) {
    setBrokenOutPanels((prev) => prev.filter((k) => k !== key));
  }

  function movePanel(key, direction) {
    setBrokenOutPanels((prev) => {
      const i = prev.indexOf(key);
      const j = i + direction;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

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
          Roast screen, alongside (not instead of) their normal small display. Off by default. Changes
          here apply to an already-open Live Roast tab within a few seconds, no refresh needed.
        </p>
        {brokenOutPanels.length > 0 && (
          <>
            <h3>Enabled, in display order</h3>
            <ul className="breakout-order-list">
              {brokenOutPanels.map((key, i) => {
                const item = BREAKOUT_PANEL_ITEMS.find((it) => it.key === key);
                if (!item) return null;
                const color = panelColors[key] || item.color;
                const isCustom = Boolean(panelColors[key]);
                return (
                  <li key={key}>
                    <input
                      type="color"
                      className="breakout-order-swatch"
                      value={color}
                      title={`${item.label} color`}
                      onChange={(e) => setPanelColor(key, e.target.value)}
                    />
                    {isCustom && (
                      <button
                        type="button"
                        className="breakout-order-swatch-reset"
                        onClick={() => resetPanelColor(key)}
                        title="Reset to default color"
                      >
                        ↺
                      </button>
                    )}
                    <span className="breakout-order-label">{item.label}</span>
                    <button type="button" onClick={() => movePanel(key, -1)} disabled={i === 0} title="Move up">
                      ▲
                    </button>
                    <button
                      type="button"
                      onClick={() => movePanel(key, 1)}
                      disabled={i === brokenOutPanels.length - 1}
                      title="Move down"
                    >
                      ▼
                    </button>
                    <button type="button" className="danger" onClick={() => disablePanel(key)} title="Remove">
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
          {BREAKOUT_PANEL_ITEMS.filter((item) => !brokenOutPanels.includes(item.key)).map((item) => (
            <button type="button" key={item.key} className="breakout-add-btn" onClick={() => enablePanel(item.key)}>
              + {item.label}
            </button>
          ))}
          {BREAKOUT_PANEL_ITEMS.every((item) => brokenOutPanels.includes(item.key)) && (
            <p className="hint">All panels are enabled.</p>
          )}
        </div>
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
