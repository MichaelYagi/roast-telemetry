import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client.js";
import RoastChart from "../components/RoastChart.jsx";
import RoastReviewCard from "../components/RoastReviewCard.jsx";
import RoastStatsPanel from "../components/RoastStatsPanel.jsx";
import WeightField from "../components/WeightField.jsx";
import { formatTemp } from "../tempUnits.js";

// Mirrors the Configure Roast form's <option> labels (LiveRoastView.jsx)
// so history shows the same human-readable name, not the raw mode enum.
// modbus_live covers both USB and Ethernet (see roast.modbus_transport --
// mode alone can't distinguish them, same reasoning as the Configure Roast
// form's own Data Source dropdown), so this is a function, not a plain
// lookup, for that one entry.
function modeLabel(roast) {
  if (roast.mode === "modbus_live") {
    return roast.modbus_transport === "tcp" ? "Direct Modbus (Ethernet)" : "Direct Modbus (USB)";
  }
  const MODE_LABELS = {
    simulator: "Simulator",
    alog_playback: ".alog Playback",
    ms6514_live: "Direct USB (thermocouple meter)",
    aillio_live: "Aillio Bullet (USB)",
    tc4_live: "TC4+ (USB, PID firmware)",
  };
  return MODE_LABELS[roast.mode] || roast.mode;
}

