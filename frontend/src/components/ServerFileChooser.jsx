import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import Modal from "./Modal.jsx";

const LAST_DIR_KEY = "roastTelemetry.lastAlogDir";

function readLastDir() {
  try {
    return window.localStorage.getItem(LAST_DIR_KEY) || "";
  } catch {
    return "";
  }
}

function saveLastDir(dir) {
  try {
    window.localStorage.setItem(LAST_DIR_KEY, dir);
  } catch {
    // storage blocked (private window, etc.) -- remembering the folder is only a convenience
  }
}

function formatSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Browses folders on the machine running the server (GET /api/files) and
// hands back the full path of the roast log file the user clicks -- the
// same path the import path field takes, so nobody has to type it. The
// browser's own file picker can't help here: it hides real paths and only
// sees the browser's computer, not the server's.
//
// Opens at `startPath` if it is a real folder (or the folder a file path
// sits in), else the last folder used, else the server's home folder.
// GET /api/files itself only ever lists .alog/.json/.csv/.tsv/.xlsx -- the
// server never has a reason to show anything else through this picker.
export default function ServerFileChooser({ open, onClose, onSelect, startPath = "" }) {
  const { t } = useTranslation();
  const [listing, setListing] = useState(null);
  const [pathInput, setPathInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const go = useCallback(async (path) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listServerFiles(path);
      setListing(result);
      setPathInput(result.path);
      saveLastDir(result.path);
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  // Each time it opens: try the field's current value (as a folder, then as
  // a file's folder), then the last folder used, then let the server pick.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      const typed = startPath.trim();
      const candidates = [];
      if (typed) {
        candidates.push(typed);
        candidates.push(typed.replace(/[\\/][^\\/]*$/, "") || typed); // the folder a file path sits in
      }
      const last = readLastDir();
      if (last) candidates.push(last);
      candidates.push("");
      for (const candidate of candidates) {
        if (cancelled) return;
        try {
          const result = await api.listServerFiles(candidate);
          if (cancelled) return;
          setListing(result);
          setPathInput(result.path);
          setError(null);
          saveLastDir(result.path);
          return;
        } catch (err) {
          if (candidate === "") {
            if (!cancelled) setError(err.message);
            return;
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // startPath is read only when the dialog opens -- typing in the page's field
    // behind it shouldn't re-navigate the open dialog.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function handleEntry(entry) {
    if (entry.kind === "dir") {
      go(entry.path);
    } else {
      onSelect(entry.path);
      onClose();
    }
  }

  function handleGo(e) {
    e.preventDefault();
    if (pathInput.trim()) go(pathInput.trim());
  }

  return (
    <Modal open={open} onClose={onClose} title={t("common.serverFileChooser.title")} wide>
      <p className="hint">{t("common.serverFileChooser.hint")}</p>

      <form className="file-chooser-pathbar" onSubmit={handleGo}>
        <button type="button" onClick={() => listing?.parent && go(listing.parent)} disabled={!listing?.parent || loading} aria-label={t("common.serverFileChooser.upOneFolder")}>
          {t("common.serverFileChooser.up")}
        </button>
        <input value={pathInput} onChange={(e) => setPathInput(e.target.value)} aria-label={t("common.serverFileChooser.folderPath")} spellCheck={false} />
        <button type="submit" disabled={loading || !pathInput.trim()}>
          {t("common.serverFileChooser.go")}
        </button>
      </form>

      {listing?.shortcuts?.length > 0 && (
        <div className="file-chooser-shortcuts">
          {listing.shortcuts.map((s) => (
            <button key={s.path} type="button" className="file-chooser-shortcut" onClick={() => go(s.path)} disabled={loading}>
              {s.label}
            </button>
          ))}
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <ul className="file-chooser-list" aria-busy={loading}>
        {listing && listing.entries.length === 0 && !error && <li className="file-chooser-empty">{t("common.serverFileChooser.empty")}</li>}
        {listing?.entries.map((entry) => (
          <li key={entry.path}>
            <button type="button" className={`file-chooser-entry file-chooser-${entry.kind}`} onClick={() => handleEntry(entry)}>
              <span className="file-chooser-icon" aria-hidden="true">
                {entry.kind === "dir" ? "📁" : "📄"}
              </span>
              <span className="file-chooser-name">{entry.name}</span>
              <span className="file-chooser-size">{formatSize(entry.size)}</span>
            </button>
          </li>
        ))}
      </ul>
      {listing?.truncated && <p className="hint">{t("common.serverFileChooser.truncated")}</p>}
    </Modal>
  );
}
