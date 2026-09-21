// Simulated devices: a "sim://<kind>" value in a device's port or host field
// makes the server run a built-in fake of that machine (see
// hardware_fakes/sim.py), so it can be tried with no hardware. Roasts recorded
// against one get the "simulated" tag automatically.

export const SIM_PREFIX = "sim://";
export const SIMULATED_TAG = "simulated";

export const SIM_DEVICES = {
  fz94: { value: `${SIM_PREFIX}fz94`, machine: "FZ-94" },
  fz94_evo: { value: `${SIM_PREFIX}fz94_evo`, machine: "FZ-94 Evo" },
  ms6514: { value: `${SIM_PREFIX}ms6514`, machine: "MS6514 meter" },
  tc4: { value: `${SIM_PREFIX}tc4`, machine: "TC4+" },
};

export function isSimulatedValue(value) {
  return typeof value === "string" && value.startsWith(SIM_PREFIX);
}

// A saved roast (History, roast detail) -- by its automatic tag.
export function isSimulatedRoast(roast) {
  return Array.isArray(roast?.tags) && roast.tags.includes(SIMULATED_TAG);
}

// The Configure Roast form, before anything is connected.
export function isSimulatedForm(form) {
  return [form.modbus_port, form.modbus_host, form.ms6514_port, form.tc4_port].some(isSimulatedValue);
}
