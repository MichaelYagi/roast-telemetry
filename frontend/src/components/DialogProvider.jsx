import { createContext, useCallback, useContext, useState } from "react";
import { useTranslation } from "react-i18next";
import Modal from "./Modal.jsx";

// Replaces window.confirm/window.alert app-wide with a custom modal --
// mounted once (see App.jsx) so every call site shares one dialog instead
// of each screen owning its own Modal + pending-state plumbing. confirm()/
// notify() return a Promise, same call shape as the window.* functions
// they replace (`if (!(await confirm(...))) return;`), so existing async
// handlers barely change.
const DialogContext = createContext(null);

export function DialogProvider({ children }) {
  const { t } = useTranslation();
  // { kind: "confirm" | "notice", title, message, confirmLabel, danger, resolve } | null
  // -- only one dialog can be open at a time, which matches every actual
  // call site (a confirm/alert always blocks the handler that triggered
  // it, same as window.confirm/window.alert did).
  const [state, setState] = useState(null);

  const confirm = useCallback(
    (message, options = {}) => {
      return new Promise((resolve) => {
        setState({
          kind: "confirm",
          title: options.title || t("common.dialog.confirmTitle"),
          message,
          confirmLabel: options.confirmLabel || t("common.dialog.delete"),
          danger: options.danger !== false,
          resolve,
        });
      });
    },
    [t]
  );

  const notify = useCallback(
    (message, options = {}) => {
      return new Promise((resolve) => {
        setState({ kind: "notice", title: options.title || t("common.dialog.noticeTitle"), message, resolve });
      });
    },
    [t]
  );

  function settle(result) {
    state?.resolve?.(result);
    setState(null);
  }

  return (
    <DialogContext.Provider value={{ confirm, notify }}>
      {children}
      <Modal open={Boolean(state)} onClose={() => settle(false)} title={state?.title}>
        {state && (
          <>
            {/* pre-line, not the default normal -- HistoryDashboard's
                bulk-delete failure notice joins multiple lines with "\n",
                which a plain <p> would otherwise collapse to one line. */}
            <p className="dialog-message">{state.message}</p>
            <div className="dialog-actions">
              {state.kind === "confirm" && (
                <button type="button" onClick={() => settle(false)}>
                  {t("common.dialog.cancel")}
                </button>
              )}
              <button type="button" className={state.danger ? "danger" : undefined} onClick={() => settle(true)}>
                {state.kind === "confirm" ? state.confirmLabel : t("common.dialog.ok")}
              </button>
            </div>
          </>
        )}
      </Modal>
    </DialogContext.Provider>
  );
}

function useDialogContext() {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useConfirm/useNotify must be used within a DialogProvider");
  return ctx;
}

// await confirm("Delete this?") -> boolean, replaces window.confirm.
export function useConfirm() {
  return useDialogContext().confirm;
}

// await notify("Something went wrong") -> void once dismissed, replaces
// window.alert. Awaiting it is optional -- most call sites fire-and-forget
// it exactly like window.alert, just without blocking the whole tab.
export function useNotify() {
  return useDialogContext().notify;
}