// Mirrors backend/app/api/roasts.py's alog_filename() exactly -- same
// input (title + the raw created_at ISO string, sliced not reformatted),
// so this label always matches the filename the browser actually saves,
// without a round trip to ask the server what it named it.
// eslint-disable-next-line no-control-regex -- deliberate: matches
// backend/app/api/roasts.py's _UNSAFE_FILENAME_CHARS exactly, including
// C0 control characters (a title with an embedded CR/LF is a real
// header-injection surface server-side, not just a cosmetic filename
// issue -- see that file's own comment on the shared regex).
const UNSAFE_FILENAME_CHARS = /[\\/:*?"<>|\x00-\x1f]/g;

function alogFilename(title, createdAt) {
  const safeTitle = title.replace(UNSAFE_FILENAME_CHARS, "_").trim() || "roast";
  const timestamp = createdAt.slice(0, 16).replace("T", "_").replace(":", "");
  return `${safeTitle}_${timestamp}.alog`;
}

// Same convention, mirrors backend/app/api/roasts.py's csv_filename().
function csvFilename(title, createdAt) {
  const safeTitle = title.replace(UNSAFE_FILENAME_CHARS, "_").trim() || "roast";
  const timestamp = createdAt.slice(0, 16).replace("T", "_").replace(":", "");
  return `${safeTitle}_${timestamp}.csv`;
}

export default function RoastDetailView() {
  const { id } = useParams();
  const [roast, setRoast] = useState(null);
  const [error, setError] = useState(null);
  const [tempUnit, setTempUnit] = useState("c"); // display only, see Settings > Temperature Unit
  // Deliberately separate from `error` above -- that one *replaces the
  // whole page* (see the early-return a few lines down), which is right
  // for "the roast itself failed to load" but way too disruptive for a
  // failed milestone delete/retime on an otherwise-fine page.
  const [milestoneError, setMilestoneError] = useState(null);
  const [newTagInput, setNewTagInput] = useState("");
  const [tagsError, setTagsError] = useState(null);
  const [allTags, setAllTags] = useState([]); // feeds the <datalist> below -- existing tags to autocomplete against

  useEffect(() => {
    api
      .getRoast(id)
      .then(setRoast)
      .catch((err) => setError(err.message));
  }, [id]);

  // Fetched once on mount, same tradeoff as tempUnit's getSettings() call
  // below -- a tag added elsewhere mid-session won't appear in the
  // suggestions until next reload.
  useEffect(() => {
    api.listTags().then(setAllTags);
  }, []);

  async function handleSaveGreenWeight(grams) {
    await api.setWeightGreen(id, grams);
    setRoast((r) => (r ? { ...r, weight_green_g: grams } : r));
  }

  async function handleDeleteGreenWeight() {
    await api.deleteWeightGreen(id);
    setRoast((r) => (r ? { ...r, weight_green_g: null } : r));
  }

  async function handleSaveRoastedWeight(grams) {
    await api.setWeightRoasted(id, grams);
    setRoast((r) => (r ? { ...r, weight_roasted_g: grams } : r));
  }

  async function handleDeleteRoastedWeight() {
    await api.deleteWeightRoasted(id);
    setRoast((r) => (r ? { ...r, weight_roasted_g: null } : r));
  }

  // Both add and remove go through this one function -- always PUTting
  // the roast's *entire* tag list (set_roast_tags' replace-the-whole-set
  // semantics), not a single add/remove call, so there's only one code
  // path to get right rather than two nearly-identical ones.
  async function handleTagsChange(nextTags) {
    setTagsError(null);
    try {
      await api.setTags(id, nextTags);
      setRoast((r) => (r ? { ...r, tags: nextTags } : r));
    } catch (err) {
      setTagsError(err.message);
    }
  }

  function handleAddTag() {
    const tag = newTagInput.trim();
    if (!tag || roast.tags.includes(tag)) {
      setNewTagInput("");
      return;
    }
    setNewTagInput("");
    handleTagsChange([...roast.tags, tag]);
  }

  function handleRemoveTag(tag) {
    handleTagsChange(roast.tags.filter((t) => t !== tag));
  }

  // No websocket on this page (one-time REST fetch, see the effect
  // above) -- unlike LiveRoastView.jsx, there's no server-pushed
  // "event_deleted"/"event_updated" message to pick up, so these mutate
  // `roast.events` directly on success, same direct-mutation pattern
  // handleSaveGreenWeight/handleSaveRoastedWeight above already use.
  async function handleDeleteMilestone(eventId) {
    setMilestoneError(null);
    try {
      await api.deleteEvent(id, eventId);
      setRoast((r) => (r ? { ...r, events: r.events.filter((e) => e.id !== eventId) } : r));
    } catch (err) {
      setMilestoneError(err.message);
    }
  }

  async function handleRetimeMilestone(eventId, timeS) {
    setMilestoneError(null);
    try {
      const updated = await api.retimeEvent(id, eventId, timeS);
      setRoast((r) => (r ? { ...r, events: r.events.map((e) => (e.id === eventId ? updated : e)) } : r));
    } catch (err) {
      setMilestoneError(err.message);
    }
  }

  // Matches Artisan's own "Weight loss" convention (a negative percentage,
  // e.g. "-13.2%") -- roast_review.py computes the same ratio but as a
  // positive "percent lost" for the AI review prompt; this is purely a
  // different display convention for the same underlying numbers, not a
  // second formula.
  const weightLossPct =
    roast?.weight_green_g && roast?.weight_roasted_g != null
      ? (((roast.weight_roasted_g / roast.weight_green_g) - 1) * 100).toFixed(1)
      : null;

  // One-time fetch, not the live SSE subscription LiveRoastView uses --
  // a finished roast's page doesn't need to react to a setting saved in
  // another tab while it's open.
  useEffect(() => {
    api.getSettings().then((s) => setTempUnit(s.temperature_unit || "c"));
  }, []);

  if (error) return <p className="error panel">{error}</p>;
  if (!roast) return <p className="panel">Loading…</p>;

  return (
    <div className="detail-view">
      <div className="panel no-print">
        <h2>{roast.title}</h2>
        <p className="sub">
          {roast.mode} · status: <strong>{roast.status}</strong> · duration:{" "}
          {roast.duration_s ? `${Math.floor(roast.duration_s / 60)}:${String(Math.round(roast.duration_s % 60)).padStart(2, "0")}` : "—"}
        </p>
        <div className="detail-download-list">
          {roast.alog_path && (
            // No target="_blank" -- the response is Content-Disposition:
            // attachment, so it downloads without navigating away; adding
            // _blank just pops an empty new tab in some browsers while the
            // file downloads silently in the background, looking like a
            // no-op click. Real Artisan's own native format -- File > Open
            // in Artisan itself opens this directly, no conversion needed.
            <p>
              Download <a href={api.alogDownloadUrl(roast.id)}>{alogFilename(roast.title, roast.created_at)}</a>
            </p>
          )}
          <p>
            Download <a href={api.csvDownloadUrl(roast.id)}>{csvFilename(roast.title, roast.created_at)}</a>
          </p>
          <p>
            {/* Browser-native print-to-PDF rather than a generated file --
                no new dependency, and "Save as PDF" in the print dialog is
                already a real PDF export. The report itself is just this
                page with .no-print-marked chrome (this whole block, nav
                controls elsewhere on the page) hidden via the @media print
                rules in styles.css -- see those for what stays visible. */}
            <button type="button" className="link-like" onClick={() => window.print()}>
              Print report
            </button>
          </p>
        </div>
      </div>

      {/* Print-only header -- the interactive one above (with its
          download links/print button) is hidden when printing, so the
          report needs its own plain title/stat line to replace it. */}
      <div className="print-only detail-print-header">
        <h2>{roast.title}</h2>
        <p className="sub">
          {roast.mode} · status: {roast.status} · duration:{" "}
          {roast.duration_s ? `${Math.floor(roast.duration_s / 60)}:${String(Math.round(roast.duration_s % 60)).padStart(2, "0")}` : "—"}
        </p>
      </div>

      <div className="panel">
        <RoastChart
          profile={roast.profile}
          events={roast.events}
          tempUnit={tempUnit}
          onDeleteEvent={handleDeleteMilestone}
          onRetimeEvent={handleRetimeMilestone}
        />
        {milestoneError && <p className="error no-print">{milestoneError}</p>}
      </div>

      <div className="detail-grid">
        <div className="panel">
          <h3>Data source</h3>
          <ul className="kv-list">
            <li>
              <span>Mode</span>
              <span>{modeLabel(roast)}</span>
            </li>
            <li>
              <span>Roasted by</span>
              <span>{roast.created_by_username || "—"}</span>
            </li>
            {roast.mode === "alog_playback" && (
              <>
                <li>
                  <span>Source file</span>
                  <span>{roast.source_alog_path || "—"}</span>
                </li>
                <li>
                  <span>Speed</span>
                  <span>{roast.playback_speed != null ? `${roast.playback_speed}x` : "—"}</span>
                </li>
              </>
            )}
            {roast.mode === "modbus_live" && (
              <>
                <li>
                  <span>Connection</span>
                  <span>
                    {roast.modbus_transport === "tcp"
                      ? `${roast.modbus_host || "—"}:${roast.modbus_tcp_port || "—"}`
                      : roast.modbus_port || "—"}
                  </span>
                </li>
                <li>
                  <span>Device profile</span>
                  <span>{roast.modbus_device_profile_name || "Custom (advanced fields)"}</span>
                </li>
              </>
            )}
            {roast.mode === "ms6514_live" && (
              <li>
                <span>Serial port</span>
                <span>{roast.ms6514_port || "—"}</span>
              </li>
            )}
            {roast.mode === "aillio_live" && (
              <li>
                <span>Model</span>
                <span>{roast.aillio_model ? `Aillio Bullet ${roast.aillio_model.toUpperCase()}` : "—"}</span>
              </li>
            )}
            {roast.mode === "tc4_live" && (
              <li>
                <span>Serial port</span>
                <span>{roast.tc4_port || "—"}</span>
              </li>
            )}
          </ul>

          <h3>Batch</h3>
          <ul className="kv-list">
            <li>
              <span>Beans</span>
              <span>{roast.beans || "—"}</span>
            </li>
            <li>
              <span>Tags</span>
              <span className="tag-edit-group">
                {roast.tags.map((t) => (
                  <span key={t} className="tag-chip">
                    {t}
                    <button type="button" className="tag-chip-remove no-print" onClick={() => handleRemoveTag(t)} aria-label={`Remove tag ${t}`}>
                      ×
                    </button>
                  </span>
                ))}
                <span className="input-suffix-group no-print">
                  <input
                    type="text"
                    value={newTagInput}
                    onChange={(e) => setNewTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddTag();
                      }
                    }}
                    placeholder="Add tag…"
                    list="existing-tags"
                  />
                  <button type="button" onClick={handleAddTag} disabled={!newTagInput.trim()}>
                    Add
                  </button>
                  {/* Native browser autocomplete against every tag used
                      anywhere -- no library needed, and it degrades to a
                      plain text input on anything that doesn't support
                      <datalist> (rare, but free either way). */}
                  <datalist id="existing-tags">
                    {allTags
                      .filter((t) => !roast.tags.includes(t.tag))
                      .map((t) => (
                        <option key={t.tag} value={t.tag} />
                      ))}
                  </datalist>
                </span>
              </span>
            </li>
            {tagsError && (
              <li>
                <span></span>
                <span className="error">{tagsError}</span>
              </li>
            )}
            <li>
              <span>Green weight</span>
              <span>
                <WeightField value={roast.weight_green_g} onSave={handleSaveGreenWeight} onDelete={handleDeleteGreenWeight} noPrint />
              </span>
            </li>
            <li>
              <span>Roasted weight</span>
              <span>
                <WeightField value={roast.weight_roasted_g} onSave={handleSaveRoastedWeight} onDelete={handleDeleteRoastedWeight} noPrint />
              </span>
            </li>
            {weightLossPct != null && (
              <li>
                <span>Weight loss</span>
                <span>{weightLossPct}%</span>
              </li>
            )}
          </ul>
        </div>

        <div className="panel">
          <h3>Roast Stats</h3>
          <RoastStatsPanel roastId={roast.id} />
        </div>

        <div className="panel">
          <h3>Events</h3>
          <ul className="event-feed">
            {roast.events.map((ev) => (
              <li key={ev.id}>
                <strong>{ev.label}</strong> @ {Math.floor(ev.time_s / 60)}:
                {String(Math.round(ev.time_s % 60)).padStart(2, "0")}
                {ev.value != null && ev.channel ? ` (${ev.channel}: ${ev.value})` : ev.value != null ? ` (${formatTemp(ev.value, tempUnit)})` : ""}
              </li>
            ))}
          </ul>
        </div>

        <div className="panel">
          <h3>Notes</h3>
          <ul className="note-feed">
            {roast.notes.map((n) => (
              <li key={n.id}>{n.text}</li>
            ))}
            {roast.notes.length === 0 && <li>No notes.</li>}
          </ul>
        </div>

        <RoastReviewCard roastId={roast.id} roastActive={roast.status === "roasting" || roast.status === "cooling"} />
      </div>
    </div>
  );
}
