import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { formatMessage } from "../lib/mdToHtml.js";

const POLL_INTERVAL_MS = 2000;

// Asks the local Ollama model to comment on the roasts the Analysis page is
// showing. Only appears when Ollama is set up (a URL and a model in Settings).
// Only summary numbers are sent -- see backend/app/analysis_insights.py.
export default function AnalysisInsights({ filters, groupBy }) {
  const [configured, setConfigured] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((s) => !cancelled && setConfigured(Boolean(s.ollama_url?.trim() && s.ollama_model?.trim())))
      .catch(() => !cancelled && setConfigured(false));
    return () => {
      cancelled = true;
    };
  }, []);

  return configured ? <InsightsPanel filters={filters} groupBy={groupBy} /> : null;
}

function InsightsPanel({ filters, groupBy }) {
  const [question, setQuestion] = useState("");
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  function poll(id) {
    timer.current = setTimeout(async () => {
      try {
        const next = await api.getInsight(id);
        setJob(next);
        if (next.status === "pending") poll(id);
      } catch (err) {
        setError(err.message);
        setJob(null);
      }
    }, POLL_INTERVAL_MS);
  }

  async function ask() {
    setError(null);
    clearTimeout(timer.current);
    try {
      const started = await api.requestInsight({ ...filters, group_by: groupBy, question: question.trim() || null });
      setJob(started);
      poll(started.id);
    } catch (err) {
      setError(err.message);
    }
  }

  const busy = job?.status === "pending";

  return (
    <div className="panel insights-panel no-print">
      <h3>Ask the AI</h3>
      <p className="hint">
        Sends this chart's summary numbers (averages, spread and recent roasts, not full curves) to your Ollama model and
        shows what it makes of them. It follows the filters and grouping above. A local model can take a minute or two.
      </p>
      <label className="insights-question">
        Question (optional)
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Which of my beans do I roast most consistently?"
          maxLength={500}
        />
      </label>
      <button type="button" onClick={ask} disabled={busy}>
        {busy ? "Thinking…" : job ? "Ask again" : "Analyse"}
      </button>

      {busy && (
        <p className="hint">
          <span className="spinner" aria-hidden="true" /> Waiting for {job.model}…
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {job?.status === "failed" && <p className="error">The analysis failed: {job.error}</p>}
      {job?.status === "ready" && (
        <>
          <div className="review-text" dangerouslySetInnerHTML={{ __html: formatMessage(job.text) }} />
          <p className="hint review-meta">
            {job.model} · {new Date(job.completed_at || job.created_at).toLocaleString()} · AI can be wrong; check the
            numbers above.
          </p>
        </>
      )}
    </div>
  );
}
