import { Button, Card, Input } from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { Credential } from "../api/types";
import { ErrorNote, Loading, PageHeader, StatusChip } from "../components/common";
import { useQuery } from "../hooks/useQuery";
import { LANG_NAMES, SUPPORTED, useI18n, type Locale, type MessageKey } from "../i18n";

/** Editable settings, grouped the way the page presents them. */
const GROUPS: { title: MessageKey; hint?: MessageKey; fields: SettingField[] }[] = [
  {
    title: "settings.schedules",
    hint: "settings.schedules_hint",
    fields: [
      { key: "resync_cron", label: "settings.resync_cron", hint: "settings.resync_cron_hint", mono: true },
      { key: "refresh_cron", label: "settings.refresh_cron", hint: "settings.refresh_cron_hint", mono: true },
      { key: "dedup_cron", label: "settings.dedup_cron", hint: "settings.dedup_cron_hint", mono: true },
    ],
  },
  {
    title: "settings.download",
    fields: [
      { key: "dl_sleep", label: "settings.dl_sleep", hint: "settings.dl_sleep_hint" },
      {
        key: "pin_stall_timeout",
        label: "settings.pin_stall_timeout",
        hint: "settings.pin_stall_timeout_hint",
      },
    ],
  },
  {
    title: "settings.display",
    fields: [
      { key: "per_page_boards", label: "settings.per_page_boards" },
      { key: "per_page_pins", label: "settings.per_page_pins" },
      { key: "per_page_dupes", label: "settings.per_page_dupes" },
    ],
  },
];

interface SettingField {
  key: string;
  label: MessageKey;
  hint?: MessageKey;
  mono?: boolean;
}

export function SettingsPage() {
  const { t, locale, setLocale } = useI18n();
  const settings = useQuery(() => api.settings(), []);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  // Seed the form once the server values arrive.
  useEffect(() => {
    if (!settings.data) return;
    const next: Record<string, string> = {};
    for (const [key, value] of Object.entries(settings.data.values)) {
      next[key] = String(value);
    }
    setDraft(next);
  }, [settings.data]);

  const save = async () => {
    setBusy(true);
    try {
      const payload = await api.saveSettings(draft);
      settings.setData(payload);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    } finally {
      setBusy(false);
    }
  };

  if (settings.loading && !settings.data) return <Loading />;

  return (
    <div className="w-full">
      <PageHeader title={t("settings.title")} />

      {settings.error ? (
        <ErrorNote message={settings.error} onRetry={settings.refresh} />
      ) : null}

      <div className="flex flex-col gap-4">
        {GROUPS.map((group) => (
          <Card key={group.title} className="bg-card border-line border">
            <Card.Header className="px-5 pt-5">
              <Card.Title className="text-base">{t(group.title)}</Card.Title>
              {group.hint ? (
                <Card.Description className="text-ink-dim text-xs">
                  {t(group.hint)}
                </Card.Description>
              ) : null}
            </Card.Header>
            <Card.Content className="grid grid-cols-1 gap-4 p-5 md:grid-cols-3">
              {group.fields.map((field) => (
                <label key={field.key} className="flex flex-col gap-1.5">
                  <span className="text-sm font-medium">{t(field.label)}</span>
                  <Input
                    value={draft[field.key] ?? ""}
                    className={field.mono ? "font-mono" : undefined}
                    onChange={(e) =>
                      setDraft((prev) => ({ ...prev, [field.key]: e.target.value }))
                    }
                  />
                  {field.hint ? (
                    <span className="text-ink-dim text-xs">{t(field.hint)}</span>
                  ) : null}
                </label>
              ))}
            </Card.Content>
          </Card>
        ))}

        {/* Language is client-side state, so it applies immediately and is not
            part of the saved payload. */}
        <Card className="bg-card border-line border">
          <Card.Header className="px-5 pt-5">
            <Card.Title className="text-base">{t("settings.language")}</Card.Title>
            <Card.Description className="text-ink-dim text-xs">
              {t("settings.language_hint")}
            </Card.Description>
          </Card.Header>
          <Card.Content className="flex flex-row flex-wrap items-center gap-2 p-5">
            {SUPPORTED.map((code) => (
              <Button
                key={code}
                size="sm"
                variant={locale === code ? "primary" : "ghost"}
                onPress={() => setLocale(code as Locale)}
              >
                {LANG_NAMES[code]}
              </Button>
            ))}
          </Card.Content>
        </Card>

        {/* Read-only runtime facts, then the save action. */}
        <Card className="bg-card border-line border">
          {/* Read-only runtime facts on the left, the save actions on the right;
              `mr-auto` keeps the facts left-aligned even when the row wraps. */}
          <Card.Content className="flex w-full flex-row flex-wrap items-end gap-6 p-5">
            <div className="mr-auto flex flex-wrap gap-8">
              <div>
                <div className="text-sm font-medium">{t("settings.concurrency")}</div>
                <div className="font-mono text-lg">{settings.data?.max_concurrency ?? "—"}</div>
                <div className="text-ink-dim text-xs">{t("settings.concurrency_hint")}</div>
              </div>
              <div>
                <div className="text-sm font-medium">{t("settings.queue")}</div>
                <div
                  className={settings.data?.queue_up ? "text-accent" : "text-red-300"}
                >
                  {settings.data?.queue_up ? t("settings.queue_up") : t("settings.queue_down")}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <AnimatePresence>
                {saved && (
                  <motion.span
                    initial={{ opacity: 0, x: 8 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0 }}
                    className="text-accent text-sm"
                  >
                    {t("settings.saved")}
                  </motion.span>
                )}
              </AnimatePresence>
              <Button
                variant="ghost"
                isDisabled={busy}
                onPress={() => void api.syncAll()}
              >
                {t("settings.sync_all")}
              </Button>
              <Button variant="primary" isDisabled={busy} onPress={() => void save()}>
                {t("settings.save")}
              </Button>
            </div>
          </Card.Content>
        </Card>

        <SessionsCard />
      </div>
    </div>
  );
}

