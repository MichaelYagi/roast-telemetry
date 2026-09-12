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
      <div className="readout-box readout-ror">
        <span className="readout-label">&Delta; BT</span>
        <span className="readout-value">{fmt(latest?.ror_bt)}</span>
      </div>
    </div>
  );
}
