/** Wire types mirroring app/api/schemas.py. */

export interface PageMeta {
  page: number;
  pages: number;
  total: number;
}

export interface Paged<T> {
  items: T[];
  meta: PageMeta;
}

export interface Board {
  id: number;
  url: string;
  display_url: string;
  title: string;
  slug: string | null;
  status: string;
  is_active: boolean;
  pin_count: number;
  downloaded_count: number;
  skipped_count: number;
  error_count: number;
  processed_count: number;
  auto_resync: boolean;
  last_error: string | null;
  log_tail: string | null;
  credential_id: number | null;
  covers: string[];
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
}

export interface Pin {
  id: number;
  board_id: number;
  filename: string;
  rel_path: string;
  url: string;
  media_type: "image" | "video";
  width: number | null;
  height: number | null;
  title: string | null;
  description: string | null;
  source_url: string | null;
  file_size: number | null;
  dup_group: number | null;
  storage_backend: string;
  tags: string[];
}

export interface DuplicateGroup {
  group_id: number;
  keeper_id: number;
  pins: Pin[];
}

export interface Credential {
  id: number;
  name: string;
  status: string;
  note: string | null;
  last_error: string | null;
  last_checked_at: string | null;
}

export interface Stats {
  boards: number;
  pins: number;
  sessions: number;
}

export interface SidebarCounts {
  boards: number;
  creds: number;
  creds_active: number;
  syncing: number;
  removable: number;
}

export interface SettingsPayload {
  values: Record<string, string | number | boolean>;
  defaults: Record<string, string | number | boolean>;
  editable: string[];
  max_concurrency: number;
  queue_up: boolean;
}

export interface DedupStatus {
  running?: boolean;
  phase?: string;
  cur?: number;
  total?: number;
  groups?: number;
  removable: number;
  total_groups: number;
  at?: string;
}
