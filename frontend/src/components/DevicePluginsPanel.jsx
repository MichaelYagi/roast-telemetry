import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { useConfirm } from "./DialogProvider.jsx";

// Settings > Device plugins -- third-party protocol plugins dropped into
// device_plugins/installed/ (see device_plugins/README.md). The checkbox is
// a reversible visibility toggle (hides it from the New Roast form without
// touching the file or restarting the server); Delete is permanent --
// removes it from the running server immediately and deletes its file, so
// it's gone even from this list until someone drops the file back in. An
// empty list (the common case -- nothing ships installed by default) means
// this whole panel renders nothing, same as the New Roast form's own
// "Plugin device" option only appearing once one exists.
export default function DevicePluginsPanel() {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [plugins, setPlugins] = useState([]);
  const [error, setError] = useState(null);

  function load() {
    api.listDevicePlugins().then(setPlugins).catch((err) => setError(err.message));
  }
  useEffect(load, []);

  async function toggleEnabled(plugin) {
    setError(null);
    try {
      await api.setDevicePluginEnabled(plugin.kind, !plugin.enabled);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDelete(plugin) {
    if (!(await confirm(t("settings.devicePlugins.confirmDelete", { label: plugin.label })))) return;
    setError(null);
    try {
      await api.deleteDevicePlugin(plugin.kind);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  if (plugins.length === 0) return null;

  return (
    <div className="panel">
      <h2>{t("settings.devicePlugins.heading")}</h2>
      <p className="hint">{t("settings.devicePlugins.hint")}</p>
      {error && <p className="error">{error}</p>}
      <ul className="kv-list">
        {plugins.map((p) => (
          <li key={p.kind}>
            <span>
              <label className="checkbox-label">
                <input type="checkbox" checked={p.enabled} onChange={() => toggleEnabled(p)} />
                {p.label}
              </label>
            </span>
            <span>
              <span className="hint">{p.kind}</span>{" "}
              <button type="button" onClick={() => handleDelete(p)}>
                {t("settings.devicePlugins.delete")}
              </button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
