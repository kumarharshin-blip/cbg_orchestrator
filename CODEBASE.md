# Identification Orchestrator — Codebase Reference (Single Source of Truth)

> Last updated: 2026-03-09 (integrated AuthorizationMiddleware; refactored /identification to JSON body)  
> Update this file after every meaningful change.

---

## 1. Purpose

A **FastAPI-based async orchestrator** that receives file-processing requests (via direct upload or S3 path), persists them in a PostgreSQL database, and offloads heavy work to an external **split service** running at `http://localhost:8900/split`. It exposes simple REST endpoints so callers get an immediate `202 Accepted` and can poll for results.

---

## 2. Repository Layout

```
identification_orchestrator/
├── app/
│   ├── main.py                    # FastAPI app, all HTTP endpoints, startup/shutdown hooks
│   ├── config.py                  # Pydantic Settings, logging setup
│   ├── database.py                # Async SQLAlchemy engine, session factory, Base, init_db()
│   ├── models.py                  # SQLAlchemy ORM model: FileJob
│   ├── schemas.py                 # Pydantic I/O schemas
│   ├── crud.py                    # DB helpers (create / read / update)
│   ├── services.py                # Background processing logic, S3 download, restart helper
│   ├── get_location_api.py        # Standalone utility — generates S3 pre-signed upload URLs (not wired into main app)
│   └── authorization_security/
│       ├── authorization_middleware.py  # Starlette middleware: timestamp + SHA256 + Fernet decryption
│       ├── file_sanitizer.py           # File size/MIME/PDF security checks + first-page extraction
│       └── create_test_request.py      # Dev helper to construct signed test requests
├── uploads/                       # Runtime directory for locally-saved uploads (auto-created on startup)
├── requirements.txt
├── example_client.py
├── API_EXAMPLES.md
├── flow_diagram.txt               # Mermaid source for the flow diagram
└── CODEBASE.md                    # ← this file
```

---

## 3. Configuration (`app/config.py`)

All values come from environment variables or a `.env` file (via `pydantic-settings`).

| Setting | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:...@localhost:5432/vx_db_1` | Async PostgreSQL connection string |
| `APP_NAME` | `File Processing Service` | |
| `APP_VERSION` | `1.0.0` | |
| `DEBUG` | `True` | Enables SQLAlchemy echo |
| `UPLOAD_DIR` | `uploads` | Relative dir for saved uploads |
| `MAX_UPLOAD_SIZE` | `104857600` (100 MB) | Max file size for uploads |
| `LOG_FILE` | `pipeline.log` | Rotating log file (10 MB × 5 backups) |
| `LOG_LEVEL` | `INFO` | |
| `SPLIT_SERVICE_URL` | `http://localhost:8900/split` | External split/identification service |
| `SPLIT_SERVICE_TIMEOUT` | `300` s | httpx timeout for split service calls |
| `DEFAULT_CHANNEL_ID` | `default_channel` | Sent as form field to split service |
| `AWS_REGION` | `ap-south-1` | |
| `AWS_ACCESS_KEY_ID` | `""` | Optional; boto3 falls back to IAM/env vars |
| `AWS_SECRET_ACCESS_KEY` | `""` | Optional |

Settings are loaded once via `@lru_cache()` (`get_settings()`).

Logging is configured by `setup_logging()`:
- Rotating file handler → `pipeline.log`
- Console handler
- Noisy libs (`uvicorn.access`, `sqlalchemy.engine`) silenced to WARNING

---

## 4. Database (`app/database.py` + `app/models.py`)

### Engine & Session

```
engine = create_async_engine(DATABASE_URL, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
```

`get_db()` — FastAPI dependency; yields a session, no auto-commit; caller controls commits.

`init_db()` — runs `Base.metadata.create_all` (idempotent table creation) called at startup.

### `FileJob` table (`file_jobs`)

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | auto-increment |
| `filename` | String(255) nullable | original filename |
| `local_filepath` | String(512) nullable | path under `uploads/` |
| `s3_path` | String(1024) nullable | full S3 URL |
| `request_id` | String(100) unique | client-supplied unique ID |
| `split` | Integer (0/1) | SQLite-compatible boolean |
| `request_metadata` | Text nullable | JSON string |
| `status` | String(50) indexed | `Processing` → `Finished Processing` or `Failed` |
| `results` | Text nullable | JSON string of results or error |
| `created_at` | DateTime(tz) | server default `now()` |
| `updated_at` | DateTime(tz) | auto-updated `now()` |

Composite index: `ix_status_updated` on `(status, updated_at)`.

---

## 5. Schemas (`app/schemas.py`)

| Schema | Used for |
|---|---|
| `FileJobCreate` | Internal — creating a DB row |
| `FileJobResponse` | Full job read (all fields) |
| `FileJobStatusResponse` | `GET /status/{request_id}` response |
| `FileUploadResponse` | `POST /identification` 202 response |

---

## 6. CRUD (`app/crud.py`)

