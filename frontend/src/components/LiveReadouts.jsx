// The colored ET/BT/deltaBT stat boxes Artisan pins beside its scope,
// doubling as a color legend for the curves (no separate chart legend).
function fmt(value) {
  return value == null ? "- . -" : value.toFixed(1);
}

export default function LiveReadouts({ latest }) {
  return (
    <div className="readout-col">
      <div className="readout-box readout-et">
        <span className="readout-label">ET</span>
        <span className="readout-value">{fmt(latest?.et)}</span>
      </div>
      <div className="readout-box readout-bt">
        <span className="readout-label">BT</span>
        <span className="readout-value">{fmt(latest?.bt)}</span>
      </div>
      {latest?.dt != null && (
        // Only a handful of modes (the FZ-94's slave-12 probe) ever
        // populate this -- shown only once real data actually arrives,
        // rather than a permanent 4th box showing "- . -" for every
        // other mode that has no drum-space-temperature probe at all.
        <div className="readout-box readout-dt">
          <span className="readout-label">DT</span>
          <span className="readout-value">{fmt(latest?.dt)}</span>
        </div>
      )}
      <div className="readout-box readout-ror">
        <span className="readout-label">&Delta; BT</span>
        <span className="readout-value">{fmt(latest?.ror_bt)}</span>
      </div>
    </div>
  );
}
