/** Thin fetch wrapper + typed endpoint helpers. */
import type {
  Board,
  Credential,
  DedupStatus,
  DuplicateGroup,
  Paged,
  Pin,
  SettingsPayload,
  SidebarCounts,
  Stats,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!res.ok) {
    // FastAPI puts the message in `detail`; fall back to the status text.
    let message = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export interface BoardQuery {
  q?: string;
  status?: string;
  tag?: string;
  page?: number;
}

export interface PinQuery {
  q?: string;
  type?: string;
  dupes?: boolean;
  sort?: string;
  page?: number;
}

export const api = {
  boards: (query: BoardQuery = {}) =>
    request<Paged<Board>>(`/api/boards${qs({ ...query })}`),

  board: (id: number) => request<Board>(`/api/boards/${id}`),

  addBoard: (body: { url: string; credential_id?: number | null; auto?: boolean }) =>
    request<Board>("/api/boards", { method: "POST", body: JSON.stringify(body) }),

  resyncBoard: (id: number, full = false) =>
    request<Board>(`/api/boards/${id}/resync${qs({ full })}`, { method: "POST" }),

  toggleAutoResync: (id: number) =>
    request<Board>(`/api/boards/${id}/auto-resync`, { method: "POST" }),

  deleteBoard: (id: number) =>
    request<void>(`/api/boards/${id}`, { method: "DELETE" }),

  pins: (boardId: number, query: PinQuery = {}) =>
    request<Paged<Pin>>(`/api/boards/${boardId}/pins${qs({ ...query })}`),

  bulkPins: (body: { pin_ids: number[]; name?: string; action: string }) =>
    request<{ affected: number }>("/api/pins/bulk", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  duplicates: (page = 1) =>
    request<Paged<DuplicateGroup>>(`/api/duplicates${qs({ page })}`),

  dedupStatus: () => request<DedupStatus>("/api/duplicates/status"),

  rescanDuplicates: () =>
    request<{ queued: boolean }>("/api/duplicates/rescan", { method: "POST" }),

  resolveDuplicates: (body: { pin_ids?: number[]; all?: boolean }) =>
    request<{ deleted: number }>("/api/duplicates/resolve", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  credentials: () => request<Credential[]>("/api/credentials"),

  addCredential: (body: { name: string; cookies: string }) =>
    request<Credential>("/api/credentials", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  validateCredential: (id: number) =>
    request<Credential>(`/api/credentials/${id}/validate`, { method: "POST" }),

  deleteCredential: (id: number) =>
    request<void>(`/api/credentials/${id}`, { method: "DELETE" }),

  stats: () => request<Stats>("/api/stats"),

  sidebar: () => request<SidebarCounts>("/api/sidebar"),

  settings: () => request<SettingsPayload>("/api/settings"),

  saveSettings: (values: Record<string, string>) =>
    request<SettingsPayload>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(values),
    }),

  syncAll: () =>
    request<{ queued: boolean }>("/api/settings/sync-all", { method: "POST" }),
};
