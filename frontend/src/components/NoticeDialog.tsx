import { Button, Modal } from "@heroui/react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { useI18n } from "../i18n";

type NoticeTone = "info" | "danger";

interface Notice {
  title: string;
  message: string;
  tone: NoticeTone;
}

interface NoticeContextValue {
  showNotice: (message: string, title?: string) => void;
  showError: (error: unknown, title?: string) => void;
}

const NoticeContext = createContext<NoticeContextValue | null>(null);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** App-owned replacement for browser-native alert dialogs.
 *
 * Keeping one provider at the application root gives every async action the
 * same accessible, themed feedback without relying on `window.alert`, which
 * looks out of place and can be suppressed by embedded browsers.
 */
export function NoticeProvider({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const [notice, setNotice] = useState<Notice | null>(null);

  const showNotice = useCallback(
    (message: string, title?: string) =>
      setNotice({ title: title ?? t("common.notice_title"), message, tone: "info" }),
    [t],
  );
  const showError = useCallback(
    (error: unknown, title?: string) =>
      setNotice({
        title: title ?? t("common.error_title"),
        message: errorMessage(error),
        tone: "danger",
      }),
    [t],
  );

  const value = useMemo(() => ({ showNotice, showError }), [showNotice, showError]);

  return (
    <NoticeContext.Provider value={value}>
      {children}
      <Modal isOpen={notice !== null} onOpenChange={(open) => !open && setNotice(null)}>
        <Modal.Backdrop>
          <Modal.Container size="sm">
            <Modal.Dialog>
              <Modal.Header>
                <div className="flex items-center gap-3">
                  <span
                    aria-hidden="true"
                    className={[
                      "grid size-8 shrink-0 place-items-center rounded-full border text-sm font-bold",
                      notice?.tone === "danger"
                        ? "border-red-400/40 bg-red-500/10 text-red-300"
                        : "border-accent/40 bg-accent-soft text-accent",
                    ].join(" ")}
                  >
                    {notice?.tone === "danger" ? "!" : "i"}
                  </span>
                  <Modal.Heading>{notice?.title}</Modal.Heading>
                </div>
                <Modal.CloseTrigger aria-label={t("common.close")} />
              </Modal.Header>
              <Modal.Body>
                <p className="text-ink-dim text-sm whitespace-pre-wrap">
                  {notice?.message}
                </p>
              </Modal.Body>
              <Modal.Footer className="flex justify-end">
                <Button variant="primary" onPress={() => setNotice(null)}>
                  {t("common.ok")}
                </Button>
              </Modal.Footer>
            </Modal.Dialog>
          </Modal.Container>
        </Modal.Backdrop>
      </Modal>
    </NoticeContext.Provider>
  );
}

export function useNotice(): NoticeContextValue {
  const value = useContext(NoticeContext);
  if (!value) throw new Error("useNotice must be used inside NoticeProvider");
  return value;
}
