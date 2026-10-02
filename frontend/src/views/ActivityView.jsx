import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

const ACTIONS = [
  "create", "import", "delete", "set_tags", "set_weight", "set_outcome", "set_beans",
  "add_note", "update_note", "delete_note", "add_event", "delete_event", "retime_event",
  "safe_state", "automation_started", "automation_stopped", "automation_rule_fired",
  "safety_disabled", "safety_enabled",
  "login", "logout",
];

function formatWhen(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// Who did what, when -- roast deletes/edits and safety/control events
// (Emergency Stop, fail-safe trips, automation start/stop/fire). See
// backend/app/storage.py's activity_log table for what gets written and why.
export default function ActivityView() {
  const { t } = useTranslation();
  const [entries, setEntries] = useState([]);
  const [filters, setFilters] = useState({ category: "", action: "", q: "" });
  // Same debounced-search-separate-from-the-fetched-filter split as
  // HistoryDashboard.jsx's own searchInput -- typing shouldn't fire a
  // request per keystroke.
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  function refresh() {
    setLoading(true);
    setError(null);
    const filterParams = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    const pageParams = { ...filterParams, limit: pageSize, offset: (page - 1) * pageSize };
    return Promise.all([api.listActivity(pageParams), api.countActivity(filterParams)])
      .then(([rows, count]) => {
        setEntries(rows);
        setTotalCount(count.total);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
  }, [filters, page]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = setTimeout(() => {
      setFilters((f) => ({ ...f, q: searchInput.trim() }));
      setPage(1);
    }, 300);
    return () => clearTimeout(id);
  }, [searchInput]);

  function updateFilter(key, value) {
    setFilters({ ...filters, [key]: value });
    setPage(1);
  }

  const anyFilter = Object.values(filters).some(Boolean) || Boolean(searchInput);
  function clearFilters() {
    setFilters({ category: "", action: "", q: "" });
    setSearchInput("");
    setPage(1);
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  return (
    <div className="activity-view">
      <div className="panel filters-grid">
        <label className="filter-search">
          {t("activity.filters.search")}
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("activity.filters.searchPlaceholder")}
          />
        </label>
        <label>
          {t("activity.filters.category")}
          <select value={filters.category} onChange={(e) => updateFilter("category", e.target.value)}>
            <option value="">{t("activity.filters.all")}</option>
            <option value="roast">{t("activity.filters.roastEdits")}</option>
            <option value="safety">{t("activity.filters.safetyControl")}</option>
            <option value="auth">{t("activity.filters.signIns")}</option>
          </select>
        </label>
        <label>
          {t("activity.filters.action")}
          <select value={filters.action} onChange={(e) => updateFilter("action", e.target.value)}>
            <option value="">{t("activity.filters.all")}</option>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        {anyFilter && (
          <button type="button" className="filters-clear" onClick={clearFilters}>
            {t("activity.filters.clearFilters")}
          </button>
        )}
      </div>

      <div className="panel">
        {/* A dedicated row, not the shared .table-toolbar (that one's
            justify-content: flex-end, meant for a single right-aligned
            item elsewhere -- with two items of very different lengths it
            mashed the hint text straight into "Download" with no gap).
            space-between plus wrap keeps them on one line when there's
            room and drops Download to its own line on a narrow phone
            instead of squeezing both into overlapping text. */}
        <div className="activity-toolbar">
          <p className="hint">{t("activity.hint")}</p>
          <span className="activity-download">
            {t("activity.download")}{" "}
            {totalCount > 0 ? (
              <>
                <a href={api.activityExportUrl("csv", filters)} download>
                  CSV
                </a>{" "}
                ·{" "}
                <a href={api.activityExportUrl("json", filters)} download>
                  JSON
                </a>
              </>
            ) : (
              // Nothing to export -- plain greyed-out text, not disabled
              // links (an <a> has no real disabled state, and this avoids
              // a dead click/keyboard-focus target for an empty download).
              <span className="download-disabled" aria-disabled="true">
                CSV · JSON
              </span>
            )}
          </span>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="table-scroll">
          <table className="roast-table activity-table">
            <thead>
              <tr>
                <th>{t("activity.table.time")}</th>
                <th>{t("activity.table.category")}</th>
                <th>{t("activity.table.action")}</th>
                <th>{t("activity.table.roast")}</th>
                <th>{t("activity.table.message")}</th>
                <th>{t("activity.table.user")}</th>
                <th>{t("activity.table.platform")}</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr className="table-message">
                  <td colSpan={7}>{t("activity.table.loading")}</td>
                </tr>
              )}
              {!loading && entries.length === 0 && (
                <tr className="table-message">
                  <td colSpan={7}>{anyFilter ? t("activity.table.noMatch") : t("activity.table.noneYet")}</td>
                </tr>
              )}
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="cell-created">{formatWhen(e.created_at)}</td>
                  <td>
                    <span className={`status-pill status-${e.category}`}>{e.category}</span>
                  </td>
                  <td>{e.action}</td>
                  <td className="cell-title">
                    {e.roast_id ? <Link to={`/roasts/${e.roast_id}`}>{e.roast_title || e.roast_id}</Link> : e.roast_title || "—"}
                  </td>
                  <td>{e.message}</td>
                  <td>{e.username || "—"}</td>
                  <td>{e.platform || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {totalCount > 0 && (
          <div className="pagination-row">
            <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>
              {t("activity.pagePrev")}
            </button>
            <span>{t("activity.pageOf", { page, totalPages, count: totalCount })}</span>
            <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)}>
              {t("activity.pageNext")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
