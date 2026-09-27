import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en/translation.json";
import ja from "./locales/ja/translation.json";

// One shared instance, imported once for its side effect (App.jsx's own
// entry point does `import "./i18n.js"` before anything renders). The
// active language is Settings > Language (AppSettings.language, "en" by
// default) -- App.jsx calls i18n.changeLanguage() once that setting is
// known, so this only needs a safe initial guess before that lands, not
// browser-language detection (a self-hosted single-roaster app has one
// operator, not many visitors with different browser locales).
i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    ja: { translation: ja },
  },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React already escapes -- double-escaping would show literal &amp; etc.
});

export default i18n;
