# Initial-profile authentication parity investigation

Subsequent live evidence used `/api/v1/users/self/profile`, whereas production
uses `/api/v1/users/self`. Continue with the [transport parity probe](http-transport-parity.md),
not token creation. The helper's new `Transport` mode is the deliberate exception
to live profile preflight: it diagnoses that failing request without courses/files.
The findings below describe the completed credential-parity investigation.

## Finding and evidence boundary

The reported historical live failure has **not been reproduced or causally
identified**. The current source and installed package do not contain a
MIME-specific credential path. No evidence justifies changing Python
authentication, token validation, MIME acceptance or redirect authorization.
Redirect/MIME investigation is paused until the profile preflight succeeds.

The supplied observation is `operation=profile / authorization_error` with two
tokens. In the current HTTP client, HTTP **401** maps to `authentication_error`
and HTTP **403** maps to `authorization_error`. A local credential/provider scope
rejection can also raise `authorization_error`. Missing, empty or malformed
environment tokens instead raise `configuration_error` before HTTP. The supplied
code alone does not establish which origin rejected the request, why it was
rejected, or what was handed to the historical process. No response body, token,
header or hostname is requested as evidence.

## Concrete execution-path comparison

| Boundary | Foundation / academic smoke | File smoke | MIME probe |
| --- | --- | --- | --- |
| Environment at `run()` | `open_canvas_connection()` uses process mapping | Copies process mapping | Copies process mapping |
| CLI settings changes | None | Sample mode clamps `MAX_DOWNLOAD_BYTES` | Clamps `MAX_DOWNLOAD_BYTES` to probe cap |
| Settings loader | `load_settings` | Same | Same |
| Credential construction | `EnvironmentCredentialSource.from_environment` | Same, copied mapping | Same, copied mapping |
| Access scope | Local principal + fresh connection UUID | Same construction | Same construction |
| API client | `CanvasHttpClient` | Same | Same |
| Token retrieval | Scoped `get_access_token` via `_access_token` | Same | Same |
| First target | Canonical origin + `/api/v1/users/self` | Same | Same |
| Header construction | `Bearer ` + unmodified provider token | Same | Same |

There is no credential read at import time. `load_settings` and
`from_environment` run synchronously, without an intervening await, when the
composition context is entered. Later environment mutation does not alter an
already-created provider's token. The copies in file/MIME smoke do not alter the
credential string or base URL. No `.env`, keyring or alternate credential lookup
is used.

`CANVAS_APPLICATION_PROFILE` is read by MIME probe, but only supplied to
`MimeDiagnostic` **after** `get_profile()` succeeds. It scopes the diagnostic
registry together with canonical origin and the authenticated Canvas user; it is
not an HTTP header, credential selector, connection identity or Canvas principal.
Synthetic runs with `personal` and a different profile produced identical
authentication. The provider, not this setting, binds the authenticated user.

`https://canvas.narxoz.kz` and `https://canvas.narxoz.kz/` both normalize to the
same origin and exact `/api/v1/users/self` request. Tests use a mocked network
backend: that hostname is never contacted by these checks.

## Isolated mode and installed/source selection

Before changes, both project-venv invocations, with and without `-I`, selected
`installed_wheel`; all **53** installed Python sources matched the source tree's
file set and hashes. The `src/` layout is not automatically selected just because
the working directory is the project root.

Explicit subprocess tests demonstrate the potential difference: when
`PYTHONPATH` points to `src`, normal Python selects `project_source`, while `-I`
selects `installed_wheel`. Both run the same real CLI composition against the mock
HTTP encoder and pass parity. From an unrelated temporary working directory,
`CANVAS_BASE_URL` and `CANVAS_ACCESS_TOKEN` still reach configuration with `-I`.
Isolated mode ignores `PYTHONPATH`; it does **not** discard the Canvas environment.
It is retained in the canonical helper. These findings do not retroactively
identify the package or environment of an earlier failed shell.

Offline commands (synthetic token is internal; no live request or runtime files):

```powershell
& 'E:\canvas_mcp\.venv\Scripts\python.exe' 'E:\canvas_mcp\scripts\auth_parity.py'
& 'E:\canvas_mcp\.venv\Scripts\python.exe' -I 'E:\canvas_mcp\scripts\auth_parity.py'
```

Output is only a package-source class and a parity boolean. No paths, credential
values or request headers are printed. Real run functions, real credential
providers and the real HTTP encoder execute; only the transport is mocked. A
successful synthetic profile is deliberately stopped before course/file/MIME
work. CLI catch-all output from that stop is captured internally and is not a
real failure. Tests compare method, target, origin and exact bearer-header bytes
against the **same fixed synthetic expected request**, never a live credential.

## SecureString conversion

On this Windows PowerShell/.NET environment, BSTR/Marshal and
`NetworkCredential('', secureString).Password` produced identical strings from
the same SecureString for a synthetic token, punctuation/padding, empty input and
non-ASCII input. Only equality/emptiness booleans were returned. This eliminates
a reproduced conversion-method difference on this host, not a historical
copy/paste/input difference in another shell.

