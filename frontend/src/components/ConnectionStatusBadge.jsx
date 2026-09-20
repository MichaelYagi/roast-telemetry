import { useEffect, useRef, useState } from "react";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";
import { analyzeReadSamples, channelsForMode } from "../connectionPlausibility.js";

// Enough samples to judge "steady vs varies" without waiting on a fixed
// timer -- capped so a connection left armed for a long time doesn't
// keep growing this buffer forever.
const MAX_BUFFERED_SAMPLES = 10;

// Ambient, automatic counterpart to ConnectionTestPanel's own manual
// "Run read-only test" button -- shown the instant a connection goes
// "armed" (see LiveRoastView's own phase/LIVE_MODES gate around where
// this is rendered), no button to remember to click. Exists specifically
// to close the gap where you could click ON then START without ever
// finding out the wrong preset/device was selected -- see this session's
// own "is there a check for connecting to the wrong machine" discussion.
//
// Optimized for speed over thoroughness, deliberately not a smaller copy
// of the manual test: evaluates on every new `latest` sample as it
// streams in (the live tick rate roasts already run at, ~1/sec by
// default -- see RoastCreateRequest.sample_interval_s), not a fixed
// multi-second collection window. A verdict is visible after the very
// first reading; it only gets more confident (steady-vs-varies) as more
// samples arrive. Same plausibility bounds as the manual test (shared
// via connectionPlausibility.js) -- this is a faster front door onto the
// same check, not a different, looser one.
export default function ConnectionStatusBadge({ roastId, latest, mode, tempUnit = "c" }) {
  const samplesRef = useRef([]);
  const [results, setResults] = useState(null);

  // A fresh connection (even to the same mode) should judge only its own
  // readings, not ones left over from whatever was armed before it.
  useEffect(() => {
    samplesRef.current = [];
    setResults(null);
  }, [roastId]);

  useEffect(() => {
    if (!latest) return;
    samplesRef.current = [...samplesRef.current, latest].slice(-MAX_BUFFERED_SAMPLES);
    setResults(analyzeReadSamples(samplesRef.current, channelsForMode(mode), tempUnit, { celsiusToUnit, unitSuffix }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest]);

  if (!results) {
    return <p className="hint connection-status-badge">Checking connection…</p>;
  }

  const failed = results.filter((r) => r.status === "fail");
  const passed = results.filter((r) => r.status === "pass");

  if (failed.length > 0) {
    return (
      <p className="connection-test-write connection-test-fail connection-status-badge">
        ✗ {failed.map((r) => r.label).join(", ")} {failed.length === 1 ? "looks" : "look"} implausible -- double-check
        the selected preset/port before starting. See Test Connection below for detail.
      </p>
    );
  }
  // Every channel came back "warn" (no data at all so far) -- not itself
  // a failure (a channel can be legitimately unconfigured, e.g. DT), but
  // there's nothing to call plausible yet either.
  if (passed.length === 0) {
    return <p className="hint connection-status-badge">Waiting for a reading…</p>;
  }
  // Deliberately not listing every passing channel by name here -- fine
  // (even useful) for the failure case above, where it says exactly
  // what's wrong, but just noise once everything's fine ("BT, ET, DT,
  // Burner SV, Air RPM, Drum RPM look plausible" helps nobody). Anyone
  // who wants the per-channel breakdown has Test Connection right below.
  return (
    <p className="connection-test-write connection-test-pass connection-status-badge">
      ✓ Readings look plausible
    </p>
  );
}
