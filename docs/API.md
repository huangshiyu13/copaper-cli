# Copaper API v1

Base URL: the configured Copaper website plus `/api/v1`.

Every request uses `Authorization: Bearer <API Key>`. HTTPS is required outside loopback development. Session cookies do not authenticate these endpoints. Send keys only in the header, never in URLs or query parameters. Do not follow redirects with credentials.

Responses use JSON except file, output and ZIP downloads. Errors have `{"error":{"code":"...","message":"..."}}`. HTTP 401 means invalid/expired/revoked credentials; 403 means insufficient permissions; 404 also covers inaccessible projects; 409/412 means stale data or a conflict; 413 means an upload is too large; 429 means retry later according to `Retry-After`.

Keys have `read`, `write` and `compile` capabilities, an expiry, and optionally an allowlist of projects. A key cannot grant permissions beyond its owner's current project access. Read-only collaborators cannot write even with a write-capable key. Project-restricted keys cannot create projects.

Project and build identifiers are opaque strings. Use the identifiers returned by the API.

| Method | Endpoint | Capability | Result |
| --- | --- | --- | --- |
| GET | `/me` | authenticated | `user`, safe `key` metadata, `apiVersion: 1` |
| GET | `/projects?cursor=...` | read | `projects`, `nextCursor` |
| POST | `/projects` | write | Create from `name`, optional `template: blank/example` |
| GET | `/projects/{id}` | read | Project manifest |
| PATCH | `/projects/{id}` | write + owner | Rename using `name` |
| GET | `/projects/{id}/files?path=...` | read | Raw file bytes; optional `If-Match: "{hash}"` |
| PUT | `/projects/{id}/blobs/{hash}` | write | Stage raw file bytes as `application/octet-stream` |
| POST | `/projects/{id}/apply` | write | Apply staged changes against a manifest revision |
| POST | `/projects/{id}/compile` | compile | Compile with optional `root` and `compiler` |
| GET | `/projects/{id}/outputs/latest` | read | Most recent API compilation for this account |
| GET | `/projects/{id}/outputs/{build}/{name}` | read | Download an allowed output of that build |
| GET | `/projects/{id}/zip` | read | Source ZIP |

A manifest contains `id`, `name`, `compiler`, `revision` and `files`. Each file has `path`, `hash` (SHA-256 of its bytes) and `size`. Paths use forward slashes and are relative to the project. Clients must reject unsafe paths and validate bytes against hashes.

For a write:

1. Read the current manifest.
2. Upload changed bytes to the blob endpoint using their SHA-256. Uploaded content is limited to the authorized project and expires if unused.
3. POST `{"revision":"...","changes":[{"path":"main.tex","hash":"..."},{"path":"old.tex","hash":null}]}` to `/apply`.
4. Treat 409/412 as a request to re-read and reconcile changes. Never reuse an old revision to force an overwrite.

`null` deletes a file. A file already present in the same project may be moved using its existing hash. There must be no duplicate paths in a change list. Limits: 32 MiB per file, 128 MiB and 1500 files per project. Request concurrency and rate limits apply.

Compilation returns an opaque build `id`, `status`, `createdAt`, and `files` with names and sizes. Failed compilations may still have logs to download. A key needs read permission to download outputs. Build outputs expire and can be recreated with another compilation.

API Key creation, persistent display and revocation are account website operations; an API Key cannot create more API Keys.
