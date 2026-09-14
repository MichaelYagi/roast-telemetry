import { useEffect, useRef, useState } from "react";
import { roastStreamUrl } from "./client.js";

/**
 * Subscribes to a roast's live WebSocket stream and keeps an
 * incrementally-updated { profile, events, notes, status } object plus a
 * connection-status string ("connecting" | "open" | "closed" | "error").
 */
export function useRoastStream(roastId) {
  const [roast, setRoast] = useState(null);
  const [connectionStatus, setConnectionStatus] = useState("connecting");
  // Live readings while connected but not yet recording (roast.status ===
  // "idle" -- see RoastSession.connect() in the backend): the server
  // deliberately never appends these to roast.profile (that's what keeps
  // the chart showing no curve pre-recording, matching Artisan), so
  // there's nothing in `roast` itself for a caller to read the latest
  // BT/ET/etc. from during that window -- this is that value instead.
  const [latestPreview, setLatestPreview] = useState(null);
  // The most recent server-side error (a failed read/tick, or anything
  // else the backend's _run_loop published as type:"error") -- was
  // silently dropped before (no "error" case existed here at all), which
  // meant a real connection fault had no way to reach the UI.
  const [lastError, setLastError] = useState(null);
  // Delayed milestone-triggered automations (AlarmRule with delay_s > 0)
  // that have been scheduled but haven't fired yet -- see
  // RoastSession._fire_delayed_alarm's alarm_scheduled/alarm_fired
  // messages. Keyed by rule_id so a fired one can be removed by id
  // rather than assuming array order.
  const [pendingAlarms, setPendingAlarms] = useState([]);
  const wsRef = useRef(null);

  useEffect(() => {
    if (!roastId) return undefined;
    setConnectionStatus("connecting");
    setLatestPreview(null);
    setLastError(null);
    setPendingAlarms([]);
    const ws = new WebSocket(roastStreamUrl(roastId));
    wsRef.current = ws;

    ws.onopen = () => setConnectionStatus("open");
    ws.onclose = () => setConnectionStatus("closed");
    ws.onerror = () => setConnectionStatus("error");

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "snapshot") {
        setRoast(message.roast);
      } else if (message.type === "preview") {
        setLatestPreview(message.sample);
      } else if (message.type === "sample") {
        setRoast((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            profile: [...prev.profile, message.sample],
            events: [...prev.events, ...(message.events || [])],
          };
        });
      } else if (message.type === "finished") {
        setRoast((prev) => (prev ? { ...prev, status: message.status } : prev));
      } else if (message.type === "note") {
        setRoast((prev) => (prev ? { ...prev, notes: [...prev.notes, message.note] } : prev));
      } else if (message.type === "event") {
        setRoast((prev) => (prev ? { ...prev, events: [...prev.events, message.event] } : prev));
      } else if (message.type === "error") {
        setLastError(message.message || "unknown error");
      } else if (message.type === "alarm_scheduled") {
        setPendingAlarms((prev) => [
          ...prev,
          { ruleId: message.rule_id, trigger: message.trigger, delaySeconds: message.delay_s, scheduledAt: Date.now() },
        ]);
      } else if (message.type === "alarm_fired") {
        setPendingAlarms((prev) => prev.filter((a) => a.ruleId !== message.rule_id));
      }
    };

    return () => ws.close();
  }, [roastId]);

  return { roast, connectionStatus, latestPreview, lastError, pendingAlarms, setRoast };
}