| Function | Description |
|---|---|
| `create_file_job(db, job)` | Insert a new `FileJob`; flushes but does NOT commit |
| `get_file_job_by_request_id(db, request_id)` | Fetch single job by `request_id` |
| `get_file_job_by_id(db, id)` | Fetch single job by PK |
| `get_jobs_by_status(db, status)` | List all jobs with given status |
| `update_job_status(db, request_id, status, results)` | Update `status`, `results`, `updated_at`; flushes but does NOT commit |
| `get_all_jobs(db, skip, limit)` | Paginated full listing |

> **Commit responsibility** lies with the caller (endpoint or `process_file`).

---

## 7. Services (`app/services.py`)

### `parse_s3_url(s3_url) → (bucket, key)`
Handles three URL formats:
- `s3://bucket/key`
- `https://bucket.s3.region.amazonaws.com/key`
- `https://s3.region.amazonaws.com/bucket/key`

### `download_from_s3(s3_url, destination_path)`
Uses `boto3.client('s3').download_file(...)`. Raises on any boto3 error.

### `process_file(request_id)` — the core background coroutine

Runs in its own `AsyncSessionLocal` session:

1. Fetch `FileJob` by `request_id`.
2. If `s3_path` and no `local_filepath` → download to a `tempfile`, track path for cleanup.
3. Validate local file exists.
4. Parse `request_metadata` JSON.
5. Open `file_path`, build multipart POST to `SPLIT_SERVICE_URL` with fields:
   - `file` (binary)
   - `split` (`"true"` / `"false"`)
   - `metadata` (JSON string)
   - `request_id`
   - `channel_id` (from settings)
6. On HTTP 200:
   - Check `response.json().get('error')` — if set, raise.
   - Build `result_data` dict, JSON-serialise → `results`.
   - `update_job_status(status="Finished Processing", results=...)` → commit.
7. On non-200 or exception:
   - Build error dict → `update_job_status(status="Failed", results=...)` → commit.
8. Always clean up temp file.

**`result_data` shape (on success):**
```json
{
  "request_id": "...",
  "filename": "...",
  "split": true,
  "processing_time_seconds": 1.23,
  "processed_at": "2026-03-09T...",
  "status": "success",
  "source": "s3 | local",
  "results": [...],
  "token_usage": {...},
  "channel_id": "...",
  "metadata": {...}
}
```

### `restart_processing_jobs()`
Called at startup. Queries all `status="Processing"` jobs and fires `asyncio.create_task(process_file(job.request_id))` for each.

---

## 8. API Endpoints (`app/main.py`)

### Startup hook
1. `init_db()` — create tables
2. Create `uploads/` directory
3. `restart_processing_jobs()` — resume interrupted jobs

### `GET /`
Returns service name, version, `"running"` status.

### `GET /health`
Returns `{"status": "healthy"}`. Bypassed by auth middleware.

### `POST /identification` → `202 Accepted`

Protected by `AuthorizationMiddleware`. The raw request body must be `{ "encrypted_payload": "<Fernet token>" }` with headers `client_id`, `timestamp`, `hash`. The middleware decrypts and replaces the body before the endpoint runs.

**Decrypted JSON payload fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `case_id` | str | ✅ | Client-supplied unique identifier — used as `request_id` internally |
| `split` | bool | ❌ (default `False`) | Whether to split the file |
| `s3_path` | str | ❌ | S3 URL; mutually exclusive with `file_data` |
| `metadata` | str or dict | ❌ | Arbitrary metadata (string or JSON-serialisable dict) |
| `file_data` | str | ❌ | Base64-encoded file bytes; mutually exclusive with `s3_path` |
| `filename` | str | ❌* | Original filename; **required** when `file_data` is provided |

**Logic:**
1. `client_id` extracted from `request.state` (set by middleware) — `500` if missing.
2. Body parsed as JSON; `case_id` required → `400` if absent.
3. `case_id` becomes `request_id` for all downstream operations.
4. Exactly one of `file_data` / `s3_path` must be present → else `400`.
5. `metadata` normalised: dict/list → JSON string; validated as valid JSON if string.
6. If `file_data`: base64-decode → save to `uploads/{case_id}_{filename}`.
7. If `s3_path`: derive `filename` from path if not explicitly provided.
8. `create_file_job()` → `db.commit()`.
9. `asyncio.create_task(process_file(request_id))`.
10. Return `FileUploadResponse(request_id=case_id, status="Processing")`.

**Response schema (`FileUploadResponse`):**
```json
{ "request_id": "<case_id>", "status": "Processing", "message": "Request received and processing started" }
```

### `GET /status/{request_id}` → `200`

Returns `FileJobStatusResponse`:
```json
{ "request_id": "...", "status": "...", "results": "..." }
```
`404` if not found.

---

## 9. Authorization Middleware (`app/authorization_security/authorization_middleware.py`)

> **Status:** Registered in `main.py` via `app.add_middleware(AuthorizationMiddleware)` (after app creation).

**Bypassed paths:** `/health`, `/docs`, `/redoc`, `/openapi.json`, `/`

