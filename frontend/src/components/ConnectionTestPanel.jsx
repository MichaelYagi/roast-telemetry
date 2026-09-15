import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";

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
// Air/Drum share identical register-map confidence (see
// modbus_bridge/engine.py's own docstring: same registers 8192/8193/
// 8451, same blog-sourced-only origin, just different slave IDs) --
// Drum is a genuine differential test if Air's nudge doesn't visibly
// move anything, not a "more trustworthy" alternative. Max mirrors the
// FZ-94 built-in profile's own default operating ranges (Air 0-100%,
// Drum 0-70%) purely so the nudge target shown here doesn't overstate
// what the drive will actually accept -- the backend clamps to
// whatever's really configured regardless.
const CHANNEL_LABEL = { fan_pct: "Air", drum_speed_pct: "Drum" };
const CHANNEL_NUDGE_MAX = { fan_pct: 100, drum_speed_pct: 70 };

function analyzeReadSamples(samples, channels, tempUnit) {
  return channels.map((ch) => {
    const isTemp = ch.unit === "°";
    // Plausibility bounds (ch.min/ch.max) always stay Celsius -- they're
    // sanity-check thresholds, not something a display preference should
    // touch. Only what gets shown to the user converts.
    const display = (v) => (isTemp ? celsiusToUnit(v, tempUnit) : v);
    const unit = isTemp ? unitSuffix(tempUnit) : ch.unit;
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
      return { ...ch, status: "fail", detail: `out of plausible range (${display(min).toFixed(1)}-${display(max).toFixed(1)})` };
    }
    const varies = max - min > 0.001;
    const last = values[values.length - 1];
    return {
      ...ch,
      status: "pass",
      detail: `${display(last).toFixed(1)}${unit}${varies ? "" : " (steady -- fine if the roaster is idle)"}`,
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
export default function ConnectionTestPanel({ roastId, latest, mode, tempUnit = "c" }) {
  const [testMode, setTestMode] = useState(null); // null | "read" | "read_write"
  const [running, setRunning] = useState(false);
  const [readResults, setReadResults] = useState(null);
  const [writeStep, setWriteStep] = useState("idle"); // idle | running | done | failed
  const [writeDetail, setWriteDetail] = useState(null);
  // "fan_pct" | "drum_speed_pct" | null -- which channel's nudge the
  // "are you sure" prompt is currently showing for (null = not showing).
  const [nudgeConfirming, setNudgeConfirming] = useState(null);
  const [nudgeResult, setNudgeResult] = useState(null); // { channel, status, detail, retryTo? } | null
  // Whether the operator actually saw/heard the channel respond, once
  // asked directly -- the write-succeeding-per-feedback check above
  // can't tell a genuine mechanical response from a drive that just
  // updates its own feedback register without the fan physically
  // spinning, so this is a separate, explicit question, one answer at a
  // time for whatever nudgeResult currently holds.
  const [visibleConfirm, setVisibleConfirm] = useState(null); // "yes" | "no" | null, for the *current* nudgeResult
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
      setReadResults(analyzeReadSamples(samplesRef.current, channelsForMode, tempUnit));
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

  async function runNudge(channel) {
    setNudgeConfirming(null);
    setVisibleConfirm(null);
    const label = CHANNEL_LABEL[channel];
    const max = CHANNEL_NUDGE_MAX[channel];
    const before = latestRef.current?.[channel] ?? 0;
    const nudged = Math.min(max, before + NUDGE_PCT);
    setNudgeResult({ channel, status: "pending", detail: `nudging ${label} to ${nudged.toFixed(0)}% -- watch/listen for it…` });
    // The nudge-up and restore writes are deliberately two separate
    // try/catches, not one -- if the FIRST fails, nothing changed at all
    // (safe, nothing to say beyond "it failed"). If the SECOND fails,
    // the channel is now genuinely sitting at the nudged value with
    // nothing automatically fixing that -- that case needs its own,
    // clearly different message (with a retry) rather than a generic
    // error that reads the same either way.
    try {
      await api.sendCommand(roastId, { [channel]: nudged });
    } catch (err) {
      setNudgeResult({ channel, status: "failed", detail: `nudge failed, nothing changed -- ${err.message}` });
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
    await restoreChannelTo(channel, before);
  }

  async function restoreChannelTo(channel, before) {
    const label = CHANNEL_LABEL[channel];
    try {
      await api.sendCommand(roastId, { [channel]: before });
      setNudgeResult({ channel, status: "done", detail: `set back to ${before.toFixed(0)}%` });
    } catch (err) {
      setNudgeResult({
        channel,
        status: "failed",
        detail: `${label} is still nudged up -- restoring it to ${before.toFixed(0)}% failed: ${err.message}. Set it back yourself with the ${label} slider below, or retry.`,
        retryTo: before,
      });
    }
  }

  function startTest(nextMode) {
    setTestMode(nextMode);
    setReadResults(null);
    setWriteStep("idle");
    setWriteDetail(null);
    setNudgeConfirming(null);
    setNudgeResult(null);
    setVisibleConfirm(null);
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
            <button type="button" className="advanced-toggle" onClick={() => setNudgeConfirming("fan_pct")}>
              Want a visible confirmation instead? (optional)
            </button>
          ) : (
            <>
              <p className="hint">
                This will briefly bump {CHANNEL_LABEL[nudgeConfirming]} up by {NUDGE_PCT}% for about 2 seconds (you
                should hear/see it respond), then set it back to exactly what it was. Only do this if that's fine
                right now.
              </p>
              <div className="event-button-row">
                <button type="button" onClick={() => runNudge(nudgeConfirming)}>
                  Confirm: nudge {CHANNEL_LABEL[nudgeConfirming]} +{NUDGE_PCT}% and back
                </button>
                <button type="button" className="danger" onClick={() => setNudgeConfirming(null)}>
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
            <button type="button" onClick={() => restoreChannelTo(nudgeResult.channel, nudgeResult.retryTo)}>
              Retry restoring {CHANNEL_LABEL[nudgeResult.channel]} to {nudgeResult.retryTo.toFixed(0)}%
            </button>
          )}

          {/* The register write reporting "done" only proves the feedback
              register echoed the commanded value back -- a drive that
              updates its own feedback without the fan/motor physically
              spinning would still land here. This is the actual human
              confirmation, asked directly rather than inferred. */}
          {nudgeResult.status === "done" && visibleConfirm === null && (
            <div className="connection-test-visible-confirm">
              <p className="hint">Did the {nudgeResult.channel === "fan_pct" ? "fan" : "drum motor"} actually move?</p>
              <div className="event-button-row">
                <button type="button" onClick={() => setVisibleConfirm("yes")}>
                  Yes, it moved
                </button>
                <button type="button" className="danger" onClick={() => setVisibleConfirm("no")}>
                  No, nothing happened
                </button>
              </div>
            </div>
          )}

          {nudgeResult.status === "done" && visibleConfirm === "yes" && (
            <p className="connection-test-write connection-test-pass">
              ✓ confirmed -- the write path genuinely reaches the hardware, not just its own feedback register.
            </p>
          )}

          {nudgeResult.status === "done" && visibleConfirm === "no" && (
            <div className="connection-test-visible-confirm-no">
              {nudgeResult.channel === "fan_pct" ? (
                <>
                  <p className="connection-test-write connection-test-fail">
                    ✗ The register write succeeded but nothing physically moved. Air and Drum share identical
                    register numbers and confidence (both blog-sourced only, not independently confirmed) -- Drum is
                    a genuine differential test, not just "try something else."
                  </p>
                  {/* Also clears nudgeResult -- the confirm/cancel dialog
                      below only renders while it's null (see the block
                      above, gated on !nudgeResult), so leaving Air's
                      result in place here would silently swallow this
                      click: the dialog would never appear. */}
                  <button
                    type="button"
                    className="advanced-toggle"
                    onClick={() => {
                      setNudgeResult(null);
                      setNudgeConfirming("drum_speed_pct");
                    }}
                  >
                    Try Drum instead
                  </button>
                </>
              ) : (
                <p className="connection-test-write connection-test-fail">
                  ✗ Drum didn't respond either -- that's real evidence the shared register scheme (8192/8193) is
                  wrong for this unit, not just something specific to Air/slave 1. Worth checking your VFD's own
                  nameplate/front-panel parameters against those numbers.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
