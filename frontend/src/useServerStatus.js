import { useEffect, useRef, useState } from "react";
import { api } from "./api/client.js";

const POLL_INTERVAL_MS = 5000;

// Powers the small status dot in the app header (App.jsx) -- global,
// not tied to any one roast/mounted screen, unlike a per-roast hook.
//
// status -- the dot's color:
// green: server reachable and something is connected to *real* hardware
// (armed through roasting/cooling) -- mode=simulator and sim:// fake
// devices don't count, see main.py's health() for why.
// yellow: server reachable, nothing real connected.
// red: the last poll failed -- either the server process is down, or
// this browser just can't reach it (same signal either way from here).
//
// activeRoast -- { id, title } | null, powering the "Live Roast" nav
// badge and the browser tab title, from any other page in the app.
// Unlike `status`, this DOES include simulator/sim:// roasts -- the
// question here is just "is a roast actually recording," not "is a real
// roaster connected" -- see main.py's health() for the same reasoning.
//
// platform/osVersion/lanIp -- the server's own OS name, OS version, and
// LAN-reachable address (see main.py's _server_platform/_server_os_version/
// _server_lan_ip) -- used by the footer and the Live Roast page's own
// connection details. Riding along on this same poll instead of a
// separate one-shot fetch, even though these rarely change mid-session.
export default function useServerStatus() {
  const [status, setStatus] = useState("yellow"); // optimistic until the first poll lands
  const [activeRoast, setActiveRoast] = useState(null);
  const [serverInfo, setServerInfo] = useState({ platform: null, osVersion: null, lanIp: null });
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    async function poll() {
      try {
        const res = await api.health();
        if (!mountedRef.current) return;
        setStatus(res.roaster_connected ? "green" : "yellow");
        setActiveRoast(res.active_roast || null);
        setServerInfo({ platform: res.platform || null, osVersion: res.os_version || null, lanIp: res.lan_ip || null });
      } catch {
        if (!mountedRef.current) return;
        setStatus("red");
        setActiveRoast(null);
        // serverInfo deliberately NOT cleared here -- a transient failed
        // poll doesn't mean the OS/version/IP just changed, so the last
        // known-good values staying put (rather than flashing blank) is
        // the less jarring choice for a footer/detail-page display.
      }
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    // Browsers throttle setInterval in background/hidden tabs -- Safari
    // on macOS noticeably more aggressively than Chrome/Edge on Windows
    // (confirmed: the same build showed the nav badge/tab title
    // immediately on Windows but not on a backgrounded Mac tab). Forcing
    // a poll on every visibility change means the moment you switch back
    // to check, it refreshes right then instead of waiting on whatever's
    // left of a possibly-stalled interval.
    function onVisibilityChange() {
      if (!document.hidden) poll();
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return { status, activeRoast, ...serverInfo };
}
