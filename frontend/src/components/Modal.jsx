import { useEffect } from "react";
import { useTranslation } from "react-i18next";

// Module-level, shared by every Modal instance -- a confirm()/notify() (see
// DialogProvider.jsx, itself built on this component) can open on top of
// an already-open Modal (e.g. AccountModal's "Revoke" confirmation), so
// two instances can genuinely be open at once. A plain per-instance
// lock/unlock would let the background scroll again the moment the inner
// one closes, even though the outer modal is still up -- counting how many
// are currently open is what makes closing the inner one a no-op instead.
let openModalCount = 0;

// Generic closable modal shell -- backdrop + panel + an X in the corner,
// closes on Escape or a backdrop click same as the X does. Not tied to
// confirm/alert specifically (see DialogProvider.jsx, which builds those
// on top of this) -- any custom modal content in the app can use this
// directly.
export default function Modal({ open, onClose, title, wide = false, children }) {
  const { t } = useTranslation();
  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  // Locks the page behind the backdrop -- without this, a tall modal body
  // (or just an accidental scroll/trackpad swipe) scrolls the page
  // underneath while the modal stays fixed in place, visibly at odds with
  // the modal appearing to own the screen.
  useEffect(() => {
    if (!open) return undefined;
    openModalCount += 1;
    document.body.style.overflow = "hidden";
    return () => {
      openModalCount -= 1;
      if (openModalCount === 0) document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal${wide ? " modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          {title && <h3>{title}</h3>}
          <button type="button" className="modal-close" onClick={onClose} aria-label={t("common.modal.close")}>
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
