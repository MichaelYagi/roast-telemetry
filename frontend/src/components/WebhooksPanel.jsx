import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { useConfirm } from "./DialogProvider.jsx";

// Settings > Webhooks -- outbound HTTP POSTs on roast events (Charge,
// Drop, an emergency stop, ...), to ntfy/Discord/Slack/Home Assistant/a
// script of your own. See backend/app/webhooks.py for the dispatch logic
// and the exact payload shape; this just manages the saved list
// (immediate CRUD, like DeviceProfileEditor.jsx, not bundled into the
// page's own big Save button).
export default function WebhooksPanel() {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [webhooks, setWebhooks] = useState([]);
  const [events, setEvents] = useState({});
  const [error, setError] = useState(null);

  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [selectedEvents, setSelectedEvents] = useState([]);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [editingId, setEditingId] = useState(null);

  function load() {
    api.listWebhooks().then(setWebhooks).catch((err) => setError(err.message));
    api.listWebhookEvents().then(setEvents).catch(() => {});
  }
  useEffect(load, []);

  function resetDraft() {
    setName("");
    setUrl("");
    setSelectedEvents([]);
    setEditingId(null);
    setTestResult(null);
  }

  function startEdit(webhook) {
    setEditingId(webhook.id);
    setName(webhook.name);
    setUrl(webhook.url);
    setSelectedEvents(webhook.events);
    setTestResult(null);
  }

  function toggleEvent(key) {
    setSelectedEvents((prev) => (prev.includes(key) ? prev.filter((e) => e !== key) : [...prev, key]));
  }

  async function handleSave() {
    setError(null);
    if (!name.trim() || !url.trim()) {
      setError(t("settings.webhooks.nameAndUrlRequired"));
      return;
    }
    try {
      const body = { name: name.trim(), url: url.trim(), events: selectedEvents, enabled: true };
      if (editingId) {
        await api.updateWebhook(editingId, { ...body, enabled: webhooks.find((w) => w.id === editingId)?.enabled ?? true });
      } else {
        await api.createWebhook(body);
      }
      resetDraft();
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function toggleEnabled(webhook) {
    try {
      await api.updateWebhook(webhook.id, { name: webhook.name, url: webhook.url, events: webhook.events, enabled: !webhook.enabled });
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDelete(id) {
    if (!(await confirm(t("settings.webhooks.confirmDelete")))) return;
    try {
      await api.deleteWebhook(id);
      if (editingId === id) resetDraft();
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleTest() {
    if (!url.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await api.testWebhook(url.trim()));
    } catch (err) {
      setTestResult({ ok: false, error: err.message });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="panel">
      <h2>{t("settings.webhooks.heading")}</h2>
      <p className="hint">{t("settings.webhooks.hint")}</p>
      {error && <p className="error">{error}</p>}

      {webhooks.length > 0 && (
        <ul className="kv-list">
          {webhooks.map((w) => (
            <li key={w.id}>
              <span>
                <label className="checkbox-label">
                  <input type="checkbox" checked={w.enabled} onChange={() => toggleEnabled(w)} />
                  {w.name}
                </label>
              </span>
              <span>
                <span className="hint">{w.url}</span>{" "}
                <span className="hint">{w.events.length === 0 ? t("settings.webhooks.allEvents") : w.events.map((e) => events[e] || e).join(", ")}</span>{" "}
                <button type="button" onClick={() => startEdit(w)}>
                  {t("settings.webhooks.edit")}
                </button>{" "}
                <button type="button" onClick={() => handleDelete(w.id)}>
                  {t("settings.webhooks.delete")}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="form-row">
        <label>
          {t("settings.webhooks.name")}
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("settings.webhooks.namePlaceholder")} />
        </label>
        <label>
          {t("settings.webhooks.url")}
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://ntfy.sh/my-roaster" />
        </label>
      </div>

      <div className="breakout-toggle-grid">
        {Object.entries(events).map(([key, label]) => (
          <label key={key} className="checkbox-label">
            <input type="checkbox" checked={selectedEvents.includes(key)} onChange={() => toggleEvent(key)} />
            {label}
          </label>
        ))}
      </div>
      <p className="hint">{selectedEvents.length === 0 ? t("settings.webhooks.allEventsHint") : null}</p>

      <div className="form-row">
        <button type="button" onClick={handleSave}>
          {editingId ? t("settings.webhooks.saveChanges") : t("settings.webhooks.add")}
        </button>
        {editingId && (
          <button type="button" onClick={resetDraft}>
            {t("settings.webhooks.cancelEdit")}
          </button>
        )}
        <button type="button" onClick={handleTest} disabled={!url.trim() || testing}>
          {testing ? t("settings.webhooks.testing") : t("settings.webhooks.sendTest")}
        </button>
      </div>
      {testResult && (
        <p className={testResult.ok ? "hint" : "error"}>
          {testResult.ok ? t("settings.webhooks.testOk") : t("settings.webhooks.testFailed", { error: testResult.error || testResult.status })}
        </p>
      )}
    </div>
  );
}
