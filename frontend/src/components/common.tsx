import { Button, Chip, Spinner } from "@heroui/react";
import type { ReactNode } from "react";

import { useI18n, type MessageKey } from "../i18n";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-bold tracking-tight">{title}</h1>
        {subtitle ? <div className="text-ink-dim mt-1 text-sm">{subtitle}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

const STATUS_KEYS: Record<string, MessageKey> = {
  pending: "status.pending",
  queued: "status.queued",
  downloading: "status.downloading",
  done: "status.done",
  // "partial" is an internal distinction; users only care that it finished.
  partial: "status.partial",
  error: "status.error",
  unchecked: "status.unchecked",
  active: "status.active",
  expired: "status.expired",
};

export function StatusChip({ status }: { status: string }) {
  const { t } = useI18n();
  const color =
    status === "error" || status === "expired"
      ? "danger"
      : status === "done" || status === "partial" || status === "active"
        ? "success"
        : "default";
  const key = STATUS_KEYS[status];
  return (
    <Chip size="sm" color={color} variant="soft">
      {key ? t(key) : status}
    </Chip>
  );
}

export function Pager({
  page,
  pages,
  onChange,
}: {
  page: number;
  pages: number;
  onChange: (page: number) => void;
}) {
  const { t } = useI18n();
  if (pages <= 1) return null;
  return (
    <div className="mt-8 flex items-center justify-center gap-3">
      <Button
        size="sm"
        variant="ghost"
        isDisabled={page <= 1}
        onPress={() => onChange(page - 1)}
      >
        ←
      </Button>
      <span className="text-ink-dim font-mono text-sm">
        {t("common.page", { page, pages })}
      </span>
      <Button
        size="sm"
        variant="ghost"
        isDisabled={page >= pages}
        onPress={() => onChange(page + 1)}
      >
        →
      </Button>
    </div>
  );
}

export function Loading() {
  const { t } = useI18n();
  return (
    <div className="text-ink-dim flex items-center justify-center gap-3 py-16 text-sm">
      <Spinner size="sm" />
      {t("common.loading")}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="border-line text-ink-dim rounded-panel border border-dashed px-6 py-16 text-center text-sm">
      {message}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <div className="rounded-panel border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span>{message || t("common.error")}</span>
        {onRetry ? (
          <Button size="sm" variant="ghost" onPress={onRetry}>
            {t("common.retry")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
