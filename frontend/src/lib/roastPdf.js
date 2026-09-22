// Building a PDF roast report directly (jsPDF + jspdf-autotable, both MIT),
// instead of only through the browser's print-to-PDF dialog. Two entry
// points:
//   - downloadRoastPdf: the roast currently open on its own page -- uses
//     whatever chart image the caller hands it (normally captured straight
//     from the live, on-screen chart via RoastChart's toImage()).
//   - buildRoastPdfBlob: used in a loop for a bulk export, where no chart is
//     on screen for most roasts -- renders each one's curve off-screen first
//     (see renderChartImage below).
// Both take the same `extra = { stats, numbersRow, metricsMeta }` bundle for
// the Roast Stats / Numbers tables -- callers fetch these themselves
// (api.getRoastStats/getRoastNumbers/getAnalysisMetrics) since none of them
// live on the plain Roast object; any of the three may be left out and that
// section is just skipped.
import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";
import { Chart, LinearScale, LineController, LineElement, PointElement } from "chart.js";
import { formatMetric, groupMetrics } from "./metricFormat.js";
import { formatEventValue } from "./eventFormat.js";
import { celsiusToUnit, formatTemp, unitSuffix } from "../tempUnits.js";
import { STAT_ROW_DEFS, formatRoastStatRow, formatDuration } from "../roastStats.js";

Chart.register(LinearScale, LineController, LineElement, PointElement);

// Mirrors RoastDetailView.jsx's own modeLabel() -- kept as a small, separate
// copy rather than an import so this module doesn't reach into a page
// component's internals for four lines of lookup text.
function modeLabel(roast) {
  if (roast.mode === "modbus_live") return roast.modbus_transport === "tcp" ? "Direct Modbus (Ethernet)" : "Direct Modbus (USB)";
  const labels = {
    simulator: "Simulator", alog_playback: ".alog Playback", ms6514_live: "Direct USB (thermocouple meter)",
    aillio_live: "Aillio Bullet (USB)", tc4_live: "TC4+ (USB, PID firmware)",
  };
  return labels[roast.mode] || roast.mode;
}

// Same milestone colors as RoastChart.jsx's own MILESTONE_COLORS, so a
// bulk-rendered report's chart doesn't clash with the interactive one.
const MILESTONE_COLORS = {
  CHARGE: "#2563eb", TURNING_POINT: "#0891b2", DRY_END: "#ca8a04", FC_START: "#dc2626",
  FC_END: "#b91c1c", SC_START: "#9333ea", SC_END: "#7e22ce", DROP: "#16a34a", COOL_END: "#334155", CUSTOM: "#64748b",
};

// A plain, non-interactive BT/ET curve with milestone dots, drawn on a
// detached canvas and handed back as a PNG data URL -- used for a bulk
// export, where the roast being drawn isn't the one open on screen. Doesn't
// attempt to match every option the live chart has (zoom, extra devices, a
// background overlay); it's a static picture for a report, not a re-creation
// of the interactive view.
export async function renderChartImage(profile, events, tempUnit, { width = 900, height = 380 } = {}) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const toUnit = (c) => (c == null ? null : celsiusToUnit(c, tempUnit));
  const bt = profile.filter((p) => p.bt != null).map((p) => ({ x: p.time_s, y: toUnit(p.bt) }));
  const et = profile.filter((p) => p.et != null).map((p) => ({ x: p.time_s, y: toUnit(p.et) }));
  const milestonePoints = events
    .filter((e) => e.value != null && MILESTONE_COLORS[e.type])
    .map((e) => ({ x: e.time_s, y: toUnit(e.value), color: MILESTONE_COLORS[e.type] }));

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      datasets: [
        { data: bt, borderColor: "#1d4ed8", borderWidth: 2, pointRadius: 0, parsing: false, tension: 0.15 },
        { data: et, borderColor: "#be123c", borderWidth: 2, pointRadius: 0, parsing: false, tension: 0.15 },
        {
          data: milestonePoints, showLine: false, pointRadius: 4, parsing: false,
          borderColor: (ctx) => milestonePoints[ctx.dataIndex]?.color, backgroundColor: (ctx) => milestonePoints[ctx.dataIndex]?.color,
        },
      ],
    },
    options: {
      responsive: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { type: "linear", title: { display: true, text: "Time (s), from Charge" } },
        y: { type: "linear", title: { display: true, text: `Temperature (${unitSuffix(tempUnit)})` } },
      },
    },
  });
  // One frame so the canvas is actually painted before reading it back.
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const dataUrl = canvas.toDataURL("image/png");
  chart.destroy();
  return dataUrl;
}

