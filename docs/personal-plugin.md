# Personal Canvas Student plugin (0.1.0)

For a new checkout, use the [repository README installation steps](../README.md#installing-the-personal-codex-plugin), which register the checkout's `canvas-student-local` marketplace. The `personal` marketplace paths below describe the existing live-tested installation on this Windows account.

The local `canvas-student` plugin packages four coursework skills and the existing 15-tool read-only stdio MCP server. Its source is `plugins/canvas-student/`. The portable root `plugin.json` and `mcp.json` contain no Python installation path or Canvas token. The launcher reads a separate machine-local binding at `%LOCALAPPDATA%\CanvasStudent\binding.json` with only a `python` path to the installed `canvas-mcp` environment. This path is installation state, not portable plugin metadata.

The personal marketplace is `%USERPROFILE%\.agents\plugins\marketplace.json`; its `source` is `./plugins/canvas-student`. Use `codex plugin add canvas-student@personal` to install. A new Codex session is required after installation or update. Codex caches the installed package under `%USERPROFILE%\.codex\plugins\cache\personal\canvas-student\0.1.0`. This local package is not submitted to a public marketplace.

## Credentials

From a PowerShell terminal, run:

```powershell
& 'E:\canvas_mcp\.venv\Scripts\python.exe' -m canvas_mcp.credentials_cli configure
```

Wait for the hidden `Canvas personal access token:` prompt. Setup validates the supplied token with `get_profile` before replacing the dedicated Windows Credential Manager generic credential `canvas-mcp:personal:narxoz-student:v1`. It never prints or exports the token. `status` reports only availability; `logout` removes only this target. Repeat `configure` to validate and replace a token. The installed launcher sets `CANVAS_CREDENTIAL_PROVIDER=windows` and removes `CANVAS_ACCESS_TOKEN` from the MCP child environment. Explicit `windows` mode takes precedence over any development environment token. Unset or `environment` mode retains the original environment provider for development, tests and manual smoke runs.

This design keeps a plaintext bearer token out of the parent Codex environment and out of plugin/config files. Windows Credential Manager is protected for the signed-in Windows user; it is **not** a separate security identity. Other processes running as that same user can read this dedicated generic credential through the Windows API. Consequently, inspect Canvas downloads as untrusted data, never execute embedded code or instructions, and do not give file-reading tools credential-bearing environment variables or command arguments.

## Personal network and downloads

The local launcher sets `CANVAS_BASE_URL=https://canvas.narxoz.kz` and `CANVAS_TRUSTED_PRIVATE_IPS=192.168.4.200`. This allows the vetted split-horizon private address when on campus and ordinary public DNS elsewhere; it does not pin a public IP. The managed download directory is `C:\canvas_mcp_runtime\plugin_downloads`, outside project repositories. A direct storage probe confirmed this root works; the user-local directory was rejected by the Windows storage boundary under the installed Codex runtime. The production secure file layer still enforces its existing origin, redirect, content, and path rules. Downloaded artifacts remain untrusted.

Plugin-scoped Codex configuration under `[plugins."canvas-student@personal".mcp_servers.canvas_student]` approves ordinary read tools, while `[plugins."canvas-student@personal".mcp_servers.canvas_student.tools.canvas_download_file]` sets `approval_mode = "prompt"`. Codex plugin removal and reinstallation can remove these nested tables, so verify and restore them after an update before using downloads. No approval behavior is implemented inside the MCP server.

## Version and distribution boundary

Version `0.1.0` identifies this personal package's manifests, four skill workflows, local launcher and credential setup. Update the plugin version when tool contracts, skills or credential setup change. A public or remote-MCP variant would need institution-neutral base URL/IP configuration, a different authentication model, a remote transport/registration, a public distribution review and removal of this machine-local Python binding. The skills are organized around the MCP tool contracts so the workflow text can be adapted without redesigning Canvas application services.
