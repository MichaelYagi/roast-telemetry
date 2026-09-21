import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";
import { TERM_TOOLTIPS } from "../termTooltips.js";
import { analyzeReadSamples as analyzeReadSamplesShared, channelsForMode } from "../connectionPlausibility.js";

const SAMPLE_WINDOW_MS = 4000;
const SAMPLE_INTERVAL_MS = 250; // just samples the already-live `latest` prop -- no extra network calls

// Per-(mode, channel) metadata for whichever channel(s) that mode's
// write-test/nudge flow actually uses. fan_pct/drum_speed_pct mean
// genuinely different things depending on which device is on the other
// end -- Modbus's Air/Drum are real RPM-reporting VFD drives (register
// writes, see modbus_bridge/engine.py's own docstring: same registers
// 8192/8193/8451, same blog-sourced-only origin, just different slave
// IDs -- Drum is a genuine differential test if Air's nudge doesn't
// visibly move anything, not a "more trustworthy" alternative); Aillio's
// Fan/Drum are a small device-native 0-100% scale sent as raw USB
// command packets, not registers at all (see aillio_bridge/r1.py --
// Heater/Fan move via relative +1/-1 packets, Drum via one absolute
// packet). TC4's Heater (OT1, plain 0-100% PWM duty) has no feedback
// register/poll at all, unlike the other two -- so it's the only one
// that can't use the round-trip write-check below, only the nudge.
// A flat, channel-name-only map can't express this (fan_pct would mean
// three different things), so this is keyed by mode first.
const CHANNEL_META = {
  modbus_live: {
    fan_pct: { label: "Air", unit: "RPM", nudgeMax: 100, nudgeAmount: 5, nudgeHoldMs: 2000, moverLabel: "fan" },
    drum_speed_pct: { label: "Drum", unit: "RPM", nudgeMax: 70, nudgeAmount: 5, nudgeHoldMs: 2000, moverLabel: "drum motor" },
  },
  aillio_live: {
    fan_pct: { label: "Fan", unit: "%", nudgeMax: 100, nudgeAmount: 10, nudgeHoldMs: 2000, moverLabel: "fan" },
    drum_speed_pct: { label: "Drum", unit: "%", nudgeMax: 100, nudgeAmount: 10, nudgeHoldMs: 2000, moverLabel: "drum motor" },
  },
  tc4_live: {
    // A 5-unit bump is plenty to visibly move an RPM/%-of-a-drive
    // reading, but 5% OT1 duty for 2 seconds is unlikely to move BT/ET
    // at all (thermal lag) -- Heater gets a bigger, longer nudge so
    // there's actually something to notice.
    heater_pct: { label: "Heater", unit: "%", nudgeMax: 100, nudgeAmount: 25, nudgeHoldMs: 4000, moverLabel: "heater (SSR/element click or glow)" },
  },
};

function meta(mode, channel) {
  return CHANNEL_META[mode]?.[channel];
}

function unitSuffixFor(mode, channel) {
  const unit = meta(mode, channel)?.unit;
  return unit === "%" ? "%" : ` ${unit}`;
}

// modbus_live and aillio_live both genuinely poll Fan back from the
// device (Air's VFD feedback register / AillioEngine.tick()'s _poll())
// -- a round-trip write-then-read-back check means something real for
// either. tc4_live's OT1/DCFAN are write-only, no such poll exists.
const ROUNDTRIP_CHANNEL = { modbus_live: "fan_pct", aillio_live: "fan_pct" };

