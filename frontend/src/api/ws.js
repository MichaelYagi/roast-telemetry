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
  const wsRef = useRef(null);

  useEffect(() => {
    if (!roastId) return undefined;
    setConnectionStatus("connecting");
    const ws = new WebSocket(roastStreamUrl(roastId));
    wsRef.current = ws;

    ws.onopen = () => setConnectionStatus("open");
    ws.onclose = () => setConnectionStatus("closed");
    ws.onerror = () => setConnectionStatus("error");

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "snapshot") {
        setRoast(message.roast);
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
      }
    };

    return () => ws.close();
  }, [roastId]);

  return { roast, connectionStatus, setRoast };
}
