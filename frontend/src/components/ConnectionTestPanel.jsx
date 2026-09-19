import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";
import { TERM_TOOLTIPS } from "../termTooltips.js";

// Every channel worth checking on a full modbus_live connection -- ms6514
// only ever has bt/et, tc4_live only has bt/et/dt (see canWrite/
// channelsForMode below), everything else (Burner SV, Air/Drum RPM) is
// FZ-94/Modbus-specific and doesn't exist on those other devices at all.
// Ranges are deliberately generous (this is a sanity check against
// "reading garbage/nothing," not a real calibration check) -- a real
// BT/ET/DT for a coffee roaster, hot or cold, comfortably fits 0-300C;
// Burner SV is a configurable setpoint range but 0-400C safely bounds any
// sane configuration; Air/Drum are real RPM readings (confirmed against a
// live FZ-94), not percentages, despite the fan_pct/drum_speed_pct field
// names -- 0-100/0-70 there is this specific machine's actual RPM range,
// not a 0-100% scale.
const READ_CHANNELS = [
  { key: "bt", label: "BT", unit: "°", min: -10, max: 300 },
  { key: "et", label: "ET", unit: "°", min: -10, max: 300 },
  { key: "dt", label: "DT", unit: "°", min: -10, max: 300 },
  { key: "burner_sv_c", label: "Burner SV", unit: "°", min: 0, max: 400 },
  { key: "fan_pct", label: "Air RPM", unit: " RPM", min: 0, max: 100 },
  { key: "drum_speed_pct", label: "Drum RPM", unit: " RPM", min: 0, max: 100 },
];

function channelsForMode(mode) {
  if (mode === "ms6514_live") return READ_CHANNELS.filter((c) => ["bt", "et"].includes(c.key));
  // tc4_live: no Burner SV register, and OT1/DCFAN are write-only -- there's
  // never anything to read back for fan_pct/drum_speed_pct (TC4 has no drum
  // channel at all), so including them here would always show a misleading
  // "no data" for reasons that have nothing to do with the connection
  // actually working.
  if (mode === "tc4_live") return READ_CHANNELS.filter((c) => ["bt", "et", "dt"].includes(c.key));
  return READ_CHANNELS;
}

const SAMPLE_WINDOW_MS = 4000;
const SAMPLE_INTERVAL_MS = 250; // just samples the already-live `latest` prop -- no extra network calls

// Air/Drum (Modbus) share identical register-map confidence (see
// modbus_bridge/engine.py's own docstring: same registers 8192/8193/
// 8451, same blog-sourced-only origin, just different slave IDs) -- Drum
// is a genuine differential test if Air's nudge doesn't visibly move
// anything, not a "more trustworthy" alternative. Heater (TC4) is a
// completely different protocol (OT1, plain 0-100% PWM duty, no register
// map at all) sharing only the same "bump it, watch it, put it back"
// shape -- each channel gets its own label/unit/nudge size/hold time
// below rather than assuming they're interchangeable.
const CHANNEL_LABEL = { fan_pct: "Air", drum_speed_pct: "Drum", heater_pct: "Heater" };
const CHANNEL_UNIT = { fan_pct: "RPM", drum_speed_pct: "RPM", heater_pct: "%" };
// Max mirrors the FZ-94 built-in profile's own default operating ranges
// (Air 0-100 RPM, Drum 0-70 RPM) purely so the nudge target shown here
// doesn't overstate what the drive will actually accept -- the backend
// clamps to whatever's really configured regardless. Heater's 0-100 is
// TC4's own real OT1 duty range, not a guess.
const CHANNEL_NUDGE_MAX = { fan_pct: 100, drum_speed_pct: 70, heater_pct: 100 };
// A 5-unit bump is plenty to visibly move an RPM reading, but 5% OT1 duty
// for 2 seconds is unlikely to move BT/ET at all (thermal lag) -- Heater
// gets a bigger, longer nudge so there's actually something to notice.
const CHANNEL_NUDGE_AMOUNT = { fan_pct: 5, drum_speed_pct: 5, heater_pct: 25 };
const CHANNEL_NUDGE_HOLD_MS = { fan_pct: 2000, drum_speed_pct: 2000, heater_pct: 4000 };
// What to ask the operator to look/listen for -- TC4 has no motor to
// watch, just a heating element/SSR and (indirectly, over a longer
// timescale than this quick nudge) BT itself.
const CHANNEL_MOVER_LABEL = { fan_pct: "fan", drum_speed_pct: "drum motor", heater_pct: "heater (SSR/element click or glow)" };

