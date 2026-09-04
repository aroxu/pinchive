import { Button, Modal, Spinner } from "@heroui/react";

import { useI18n } from "../i18n";

/**
 * In-app confirmation.
 *
 * `window.confirm` is unreliable here: embedded/in-app browsers may suppress
 * it entirely, which silently resolves to "cancel" and makes a destructive
 * action look like it did nothing at all.
 */
export function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel,
  isBusy,
  busyLabel,
  error,
  onConfirm,
  onClose,
}: {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  isBusy?: boolean;
  busyLabel?: string;
  error?: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Modal
      isOpen={isOpen}
      // Dismissing mid-request would hide the only feedback the user has.
      onOpenChange={(open) => !open && !isBusy && onClose()}
    >
      <Modal.Backdrop>
        <Modal.Container size="sm">
          <Modal.Dialog>
            <Modal.Header>
              <Modal.Heading>{title}</Modal.Heading>
              <Modal.CloseTrigger aria-label={t("common.close")} />
            </Modal.Header>
            <Modal.Body className="flex flex-col gap-3">
              <p className="text-sm">{message}</p>

              {/* A large board takes a moment to remove: say so, otherwise the
                  dialog just sits there and the action looks dead. */}
              {isBusy ? (
                <div className="text-ink-dim flex flex-row items-center gap-2 text-sm">
                  <Spinner size="sm" />
                  <span>{busyLabel ?? t("common.loading")}</span>
                </div>
              ) : null}

              {error ? <p className="text-sm text-red-300">{error}</p> : null}
            </Modal.Body>
            <Modal.Footer className="flex flex-row justify-end gap-2">
              <Button variant="ghost" isDisabled={isBusy} onPress={onClose}>
                {t("common.cancel")}
              </Button>
              <Button variant="danger" isDisabled={isBusy} onPress={onConfirm}>
                {isBusy ? (busyLabel ?? t("common.loading")) : confirmLabel}
              </Button>
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}
