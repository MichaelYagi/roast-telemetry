// Persistent, post-hoc indicators for a roast's own record (History, roast
// detail) -- distinct from the live-only "Stopped for safety: ..." banner
// (RoastToolbar.jsx's tripped_reason), which clears once the session ends.

export function isEmergencyStopped(roast) {
  return Boolean(roast?.had_emergency_stop);
}

// reached_drop is tri-state: null/undefined means "unknown" (every roast
// recorded before this field existed, or one still in progress) -- only an
// explicit false, on a roast that's actually over, counts as ended early.
// Never flag an active roast just because Drop hasn't happened *yet*.
export function endedBeforeDrop(roast) {
  return (roast?.status === "stopped" || roast?.status === "aborted") && roast?.reached_drop === false;
}
