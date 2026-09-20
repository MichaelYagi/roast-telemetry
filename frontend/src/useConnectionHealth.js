import { useEffect, useRef, useState } from "react";
import { celsiusToUnit, unitSuffix } from "./tempUnits.js";
import { analyzeReadSamples, channelsForMode } from "./connectionPlausibility.js";

// Enough samples to judge "steady vs varies" without waiting on a fixed
// timer -- capped so a long-running roast doesn't keep growing this
// buffer forever.
const MAX_BUFFERED_SAMPLES = 10;

// Powers the small connection-status dot next to the roast title in
// ArtisanToolbar -- continuously live for as long as a live-hardware
// connection is relevant (armed through roasting/cooling), not just a
// one-shot pre-roast check. Same underlying plausibility bounds as
// ConnectionTestPanel.jsx's manual "Run read-only test" (shared via
// connectionPlausibility.js), just evaluated continuously instead of
// only when someone clicks a button.
//
// Bounds are always Celsius (see analyzeReadSamples' own comment) --
// this only ever returns pass/fail/checking, never display text, so no
// tempUnit input is needed here at all.
export default function useConnectionHealth(roastId, latest, mode) {
  const samplesRef = useRef([]);
  const [status, setStatus] = useState("checking"); // "checking" | "pass" | "fail"
  const [failedLabels, setFailedLabels] = useState([]);

  // A fresh connection (even to the same mode) should judge only its own
  // readings, not ones left over from whatever was armed before it.
  useEffect(() => {
    samplesRef.current = [];
    setStatus("checking");
    setFailedLabels([]);
  }, [roastId]);

  useEffect(() => {
    if (!latest) return;
    samplesRef.current = [...samplesRef.current, latest].slice(-MAX_BUFFERED_SAMPLES);
    const results = analyzeReadSamples(samplesRef.current, channelsForMode(mode), "c", { celsiusToUnit, unitSuffix });
    const failed = results.filter((r) => r.status === "fail");
    const passed = results.filter((r) => r.status === "pass");
    if (failed.length > 0) {
      setStatus("fail");
      setFailedLabels(failed.map((r) => r.label));
    } else if (passed.length > 0) {
      setStatus("pass");
      setFailedLabels([]);
    }
    // Every channel still "warn" (no data at all yet) -- stay "checking"
    // rather than guessing either way.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest]);

  return { status, failedLabels };
}