// Same convention as RoastDetailView.jsx's own filename helpers, plus an
// optional id suffix (bulk export: many roasts, guaranteed unique without a
// separate collision check per file).
export function pdfFilename(title, createdAt, idSuffix = null) {
  const safe = (title || "roast").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").trim() || "roast";
  const timestamp = (createdAt || "").slice(0, 16).replace("T", "_").replace(":", "");
  return `${safe}_${timestamp}${idSuffix ? `_${idSuffix}` : ""}.pdf`;
}

// Fixed left/right column widths for every two-column (label, value) table
// in the report -- kv, Roast Stats, and each Numbers group. Without this,
// jspdf-autotable sizes each table's columns from only that table's own
// content, so the value column lands at a different x in every section
// (a short "Weight loss" table ends up much narrower than "Temperatures",
// whose longer labels push its value column way out to the right) --
// this keeps every section's value column lined up under the next.
// Only the label column gets a fixed width -- the value column is left
// on autoTable's own "auto" sizing, so it always starts at the same x
// (what actually fixes the misalignment) without also having to hand-match
// both widths to the page's exact usable width (get that arithmetic even
// slightly wrong and autoTable logs an "n units could not fit page"
// warning, since two explicit widths can't be auto-resized to close the gap).
const KV_COLUMN_STYLES = { 0: { cellWidth: 258 } };

