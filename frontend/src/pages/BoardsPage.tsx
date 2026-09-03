import { Button, Card, Input, ProgressBar, Switch } from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Board } from "../api/types";
import { AddBoardDialog } from "../components/AddBoardDialog";
import {
  EmptyState,
  ErrorNote,
  Loading,
  PageHeader,
  Pager,
  StatusChip,
} from "../components/common";
import { useQuery } from "../hooks/useQuery";
import { useI18n, type MessageKey } from "../i18n";

const STATUS_FILTERS: { value: string; key: MessageKey }[] = [
  { value: "", key: "boards.status.all" },
  { value: "working", key: "boards.status.working" },
  { value: "done", key: "boards.status.done" },
];

export function BoardsPage() {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);

  const boards = useQuery(
    () => api.boards({ q, status, page }),
    [q, status, page],
    // Poll so a running download updates progress without a manual refresh.
    { pollMs: 4000 },
  );
  const stats = useQuery(() => api.stats(), [], { pollMs: 8000 });

  const items = boards.data?.items ?? [];

  return (
    <div className="mx-auto w-full">
      <PageHeader
        title={t("boards.title")}
        subtitle={
          stats.data ? (
            <span className="font-mono">
              {stats.data.boards} · {stats.data.pins} · {stats.data.sessions}
            </span>
          ) : null
        }
        actions={
          <>
            <Input
              aria-label={t("boards.search")}
              placeholder={t("boards.search")}
              value={q}
              className="w-full sm:w-56"
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
            />
            <div className="flex gap-1">
              {STATUS_FILTERS.map((f) => (
                <Button
                  key={f.value || "all"}
                  size="sm"
                  variant={status === f.value ? "primary" : "ghost"}
                  onPress={() => {
                    setStatus(f.value);
                    setPage(1);
                  }}
                >
                  {t(f.key)}
                </Button>
              ))}
            </div>
            <Button size="sm" variant="primary" onPress={() => setAdding(true)}>
              {t("boards.add")}
            </Button>
          </>
        }
      />

      {boards.error ? (
        <ErrorNote message={boards.error} onRetry={boards.refresh} />
      ) : null}

      {boards.loading && items.length === 0 ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState message={q || status ? t("boards.empty_filtered") : t("boards.empty")} />
      ) : (
        <motion.div
          layout
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
        >
          <AnimatePresence mode="popLayout">
            {items.map((board) => (
              <BoardCard key={board.id} board={board} onChanged={boards.refresh} />
            ))}
          </AnimatePresence>
        </motion.div>
      )}

      <Pager page={boards.data?.meta.page ?? 1} pages={boards.data?.meta.pages ?? 1} onChange={setPage} />

      <AddBoardDialog
        isOpen={adding}
        onClose={() => setAdding(false)}
        onCreated={() => {
          setAdding(false);
          boards.refresh();
        }}
      />
    </div>
  );
}

function BoardCard({ board, onChanged }: { board: Board; onChanged: () => void }) {
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
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
    >
      <Card className="bg-card border-line hover:border-accent/50 h-full overflow-hidden border transition-colors">
        <Link to={`/boards/${board.id}`} className="block">
          <Collage covers={board.covers} />
        </Link>

        <Card.Content className="flex flex-col gap-3 p-4">
          <div className="flex items-start justify-between gap-2">
            <Link to={`/boards/${board.id}`} className="min-w-0">
              <h3 className="truncate font-semibold">{board.title}</h3>
              <p className="text-ink-dim truncate text-xs">{board.display_url}</p>
            </Link>
            <StatusChip status={board.status} />
          </div>

          {board.is_active ? (
            <ProgressBar value={board.progress_pct} className="w-full">
              <ProgressBar.Track>
                <ProgressBar.Fill />
              </ProgressBar.Track>
            </ProgressBar>
          ) : null}

          <div className="flex items-baseline justify-between">
            <span className="text-ink-dim text-xs">{t("boards.saved_pins")}</span>
            <span className="font-mono text-lg font-semibold">{board.pin_count}</span>
          </div>

          {board.last_error ? (
            <p className="truncate text-xs text-red-300" title={board.last_error}>
              {board.last_error}
            </p>
          ) : null}

          <div className="border-line flex items-center justify-between border-t pt-3">
            <Switch
              isSelected={board.auto_resync}
              isDisabled={busy}
              onChange={() => void act(() => api.toggleAutoResync(board.id))}
            >
              <Switch.Content>
                <span className="text-xs">{t("boards.auto_resync")}</span>
              </Switch.Content>
              <Switch.Control>
                <Switch.Thumb />
              </Switch.Control>
            </Switch>

            <div className="flex gap-1">
              <Button
                size="sm"
                variant="ghost"
                isDisabled={busy || board.is_active}
                onPress={() => void act(() => api.resyncBoard(board.id))}
              >
                {t("boards.resync")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                isDisabled={busy}
                onPress={() => {
                  if (window.confirm(t("boards.delete_confirm"))) {
                    void act(() => api.deleteBoard(board.id));
                  }
                }}
              >
                {t("boards.delete")}
              </Button>
            </div>
          </div>
        </Card.Content>
      </Card>
    </motion.div>
  );
}

/** 2x2 collage of the first four archived images. */
function Collage({ covers }: { covers: string[] }) {
  if (covers.length === 0) {
    return <div className="bg-card-raised aspect-[4/3] w-full" />;
  }
  return (
    <div className="bg-card-raised grid aspect-[4/3] w-full grid-cols-2 grid-rows-2 gap-0.5">
      {covers.slice(0, 4).map((src) => (
        <div key={src} className="min-h-0 min-w-0 overflow-hidden">
          <img src={src} alt="" className="size-full object-cover" loading="lazy" />
        </div>
      ))}
    </div>
  );
}