function SessionsCard() {
  const { t } = useI18n();
  const creds = useQuery(() => api.credentials(), []);
  const [name, setName] = useState("");
  const [cookies, setCookies] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.addCredential({ name, cookies });
      setName("");
      setCookies("");
      creds.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="bg-card border-line border">
      <Card.Header className="px-5 pt-5">
        <Card.Title className="text-base">{t("settings.sessions")}</Card.Title>
        <Card.Description className="text-ink-dim text-xs">
          {t("settings.sessions_hint")}
        </Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-4 p-5">
        {(creds.data ?? []).length === 0 ? (
          <p className="text-ink-dim text-sm">{t("settings.cred_empty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {(creds.data ?? []).map((cred) => (
              <CredentialRow key={cred.id} cred={cred} onChanged={creds.refresh} />
            ))}
          </ul>
        )}

        <div className="border-line grid grid-cols-1 gap-3 border-t pt-4 md:grid-cols-[12rem_1fr_auto]">
          <Input
            aria-label={t("settings.cred_name")}
            placeholder={t("settings.cred_name")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <textarea
            aria-label={t("settings.cred_cookies")}
            placeholder={t("settings.cred_cookies")}
            value={cookies}
            rows={3}
            className="bg-card-raised border-line rounded-[10px] border px-3 py-2 font-mono text-xs"
            onChange={(e) => setCookies(e.target.value)}
          />
          <Button
            variant="primary"
            isDisabled={busy || !name.trim() || !cookies.trim()}
            onPress={() => void add()}
          >
            {t("settings.cred_add")}
          </Button>
        </div>

        {error ? <p className="text-sm text-red-300">{error}</p> : null}
      </Card.Content>
    </Card>
  );
}

function CredentialRow({
  cred,
  onChanged,
}: {
  cred: Credential;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="bg-card-raised flex flex-wrap items-center justify-between gap-3 rounded-[10px] px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{cred.name}</span>
          <StatusChip status={cred.status} />
        </div>
        <div className="text-ink-dim text-xs">
          {t("settings.cred_checked", {
            at: cred.last_checked_at
              ? new Date(cred.last_checked_at).toLocaleString()
              : t("common.never"),
          })}
        </div>
        {cred.last_error ? (
          <div className="truncate text-xs text-red-300">{cred.last_error}</div>
        ) : null}
      </div>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="ghost"
          isDisabled={busy}
          onPress={() => void act(() => api.validateCredential(cred.id))}
        >
          {t("settings.cred_validate")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          isDisabled={busy}
          onPress={() => void act(() => api.deleteCredential(cred.id))}
        >
          {t("settings.cred_delete")}
        </Button>
      </div>
    </li>
  );
}