// Builds the document itself (not yet saved/exported) -- shared by both
// entry points below.
function buildDoc(roast, tempUnit, chartImage, { stats, numbersRow, metricsMeta } = {}) {
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const marginX = 40;
  let y = 48;

  doc.setFontSize(16);
  doc.text(roast.title || "Roast", marginX, y);
  y += 20;
  doc.setFontSize(10);
  doc.setTextColor(90);
  doc.text(
    `${modeLabel(roast)} · ${roast.status} · duration ${formatDuration(roast.duration_s)} · ${new Date(roast.created_at).toLocaleString()}`,
    marginX,
    y
  );
  doc.setTextColor(0);
  y += 16;

  if (chartImage) {
    const w = 515;
    const h = (w * 380) / 900;
    doc.addImage(chartImage, "PNG", marginX, y, w, h);
    y += h + 16;
  }

  const kv = [];
  if (roast.beans) kv.push(["Beans", roast.beans]);
  if (roast.weight_green_g) kv.push(["Green weight", `${roast.weight_green_g} g`]);
  if (roast.weight_roasted_g != null) kv.push(["Roasted weight", `${roast.weight_roasted_g} g`]);
  if (roast.weight_green_g && roast.weight_roasted_g != null) {
    kv.push(["Weight loss", `${(((roast.weight_roasted_g / roast.weight_green_g) - 1) * 100).toFixed(1)}%`]);
  }
  if (roast.tags?.length) kv.push(["Tags", roast.tags.join(", ")]);
  if (roast.color_agtron != null) kv.push(["Color (Agtron)", roast.color_agtron]);
  if (roast.cupping_score != null) kv.push(["Cupping score", roast.cupping_score]);
  if (roast.rating != null) kv.push(["Rating", `${roast.rating}/5`]);
  if (kv.length) {
    autoTable(doc, { startY: y, margin: { left: marginX }, body: kv, theme: "plain", styles: { fontSize: 10, cellPadding: 2 }, columnStyles: KV_COLUMN_STYLES });
    y = doc.lastAutoTable.finalY + 12;
  }

  // Free text, up to 2000 chars (see OutcomeUpdate.tasting_notes in
  // backend/app/models.py) -- its own wrapped paragraph rather than a kv
  // table row, which would either overflow or get illegibly squeezed.
  if (roast.tasting_notes) {
    if (y > 700) {
      doc.addPage();
      y = 48;
    }
    doc.setFontSize(10);
    doc.text("Tasting notes", marginX, y);
    y += 12;
    const lines = doc.splitTextToSize(roast.tasting_notes, 515);
    doc.text(lines, marginX, y);
    y += lines.length * 12 + 12;
  }

  if (stats) {
    if (y > 700) {
      doc.addPage();
      y = 48;
    }
    doc.setFontSize(11);
    doc.text("Roast Stats", marginX, y);
    const rows = STAT_ROW_DEFS.map(({ key, label }) => [label, formatRoastStatRow(key, stats)]);
    autoTable(doc, { startY: y + 10, margin: { left: marginX }, body: rows, theme: "striped", styles: { fontSize: 9, cellPadding: 3 }, columnStyles: KV_COLUMN_STYLES });
    y = doc.lastAutoTable.finalY + 12;
  }

  if (numbersRow?.metrics && metricsMeta?.length) {
    for (const [group, list] of groupMetrics(metricsMeta)) {
      const rows = list.filter((m) => numbersRow.metrics[m.key] != null).map((m) => [m.label, formatMetric(m, numbersRow.metrics[m.key], tempUnit)]);
      if (!rows.length) continue;
      if (y > 700) {
        doc.addPage();
        y = 48;
      }
      doc.setFontSize(11);
      doc.text(group, marginX, y);
      autoTable(doc, { startY: y + 10, margin: { left: marginX }, body: rows, theme: "striped", styles: { fontSize: 9, cellPadding: 3 }, columnStyles: KV_COLUMN_STYLES });
      y = doc.lastAutoTable.finalY + 12;
    }
  }

  if (roast.events?.length) {
    if (y > 680) {
      doc.addPage();
      y = 48;
    }
    doc.setFontSize(11);
    doc.text("Events", marginX, y);
    autoTable(doc, {
      startY: y + 10,
      margin: { left: marginX },
      head: [["Event", "Time", "Reading"]],
      body: roast.events.map((e) => [e.label, formatDuration(e.time_s), formatEventValue(e, formatTemp, tempUnit).replace(/^\s*\(|\)$/g, "")]),
      styles: { fontSize: 9, cellPadding: 3 },
      headStyles: { fillColor: [230, 230, 230], textColor: 0 },
    });
    y = doc.lastAutoTable.finalY + 12;
  }

  if (roast.notes?.length) {
    if (y > 700) {
      doc.addPage();
      y = 48;
    }
    doc.setFontSize(11);
    doc.text("Notes", marginX, y);
    autoTable(doc, {
      startY: y + 10,
      margin: { left: marginX },
      body: roast.notes.map((n) => [`${formatDuration(n.time_s)}${n.author ? ` · ${n.author}` : ""}: ${n.text}`]),
      theme: "plain",
      styles: { fontSize: 9, cellPadding: 3 },
    });
  }

  return doc;
}

// The currently-open roast page: `chartImage` normally comes straight from
// the live chart (RoastChart's forwarded toImage()), so the report matches
// what's on screen. Triggers a browser download.
export function downloadRoastPdf(roast, tempUnit, chartImage, extra) {
  const doc = buildDoc(roast, tempUnit, chartImage, extra);
  doc.save(pdfFilename(roast.title, roast.created_at));
}

// "Print report": builds the exact same document as downloadRoastPdf (not
// a second, CSS-driven rendering of the live page -- that was the earlier
// approach, and it could say something different from the actual PDF export
// since it was a wholly separate code path) and opens it in a new tab with
// its print dialog already triggered (jsPDF's autoPrint() embeds a
// print-on-open action in the PDF itself; every major browser's built-in
// PDF viewer honors it). What comes out of "Print" is therefore always
// identical to what "Download PDF" produces, by construction.
export function printRoastPdf(roast, tempUnit, chartImage, extra) {
  const doc = buildDoc(roast, tempUnit, chartImage, extra);
  doc.autoPrint();
  const url = doc.output("bloburl");
  window.open(url, "_blank");
}

// For a bulk export: renders its own chart image (no on-screen chart to
// borrow), and returns the PDF as a Blob instead of downloading it directly.
export async function buildRoastPdfBlob(roast, tempUnit, extra) {
  const chartImage = roast.profile?.length ? await renderChartImage(roast.profile, roast.events || [], tempUnit) : null;
  const doc = buildDoc(roast, tempUnit, chartImage, extra);
  return doc.output("blob");
}
