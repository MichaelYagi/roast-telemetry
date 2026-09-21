import { SIM_DEVICES, isSimulatedValue } from "../simulated.js";

// Sits under a port/host field. Shown so trying a machine with no hardware is
// one visible click, not something to find in the docs: either the offer
// ("No roaster? Try a simulated FZ-94"), or -- once a simulated device is
// picked -- a plain statement that it is one, with a way back to a real port.
export default function SimulatedDeviceHint({ kind, value, onChange }) {
  const device = SIM_DEVICES[kind];
  if (isSimulatedValue(value)) {
    return (
      <span className="simulated-hint simulated-hint-on">
        <span className="simulated-badge">Simulated</span>
        A built-in {device.machine} -- not a real machine. Nothing is sent to hardware.{" "}
        <button type="button" className="link-neutral" onClick={() => onChange("")}>
          Use a real one instead
        </button>
      </span>
    );
  }
  return (
    <span className="simulated-hint">
      No {device.machine}?{" "}
      <button type="button" className="link-neutral" onClick={() => onChange(device.value)}>
        Try a simulated one
      </button>
    </span>
  );
}
