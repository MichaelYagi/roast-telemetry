import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

// Every channel worth checking on a full modbus_live connection -- ms6514
// only ever has bt/et (see canWrite/channelsForMode below), everything
// else is FZ-94-specific. Ranges are deliberately generous (this is a
// sanity check against "reading garbage/nothing," not a real calibration
// check) -- a real BT/ET/DT for a coffee roaster, hot or cold, comfortably
// fits 0-300C; Burner SV is a configurable setpoint range but 0-400C
// safely bounds any sane configuration; Air/Drum are plain percentages.
const READ_CHANNELS = [
  { key: "bt", label: "BT", unit: "°", min: -10, max: 300 },
  { key: "et", label: "ET", unit: "°", min: -10, max: 300 },
  { key: "dt", label: "DT", unit: "°", min: -10, max: 300 },
  { key: "burner_sv_c", label: "Burner SV", unit: "°", min: 0, max: 400 },
  { key: "fan_pct", label: "Air %", unit: "%", min: 0, max: 100 },
  { key: "drum_speed_pct", label: "Drum %", unit: "%", min: 0, max: 100 },
];

const SAMPLE_WINDOW_MS = 4000;
const SAMPLE_INTERVAL_MS = 250; // just samples the already-live `latest` prop -- no extra network calls
const NUDGE_PCT = 5;

function analyzeReadSamples(samples, channels) {
  return channels.map((ch) => {
    const values = samples.map((s) => s?.[ch.key]).filter((v) => v != null);
    if (values.length === 0) {
      // Not necessarily a failure -- e.g. DT is off by default on most
      // FZ-94 units, so "never populated" is the *expected* shape there,
      // not a fault. Surfaced as a neutral warning either way, not a
      // red fail, since this function has no way to know which case it is.
      return { ...ch, status: "warn", detail: "no data (not configured, or not reading)" };
    }
    const min = Math.min(...values);
    const max = Math.min(300, Math.max(...values));
    const inRange = values.every((v) => v >= ch.min && v <= ch.max);
    if (!inRange) {
      return { ...ch, status: "fail", detail: `out of plausible range (${min.toFixed(1)}-${max.toFixed(1)})` };
    }
    const varies = max - min > 0.001;
    const last = values[values.length - 1];
    return {
      ...ch,
      status: "pass",
      detail: `${last.toFixed(1)}${ch.unit}${varies ? "" : " (steady -- fine if the roaster is idle)"}`,
    };
  });
}

