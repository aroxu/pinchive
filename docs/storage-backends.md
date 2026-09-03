# Storage backends: local disk and S3 object storage

**Status: plan, not implemented.** Only the local backend ships today. This
document is the design Pinchive commits to *before* any S3 code is written, so
the seams that already exist stay the right seams.

## Where things stand

The seam is already in place. Every read, write, delete, and URL for archived
media goes through the `Storage` protocol in
[app/services/storage.py](../app/services/storage.py):

| Method | Purpose |
|---|---|
| `exists(rel_path)` | does this object exist |
| `size(rel_path)` | byte size, or `None` |
| `delete(rel_path)` | remove a media file and its `.json` sidecar |
| `delete_prefix(prefix)` | remove a whole board folder |
| `local_path(rel_path)` | a real filesystem path, or `None` if remote |
| `url_for(rel_path)` | browser-facing URL |

`LocalStorage` implements it against the data volume and serves objects from the
`/media` mount. `Pin.storage_backend` already records which backend owns each
row (`"local"` today, `"s3"` later), and the API exposes it, so a mixed archive
is representable without a schema change or an API break.

The key is deliberately backend-neutral: `rel_path` is a POSIX-style
`<board-folder>/<file>` string, relative to the archive root. That doubles as an
S3 object key with no translation.

## Why this is not a drop-in swap

Three things assume a real local file today, and each one needs a decision
rather than a wrapper:

1. **gallery-dl writes to a directory.** The downloader shells out to gallery-dl
   with `--directory <dest>`, and yt-dlp/ffmpeg mux video into that same
   directory. Neither can write to S3.
2. **Hashing reads files by path.** Duplicate detection hashes across a process
   pool using `local_path()`; `ProcessPoolExecutor` workers get a path, not a
   stream.
3. **`/media` is a `StaticFiles` mount.** Serving remote objects through the app
   would proxy every byte through Python — the opposite of the low-footprint
   goal.

## The plan

### 1. Download to a local staging directory, then publish

gallery-dl keeps writing to local disk. The staging directory is where the
existing per-board folder already lives, and it stays the unit of work for a
sync: partial-progress scans, `.part` cleanup, the per-board
`.gallery-dl-archive.db`, and the stall guard all keep working untouched.

After the reconcile step in `sync_service.finish_sync`, each newly downloaded
file is uploaded and — only on a confirmed upload — removed locally.

```
gallery-dl ──▶ staging dir ──▶ hash ──▶ upload ──▶ verify ──▶ drop local copy
                   │                                              │
                   └────────── on failure: keep local, retry ──────┘
```

Publishing after hashing (not before) means dedup keeps its cheap local read and
no object is ever uploaded twice under two names.

### 2. Serve media by redirect, not by proxy

`url_for()` becomes the single place that decides how bytes reach the browser:

- `LocalStorage` → `/media/<rel_path>`, served by `StaticFiles` as now.
- `S3Storage` → a **presigned GET URL** with a TTL (default 1 hour).

Because the React client only ever reads `pin.url` from the API, no component
changes. Two consequences to design for deliberately:

- **Cache correctness.** A presigned URL changes every time it is signed, which
  would defeat the browser cache and re-download images on every poll. Sign per
  `(rel_path, TTL-bucket)` and cache the result in-process, so the URL is stable
  within a bucket window and the browser can cache it.
- **Or skip signing entirely.** With a CDN or a public-read bucket, `url_for()`
  returns a plain stable URL. This is the better default for a bucket the user
  already fronts with CloudFront/R2.

A `/media/<rel_path>` redirect endpoint stays as a compatibility path for any
link that was persisted before the migration.

### 3. Hashing stays local, on a temp copy

`local_path()` returning `None` is the contract for "no real file". The dedup
worker gains one step: if `local_path()` is `None`, download the object to a
temporary file, hash it, delete it. Since hashes are computed once and stored,
this cost is paid only for genuinely new or re-hashed pins.

Two optimisations make this rare in practice:

- Newly downloaded pins are hashed **while still in staging**, before upload, so
  the normal path never re-downloads anything.
- `content_sha256` can be seeded from the S3 `ETag` for single-part uploads,
  where the ETag *is* the MD5 — useful for a cheap existence/integrity check,
  though not a substitute for SHA-256.

### 4. Configuration

New `PINCHIVE_*` variables, all optional, defaulting to today's behaviour:

| Var | Default | Meaning |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `S3_BUCKET` | — | target bucket |
| `S3_ENDPOINT_URL` | — | set for R2/MinIO/Backblaze; omit for AWS |
| `S3_REGION` | `us-east-1` | bucket region |
| `S3_ACCESS_KEY_ID` | — | credentials; omit to use the instance role |
| `S3_SECRET_ACCESS_KEY` | — | |
| `S3_PREFIX` | `""` | key prefix inside the bucket |
| `S3_PUBLIC_BASE_URL` | — | CDN base; when set, URLs are not signed |
| `S3_SIGNED_URL_TTL` | `3600` | presign lifetime in seconds |
| `STAGING_DIR` | `<data>/staging` | where downloads land before upload |

`boto3` is the one new dependency, and it is only imported when the S3 backend
is selected — a local-only install stays as slim as it is now.

### 5. Migration between backends

A `pinchive storage migrate` command, resumable and safe to interrupt:

1. Select pins whose `storage_backend` differs from the target.
2. For each: upload, verify (size, then checksum), set `storage_backend`,
   *then* delete the source. Commit per pin so a crash resumes cleanly.
3. Report objects that failed verification instead of deleting them.

The per-pin ordering is the important part: the row is only repointed after the
copy is provably readable at the destination, so an interrupted migration always
leaves every pin readable from exactly one backend.

### 6. Failure behaviour

| Failure | Behaviour |
|---|---|
| Upload fails | keep the staged local file, mark the pin `local`, retry next sync |
| Object missing on read | pin is flagged, surfaced in the UI; the row is not deleted |
| Credentials rejected | log and fall back to local staging so downloads keep working |
| Delete fails | tombstone still recorded, so a re-sync does not re-add the pin |

The rule throughout: **never lose the only copy, and never let a storage outage
stop archiving.** Degrading to local disk is always the fallback.

## Work breakdown

| # | Task | Depends on |
|---|---|---|
| 1 | `S3Storage` implementing `Storage` (+ tests against MinIO or `moto`) | — |
| 2 | `get_storage()` selects by config; `boto3` as an optional extra | 1 |
| 3 | Staging + publish step in `sync_service` | 1 |
| 4 | Temp-file hashing path in `dedup_service` when `local_path()` is `None` | 1 |
| 5 | `/media` redirect endpoint + URL signing cache | 1 |
| 6 | `pinchive storage migrate` command | 1-4 |
| 7 | Docs: compose example, IAM policy, CDN notes | 1-6 |

Steps 1 and 2 are self-contained and land without touching the sync path, so S3
support can be merged incrementally behind the existing config default.
