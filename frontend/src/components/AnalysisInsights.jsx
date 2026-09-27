import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
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
      <h3>{t("common.analysisInsights.heading")}</h3>
      <p className="hint">{t("common.analysisInsights.hint")}</p>
      <label className="insights-question">
        {t("common.analysisInsights.questionLabel")}
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t("common.analysisInsights.questionPlaceholder")}
          maxLength={500}
        />
      </label>
      <button type="button" onClick={ask} disabled={busy}>
        {busy ? t("common.analysisInsights.thinking") : job ? t("common.analysisInsights.askAgain") : t("common.analysisInsights.analyse")}
      </button>

      {busy && (
        <p className="hint">
          <span className="spinner" aria-hidden="true" /> {t("common.analysisInsights.waitingFor", { model: job.model })}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {job?.status === "failed" && <p className="error">{t("common.analysisInsights.analysisFailed", { error: job.error })}</p>}
      {job?.status === "ready" && (
        <>
          <div className="review-text" dangerouslySetInnerHTML={{ __html: formatMessage(job.text) }} />
          <p className="hint review-meta">
            {job.model} · {new Date(job.completed_at || job.created_at).toLocaleString()} · {t("common.analysisInsights.aiCanBeWrong")}
          </p>
        </>
      )}
    </div>
  );
}
