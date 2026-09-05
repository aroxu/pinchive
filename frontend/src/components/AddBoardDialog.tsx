import { Button, Input, Modal, Switch } from "@heroui/react";
import { useState } from "react";

import { api } from "../api/client";
import { useQuery } from "../hooks/useQuery";
import { useI18n } from "../i18n";

export function AddBoardDialog({
  isOpen,
  onClose,
  onCreated,
}: {
  isOpen: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useI18n();
  const [url, setUrl] = useState("");
  const [auto, setAuto] = useState(true);
  const [credentialId, setCredentialId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Only fetch sessions while the dialog is open.
  const creds = useQuery(() => api.credentials(), [], { enabled: isOpen });

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.addBoard({
        url,
        auto,
        credential_id: !auto && credentialId ? Number(credentialId) : null,
      });
      setUrl("");
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onOpenChange={(open) => !open && onClose()}>
      <Modal.Backdrop className="backdrop-blur-sm">
        <Modal.Container size="md">
          <Modal.Dialog>
            <Modal.Header>
              <Modal.Heading>{t("boards.add")}</Modal.Heading>
              <Modal.CloseTrigger aria-label={t("common.close")} />
            </Modal.Header>

            <Modal.Body className="flex flex-col gap-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">{t("boards.url_label")}</span>
                <Input
                  autoFocus
                  value={url}
                  placeholder="https://www.pinterest.com/user/board/"
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && url.trim()) void submit();
                  }}
                />
                <span className="text-ink-dim text-xs">{t("boards.url_hint")}</span>
              </label>

              <div className="flex flex-col gap-1.5">
                <Switch isSelected={auto} onChange={setAuto}>
                  <Switch.Content>
                    <Switch.Control>
                      <Switch.Thumb />
                    </Switch.Control>
                    <span className="text-sm">{t("boards.auto_auth")}</span>
                  </Switch.Content>
                </Switch>
                <span className="text-ink-dim text-xs">{t("boards.auto_auth_hint")}</span>
              </div>

              {/* Manual session choice is only meaningful when auto is off. */}
              {!auto ? (
                <label className="flex flex-col gap-1.5">
                  <span className="text-sm font-medium">{t("boards.session")}</span>
                  <select
                    className="bg-card-raised border-line rounded-[10px] border px-3 py-2 text-sm"
                    value={credentialId}
                    onChange={(e) => setCredentialId(e.target.value)}
                  >
                    <option value="">{t("boards.session_none")}</option>
                    {(creds.data ?? []).map((c) => (
                      <option key={c.id} value={String(c.id)}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {error ? <p className="text-sm text-red-300">{error}</p> : null}
            </Modal.Body>

            <Modal.Footer className="flex justify-end gap-2">
              <Button variant="ghost" onPress={onClose}>
                {t("common.cancel")}
              </Button>
              <Button
                variant="primary"
                isDisabled={busy || !url.trim()}
                onPress={() => void submit()}
              >
                {t("boards.create")}
              </Button>
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}
