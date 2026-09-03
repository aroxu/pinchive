import { Button, Input } from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Pin } from "../api/types";
import { MediaLightbox } from "../components/MediaLightbox";
import { PinCard } from "../components/PinCard";
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

const SORTS: { value: string; key: MessageKey }[] = [
  { value: "new", key: "board.sort.new" },
  { value: "old", key: "board.sort.old" },
  { value: "big", key: "board.sort.big" },
  { value: "small", key: "board.sort.small" },
  { value: "name", key: "board.sort.name" },
];

const TYPES: { value: string; key: MessageKey }[] = [
  { value: "", key: "board.type.all" },
  { value: "image", key: "board.type.image" },
  { value: "video", key: "board.type.video" },
];

// Masonry column widths per zoom level.
const VIEW_WIDTH = { s: "9rem", m: "13rem", l: "18rem" } as const;
type ViewSize = keyof typeof VIEW_WIDTH;

export function BoardDetailPage() {
  const { t } = useI18n();
  const { boardId } = useParams();
  const id = Number(boardId);

  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [sort, setSort] = useState("new");
  const [dupes, setDupes] = useState(false);
  const [view, setView] = useState<ViewSize>("m");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [preview, setPreview] = useState<Pin | null>(null);
  const [tagName, setTagName] = useState("");
  const [logOpen, setLogOpen] = useState(false);

  const board = useQuery(() => api.board(id), [id], {
    pollMs: 4000,
    enabled: Number.isFinite(id),
  });
  const active = board.data?.is_active ?? false;

  const pins = useQuery(
    () => api.pins(id, { q, type, sort, dupes, page }),
    [id, q, type, sort, dupes, page],
    // Only poll while a download is running; a finished board is static.
    { pollMs: active ? 3000 : undefined, enabled: Number.isFinite(id) },
  );

  const items = pins.data?.items ?? [];
  const selectedIds = useMemo(() => [...selected], [selected]);

  const toggle = (pin: Pin) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pin.id)) next.delete(pin.id);
      else next.add(pin.id);
      return next;
    });
  };

  const bulk = async (action: string) => {
    if (selectedIds.length === 0) return;
    if (action === "delete" && !window.confirm(t("board.bulk_delete_confirm", { n: selectedIds.length }))) {
      return;
    }
    await api.bulkPins({ pin_ids: selectedIds, name: tagName, action });
    setSelected(new Set());
    pins.refresh();
    board.refresh();
  };

  return (
    <div className="w-full">
      <PageHeader
        title={board.data?.title ?? t("common.loading")}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <Link to="/" className="hover:text-accent underline-offset-2 hover:underline">
              ← {t("board.back")}
            </Link>
            {board.data ? <StatusChip status={board.data.status} /> : null}
            <span className="font-mono">
              {t("board.pins", { n: pins.data?.meta.total ?? board.data?.pin_count ?? 0 })}
            </span>
          </span>
        }
        actions={
          <>
            <Button
              size="sm"
              variant="ghost"
              isDisabled={active}
              onPress={() => void api.resyncBoard(id).then(board.refresh)}
            >
              {t("boards.resync")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              isDisabled={active}
              onPress={() => void api.resyncBoard(id, true).then(board.refresh)}
            >
              {t("boards.full_resync")}
            </Button>
          </>
        }
      />

      {/* toolbar */}
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <Input
          aria-label={t("board.search")}
          placeholder={t("board.search")}
          value={q}
          className="w-full sm:w-56"
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <ToolbarSelect
          value={type}
          options={TYPES}
          onChange={(v) => {
            setType(v);
            setPage(1);
          }}
        />
        <ToolbarSelect value={sort} options={SORTS} onChange={setSort} />
        <Button
          size="sm"
          variant={dupes ? "primary" : "ghost"}
          onPress={() => {
            setDupes((d) => !d);
            setPage(1);
          }}
        >
          {t("board.dupes_only")}
        </Button>
        <div className="flex gap-1">
          {(Object.keys(VIEW_WIDTH) as ViewSize[]).map((size) => (
            <Button
              key={size}
              size="sm"
              variant={view === size ? "primary" : "ghost"}
              onPress={() => setView(size)}
              aria-label={`${t("board.size")} ${size.toUpperCase()}`}
            >
              {size.toUpperCase()}
            </Button>
          ))}
        </div>
      </div>

      {/* bulk bar slides in only when something is selected */}
      <AnimatePresence>
        {selectedIds.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -8, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -8, height: 0 }}
            transition={{ duration: 0.18 }}
            className="bg-card-raised rounded-panel mb-4 overflow-hidden"
          >
            <div className="flex flex-wrap items-center gap-2 p-3">
              <span className="font-mono text-sm">
                {t("board.selected", { n: selectedIds.length })}
              </span>
              <Input
                aria-label={t("board.tag_placeholder")}
                placeholder={t("board.tag_placeholder")}
                value={tagName}
                className="w-40"
                onChange={(e) => setTagName(e.target.value)}
              />
              <Button size="sm" variant="ghost" onPress={() => void bulk("add")}>
                {t("board.bulk_tag")}
              </Button>
              <Button size="sm" variant="ghost" onPress={() => void bulk("remove")}>
                {t("board.bulk_untag")}
              </Button>
              <Button size="sm" variant="danger" onPress={() => void bulk("delete")}>
                {t("board.bulk_delete")}
              </Button>
              <Button size="sm" variant="ghost" onPress={() => setSelected(new Set())}>
                {t("board.clear_selection")}
              </Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {pins.error ? <ErrorNote message={pins.error} onRetry={pins.refresh} /> : null}

      {pins.loading && items.length === 0 ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState message={t("board.empty")} />
      ) : (
        <div className="masonry" style={{ columnWidth: VIEW_WIDTH[view] }}>
          {items.map((pin) => (
            <PinCard
              key={pin.id}
              pin={pin}
              selectable
              selected={selected.has(pin.id)}
              onOpen={setPreview}
              onToggle={toggle}
            />
          ))}
        </div>
      )}

      <Pager
        page={pins.data?.meta.page ?? 1}
        pages={pins.data?.meta.pages ?? 1}
        onChange={setPage}
      />

      {/* Download log: collapsed by default, below the pins. */}
      {board.data?.log_tail ? (
        <section className="border-line mt-10 border-t pt-4">
          <button
            type="button"
            className="text-ink-dim hover:text-ink flex items-center gap-2 text-sm"
            onClick={() => setLogOpen((open) => !open)}
            aria-expanded={logOpen}
          >
            <motion.span animate={{ rotate: logOpen ? 90 : 0 }} transition={{ duration: 0.15 }}>
              ▸
            </motion.span>
            {t("board.log")}
          </button>
          <AnimatePresence initial={false}>
            {logOpen && (
              <motion.pre
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="bg-panel text-ink-dim mt-3 overflow-x-auto rounded-[10px] p-4 font-mono text-xs whitespace-pre-wrap"
              >
                {board.data.log_tail}
              </motion.pre>
            )}
          </AnimatePresence>
        </section>
      ) : null}

      <MediaLightbox pin={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

function ToolbarSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; key: MessageKey }[];
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  return (
    <select
      className="bg-card-raised border-line rounded-[10px] border px-3 py-2 text-sm"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((option) => (
        <option key={option.value || "all"} value={option.value}>
          {t(option.key)}
        </option>
      ))}
    </select>
  );
}
