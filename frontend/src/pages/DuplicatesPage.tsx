import { Button, Card } from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

import { api } from "../api/client";
import type { DedupStatus, Pin } from "../api/types";
import { MediaLightbox } from "../components/MediaLightbox";
import { EmptyState, ErrorNote, Loading, PageHeader, Pager } from "../components/common";
import { useQuery } from "../hooks/useQuery";
import { useI18n } from "../i18n";

export function DuplicatesPage() {
  const { t } = useI18n();
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<Pin | null>(null);
  const [busy, setBusy] = useState(false);

  // Status polls continuously so a scan started elsewhere still shows up here.
  const status = useQuery(() => api.dedupStatus(), [], { pollMs: 2000 });
  const running = status.data?.running ?? false;

  const groups = useQuery(
    () => api.duplicates(page),
    [page, running],
    // While a scan runs the groups change underneath us, so refresh them too.
    { pollMs: running ? 3000 : undefined },
  );

  const items = groups.data?.items ?? [];

  const refreshAll = () => {
    groups.refresh();
    status.refresh();
  };

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      refreshAll();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full">
      <PageHeader
        title={t("dupes.title")}
        subtitle={t("dupes.subtitle")}
        actions={
          <>
            <Button
              size="sm"
              variant="ghost"
              isDisabled={busy || running}
              onPress={() => void act(() => api.rescanDuplicates())}
            >
              {running ? t("dupes.rescanning") : t("dupes.rescan")}
            </Button>
            <Button
              size="sm"
              variant="danger"
              isDisabled={busy || running || (status.data?.removable ?? 0) === 0}
              onPress={() => {
                if (window.confirm(t("dupes.delete_all_confirm"))) {
                  void act(() => api.resolveDuplicates({ all: true }));
                }
              }}
            >
              {t("dupes.delete_all")}
            </Button>
          </>
        }
      />

      <div className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-2">
        <Counter label={t("dupes.groups", { n: status.data?.total_groups ?? 0 })} />
        <Counter label={t("dupes.removable", { n: status.data?.removable ?? 0 })} accent />
        <ScanStatus status={status.data} />
      </div>

      {groups.error ? <ErrorNote message={groups.error} onRetry={groups.refresh} /> : null}

      {groups.loading && items.length === 0 ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState message={t("dupes.empty")} />
      ) : (
        <motion.div layout className="flex flex-col gap-4">
          <AnimatePresence mode="popLayout">
            {items.map((group) => (
              <motion.div
                key={group.group_id}
                layout
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.98 }}
                transition={{ duration: 0.2 }}
              >
                <Card className="bg-card border-line border">
                  <Card.Content className="flex flex-wrap gap-3 p-4">
                    {group.pins.map((pin) => (
                      <figure key={pin.id} className="w-40">
                        <button
                          type="button"
                          className="block w-full cursor-zoom-in"
                          onClick={() => setPreview(pin)}
                        >
                          <img
                            src={pin.url}
                            alt={pin.filename}
                            className="rounded-panel bg-card-raised aspect-square w-full object-cover"
                            loading="lazy"
                          />
                        </button>
                        <figcaption className="mt-2 flex items-center justify-between gap-2">
                          <span className="text-ink-dim font-mono text-[11px]">
                            {pin.width ?? "?"}×{pin.height ?? "?"}
                          </span>
                          {pin.id === group.keeper_id ? (
                            <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] font-semibold text-accent">
                              {t("dupes.keep")}
                            </span>
                          ) : (
                            <Button
                              size="sm"
                              variant="ghost"
                              isDisabled={busy}
                              aria-label={t("dupes.delete_copy")}
                              onPress={() =>
                                void act(() => api.resolveDuplicates({ pin_ids: [pin.id] }))
                              }
                            >
                              ✕
                            </Button>
                          )}
                        </figcaption>
                      </figure>
                    ))}
                  </Card.Content>
                </Card>
              </motion.div>
            ))}
          </AnimatePresence>
        </motion.div>
      )}

      <Pager
        page={groups.data?.meta.page ?? 1}
        pages={groups.data?.meta.pages ?? 1}
        onChange={setPage}
      />

      <MediaLightbox pin={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

function Counter({ label, accent }: { label: string; accent?: boolean }) {
  return (
    <span
      className={[
        "font-mono text-sm",
        accent ? "text-accent" : "text-ink-dim",
      ].join(" ")}
    >
      {label}
    </span>
  );
}

function ScanStatus({ status }: { status: DedupStatus | null | undefined }) {
  const { t } = useI18n();
  if (!status?.phase) return null;
  const label =
    status.phase === "hashing"
      ? t("dupes.phase.hashing", { cur: status.cur ?? 0, total: status.total ?? 0 })
      : status.phase === "grouping"
        ? t("dupes.phase.grouping")
        : status.phase === "queued"
          ? t("dupes.phase.queued")
          : status.at
            ? t("dupes.phase.done", { at: new Date(status.at).toLocaleString() })
            : null;
  if (!label) return null;
  return <span className="text-ink-dim font-mono text-xs">{label}</span>;
}
