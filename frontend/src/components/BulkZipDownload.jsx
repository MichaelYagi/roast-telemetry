import { useState } from "react";
import JSZip from "jszip";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { buildRoastPdfBlob, pdfFilename } from "../lib/roastPdf.js";

// A PDF report is built in the browser (see lib/roastPdf.js), one roast at a
// time -- unlike the other three formats, there's no server endpoint that
// can just add a "pdf/" folder to the zip itself. Past this many roasts
// that's too much work to do in a tab (a chart render + a full jsPDF build
// per roast), so the PDF checkbox is refused rather than silently only
// PDF-ing the first N -- a zip that quietly doesn't match "every filtered
// roast" is worse than an error asking to narrow the filters.

// "Download all (.zip)" plus a small, closed-by-default set of checkboxes for
// which extra per-roast formats to include besides .alog (always in the zip).
// Shared by History and Analysis, so the two stay in sync. `tempUnit` is only
// used for the PDF path (chart axis labels, temperature columns).
export default function BulkZipDownload({ params, tempUnit = "c" }) {
  const { t } = useTranslation();
  const EXTRA_FORMATS = [
    { key: "json", label: t("common.bulkZipDownload.formatJson") },
    { key: "roastlog_csv", label: t("common.bulkZipDownload.formatSpreadsheetCsv") },
    { key: "xlsx", label: t("common.bulkZipDownload.formatExcel") },
    { key: "pdf", label: t("common.bulkZipDownload.formatPdf") },
  ];
  const [open, setOpen] = useState(false);
  const [formats, setFormats] = useState([]);
  const [busy, setBusy] = useState(null); // e.g. "Building PDF 3/12…"
  const [error, setError] = useState(null);

  const toggle = (key) => setFormats((prev) => (prev.includes(key) ? prev.filter((f) => f !== key) : [...prev, key]));
  const wantsPdf = formats.includes("pdf");

  // The plain-link path: no PDF selected, so the server can build the whole
  // zip itself (same as before PDF export existed).
  const serverFormats = formats.filter((f) => f !== "pdf");
  const zipUrl = api.analysisExportUrl("zip", { ...params, formats: serverFormats.join(",") });

  async function handlePdfDownload() {
    setError(null);
    try {
      setBusy(t("common.bulkZipDownload.findingRoasts"));
      const table = await api.getAnalysisTable(params);
      const rows = table.rows || [];
      if (!rows.length) {
        setError(t("common.bulkZipDownload.noRoastsMatch"));
        return;
      }
      const settings = await api.getSettings().catch(() => null);
      const maxPdfRoasts = settings?.bulk_export_limit || 500;
      if (rows.length > maxPdfRoasts) {
        setError(t("common.bulkZipDownload.tooManyForPdf", { count: rows.length, max: maxPdfRoasts }));
        return;
      }

      setBusy(t("common.bulkZipDownload.fetchingZip"));
      const zipRes = await fetch(zipUrl);
      if (!zipRes.ok) throw new Error(`couldn't build the zip (${zipRes.status})`);
      const zip = await JSZip.loadAsync(await zipRes.arrayBuffer());

      const metricsMeta = await api.getAnalysisMetrics().catch(() => []);
      // Settings > Colors -- so a bulk-exported report's BT/ET curve colors
      // match what's actually shown on screen (RoastChart.jsx) instead of
      // renderChartImage's own hardcoded defaults.
      const colors = settings?.breakout_panel_colors || {};
      const used = new Set();
      for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        setBusy(t("common.bulkZipDownload.buildingPdf", { current: i + 1, total: rows.length }));
        try {
          const [roast, stats] = await Promise.all([api.getRoast(row.id), api.getRoastStats(row.id).catch(() => null)]);
          const blob = await buildRoastPdfBlob(roast, tempUnit, { stats, numbersRow: row, metricsMeta }, colors);
          let name = pdfFilename(roast.title, roast.created_at);
          if (used.has(name)) name = pdfFilename(roast.title, roast.created_at, row.id.slice(0, 8));
          used.add(name);
          zip.file(`pdf/${name}`, blob);
        } catch {
          // One roast's PDF hiccup shouldn't drop the whole export -- same
          // tolerance the server side already has for its own extra formats.
        }
      }

      setBusy(t("common.bulkZipDownload.zipping"));
      const finalBlob = await zip.generateAsync({ type: "blob" });
      const url = URL.createObjectURL(finalBlob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "roast-telemetry-export.zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || t("common.bulkZipDownload.couldntBuildZip"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <span className="bulk-zip">
      {wantsPdf ? (
        <button type="button" className="link-like" onClick={handlePdfDownload} disabled={Boolean(busy)}>
          {busy || t("common.bulkZipDownload.downloadAllZip")}
        </button>
      ) : (
        <a href={zipUrl} download>
          {t("common.bulkZipDownload.downloadAllZip")}
        </a>
      )}{" "}
      <button
        type="button"
        className="link-like bulk-zip-toggle no-print"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={t("common.bulkZipDownload.chooseFormats")}
      >
        {open ? "▾" : "▸"}
      </button>
      {open && (
        <span className="bulk-zip-formats no-print">
          {EXTRA_FORMATS.map((f) => (
            <label key={f.key} className="checkbox-label">
              <input type="checkbox" checked={formats.includes(f.key)} onChange={() => toggle(f.key)} />
              {f.label}
            </label>
          ))}
        </span>
      )}
      {error && <p className="error no-print">{error}</p>}
    </span>
  );
}