**Required request headers:**

| Header | Description |
|---|---|
| `client_id` | Channel identifier (also accepted as `client-id` or `clientId`) |
| `timestamp` | Unix timestamp in **milliseconds** (integer) |
| `hash` | `SHA256(api_key|client_id|timestamp|encrypted_payload)` hex digest |

**Request body:**
```json
{ "encrypted_payload": "<Fernet-encrypted JSON string>" }
```

**Channel config** loaded from env var `CLIENT_CONFIGS_JSON` (JSON string, e.g. injected as K8s secret):
```json
{
  "channel_id": {
    "api_key": "...",
    "encryption_key": "<44-char Fernet base64 key>",
    "billing_enabled": true
  }
}
```

**Flow:**
1. Validate timestamp within tolerance (default 300 s).
2. Look up `client_id` in `CLIENT_CONFIGS_JSON`; check `billing_enabled`.
3. Compute and verify SHA256 hash.
4. Fernet-decrypt `encrypted_payload`; parse as JSON.
5. Replace request body with decrypted bytes; set `request.state.client_id`.
6. Pass to downstream handler.

**Error codes:** `400` bad headers/body · `401` expired/wrong hash/decrypt fail · `403` channel not found/billing off · `503` config not loaded · `500` internal errors.

---

## 10. File Sanitizer (`app/authorization_security/file_sanitizer.py`)

> Used standalone; not wired into `POST /identification` currently.

**`FileSanitizer.sanitize(upload_file) → SanitizationResult`**

Steps:
1. Read bytes; reject if > `MAX_FILE_SIZE_MB` (env, default 10 MB).
2. Detect MIME with `python-magic`; must be in `{image/jpeg, image/png, image/tiff, application/pdf}`.
3. Scan for suspicious byte patterns (XSS, PHP, shell, XXE, JS event handlers).
4. PDFs: additional pattern scan for `/js`, `/javascript`, `/launch`.
5. PDFs: deep inspection via `pikepdf` (if available).
6. PDFs: extract first page via `PyMuPDF (fitz)` → render to JPEG.
7. Images: re-encode via `Pillow` to strip metadata / embedded code.

Returns `SanitizationResult(can_process: bool, file_content: bytes)`.

---

## 11. `get_location_api.py`

**Standalone utility** (not imported by `main.py`). Provides:

- `GET /getLocation?file_name=...` — returns a pre-signed S3 upload URL + `req_id` + `s3_key`.
- `POST /post-doc` — uploads a file to S3 (uses hardcoded `s3_key`; testing only).

Uses its own SQLAlchemy `SessionLocal` from a separate `engine.py` (not in this repo).

---

## 12. Job Status Lifecycle

```
[Client POST]
      │
      ▼
  "Processing"  ──── background process_file() ────►  "Finished Processing"
      │                                                       (success)
      └──────────────────────────────────────────────►  "Failed"
                                                           (any error)
```

On server restart, all `"Processing"` jobs are automatically re-queued.

---

## 13. Dependencies (`requirements.txt`)

| Package | Purpose |
|---|---|
| `fastapi==0.109.0` | Web framework |
| `uvicorn[standard]==0.27.0` | ASGI server |
| `python-multipart==0.0.6` | Form/file parsing |
| `sqlalchemy[asyncio]==2.0.25` | ORM + async support |
| `asyncpg==0.29.0` | Async PostgreSQL driver |
| `pydantic==2.5.3` | Data validation |
| `pydantic-settings==2.1.0` | Settings from env |
| `python-dotenv==1.0.0` | `.env` file loading |
| `httpx==0.27.0` | Async HTTP client (split service calls) |
| `requests==2.31.0` | Sync HTTP (example scripts) |
| `boto3==1.34.0` | AWS S3 |

Additional (used in `authorization_security/` but not in requirements.txt yet):
- `cryptography` (Fernet)
- `python-magic` (MIME detection)
- `Pillow` (image re-encode)
- `PyMuPDF` / `fitz` (PDF first-page extraction)
- `pikepdf` (deep PDF inspection)

---

## 14. Running the Service

```bash
# Activate venv
.venv\Scripts\Activate.ps1

# Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or directly:
```bash
python app/main.py
```

The external split service must be running at `http://localhost:8900/split` for jobs to complete successfully.

---

## 15. Known Gaps / TODO

- `FileSanitizer` is implemented but **not called** in `POST /identification` (intentionally deferred).
- `get_location_api.py` imports (`engine`, `models`, `schemas`) that don't exist in this repo — it's a legacy fragment and cannot be run as-is.
- `cryptography`, `python-magic`, `Pillow`, `PyMuPDF`, `pikepdf` are used in `authorization_security/` but not listed in `requirements.txt`.
- No retry logic for failed split-service calls.
- No authentication on `GET /status/{request_id}` — any caller can poll any `request_id`.
- `GET /status/{request_id}` does not go through the auth middleware (not in the bypass list, but only `POST /identification` is the protected write path — consider whether status polling should also require auth).
