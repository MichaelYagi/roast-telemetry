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
  // the chart showing no curve pre-recording), so
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
  // Transient in-app banners from a fired rule's optional `message` (see
  // AlarmRulesEditor.jsx) -- separate from pendingAlarms above, which
  // tracks the pre-fire "still waiting" state, not the post-fire
  // announcement. LiveRoastView.jsx is responsible for auto-dismissing
  // these after a few seconds; this hook just appends/lets them be removed.
  const [alarmNotifications, setAlarmNotifications] = useState([]);
  const wsRef = useRef(null);

  useEffect(() => {
    if (!roastId) return undefined;
    setConnectionStatus("connecting");
    setLatestPreview(null);
    setLastError(null);
    setPendingAlarms([]);
    setAlarmNotifications([]);
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
            // Without this, roast.status only ever got set once, from the
            // initial "snapshot" message at WS-connect time -- always
            // "idle" for modbus_live/ms6514_live, since connect() happens
            // before START. Nothing else ever updated it afterward, so the
            // UI stayed stuck showing "idle" forever after a real START
            // (elapsed time frozen at 0:00, every milestone button
            // permanently disabled) even though the backend had genuinely
            // begun recording -- the chart looked fine since it reads
            // roast.profile directly, which *was* filling in correctly.
            status: message.status || prev.status,
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
      } else if (message.type === "event_deleted") {
        setRoast((prev) => (prev ? { ...prev, events: prev.events.filter((e) => e.id !== message.event_id) } : prev));
      } else if (message.type === "event_updated") {
        setRoast((prev) =>
          prev ? { ...prev, events: prev.events.map((e) => (e.id === message.event.id ? message.event : e)) } : prev
        );
      } else if (message.type === "error") {
        setLastError(message.message || "unknown error");
      } else if (message.type === "alarm_scheduled") {
        setPendingAlarms((prev) => [
          ...prev,
          { ruleId: message.rule_id, trigger: message.trigger, delaySeconds: message.delay_s, scheduledAt: Date.now() },
        ]);
      } else if (message.type === "alarm_fired") {
        setPendingAlarms((prev) => prev.filter((a) => a.ruleId !== message.rule_id));
        if (message.message) {
          const id = `${message.rule_id}-${Date.now()}`;
          setAlarmNotifications((prev) => [...prev, { id, text: message.message }]);
          // Self-dismissing -- scheduled once, right here, rather than a
          // separate effect trying to track "which notifications already
          // have a pending timer" across re-renders.
          setTimeout(() => setAlarmNotifications((prev) => prev.filter((n) => n.id !== id)), 8000);
        }
      }
    };

    return () => ws.close();
  }, [roastId]);

  function dismissAlarmNotification(id) {
    setAlarmNotifications((prev) => prev.filter((n) => n.id !== id));
  }

  return { roast, connectionStatus, latestPreview, lastError, pendingAlarms, alarmNotifications, dismissAlarmNotification, setRoast };
}
