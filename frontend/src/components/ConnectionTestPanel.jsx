import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";
import { TERM_TOOLTIPS } from "../termTooltips.js";
import { analyzeReadSamples as analyzeReadSamplesShared, channelsForMode } from "../connectionPlausibility.js";

const SAMPLE_WINDOW_MS = 4000;
const SAMPLE_INTERVAL_MS = 250; // just samples the already-live `latest` prop -- no extra network calls

// Per-(mode, channel) metadata for whichever channel(s) that mode's
// write-test/nudge flow actually uses. fan_pct/drum_speed_pct mean
// genuinely different things depending on which device is on the other
// end -- Modbus's Fan/Drum are real RPM-reporting VFD drives (register
// writes, see modbus_bridge/engine.py's own docstring: same registers
// 8192/8193/8451, same blog-sourced-only origin, just different slave
// IDs -- Drum is a genuine differential test if Fan's nudge doesn't
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
    fan_pct: { label: "Fan", unit: "RPM", nudgeMax: 100, nudgeAmount: 5, nudgeHoldMs: 2000, moverLabel: "fan" },
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
// device (Fan's VFD feedback register / AillioEngine.tick()'s _poll())
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
  const { t } = useTranslation();
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

  // ms6514 is a read-only thermocouple meter (no Burner/Fan/Drum, no
  // apply_command effect at all -- see ms6514_bridge/engine.py) -- nothing
  // to write there. modbus_live has Fan/Drum VFD registers plus a Burner
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
      setWriteDetail(t("common.connectionTestPanel.noReading", { label }));
      return;
    }
    try {
      // The benign write: read the channel's own current value, write
      // that exact same value straight back. On an idle machine (Fan
      // almost always already off) this writes "off" over "off" --
      // genuinely zero physical effect, while still exercising the real
      // write path (command encoding, the connection, the device's own
      // acknowledgement) end to end. More benign than an on/off toggle.
      await api.sendCommand(roastId, { [channel]: before });
      await new Promise((resolve) => setTimeout(resolve, 1500)); // let the device report back
      const after = latestRef.current?.[channel];
      if (after != null && Math.abs(after - before) > 1) {
        setWriteStep("failed");
        setWriteDetail(
          t("common.connectionTestPanel.writeMismatch", { before: before.toFixed(0), after: after.toFixed(0), suffix })
        );
        return;
      }
      setWriteStep("done");
      setWriteDetail(t("common.connectionTestPanel.writeConfirmed", { label, before: before.toFixed(0), suffix }));
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
      detail: t("common.connectionTestPanel.nudging", {
        label,
        direction: direction > 0 ? t("common.connectionTestPanel.up") : t("common.connectionTestPanel.down"),
        value: nudged.toFixed(0),
        suffix: unitSuffixFor(mode, channel),
      }),
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
      setNudgeResult({ channel, status: "failed", detail: t("common.connectionTestPanel.nudgeFailed", { message: err.message }) });
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, meta(mode, channel).nudgeHoldMs));
    await restoreChannelTo(channel, before);
  }

  async function restoreChannelTo(channel, before) {
    const { label } = meta(mode, channel);
    try {
      await api.sendCommand(roastId, { [channel]: before });
      setNudgeResult({
        channel,
        status: "done",
        detail: t("common.connectionTestPanel.restoredTo", { value: before.toFixed(0), suffix: unitSuffixFor(mode, channel) }),
      });
    } catch (err) {
      setNudgeResult({
        channel,
        status: "failed",
        detail: t("common.connectionTestPanel.restoreFailed", {
          label,
          value: before.toFixed(0),
          suffix: unitSuffixFor(mode, channel),
          message: err.message,
        }),
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
      <h3>{t("common.connectionTestPanel.heading")}</h3>
      {simulated && (
        <p className="simulated-note">
          <span className="simulated-badge">{t("common.connectionTestPanel.simulatedBadge")}</span>
          {t("common.connectionTestPanel.simulatedNote")}
        </p>
      )}
      <p className="hint">
        {t("common.connectionTestPanel.intro")}
        {canWrite && mode === "modbus_live" && t("common.connectionTestPanel.introModbus")}
        {canWrite && mode === "aillio_live" && t("common.connectionTestPanel.introAillio")}
        {canWrite && mode === "tc4_live" && t("common.connectionTestPanel.introTc4")}
      </p>
      <div className="event-button-row">
        <button type="button" onClick={() => startTest("read")} disabled={running}>
          {running && testMode === "read" ? t("common.connectionTestPanel.testing") : t("common.connectionTestPanel.runReadOnly")}
        </button>
        {canWrite && (
          <button type="button" onClick={() => startTest("read_write")} disabled={running}>
            {running && testMode === "read_write" ? t("common.connectionTestPanel.testing") : t("common.connectionTestPanel.runReadWrite")}
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
          {writeStep === "running" &&
            t("common.connectionTestPanel.testingWrite", { label: meta(mode, ROUNDTRIP_CHANNEL[mode]).label })}
          {writeStep === "done" && t("common.connectionTestPanel.writePassed", { detail: writeDetail })}
          {writeStep === "failed" && t("common.connectionTestPanel.writeFailed", { detail: writeDetail })}
        </p>
      )}
      {testMode === "read_write" && mode === "tc4_live" && writeStep === "skipped" && (
        <p className="hint">{t("common.connectionTestPanel.tc4SkippedHint")}</p>
      )}

      {testMode === "read_write" && (writeStep === "done" || writeStep === "skipped") && !nudgeResult && (
        <div className="connection-test-nudge">
          {!nudgeConfirming ? (
            <button type="button" className="advanced-toggle" onClick={() => setNudgeConfirming(defaultNudgeChannel)}>
              {mode === "tc4_live"
                ? t("common.connectionTestPanel.confirmHeaterResponds")
                : t("common.connectionTestPanel.wantVisibleConfirmation")}
            </button>
          ) : (
            <>
              <p className="hint">
                {t("common.connectionTestPanel.nudgeExplain", {
                  label: meta(mode, nudgeConfirming).label,
                  direction:
                    (latestRef.current?.[nudgeConfirming] ?? 0) + meta(mode, nudgeConfirming).nudgeAmount > meta(mode, nudgeConfirming).nudgeMax
                      ? t("common.connectionTestPanel.down")
                      : t("common.connectionTestPanel.up"),
                  amount: meta(mode, nudgeConfirming).nudgeAmount,
                  suffix: unitSuffixFor(mode, nudgeConfirming),
                  seconds: (meta(mode, nudgeConfirming).nudgeHoldMs / 1000).toFixed(0),
                  mover: meta(mode, nudgeConfirming).moverLabel,
                })}
              </p>
              <div className="event-button-row">
                <button type="button" onClick={() => runNudge(nudgeConfirming)}>
                  {t("common.connectionTestPanel.confirmNudge", {
                    label: meta(mode, nudgeConfirming).label,
                    sign:
                      (latestRef.current?.[nudgeConfirming] ?? 0) + meta(mode, nudgeConfirming).nudgeAmount > meta(mode, nudgeConfirming).nudgeMax
                        ? "-"
                        : "+",
                    amount: meta(mode, nudgeConfirming).nudgeAmount,
                    suffix: unitSuffixFor(mode, nudgeConfirming),
                  })}
                </button>
                <button type="button" className="danger" onClick={() => setNudgeConfirming(null)}>
                  {t("common.connectionTestPanel.cancel")}
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
              {t("common.connectionTestPanel.retryRestoring", {
                label: meta(mode, nudgeResult.channel).label,
                value: nudgeResult.retryTo.toFixed(0),
                suffix: unitSuffixFor(mode, nudgeResult.channel),
              })}
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
              <p className="hint">
                {t("common.connectionTestPanel.didItRespond", { mover: meta(mode, nudgeResult.channel).moverLabel })}
              </p>
              <div className="event-button-row">
                <button type="button" onClick={() => setVisibleConfirm("yes")}>
                  {t("common.connectionTestPanel.yesResponded")}
                </button>
                <button type="button" className="danger" onClick={() => setVisibleConfirm("no")}>
                  {t("common.connectionTestPanel.noNothingHappened")}
                </button>
              </div>
            </div>
          )}

          {nudgeResult.status === "done" && visibleConfirm === "yes" && (
            <p className="connection-test-write connection-test-pass">
              {t("common.connectionTestPanel.confirmedGeneric", {
                suffix: nudgeResult.channel === "heater_pct" ? "" : t("common.connectionTestPanel.notJustFeedback"),
              })}
            </p>
          )}

          {nudgeResult.status === "done" && visibleConfirm === "no" && (
            <div className="connection-test-visible-confirm-no">
              {nudgeResult.channel === "fan_pct" && mode === "modbus_live" ? (
                <>
                  <p className="connection-test-write connection-test-fail">
                    {t("common.connectionTestPanel.modbusAirFailed")}
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
                    {t("common.connectionTestPanel.tryDrumInstead")}
                  </button>
                </>
              ) : nudgeResult.channel === "drum_speed_pct" && mode === "modbus_live" ? (
                <p className="connection-test-write connection-test-fail">
                  {t("common.connectionTestPanel.modbusDrumFailed")}
                </p>
              ) : nudgeResult.channel === "fan_pct" && mode === "aillio_live" ? (
                <>
                  <p className="connection-test-write connection-test-fail">
                    {t("common.connectionTestPanel.aillioFanFailed")}
                  </p>
                  <button
                    type="button"
                    className="advanced-toggle"
                    onClick={() => {
                      setNudgeResult(null);
                      setNudgeConfirming("drum_speed_pct");
                    }}
                  >
                    {t("common.connectionTestPanel.tryDrumInstead")}
                  </button>
                </>
              ) : nudgeResult.channel === "drum_speed_pct" && mode === "aillio_live" ? (
                <p className="connection-test-write connection-test-fail">
                  {t("common.connectionTestPanel.aillioDrumFailed")}
                </p>
              ) : (
                <p className="connection-test-write connection-test-fail">
                  {t("common.connectionTestPanel.tc4Failed", {
                    pct: meta("tc4_live", "heater_pct").nudgeAmount,
                    seconds: (meta("tc4_live", "heater_pct").nudgeHoldMs / 1000).toFixed(0),
                  })}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
