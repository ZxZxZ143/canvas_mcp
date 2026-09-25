# Public URL production strategy and local parity diagnostic

Canvas documents `GET /api/v1/files/:id/public_url` as an authenticated API for an **inline preview URL**. Its `submission_id` parameter allows access to an attachment belonging to a submission when Canvas verifies both membership and the caller's right to read that submission. The current upstream controller grants a URL when the caller may read that verified submission **or** has attachment download access. A successful response therefore proves that Canvas issued a capability, but does not by itself prove a separate attachment `:download` permission or that the capability serves original file bytes.

Sources: [Canvas Files API](https://canvas.instructure.com/doc/api/files.html#get-public-inline-preview-url) and [Canvas FilesController `public_url`](https://github.com/instructure/canvas-lms/blob/master/app/controllers/files_controller.rb).

Production `FileService.download_file(FileReference)` now refreshes source membership and metadata, requests this capability from the configured Canvas API with Bearer authorization, then downloads it anonymously through the guarded downloader. For a submission attachment it rechecks the current user's own submission and passes the verified submission ID. Failed capability acquisition or validation returns a safe error without a browser-route fallback. The URL stays inside infrastructure and is never logged, persisted, or printed.

`PublicUrlParity` remains a local, opt-in, one-candidate diagnostic. It separately probes the old browser route and the capability to compare their responses; it is not the production download call.

The URL must be HTTPS. The exact configured Canvas origin is accepted for API-issued capabilities; external origins require an exact entry in `CANVAS_DOWNLOAD_ORIGINS`. Every destination and redirect undergoes the DNS, IP, TLS, hop-limit, byte-limit, and response validation rules. The capability request and every following hop are anonymous, including a capability on the Canvas origin. A same-origin capability may use public/global DNS or only the configured exact `CANVAS_TRUSTED_PRIVATE_IPS` for private DNS; external origins and chains after leaving Canvas remain public-only. An unknown external origin produces `public_url_origin_unapproved`; the operator must verify it separately before approval. Neither a signed URL nor a wildcard domain is suitable for this setting.

Run in a dedicated local shell:

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode PublicUrlParity -Live -TrustedPrivateIps '192.168.4.200'
```

Use a trusted private IP only when the configured Canvas API hostname intentionally resolves to that verified institution address. The helper prompts locally for the Canvas origin, previously approved external download origins, and token. Share only its fixed `variant=...` lines, never the token or a signed URL. The diagnostic erases quarantined bytes after bounded format checks. A MIME mismatch produces `reason=mime_evidence_mismatch` and a failing exit status even if the bytes identify as the expected format.

If parity reports `public_url_origin_unapproved`, run the separate operator inspection in the same private shell:

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode PublicUrlOrigin -Live -TrustedPrivateIps '192.168.4.200'
```

This mode makes the same fresh, authenticated API request for one validated candidate, but performs no download. It prints only `public_url_origin=https://...` locally, with no signed path or query. Verify the exact HTTPS origin independently before entering it at the `CANVAS_DOWNLOAD_ORIGINS` prompt on a subsequent `PublicUrlParity` run. Do not paste a signed URL into that prompt or into chat.

The successful real Narxoz parity run used `https://canvas.narxoz.kz` with exact private-IP trust for `192.168.4.200`. The browser route returned HTTP 200 HTML. The authenticated API issued a same-origin capability; its anonymous GET returned HTTP 200 binary, identified and validated as DOCX, with `reason=none`. The subsequent installed production `FileSample` also passed normal download, format, hash and cleanup checks on 2026-09-25. Neither result disclosed a file identity or signed URL.
