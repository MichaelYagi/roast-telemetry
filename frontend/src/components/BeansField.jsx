import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

const same = (a, b) => (a || "").trim().toLowerCase() === (b || "").trim().toLowerCase();

// One field for a roast's beans. Type a name, or pick one from the list as you
// type. A name that isn't in the list yet is added to it automatically when the
// roast is saved, so the Beans page always shows every name in use.
// `onCommit(text)` fires when the field is left, on Enter, or when a suggestion
// is picked; leave it out to just track typing through `onChange`.
export default function BeansField({ label, value, onChange, onCommit, className = "" }) {
  const { t } = useTranslation();
  const listId = useId();
  const [beans, setBeans] = useState([]);

  const load = () => api.listBeans().then(setBeans).catch(() => {});

  useEffect(() => {
    load();
  }, []);

  const match = beans.find((b) => same(b.name, value));
  const typed = (value || "").trim();

  // After the parent has saved it, reload the list so the status line is up to date.
  const commit = (text) => Promise.resolve(onCommit?.(text)).then(load);

  return (
    <div className={`beans-field ${className}`}>
      <label>
        {label ?? t("common.beansField.label")}
        <input
          list={listId}
          value={value || ""}
          onChange={(e) => {
            const next = e.target.value;
            onChange?.(next);
            // Picking a suggestion sets the exact saved name -- treat that as done.
            if (beans.some((b) => b.name === next)) commit(next);
          }}
          onBlur={() => commit(value || "")}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), commit(value || ""))}
          placeholder={t("common.beansField.placeholder")}
          maxLength={200}
        />
        <datalist id={listId}>
          {beans.map((b) => (
            <option key={b.id} value={b.name} />
          ))}
        </datalist>
      </label>
      <span className="beans-field-status no-print">
        {match ? (
          <>
            {t("common.beansField.inYourBeans")}
            <Link to="/beans">{t("common.beansField.details")}</Link>
          </>
        ) : typed ? (
          <>{t("common.beansField.newBeans")}</>
        ) : (
          <Link to="/beans">{t("common.beansField.manageBeans")}</Link>
        )}
      </span>
    </div>
  );
}
