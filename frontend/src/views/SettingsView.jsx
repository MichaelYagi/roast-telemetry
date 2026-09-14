import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";

const CHECK_DEBOUNCE_MS = 600;

// Quick-pick swatches shown in a popover next to each item's native color
// input -- the native input alone (see .breakout-order-swatch below) still
// covers "any color at all", this is just a faster path for a reasonable
// spread of visually distinct options. A plain Tailwind-ish 500-weight
// sweep around the wheel, not tied to any of BREAKOUT_PANEL_ITEMS' own
// built-in defaults.
const PRESET_COLORS = [
  "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16", "#22c55e",
  "#10b981", "#06b6d4", "#0ea5e9", "#3b82f6", "#6366f1", "#8b5cf6",
  "#a855f7", "#d946ef", "#ec4899", "#64748b",
];

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
  // Which row's preset-palette popover is open, if any -- a single value
  // (not per-row state) is what makes "only one open at a time" automatic,
  // same reasoning as the native color inputs already being modal popovers.
  const [paletteOpenFor, setPaletteOpenFor] = useState(null);
  const paletteRef = useRef(null);

  useEffect(() => {
    if (!paletteOpenFor) return undefined;
    function onDocClick(e) {
      // Skip entirely for a click on *any* row's toggle button (not just
      // this one's) -- that button's own onClick is the sole authority
      // for what paletteOpenFor becomes next (open this row / close if
      // already open). Without this bailout, clicking a *different* row's
      // toggle while one is open raced two independent state updates
      // against each other for the same click -- this handler's
      // unconditional close, and that button's open -- and they canceled
      // out instead of switching rows, needing a second click to recover.
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
                const paletteOpen = paletteOpenFor === key;
                return (
                  <li key={key}>
                    <div className="breakout-order-color" ref={paletteOpen ? paletteRef : null}>
                      <input
                        type="color"
                        className="breakout-order-swatch"
                        value={color}
                        title={`${item.label} color -- opens the full color picker`}
                        onChange={(e) => setPanelColor(key, e.target.value)}
                      />
                      <button
                        type="button"
                        className="breakout-order-palette-toggle"
                        onClick={() => setPaletteOpenFor((k) => (k === key ? null : key))}
                        title="Choose from preset colors"
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
                                setPanelColor(key, c);
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
