import { useEffect, useRef, useState } from "react";
import { api } from "./api/client.js";

const POLL_INTERVAL_MS = 5000;

// Powers the small status dot in the app header (App.jsx) -- global,
// not tied to any one roast/mounted screen, unlike a per-roast hook.
//
// green: server reachable and something is connected to *real* hardware
// (armed through roasting/cooling) -- mode=simulator and sim:// fake
// devices don't count, see main.py's health() for why.
// yellow: server reachable, nothing real connected.
// red: the last poll failed -- either the server process is down, or
// this browser just can't reach it (same signal either way from here).
export default function useServerStatus() {
  const [status, setStatus] = useState("yellow"); // optimistic until the first poll lands
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    async function poll() {
      try {
        const res = await api.health();
        if (!mountedRef.current) return;
        setStatus(res.roaster_connected ? "green" : "yellow");
      } catch {
        if (!mountedRef.current) return;
        setStatus("red");
      }
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, []);

  return status;
}
