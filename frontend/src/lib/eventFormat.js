// What to show in brackets after an event on a roast's page.
//  - an event tied to a control (Fan, Burner...): "(Burner: 60)"
//  - a plain marker with a number but no control ("--" in the log file): "(value 18.5)"
//  - anything else with a number is a bean temperature: "(204.4°F)"
export function formatEventValue(ev, formatTemp, tempUnit) {
  if (ev.value == null) return "";
  const raw = Number.isInteger(ev.value) ? ev.value : Math.round(ev.value * 100) / 100;
  if (ev.channel && ev.channel !== "--") return ` (${ev.channel}: ${raw})`;
  if (ev.channel === "--") return ` (value ${raw})`;
  return ` (${formatTemp(ev.value, tempUnit)})`;
}