To compare the same locally entered SecureString without network access:

```powershell
& 'E:\canvas_mcp\scripts\Compare-CanvasSecureString.ps1'
```

Output is exactly `length_equal`, `content_equal`, `empty_a`, `empty_b` booleans.
An exception returns a failed comparison, never its details. The canonical live
helper retains the previously used BSTR method for consistency, not as a claim
that NetworkCredential was faulty. No Python security logic compensates for
shell conversion. Zero-freeing BSTR does not erase immutable .NET/Python strings.

## Offline credential presence

`scripts/credential_presence.py` passes an environment snapshot into the existing
composition, retrieves through the scoped provider and actual client's
`_access_token`, and checks an `AccessToken` with the identical nonempty value.
Socket connection and DNS entry points are forbidden. No HTTP call is made.
Output is exactly:

```text
credential_present = true
credential_empty = false
```

Here `present` means the **entire local handoff** succeeded, not merely that a
variable exists. Missing/empty input gives false/true; invalid nonempty input or
other configuration failure gives false/false. Exit 0 means handoff confirmed;
exit 1 means it was not. This is not proof of Canvas acceptance. The live helper
runs it automatically; do not put credentials in command arguments to run it.

## Canonical live helper

Use a dedicated, trusted, non-transcribed PowerShell terminal. Do not paste a
token into chat/files or enable tracing/debug logging. The helper prompts for an
HTTPS origin and hidden token; file modes also prompt for **previously approved**
CDN origins (blank is permitted). Never enter signed URLs or guess new origins.
Install the current wheel first; the helper uses the fixed project venv and `-I`.

Canonical profile/course smoke:

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode Profile -Live
```

Canonical headers-only MIME topology command, gated on a fresh successful
profile/course smoke using the **exact same token and environment**:

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode MimeTopology -Live
```

The latter dispatches `canvas_mcp.mime_probe --live
--allow-mismatch-diagnostic --redirect-topology` only after successful preflight.
Other modes use that same helper: `Academic`, `Files` (metadata), `FileSample`,
`MimeDiagnostic`. The last supports explicit `-RememberIfValidated`; topology
rejects remembering. No diagnostics run by default or without `-Live`.

The helper uses one hidden SecureString → BSTR → process environment handoff,
checks local presence, then runs the existing profile/course smoke. Preflight
includes course reads and may print the normalized display name; it is not a
profile-only or privacy-safe topology report. Failure at either preflight blocks
all downstream modes. The new helper does not alter production auth. It sets the
same origin/token, personal diagnostic profile, 20s request and 120s download
timeouts for its children; environment size limits and other settings remain
subject to existing validation.

Finally it removes `CANVAS_ACCESS_TOKEN`, zero-frees the BSTR, disposes the
SecureString and restores the previous nonsecret settings. Any preexisting token
variable is also removed, not restored: use a dedicated shell. No token is stored
in a file; process environments/immutable memory cannot promise zeroization.
Only an external runtime parent is created after successful preflight when the
selected mode needs it. Do not precreate managed leaves, relax ACLs or delete
unfamiliar files. Existing storage/origin policy remains authoritative.

All earlier live command examples now invoke this helper, so credential conversion
and cleanup do not drift across guides. Do not run file/MIME modes to investigate
the current failure until `Profile` succeeds. No MCP work is included.

## Review and verification

The read-only `credential-parity-reviewer` found no HIGH/CRITICAL credential-path
issue and independently verified the shared path, URL normalization, profile
separation and error-code distinction. Its suggestions to enforce offline
presence checking and exercise helper cleanup/downstream gates were implemented.
Final re-review found no unresolved HIGH/CRITICAL findings. The reviewer separately
ran the original configuration/composition tests (**43 passed**) and the final
native helper tests (**9 passed**), and independently checked source versus
isolated-installed subprocess parity without live credentials or requests.

Executed on 2026-09-25:

- Full suite: **963 passed in 267.62 seconds**, including **77 new tests**.
- Ruff lint passed; formatting: **95 files already formatted** (source, tests,
  and Python dev scripts).
- Mypy: **53 source files**, no issues.
- Wheel and source distribution build, local wheel reinstall and `pip check`
  passed.
- Isolated imports: **53 modules**; all installed/source Python file sets and
  hashes matched. No import-time network access occurred.
- Installed CLI help: **4 passed**. Installed MIME opt-in/incompatible-mode
  gates: **5 passed**.
- Both normal and isolated synthetic parity scripts returned
  `authentication_parity = true`; the default project invocations both selected
  `installed_wheel`. Explicit `PYTHONPATH`/unrelated-working-directory subprocess
  cases also passed in the full suite.

After all automated checks, a presence-only process-environment check found
neither `CANVAS_BASE_URL` nor `CANVAS_ACCESS_TOKEN` available. **No live Canvas
request was made.** The historical live root cause remains unestablished; run
the canonical Profile command locally, and do not resume MIME/redirect diagnosis
unless that preflight succeeds. Offline parity is not presented as a successful
live Narxoz test. No production authentication/MIME/redirect source was changed.
