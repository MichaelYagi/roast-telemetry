import { useTranslation } from "react-i18next";
import { SIM_DEVICES, isSimulatedValue } from "../simulated.js";

// Sits under a port/host field. Shown so trying a machine with no hardware is
// one visible click, not something to find in the docs: either the offer
// ("No roaster? Try a simulated FZ-94"), or -- once a simulated device is
// picked -- a plain statement that it is one, with a way back to a real port.
export default function SimulatedDeviceHint({ kind, value, onChange }) {
  const { t } = useTranslation();
  const device = SIM_DEVICES[kind];
  if (isSimulatedValue(value)) {
    return (
      <span className="simulated-hint simulated-hint-on">
        <span className="simulated-badge">{t("common.simulatedDeviceHint.simulatedBadge")}</span>
        {t("common.simulatedDeviceHint.builtInHint", { machine: device.machine })}{" "}
        <button type="button" className="link-neutral" onClick={() => onChange("")}>
          {t("common.simulatedDeviceHint.useRealOne")}
        </button>
      </span>
    );
  }
  return (
    <span className="simulated-hint">
      {t("common.simulatedDeviceHint.noMachine", { machine: device.machine })}{" "}
      <button type="button" className="link-neutral" onClick={() => onChange(device.value)}>
        {t("common.simulatedDeviceHint.trySimulated")}
      </button>
    </span>
  );
}
