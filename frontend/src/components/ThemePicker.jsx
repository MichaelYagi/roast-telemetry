import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const STORAGE_KEY = "roast-telemetry:theme";

// Swatch colors mirror styles.css's :root / [data-theme="coffee"] blocks --
// duplicated here (rather than read from computed CSS) since they're
// static per theme and this needs to render a preview for a theme even
// while a *different* one is currently applied to the page.
const THEME_IDS = [
  { id: "light", swatch: ["#f5f5f4", "#ffffff", "#2e6da4"] },
  { id: "dark", swatch: ["#17171a", "#222226", "#5b9bd5"] },
  { id: "coffee", swatch: ["#1b120d", "#2a1d16", "#d68c3f"] },
  { id: "croissant", swatch: ["#fbf3e3", "#fffdf7", "#c8862b"] },
  { id: "matcha", swatch: ["#eef2e3", "#f8faf3", "#5c8a3a"] },
  { id: "garbagefire", swatch: ["#ffe4e1", "#fafad2", "#ff0000"] },
];

function applyTheme(id) {
  if (id === "light") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", id);
  }
}

function Swatch({ colors }) {
  return (
    <span className="theme-swatch">
      {colors.map((c, i) => (
        <span key={i} style={{ background: c }} />
      ))}
    </span>
  );
}

// Lives next to the header title (App.jsx) so it's reachable from every
// page, not tucked away in Settings -- a display preference, not
// something that needs to sync across devices, so it's plain localStorage
// rather than the backend AppSettings model. index.html has a matching
// inline script that applies the saved choice before first paint, so
// picking Coffee doesn't flash Light on every reload.
export default function ThemePicker() {
  const { t } = useTranslation();
  const THEMES = THEME_IDS.map((th) => ({ ...th, label: t(`common.themePicker.${th.id}`) }));
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) || "light";
    } catch {
      return "light";
    }
  });
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKeyDown(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  function choose(id) {
    setTheme(id);
    setOpen(false);
    applyTheme(id);
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Private browsing / storage disabled -- theme still applies for this
      // session via the DOM attribute, it just won't persist across reloads.
    }
  }

  const current = THEMES.find((th) => th.id === theme) || THEMES[0];

  return (
    <div className="theme-picker" ref={rootRef}>
      <button
        type="button"
        className="theme-picker-button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        title={t("common.themePicker.changeTheme")}
      >
        <Swatch colors={current.swatch} />
        <span className="theme-picker-label">{current.label}</span>
        <span className="theme-picker-caret">▾</span>
      </button>
      {open && (
        <div className="theme-picker-menu" role="menu">
          {THEMES.map((th) => (
            <button
              type="button"
              key={th.id}
              role="menuitemradio"
              aria-checked={th.id === theme}
              className={`theme-picker-option${th.id === theme ? " selected" : ""}`}
              onClick={() => choose(th.id)}
            >
              <Swatch colors={th.swatch} />
              <span className="theme-picker-option-label">{th.label}</span>
              <span className="theme-picker-option-check">✓</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
