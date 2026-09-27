// Plain-language expansions for the abbreviated labels shown throughout
// the app (chart toggles, readout panels, control sliders, Connection
// Test rows) -- shown as a native hover tooltip wherever that exact label
// text appears. See docs/glossary.html for the fuller explanations these
// are trimmed down from; keep the two in sync when adding a term to one.
export const TERM_TOOLTIPS = {
  BT: "Bean Temperature -- how hot the beans themselves are",
  ET: "Environmental/Exhaust Temperature -- how hot the air around the beans is",
  DT: "Drum Temperature -- a separate probe reading the drum chamber's air, not the beans",
  "RoR (BT)": "Rate of Rise -- how fast Bean Temperature is climbing, in °/min",
  "RoR (ET)": "Rate of Rise -- how fast Exhaust Temperature is climbing, in °/min",
  "ΔBT (RoR)": "Rate of Rise -- how fast Bean Temperature is climbing, in °/min",
  "ΔET (RoR)": "Rate of Rise -- how fast Exhaust Temperature is climbing, in °/min",
  Burner: "Heat source control",
  "Burner %": "Heat source control, as a 0-100% slider",
  SV: "Setpoint Value -- the target temperature the Burner's own controller is holding",
  "Burner SV": "Setpoint Value -- the target temperature the Burner's own controller is holding",
  Fan: "Fan/airflow control, in RPM (not a percentage, despite the fan_pct field name)",
  "Fan RPM": "Fan/airflow control, in RPM (not a percentage, despite the fan_pct field name)",
  Drum: "Drum rotation speed control, in RPM (not a percentage, despite the drum_speed_pct field name)",
  "Drum RPM": "Drum rotation speed control, in RPM (not a percentage, despite the drum_speed_pct field name)",
  Damper: "Airflow-restricting flap, on roasters that have one",
  "DRY%": "Percent of the roast spent in the drying phase (Charge to Dry End)",
  "Maillard%": "Percent of the roast spent in the browning phase (Dry End to First Crack Start)",
  "DEV%": "Percent of the roast spent in the development phase (First Crack Start to Drop)",
  "DEV TIME": "How long the development phase (First Crack Start to Drop) has lasted",
  "»DRY": "Estimated time remaining until Dry End",
  "»FCs": "Estimated time remaining until First Crack Start",
  "FC START": "First Crack Start",
  "FC END": "First Crack End",
  "SC START": "Second Crack Start",
  "SC END": "Second Crack End",
};
