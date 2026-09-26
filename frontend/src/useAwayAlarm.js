import { useEffect, useRef } from "react";

const BEEP_INTERVAL_MS = 5000; // a nudge every 5s -- 12s felt like too long a gap
const BEEP_DURATION_S = 0.5;
const BEEP_FREQUENCY_HZ = 660; // E5 -- a softer sine chime, not the old harsh square wave
const BEEP_GAIN = 0.12; // quiet -- meant to be noticed, not startling

// Reminds with a repeating, gentle chime whenever the tab is hidden
// (minimized, or switched away from) while `active` -- a roast is
// actually roasting, heat genuinely being applied. Distinct from the
// server-side "no viewer" watchdog (Settings > Roaster safety,
// client_watchdog_s): that one only fires once the live connection
// itself actually drops (a crashed/closed tab, after however many
// seconds it's set to). This one fires immediately on switching away,
// even with the tab -- and its connection -- still very much alive.
// They catch different failure modes, not the same one twice.
// Settings > Roaster safety can turn this one off entirely (see
// away_alarm_enabled, threaded through from LiveRoastView.jsx) -- this
// hook itself doesn't know or care why `active` might be false.
//
// Synthesized with the Web Audio API rather than an embedded sound
// file -- no asset to source/license, and it repeats for as long as the
// tab stays hidden, not a single easy-to-miss beep.
export default function useAwayAlarm(active) {
  const audioCtxRef = useRef(null);
  const intervalRef = useRef(null);

  function beep() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!audioCtxRef.current) audioCtxRef.current = new Ctx();
    const ctx = audioCtxRef.current;
    // Autoplay policies suspend a freshly-created context until a user
    // gesture resumes it -- by the time a roast is actually roasting,
    // the operator has already clicked ON/START, which counts.
    if (ctx.state === "suspended") ctx.resume().catch(() => {});

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine"; // soft chime, not an alarm tone
    osc.frequency.value = BEEP_FREQUENCY_HZ;
    // Ramped rather than a hard on/off -- an instant jump to full gain
    // is what makes a tone read as a "beep"; fading it in and back out
    // is what makes the same tone read as a chime.
    const now = ctx.currentTime;
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(BEEP_GAIN, now + 0.08);
    gain.gain.linearRampToValueAtTime(0, now + BEEP_DURATION_S);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + BEEP_DURATION_S);
  }

  useEffect(() => {
    function stopAlarm() {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    }

    // Also called right away below, not just from the event listener --
    // `active` can flip true (e.g. pressing START) while the tab happens
    // to already be hidden for some other reason, and that case has no
    // visibilitychange event of its own to react to; without this check
    // up front the alarm would silently never arm until the next time
    // the tab's visibility happens to change again, which might be never.
    function syncAlarmToCurrentVisibility() {
      if (!active || !document.hidden) {
        stopAlarm();
        return;
      }
      if (!intervalRef.current) {
        beep(); // immediately, not just on the first interval tick
        intervalRef.current = setInterval(beep, BEEP_INTERVAL_MS);
      }
    }

    syncAlarmToCurrentVisibility();
    document.addEventListener("visibilitychange", syncAlarmToCurrentVisibility);
    return () => {
      document.removeEventListener("visibilitychange", syncAlarmToCurrentVisibility);
      stopAlarm();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
}
