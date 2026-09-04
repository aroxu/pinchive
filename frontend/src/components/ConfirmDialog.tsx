import { Button, Modal } from "@heroui/react";

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
  onConfirm,
  onClose,
}: {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  isBusy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Modal isOpen={isOpen} onOpenChange={(open) => !open && onClose()}>
      <Modal.Backdrop>
        <Modal.Container size="sm">
          <Modal.Dialog>
            <Modal.Header>
              <Modal.Heading>{title}</Modal.Heading>
              <Modal.CloseTrigger aria-label={t("common.close")} />
            </Modal.Header>
            <Modal.Body>
              <p className="text-sm">{message}</p>
            </Modal.Body>
            <Modal.Footer className="flex flex-row justify-end gap-2">
              <Button variant="ghost" isDisabled={isBusy} onPress={onClose}>
                {t("common.cancel")}
              </Button>
              <Button variant="danger" isDisabled={isBusy} onPress={onConfirm}>
                {confirmLabel}
              </Button>
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}
