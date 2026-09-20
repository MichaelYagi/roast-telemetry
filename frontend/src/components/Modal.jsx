import { useEffect } from "react";

// Generic closable modal shell -- backdrop + panel + an X in the corner,
// closes on Escape or a backdrop click same as the X does. Not tied to
// confirm/alert specifically (see DialogProvider.jsx, which builds those
// on top of this) -- any custom modal content in the app can use this
// directly.
export default function Modal({ open, onClose, title, wide = false, children }) {
  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal${wide ? " modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          {title && <h3>{title}</h3>}
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