function unitSuffixFor(channel) {
  const unit = CHANNEL_UNIT[channel];
  return unit === "%" ? "%" : ` ${unit}`;
}

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
    // No artificial cap here -- this feeds the "out of plausible range"
    // detail text below, which exists specifically to show *how far* out
    // of range a bad reading actually is. A hardcoded ceiling (this used
    // to be `Math.min(300, ...)`, understating anything above 300 even
    // for Burner SV's real 400 ceiling) defeats that purpose by hiding
    // the true severity of the out-of-range value.
    const max = Math.max(...values);
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
  const [writeStep, setWriteStep] = useState("idle"); // idle | running | done | failed | skipped
  const [writeDetail, setWriteDetail] = useState(null);
  // "fan_pct" | "drum_speed_pct" | "heater_pct" | null -- which channel's
  // nudge the "are you sure" prompt is currently showing for (null = not
  // showing).
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
  // apply_command effect at all -- see ms6514_bridge/engine.py) -- nothing
  // to write there. modbus_live has Air/Drum VFD registers plus a Burner
  // SV register. tc4_live has real Heater (OT1)/Fan (DCFAN) control, just
  // with no feedback register at all -- see the write-test branching
  // below for how that changes what "write check" even means.
  const canWrite = mode === "modbus_live" || mode === "tc4_live";
  const channels = channelsForMode(mode);
  const defaultNudgeChannel = mode === "tc4_live" ? "heater_pct" : "fan_pct";

  useEffect(() => {
    if (!running) return undefined;
    samplesRef.current = [];
    const interval = setInterval(() => {
      if (latestRef.current) samplesRef.current.push(latestRef.current);
    }, SAMPLE_INTERVAL_MS);
    const timeout = setTimeout(() => {
      clearInterval(interval);
      setReadResults(analyzeReadSamples(samplesRef.current, channels, tempUnit));
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
  //
  // modbus_live is the only mode with an actual feedback register to poll
  // (Air's fan_pct read-back) -- tc4_live's OT1/DCFAN are write-only, so
  // TC4Engine.tick() always reports heater_pct/fan_pct as null; polling
  // that would always read null and vacuously "pass" without proving
  // anything, so tc4_live skips straight to the nudge below instead of
  // running a register check that can't ever fail honestly.
  useEffect(() => {
    if (running || testMode !== "read_write" || readResults === null || writeStep !== "idle") return;
    if (mode === "modbus_live") {
      runWriteCheck();
    } else {
      setWriteStep("skipped");
    }
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
        setWriteDetail(`wrote ${before.toFixed(0)} RPM but feedback now reads ${after.toFixed(0)} RPM -- write may not be reaching the drive`);
        return;
      }
      setWriteStep("done");
      setWriteDetail(`wrote Air back at its current ${before.toFixed(0)} RPM (no-op) -- feedback confirms it took effect`);
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
    const amount = CHANNEL_NUDGE_AMOUNT[channel];
    const before = latestRef.current?.[channel] ?? 0;
    // Real bug: clamping a nudge-up to `max` silently no-ops when already
    // at/near the ceiling (before === nudged), which then reports "done"
    // with nothing having actually moved -- a false "the write path/drive
    // doesn't work" diagnosis for a channel that's simply already maxed
    // out. Nudge down instead whenever nudging up would clamp to a no-op.
    const direction = before + amount > max ? -1 : 1;
    const nudged = Math.max(0, Math.min(max, before + direction * amount));
    setNudgeResult({
      channel,
      status: "pending",
      detail: `nudging ${label} ${direction > 0 ? "up" : "down"} to ${nudged.toFixed(0)}${unitSuffixFor(channel)} -- watch/listen for it…`,
    });
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
    await new Promise((resolve) => setTimeout(resolve, CHANNEL_NUDGE_HOLD_MS[channel]));
    await restoreChannelTo(channel, before);
  }

  async function restoreChannelTo(channel, before) {
    const label = CHANNEL_LABEL[channel];
    try {
      await api.sendCommand(roastId, { [channel]: before });
      setNudgeResult({ channel, status: "done", detail: `set back to ${before.toFixed(0)}${unitSuffixFor(channel)}` });
    } catch (err) {
      setNudgeResult({
        channel,
        status: "failed",
        detail: `${label} is still nudged up -- restoring it to ${before.toFixed(0)}${unitSuffixFor(channel)} failed: ${err.message}. Set it back yourself with the ${label} slider below, or retry.`,
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
        {canWrite && mode === "modbus_live" &&
          " Read + write also confirms Air can be controlled, using the most benign possible write: reading its current value back and writing that exact same value again (a no-op)."}
        {canWrite && mode === "tc4_live" &&
          " Read + write also confirms Heater can be controlled -- TC4 has no write feedback register at all, so instead of a silent round-trip this briefly bumps OT1 and asks you to confirm you actually saw/heard it respond, then sets it back."}
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
              <span className="connection-test-label" title={TERM_TOOLTIPS[r.label]}>{r.label}</span>
              <span className="connection-test-detail">{r.detail}</span>
            </li>
          ))}
        </ul>
      )}

      {testMode === "read_write" && mode === "modbus_live" && writeStep !== "idle" && (
        <p className={`connection-test-write connection-test-${writeStep === "done" ? "pass" : writeStep === "failed" ? "fail" : "pending"}`}>
          {writeStep === "running" && "Testing write (Air, no-op round-trip)…"}
          {writeStep === "done" && `✓ Write check passed — ${writeDetail}`}
          {writeStep === "failed" && `✗ Write check failed — ${writeDetail}`}
        </p>
      )}
      {testMode === "read_write" && mode === "tc4_live" && writeStep === "skipped" && (
        <p className="hint">
          TC4's OT1/DCFAN outputs are write-only -- there's no feedback register to read back and confirm against,
          so use the check below to actually watch/listen for Heater responding.
        </p>
      )}

      {testMode === "read_write" && (writeStep === "done" || writeStep === "skipped") && !nudgeResult && (
        <div className="connection-test-nudge">
          {!nudgeConfirming ? (
            <button type="button" className="advanced-toggle" onClick={() => setNudgeConfirming(defaultNudgeChannel)}>
              {mode === "tc4_live" ? "Confirm Heater actually responds" : "Want a visible confirmation instead? (optional)"}
            </button>
          ) : (
            <>
              <p className="hint">
                This will briefly bump {CHANNEL_LABEL[nudgeConfirming]}{" "}
                {(latestRef.current?.[nudgeConfirming] ?? 0) + CHANNEL_NUDGE_AMOUNT[nudgeConfirming] > CHANNEL_NUDGE_MAX[nudgeConfirming] ? "down" : "up"}{" "}
                by {CHANNEL_NUDGE_AMOUNT[nudgeConfirming]}{unitSuffixFor(nudgeConfirming)} for about{" "}
                {(CHANNEL_NUDGE_HOLD_MS[nudgeConfirming] / 1000).toFixed(0)} seconds (you should see/hear the{" "}
                {CHANNEL_MOVER_LABEL[nudgeConfirming]} respond), then set it back to exactly what it was. Only do this
                if that's fine right now.
              </p>
              <div className="event-button-row">
                <button type="button" onClick={() => runNudge(nudgeConfirming)}>
                  Confirm: nudge {CHANNEL_LABEL[nudgeConfirming]}{" "}
                  {(latestRef.current?.[nudgeConfirming] ?? 0) + CHANNEL_NUDGE_AMOUNT[nudgeConfirming] > CHANNEL_NUDGE_MAX[nudgeConfirming] ? "-" : "+"}
                  {CHANNEL_NUDGE_AMOUNT[nudgeConfirming]}{unitSuffixFor(nudgeConfirming)} and back
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
              Retry restoring {CHANNEL_LABEL[nudgeResult.channel]} to {nudgeResult.retryTo.toFixed(0)}{unitSuffixFor(nudgeResult.channel)}
            </button>
          )}

          {/* The register write reporting "done" only proves the feedback
              register echoed the commanded value back (modbus_live) -- a
              drive that updates its own feedback without the fan/motor
              physically spinning would still land here. For tc4_live
              there's no feedback register at all, so this confirmation is
              the *only* evidence a write test has. Either way, this is the
              actual human confirmation, asked directly rather than
              inferred. */}
          {nudgeResult.status === "done" && visibleConfirm === null && (
            <div className="connection-test-visible-confirm">
              <p className="hint">Did the {CHANNEL_MOVER_LABEL[nudgeResult.channel]} actually respond?</p>
              <div className="event-button-row">
                <button type="button" onClick={() => setVisibleConfirm("yes")}>
                  Yes, it responded
                </button>
                <button type="button" className="danger" onClick={() => setVisibleConfirm("no")}>
                  No, nothing happened
                </button>
              </div>
            </div>
          )}

          {nudgeResult.status === "done" && visibleConfirm === "yes" && (
            <p className="connection-test-write connection-test-pass">
              ✓ confirmed -- the write path genuinely reaches the hardware
              {nudgeResult.channel === "heater_pct" ? "" : ", not just its own feedback register"}.
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
              ) : nudgeResult.channel === "drum_speed_pct" ? (
                <p className="connection-test-write connection-test-fail">
                  ✗ Drum didn't respond either -- that's real evidence the shared register scheme (8192/8193) is
                  wrong for this unit, not just something specific to Air/slave 1. Worth checking your VFD's own
                  nameplate/front-panel parameters against those numbers.
                </p>
              ) : (
                <p className="connection-test-write connection-test-fail">
                  ✗ The OT1 write itself succeeded (no serial error) but nothing visibly responded. Since TC4 has no
                  feedback register at all, this either means a real wiring/SSR problem, or the{" "}
                  {CHANNEL_NUDGE_AMOUNT.heater_pct}% bump for {(CHANNEL_NUDGE_HOLD_MS.heater_pct / 1000).toFixed(0)}s
                  just wasn't enough to notice -- check the board's own OT1 output directly (multimeter/scope on the
                  pin, or the heating element itself) before assuming the write path is broken.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