// A guided, in-app check of ON/OFF, a comprehensive read across every
// configured channel, and (optionally) one write -- shown only while
// phase === "armed" (connected via ON, not yet recording -- see
// LiveRoastView's handleToggleConnect/RoastSession.connect()). The write
// check is hard-gated to that same window by its caller (this component
// is simply never rendered outside it) and by the backend's own
// apply_command status check, which never allows writes outside
// IDLE/ROASTING/COOLING -- there's no path from here to a write firing
// mid-roast.
export default function ConnectionTestPanel({ roastId, latest, mode }) {
  const [testMode, setTestMode] = useState(null); // null | "read" | "read_write"
  const [running, setRunning] = useState(false);
  const [readResults, setReadResults] = useState(null);
  const [writeStep, setWriteStep] = useState("idle"); // idle | running | done | failed
  const [writeDetail, setWriteDetail] = useState(null);
  const [nudgeConfirming, setNudgeConfirming] = useState(false);
  const [nudgeResult, setNudgeResult] = useState(null);
  const samplesRef = useRef([]);
  const latestRef = useRef(latest);

  useEffect(() => {
    latestRef.current = latest;
  }, [latest]);

  // ms6514 is a read-only thermocouple meter (no Burner/Air/Drum, no
  // apply_command effect at all -- see ms6514_bridge/engine.py) -- only
  // modbus_live has anything writable to check.
  const canWrite = mode === "modbus_live";
  const channelsForMode = mode === "ms6514_live" ? READ_CHANNELS.filter((c) => ["bt", "et"].includes(c.key)) : READ_CHANNELS;

  useEffect(() => {
    if (!running) return undefined;
    samplesRef.current = [];
    const interval = setInterval(() => {
      if (latestRef.current) samplesRef.current.push(latestRef.current);
    }, SAMPLE_INTERVAL_MS);
    const timeout = setTimeout(() => {
      clearInterval(interval);
      setReadResults(analyzeReadSamples(samplesRef.current, channelsForMode));
      setRunning(false);
    }, SAMPLE_WINDOW_MS);
    return () => {
      clearInterval(interval);
      clearTimeout(timeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  // Runs once the read sampling window above finishes, only for the
  // read+write mode -- kept as a separate effect (rather than inlined at
  // the end of the one above) so the read results render immediately,
  // with the write check's own "running…" state appearing right after,
  // instead of the whole panel waiting on both before showing anything.
  useEffect(() => {
    if (running || testMode !== "read_write" || readResults === null || writeStep !== "idle") return;
    runWriteCheck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, testMode, readResults]);

  async function runWriteCheck() {
    setWriteStep("running");
    setWriteDetail(null);
    const before = latestRef.current?.fan_pct;
    if (before == null) {
      setWriteStep("failed");
      setWriteDetail("no Air reading available yet -- can't verify a round-trip");
      return;
    }
    try {
      // The benign write: read Air's own current value, write that exact
      // same value straight back. On an idle machine (Air almost always
      // already off) this writes "off" over "off" -- genuinely zero
      // physical effect, while still exercising the real write path
      // (command encoding, the control connection, the PLC's
      // acknowledgement) end to end. More benign than an on/off toggle.
      await api.sendCommand(roastId, { fan_pct: before });
      await new Promise((resolve) => setTimeout(resolve, 1500)); // let the drive report back
      const after = latestRef.current?.fan_pct;
      if (after != null && Math.abs(after - before) > 1) {
        setWriteStep("failed");
        setWriteDetail(`wrote ${before.toFixed(0)}% but feedback now reads ${after.toFixed(0)}% -- write may not be reaching the drive`);
        return;
      }
      setWriteStep("done");
      setWriteDetail(`wrote Air back at its current ${before.toFixed(0)}% (no-op) -- feedback confirms it took effect`);
    } catch (err) {
      setWriteStep("failed");
      setWriteDetail(err.message);
    }
  }

  async function runNudge() {
    setNudgeConfirming(false);
    const before = latestRef.current?.fan_pct ?? 0;
    const nudged = Math.min(100, before + NUDGE_PCT);
    setNudgeResult({ status: "pending", detail: `nudging Air to ${nudged.toFixed(0)}% -- watch/listen for it…` });
    // The nudge-up and restore writes are deliberately two separate
    // try/catches, not one -- if the FIRST fails, nothing changed at all
    // (safe, nothing to say beyond "it failed"). If the SECOND fails,
    // Air is now genuinely sitting at the nudged value with nothing
    // automatically fixing that -- that case needs its own, clearly
    // different message (with a retry) rather than a generic error that
    // reads the same either way.
    try {
      await api.sendCommand(roastId, { fan_pct: nudged });
    } catch (err) {
      setNudgeResult({ status: "failed", detail: `nudge failed, nothing changed -- ${err.message}` });
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
    await restoreAirTo(before);
  }

  async function restoreAirTo(before) {
    try {
      await api.sendCommand(roastId, { fan_pct: before });
      setNudgeResult({ status: "done", detail: `set back to ${before.toFixed(0)}%` });
    } catch (err) {
      setNudgeResult({
        status: "failed",
        detail: `Air is still nudged up -- restoring it to ${before.toFixed(0)}% failed: ${err.message}. Set it back yourself with the Air slider below, or retry.`,
        retryTo: before,
      });
    }
  }

  function startTest(nextMode) {
    setTestMode(nextMode);
    setReadResults(null);
    setWriteStep("idle");
    setWriteDetail(null);
    setNudgeConfirming(false);
    setNudgeResult(null);
    setRunning(true);
  }

  return (
    <div className="panel connection-test-panel">
      <h3>Test Connection</h3>
      <p className="hint">
        Verifies the connection is actually working, before committing to a roast -- reads every configured
        channel for a few seconds.
        {canWrite &&
          " Read + write also confirms Air can be controlled, using the most benign possible write: reading its current value back and writing that exact same value again (a no-op)."}
      </p>
      <div className="event-button-row">
        <button type="button" onClick={() => startTest("read")} disabled={running}>
          {running && testMode === "read" ? "Testing…" : "Run read-only test"}
        </button>
        {canWrite && (
          <button type="button" onClick={() => startTest("read_write")} disabled={running}>
            {running && testMode === "read_write" ? "Testing…" : "Run read + write test"}
          </button>
        )}
      </div>

      {readResults && (
        <ul className="connection-test-results">
          {readResults.map((r) => (
            <li key={r.key} className={`connection-test-row connection-test-${r.status}`}>
              <span className="connection-test-icon">{r.status === "pass" ? "✓" : r.status === "warn" ? "•" : "✗"}</span>
              <span className="connection-test-label">{r.label}</span>
              <span className="connection-test-detail">{r.detail}</span>
            </li>
          ))}
        </ul>
      )}

      {testMode === "read_write" && writeStep !== "idle" && (
        <p className={`connection-test-write connection-test-${writeStep === "done" ? "pass" : writeStep === "failed" ? "fail" : "pending"}`}>
          {writeStep === "running" && "Testing write (Air, no-op round-trip)…"}
          {writeStep === "done" && `✓ Write check passed — ${writeDetail}`}
          {writeStep === "failed" && `✗ Write check failed — ${writeDetail}`}
        </p>
      )}

      {testMode === "read_write" && writeStep === "done" && !nudgeResult && (
        <div className="connection-test-nudge">
          {!nudgeConfirming ? (
            <button type="button" className="advanced-toggle" onClick={() => setNudgeConfirming(true)}>
              Want a visible confirmation instead? (optional)
            </button>
          ) : (
            <>
              <p className="hint">
                This will briefly bump Air up by {NUDGE_PCT}% for about 2 seconds (you should hear/see the fan
                respond), then set it back to exactly what it was. Only do this if that's fine right now.
              </p>
              <div className="event-button-row">
                <button type="button" onClick={runNudge}>
                  Confirm: nudge Air +{NUDGE_PCT}% and back
                </button>
                <button type="button" className="danger" onClick={() => setNudgeConfirming(false)}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {nudgeResult && (
        <>
          <p className={`connection-test-write connection-test-${nudgeResult.status === "done" ? "pass" : nudgeResult.status === "failed" ? "fail" : "pending"}`}>
            {nudgeResult.detail}
          </p>
          {nudgeResult.retryTo != null && (
            <button type="button" onClick={() => restoreAirTo(nudgeResult.retryTo)}>
              Retry restoring Air to {nudgeResult.retryTo.toFixed(0)}%
            </button>
          )}
        </>
      )}
    </div>
  );
}