function analyzeReadSamples(samples, channels, tempUnit) {
  return analyzeReadSamplesShared(samples, channels, tempUnit, { celsiusToUnit, unitSuffix });
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
export default function ConnectionTestPanel({ roastId, latest, mode, tempUnit = "c", simulated = false }) {
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
  // SV register. aillio_live has real Heater/Fan/Drum control, genuinely
  // polled back from the device. tc4_live has real Heater (OT1)/Fan
  // (DCFAN) control, just with no feedback register at all -- see the
  // write-test branching below for how that changes what "write check"
  // even means.
  const canWrite = mode === "modbus_live" || mode === "aillio_live" || mode === "tc4_live";
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
  // modbus_live/aillio_live both have an actual feedback channel to poll
  // (see ROUNDTRIP_CHANNEL's own comment) -- tc4_live's OT1/DCFAN are
  // write-only, so TC4Engine.tick() always reports heater_pct/fan_pct as
  // null; polling that would always read null and vacuously "pass"
  // without proving anything, so tc4_live skips straight to the nudge
  // below instead of running a check that can't ever fail honestly.
  useEffect(() => {
    if (running || testMode !== "read_write" || readResults === null || writeStep !== "idle") return;
    if (ROUNDTRIP_CHANNEL[mode]) {
      runWriteCheck();
    } else {
      setWriteStep("skipped");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, testMode, readResults]);

  async function runWriteCheck() {
    setWriteStep("running");
    setWriteDetail(null);
    const channel = ROUNDTRIP_CHANNEL[mode];
    const { label, unit } = meta(mode, channel);
    const suffix = unitSuffixFor(mode, channel);
    const before = latestRef.current?.[channel];
    if (before == null) {
      setWriteStep("failed");
      setWriteDetail(`no ${label} reading available yet -- can't verify a round-trip`);
      return;
    }
    try {
      // The benign write: read the channel's own current value, write
      // that exact same value straight back. On an idle machine (Fan/Air
      // almost always already off) this writes "off" over "off" --
      // genuinely zero physical effect, while still exercising the real
      // write path (command encoding, the connection, the device's own
      // acknowledgement) end to end. More benign than an on/off toggle.
      await api.sendCommand(roastId, { [channel]: before });
      await new Promise((resolve) => setTimeout(resolve, 1500)); // let the device report back
      const after = latestRef.current?.[channel];
      if (after != null && Math.abs(after - before) > 1) {
        setWriteStep("failed");
        setWriteDetail(`wrote ${before.toFixed(0)}${suffix} but feedback now reads ${after.toFixed(0)}${suffix} -- write may not be reaching the device`);
        return;
      }
      setWriteStep("done");
      setWriteDetail(`wrote ${label} back at its current ${before.toFixed(0)}${suffix} (no-op) -- feedback confirms it took effect`);
    } catch (err) {
      setWriteStep("failed");
      setWriteDetail(err.message);
    }
  }

  async function runNudge(channel) {
    setNudgeConfirming(null);
    setVisibleConfirm(null);
    const { label, nudgeMax: max, nudgeAmount: amount } = meta(mode, channel);
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
      detail: `nudging ${label} ${direction > 0 ? "up" : "down"} to ${nudged.toFixed(0)}${unitSuffixFor(mode, channel)} -- watch/listen for it…`,
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
    await new Promise((resolve) => setTimeout(resolve, meta(mode, channel).nudgeHoldMs));
    await restoreChannelTo(channel, before);
  }

  async function restoreChannelTo(channel, before) {
    const { label } = meta(mode, channel);
    try {
      await api.sendCommand(roastId, { [channel]: before });
      setNudgeResult({ channel, status: "done", detail: `set back to ${before.toFixed(0)}${unitSuffixFor(mode, channel)}` });
    } catch (err) {
      setNudgeResult({
        channel,
        status: "failed",
        detail: `${label} is still nudged up -- restoring it to ${before.toFixed(0)}${unitSuffixFor(mode, channel)} failed: ${err.message}. Set it back yourself with the ${label} slider below, or retry.`,
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
      {simulated && (
        <p className="simulated-note">
          <span className="simulated-badge">Simulated</span> This is a built-in simulated device, so passing checks here
          say nothing about a real machine -- nothing is sent to hardware.
        </p>
      )}
      <p className="hint">
        Verifies the connection is actually working, before committing to a roast -- reads every configured
        channel for a few seconds.
        {canWrite && mode === "modbus_live" &&
          " Read + write also confirms Air can be controlled, using the most benign possible write: reading its current value back and writing that exact same value again (a no-op)."}
        {canWrite && mode === "aillio_live" &&
          " Read + write also confirms Fan can be controlled, using the most benign possible write: reading its current value back and writing that exact same value again (a no-op)."}
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

      {testMode === "read_write" && ROUNDTRIP_CHANNEL[mode] && writeStep !== "idle" && (
        <p className={`connection-test-write connection-test-${writeStep === "done" ? "pass" : writeStep === "failed" ? "fail" : "pending"}`}>
          {writeStep === "running" && `Testing write (${meta(mode, ROUNDTRIP_CHANNEL[mode]).label}, no-op round-trip)…`}
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
                This will briefly bump {meta(mode, nudgeConfirming).label}{" "}
                {(latestRef.current?.[nudgeConfirming] ?? 0) + meta(mode, nudgeConfirming).nudgeAmount > meta(mode, nudgeConfirming).nudgeMax ? "down" : "up"}{" "}
                by {meta(mode, nudgeConfirming).nudgeAmount}{unitSuffixFor(mode, nudgeConfirming)} for about{" "}
                {(meta(mode, nudgeConfirming).nudgeHoldMs / 1000).toFixed(0)} seconds (you should see/hear the{" "}
                {meta(mode, nudgeConfirming).moverLabel} respond), then set it back to exactly what it was. Only do
                this if that's fine right now.
              </p>
              <div className="event-button-row">
                <button type="button" onClick={() => runNudge(nudgeConfirming)}>
                  Confirm: nudge {meta(mode, nudgeConfirming).label}{" "}
                  {(latestRef.current?.[nudgeConfirming] ?? 0) + meta(mode, nudgeConfirming).nudgeAmount > meta(mode, nudgeConfirming).nudgeMax ? "-" : "+"}
                  {meta(mode, nudgeConfirming).nudgeAmount}{unitSuffixFor(mode, nudgeConfirming)} and back
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
              Retry restoring {meta(mode, nudgeResult.channel).label} to {nudgeResult.retryTo.toFixed(0)}{unitSuffixFor(mode, nudgeResult.channel)}
            </button>
          )}

          {/* The register write reporting "done" only proves the feedback
              register/poll echoed the commanded value back (modbus_live/
              aillio_live) -- a drive that updates its own feedback
              without the fan physically spinning would still land here.
              For tc4_live there's no feedback register at all, so this
              confirmation is the *only* evidence a write test has.
              Either way, this is the actual human confirmation, asked
              directly rather than inferred. */}
          {nudgeResult.status === "done" && visibleConfirm === null && (
            <div className="connection-test-visible-confirm">
              <p className="hint">Did the {meta(mode, nudgeResult.channel).moverLabel} actually respond?</p>
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
              {nudgeResult.channel === "fan_pct" && mode === "modbus_live" ? (
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
              ) : nudgeResult.channel === "drum_speed_pct" && mode === "modbus_live" ? (
                <p className="connection-test-write connection-test-fail">
                  ✗ Drum didn't respond either -- that's real evidence the shared register scheme (8192/8193) is
                  wrong for this unit, not just something specific to Air/slave 1. Worth checking your VFD's own
                  nameplate/front-panel parameters against those numbers.
                </p>
              ) : nudgeResult.channel === "fan_pct" && mode === "aillio_live" ? (
                <>
                  <p className="connection-test-write connection-test-fail">
                    ✗ The device acknowledged the write (no USB error) but nothing physically moved. Fan and Drum use
                    the same underlying command mechanism on this device -- Drum is a genuine differential test, not
                    just "try something else."
                  </p>
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
              ) : nudgeResult.channel === "drum_speed_pct" && mode === "aillio_live" ? (
                <p className="connection-test-write connection-test-fail">
                  ✗ Drum didn't respond either -- worth double-checking this is genuinely the roaster's own Fan/Drum
                  command (see aillio_bridge/r1.py for the confirmed opcodes) rather than a connection/firmware issue
                  specific to this unit.
                </p>
              ) : (
                <p className="connection-test-write connection-test-fail">
                  ✗ The OT1 write itself succeeded (no serial error) but nothing visibly responded. Since TC4 has no
                  feedback register at all, this either means a real wiring/SSR problem, or the{" "}
                  {meta("tc4_live", "heater_pct").nudgeAmount}% bump for{" "}
                  {(meta("tc4_live", "heater_pct").nudgeHoldMs / 1000).toFixed(0)}s just wasn't enough to notice --
                  check the board's own OT1 output directly (multimeter/scope on the pin, or the heating element
                  itself) before assuming the write path is broken.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
