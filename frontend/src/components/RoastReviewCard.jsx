import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

// formatMessage is loaded globally via index.html's script tag pointing at
// https://michaelyagi.github.io/js/md_to_html.js
/* global formatMessage */

const POLL_INTERVAL_MS = 3000;

export default function RoastReviewCard({ roastId, roastActive }) {
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
      .then((r) => {
        setReview(r);
        if (r.status === "pending") scheduleNextPoll();
      })
      .catch((err) => {
        if (err.status === 404) {
          setReview(null);
        } else {
          setError(err.message);
        }
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
      <h3>AI Review</h3>
      <div className="review-card-body">
        {error && <p className="error">{error}</p>}

        {review === undefined && <p className="hint">Loading…</p>}

        {review !== undefined && (
          <>
            {busy && (
              <p className="hint">
                <span className="spinner" aria-hidden="true" /> Generating review…
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
              <p className="error">Review failed: {review.error}</p>
            )}

            {!busy && (
              <button type="button" className="no-print" onClick={handleGenerate} disabled={roastActive}>
                {review ? "Regenerate review" : "Generate review"}
              </button>
            )}
            {roastActive && <p className="hint no-print">Finish the roast before generating a review.</p>}
            {!roastActive && !review && (
              <p className="hint no-print">Uses a local Ollama server -- configure it under Settings.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
