import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { formatMessage } from "../lib/mdToHtml.js";


const POLL_INTERVAL_MS = 3000;

// The AI Review box only shows when Ollama is set up: both a URL and a
// model chosen under Settings. Otherwise it renders nothing at all (and
// doesn't request a review), since there is nothing it could do. Settings are
// read when the roast page opens, so setting Ollama up and coming back to a
// roast shows the box. A saved review for a roast is hidden along with it
// while the connection is unset.
export default function RoastReviewCard(props) {
  const [configured, setConfigured] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((s) => {
        if (!cancelled) setConfigured(Boolean(s.ollama_url?.trim() && s.ollama_model?.trim()));
      })
      .catch(() => {
        if (!cancelled) setConfigured(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return configured ? <RoastReviewPanel {...props} /> : null;
}

function RoastReviewPanel({ roastId, roastActive }) {
  const { t } = useTranslation();
  const [review, setReview] = useState(undefined); // undefined = loading, null = none yet, object = loaded
  const [error, setError] = useState(null);
  const [generating, setGenerating] = useState(false);
  const pollTimer = useRef(null);

  function scheduleNextPoll() {
    clearTimeout(pollTimer.current);
    pollTimer.current = setTimeout(fetchReview, POLL_INTERVAL_MS);
  }

  function fetchReview() {
    api
      .getReview(roastId)
      // status "none" (no review row yet -- the common, default state)
      // comes back as a normal 200, not a 404 -- see backend's
      // ReviewStatus.NONE comment for why a 404 here used to fire on
      // every unreviewed roast's detail page during ordinary browsing.
      // Any error caught below is now a genuine one (network failure,
      // or the roast itself not existing).
      .then((r) => {
        setReview(r.status === "none" ? null : r);
        if (r.status === "pending") scheduleNextPoll();
      })
      .catch((err) => {
        setError(err.message);
      });
  }

  useEffect(() => {
    setReview(undefined);
    setError(null);
    fetchReview();
    return () => clearTimeout(pollTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roastId]);

  async function handleGenerate() {
    setError(null);
    setGenerating(true);
    try {
      const r = await api.generateReview(roastId);
      setReview(r);
      if (r.status === "pending") scheduleNextPoll();
    } catch (err) {
      setError(err.message);
    } finally {
      setGenerating(false);
    }
  }

  const busy = generating || review?.status === "pending";

  return (
    <div className="panel">
      <h3>{t("common.roastReviewCard.heading")}</h3>
      <div className="review-card-body">
        {error && <p className="error">{error}</p>}

        {review === undefined && <p className="hint">{t("common.roastReviewCard.loading")}</p>}

        {review !== undefined && (
          <>
            {busy && (
              <p className="hint">
                <span className="spinner" aria-hidden="true" /> {t("common.roastReviewCard.generating")}
              </p>
            )}

            {!busy && review?.status === "ready" && (
              <>
                <div className="review-text" dangerouslySetInnerHTML={{ __html: formatMessage(review.review_text) }} />
                <p className="hint review-meta">
                  {review.model} · {new Date(review.completed_at || review.created_at).toLocaleString()}
                </p>
              </>
            )}

            {!busy && review?.status === "failed" && (
              <p className="error">{t("common.roastReviewCard.reviewFailed", { error: review.error })}</p>
            )}

            {!busy && (
              <button type="button" className="no-print" onClick={handleGenerate} disabled={roastActive}>
                {review ? t("common.roastReviewCard.regenerateReview") : t("common.roastReviewCard.generateReview")}
              </button>
            )}
            {roastActive && <p className="hint no-print">{t("common.roastReviewCard.roastNotEnded")}</p>}
          </>
        )}
      </div>
    </div>
  );
}
