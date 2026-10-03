import { useTranslation } from "react-i18next";

// Sits under a serial-port <input list="...">. The <datalist> itself still
// carries each <option>'s label (the driver's description, e.g. "USB-SERIAL
// CH340") for browsers that show it -- but Chrome/Edge don't render
// <option label> in a datalist's suggestion popup at all, only `value`, a
// long-standing engine limitation, not something fixable from this app's
// side. Confirmed live: on Chrome, the dropdown showed bare "COM3"/"COM4"
// with zero way to tell which one is the actual roaster, forcing a guess
// every time the OS happens to enumerate them in a different order. This is
// the same port list rendered as plain, always-visible, clickable text
// instead -- works identically on every browser since it doesn't depend on
// datalist rendering at all.
export default function SerialPortHints({ ports, onPick }) {
  const { t } = useTranslation();
  const real = ports.filter((p) => !p.simulated);
  if (!real.length) return null;
  return (
    <div className="serial-port-hints">
      <span className="hint">{t("liveRoast.detectedPorts")}</span>
      {real.map((p) => (
        <button key={p.device} type="button" className="serial-port-hint-chip" onClick={() => onPick(p.device)}>
          {p.description ? `${p.device} — ${p.description}` : p.device}
        </button>
      ))}
    </div>
  );
}
