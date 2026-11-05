# @ai-nd-co/codex-web-remote

[![CI](https://github.com/ai-nd-co/codex-web-remote/actions/workflows/ci.yml/badge.svg)](https://github.com/ai-nd-co/codex-web-remote/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/@ai-nd-co/codex-web-remote.svg)](https://www.npmjs.com/package/@ai-nd-co/codex-web-remote)
[![node](https://img.shields.io/node/v/@ai-nd-co/codex-web-remote.svg)](https://nodejs.org/)
[![license](https://img.shields.io/npm/l/@ai-nd-co/codex-web-remote.svg)](./LICENSE)

**One command, a browser, and you're driving `codex app-server` from anywhere.**

`codex-web-remote` is a self-hosted web client for [openai/codex](https://github.com/openai/codex)'s `app-server`. `npx @ai-nd-co/codex-web-remote` serves a bundled React UI **and** the host-side security-boundary proxy on a local port, and either **connects to a running `codex app-server`** or **auto-launches one** — defaulting the codex command to [`npx @ai-nd-co/codex@alpha`](https://www.npmjs.com/package/@ai-nd-co/codex).

- No account, no cloud round-trip — everything stays on your machine (or your tailnet).
- The proxy is the security boundary: it injects the app-server bearer token host-side, strips browser `Origin`, and enforces a deny-by-default RPC method allowlist so the browser can't call `fs/*`, `process/*`, config writes, or raw MCP.
- Use it locally on `127.0.0.1`, or expose it privately over Tailscale Serve / TLS to drive codex from your phone or a laptop across the tailnet.

---

## Table of contents

- [Quickstart](#quickstart)
- [Which codex does it use?](#which-codex-does-it-use)
- [Requirements](#requirements)
- [CLI reference](#cli-reference)
- [Environment variables](#environment-variables)
- [Run modes: connect vs. launch (smart default)](#run-modes-connect-vs-launch-smart-default)
- [Features](#features)
- [Security model](#security-model)
- ["Bypass approvals" is a UI toggle, not a flag](#bypass-approvals-is-a-ui-toggle-not-a-flag)
- [Remote access (Tailscale is the only recommended path)](#remote-access-tailscale-is-the-only-recommended-path)
- [Contributing & releases](#contributing--releases)
- [License](#license)

---

## Quickstart

```bash
# 1) Serve the UI + proxy on http://127.0.0.1:8787/
#    - If a codex app-server is already up on ws://127.0.0.1:4222 -> connect.
#    - If not -> auto-launch `npx @ai-nd-co/codex@alpha`, then connect.
npx @ai-nd-co/codex-web-remote
```

When it's ready you'll see:

```
[codex-web-remote] READY -- open: http://127.0.0.1:8787/
```

Open that URL, start a thread, and go.

## Which codex does it use?

- **Default (no flags, nothing running):** `npx @ai-nd-co/codex-web-remote` auto-launches [`npx @ai-nd-co/codex@alpha`](https://www.npmjs.com/package/@ai-nd-co/codex) as its `codex app-server`. That default lives in [`bin/lib/cli.mjs`](./bin/lib/cli.mjs) as `DEFAULT_CODEX_CMD = 'npx @ai-nd-co/codex@alpha'`.
- **You already have a codex app-server running on `ws://127.0.0.1:4222`?** The CLI probes it and just connects — nothing new is spawned.
- **You explicitly pass `--upstream <url>` (or set `CODEX_WEB_REMOTE_UPSTREAM`)?** The CLI is **connect-only** — it will *never* auto-launch a codex against a URL you named. It might belong to another stack.
- **You want a different codex binary or a different `codex` package/tag?** Pass `--codex "<command>"` (or set `CODEX_APP_SERVER_CMD`). The value is whitespace-split into argv and spawned with `shell: false` — no shell interpretation, even on Windows.

Worked examples:

```bash
# Default. When no app-server is up on ws://127.0.0.1:4222, auto-launches
# `npx @ai-nd-co/codex@alpha` (see bin/lib/cli.mjs DEFAULT_CODEX_CMD).
npx @ai-nd-co/codex-web-remote

# Explicit / pin the alpha for reproducibility. Same behavior as the default;
# spelling it out is nice in scripts and docs.
npx @ai-nd-co/codex-web-remote --codex "npx @ai-nd-co/codex@alpha"

# Env form of the same override. Handy in shell rc files, systemd units, etc.
CODEX_APP_SERVER_CMD="npx @ai-nd-co/codex@alpha" npx @ai-nd-co/codex-web-remote

# Connect to a codex app-server you started yourself. Connect-only: the CLI
# will NOT auto-launch anything, regardless of reachability.
npx @ai-nd-co/codex-web-remote --upstream ws://127.0.0.1:4222

# Point the auto-launcher at a locally-built codex.exe instead of the alpha.
npx @ai-nd-co/codex-web-remote --codex "C:\path\to\codex.exe"

# Common tweaks.
npx @ai-nd-co/codex-web-remote --port 8888 --open
npx @ai-nd-co/codex-web-remote --upstream ws://127.0.0.1:4222 --token-file /abs/path/to/ws.token
```

## Requirements

- **Node.js 20 or newer.**
- Something to run as the `codex app-server`. Options, in order of "easiest":
  1. Nothing — let the CLI auto-launch [`npx @ai-nd-co/codex@alpha`](https://www.npmjs.com/package/@ai-nd-co/codex).
  2. A `codex` binary on your PATH or an absolute path passed via `--codex`.
  3. A `codex app-server` you already have running elsewhere — pass `--upstream <ws-url>`.

## CLI reference

`npx @ai-nd-co/codex-web-remote --help` prints the same table.

| Flag | Default | Purpose |
|---|---|---|
| `--port <n>` | `8787` | Local port for the UI + proxy. |
| `--host <host>` | `127.0.0.1` | Bind interface. Keep loopback unless you know why (see [Remote access](#remote-access-tailscale-is-the-only-recommended-path)). |
| `--upstream <ws-url>` | `ws://127.0.0.1:4222` | Existing codex app-server to connect to. **When you supply this flag (or its env), the CLI is connect-only — it will NEVER auto-launch codex, regardless of reachability.** Only the *default* upstream triggers auto-launch when unreachable. Must be `ws://` or `wss://`. |
| `--codex "<command>"` | `npx @ai-nd-co/codex@alpha` | Codex command used **only** when auto-launching. Whitespace-split into argv, spawned `shell: false`. Host-side only — nothing on the wire can influence it. |
| `--token-file <abs-path>` | *(none)* | Absolute path to a file whose contents are the bearer token the proxy adds to the upstream WS handshake. **Host-side only** — the browser never sees the token or the path. |
| `--allow-origin <origin>` | *(loopback + concrete LISTEN_HOST)* | Additional browser Origin allowed to open the WS. **Repeatable.** The proxy already trusts its own concrete `LISTEN_HOST` origin by default, so a bare `--host 192.168.1.10` bind works out of the box. `--allow-origin` is required only for (a) wildcard binds like `--host 0.0.0.0`, where there's no single concrete origin to trust, or (b) a DIFFERENT externally visible origin (a tailnet host, a reverse proxy). No wildcards — pass every specific origin you expect. |
| `--basic-auth <user:pass>` | *(off)* | Turn on **HTTP Basic Auth** on both the served UI **and** the WS upgrade. Host-side only — the value is never printed to a log line and never returned in `/proxy/healthz`. The browser holds and replays the credentials on the WS upgrade (that's what makes it work). Additive to the origin allowlist. **Leaks into shell history and `argv`** — prefer `--basic-auth-file`. |
| `--basic-auth-file <abs-path>` | *(off)* | Path to a file whose (trimmed) contents are the `user:pass` credential. Preferred over `--basic-auth`: the CREDENTIAL stays out of shell history + `argv` (only the file path appears there). The resolved credential is then forwarded to the proxy child via `CWR_BASIC_AUTH`, and stripped from the launcher / codex child env. |
| `--insecure-expose` | *(off)* | Opt-in escape hatch that lets the CLI serve on a non-loopback host WITHOUT authentication or WITHOUT TLS. Disposable dev networks only; prints a loud warning at startup. Without this flag, both configurations FAIL CLOSED. |
| `--open` | *(off)* | Open the served URL in the default browser after startup. |
| `--version`, `-v` | — | Print the package version and exit. |
| `--help`, `-h` | — | Print help and exit. |

## Environment variables

Flag equivalents (the flag wins if both are set). All host-side only; nothing supplied by the browser can influence them.

| Env | Same as |
|---|---|
| `CODEX_APP_SERVER_CMD` | `--codex "<command>"` |
| `CODEX_WEB_REMOTE_UPSTREAM` | `--upstream <ws-url>` — supplying this makes the run **connect-only**. |
| `CODEX_WEB_REMOTE_TOKEN_FILE` | `--token-file <abs-path>` |
| `CWR_ALLOW_ORIGIN` | comma-separated list of origins; equivalent to repeating `--allow-origin`. A `--allow-origin` flag on the command line REPLACES this env list. |
| `CWR_BASIC_AUTH` | same as `--basic-auth "user:pass"`. |
| `CWR_BASIC_AUTH_FILE` | same as `--basic-auth-file <path>` (preferred). |

Optional (advanced — proxy-tier tuning, read by the proxy directly): `TLS_CERT_FILE`, `TLS_KEY_FILE`, `MAX_FRAME_BYTES`, `MAX_CONNECTIONS`, `CONNECTION_IDLE_TIMEOUT_MS`, `HEARTBEAT_INTERVAL_MS`, `ALLOWED_CLIENT_ORIGINS`, `ALLOW_NO_ORIGIN`, `PROXY_TRACE`. See [`proxy/server.mjs`](./proxy/server.mjs) for the full list and defaults. Prefer the CLI's `--allow-origin` / `--basic-auth` flags over setting `ALLOWED_CLIENT_ORIGINS` / `CWR_BASIC_AUTH` directly — the CLI validates the values, adds the loopback defaults, and prints the non-loopback-without-`--allow-origin` warning.

## Run modes: connect vs. launch (smart default)

Every invocation resolves to exactly one of two modes, decided by [`bin/lib/cli.mjs::decideRunMode`](./bin/lib/cli.mjs):

1. **`connect`** — you passed `--upstream <url>` (or set `CODEX_WEB_REMOTE_UPSTREAM`). Connect-only, no matter what. Reachability is your concern.
2. **`connect`** — you used the default upstream and the CLI probed `ws://127.0.0.1:4222` open. Just connect.
3. **`launch`** — you used the default upstream and nothing was listening. The CLI spawns the launcher (`scripts/launch-appserver.mjs`) with `CODEX_APP_SERVER_CMD = 'npx @ai-nd-co/codex@alpha'` (or your `--codex` override), waits for the app-server port to come up, then connects.

Both modes then start the proxy on `--host:--port`, health-gate `/proxy/healthz`, and print the URL. On `SIGINT`/`SIGTERM`/`SIGHUP` the CLI tears down whatever it started (proxy + launched codex, if any) — on Windows the launched codex is killed with `taskkill /PID <pid> /T /F`.

## Features

Everything the bundled web UI ships with today:

- **Thread lifecycle** — `initialize` -> `thread/list` / `thread/read` / `thread/start` / `thread/resume` / `turn/start` / `turn/interrupt`, all through the proxy's allowlist. Native `thread/fork` support.
- **cwd-grouped sidebar with per-project "+ new"** — threads are grouped by their working directory; each group header has a "+ new" button that starts a new thread whose `cwd` matches the group.
- **Rich turn UI** — Markdown, tables, syntax-highlighted code blocks (curated language set), a real side-by-side diff viewer for file changes, and a live-updating command-execution card with streaming stdout/stderr.
- **Reasoning display** — collapsible reasoning cards for models that emit them.
- **Approvals modal** — surfaces server-issued approval requests (command execution, file change, permissions, network) with a proper Accept/Reject flow.
- **Attachments and @-mentions** — file attachments and typeahead file/path mentions in the composer.
- **Background terminals + sub-agents** — dedicated panels for background terminals (`thread/backgroundTerminals/list|terminate|clean`) and sub-agent activity.
- **Model / settings / context** — model picker + settings panel driven by `thread/settings/update`; live context-usage indicator and compaction card (`thread/compact/start`).
- **Multi-host** — the proxy can be configured with an opaque host registry (`HOSTS_CONFIG_FILE`) so the UI can switch between multiple upstream codex instances without ever seeing their URLs or tokens.
- **Stick-to-bottom scrolling** — transcript pins to the newest turn while streaming, releases when the user scrolls up.
- **TLS / wss** — served either through Tailscale Serve (recommended) or by the proxy itself with `TLS_CERT_FILE` + `TLS_KEY_FILE`.

## Security model

The proxy is the *real* security boundary — a naïve pass-through would give any browser full app-server authority (`fs/*`, `process/spawn`, config writes, plugin installs, `account/logout`, raw MCP). It does the following, all verified against upstream `openai/codex`:

- **Host-side upstream bearer token (when configured).** If a bearer token is provided (`UPSTREAM_TOKEN_FILE` / `UPSTREAM_TOKEN` / a host record in `HOSTS_CONFIG_FILE`), the proxy attaches it as `Authorization: Bearer …` on the **upstream** handshake to codex, and **overwrites any client-supplied `Authorization`**. The upstream token is never sent to the browser, never embedded in served files, never logged. When no token is set, the proxy prints a startup WARNING — codex is safe to reach without one only when it's loopback-bound and only trusted local clients can reach the proxy. The **browser-facing** auth is a separate mechanism: HTTP Basic Auth via `--basic-auth-file`.
- **`Origin` header stripped upstream.** app-server 403s any request that carries an `Origin` header *before* auth is checked. Browsers always send `Origin`. The proxy strips it, so the browser can talk to codex at all.
- **Client-origin allowlist enforced at the WS upgrade.** Only same-host origins listed in `ALLOWED_CLIENT_ORIGINS` (default: the proxy's own origin) can open the WS. `Origin: null` (sandboxed iframes, `file://` pages) and missing `Origin` are both refused by default — this blocks the "hostile website opens loopback WS" attack. Additional origins (LAN IP, tailnet host) are added via `--allow-origin <origin>` (repeatable); no wildcards.
- **Optional HTTP Basic Auth (`--basic-auth-file <path>` / `--basic-auth user:pass` / `CWR_BASIC_AUTH{_FILE}`).** When set, gates **both** the served UI **and** the WS upgrade (protecting the page but not the WS would leave codex reachable to any non-browser client on the network). Constant-time compare; the credential is host-side, never logged, never returned in `/proxy/healthz`. The launcher's env and codex's env are scrubbed of the credential before spawn. Additive — the Origin allowlist and the host-side upstream bearer token stay enforced. Off by default; the loopback-only default is unchanged. **Fail-closed**: the CLI refuses to serve on a non-loopback bind without Basic Auth, and refuses to serve Basic Auth over plain HTTP on a non-loopback bind (see [Remote access](#remote-access-tailscale-is-the-only-recommended-path)).
- **Per-connection isolation.** One inbound browser WS → one upstream WS, its own request-id space. No cross-client leakage.
- **Narrow RPC method allowlist, deny-by-default.** Only the ~30 methods the UI actually needs are forwarded (`initialize`, `thread/*`, `turn/*`, and the like — see [`proxy/server.mjs`](./proxy/server.mjs)). Anything else returns JSON-RPC `-32601` and is dropped. `fs/*`, `process/*`, `account/logout`, `config/write`, `plugin/install*`, and raw MCP pass-through are **not** on the list; adding one requires a code change.
- **Response gating.** A client `{id, result|error}` frame is only forwarded when it answers a real upstream-issued request for that connection.
- **Frame typing + resource caps.** JSON-RPC is TEXT only; binary frames are dropped. `MAX_FRAME_BYTES`, `MAX_CONNECTIONS`, `MAX_PENDING_BYTES`, upstream-handshake and idle timeouts, heartbeat liveness with a two-strikes terminate, bidirectional `bufferedAmount` high-water.
- **Static/HTTP hardening.** Every response — including raw-socket WS-upgrade rejections — carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a strict `Content-Security-Policy` with `frame-ancestors 'none'` + `base-uri 'none'` + `object-src 'none'` + `form-action 'none'` + `'self'` `script-src`, `Cross-Origin-Opener-Policy: same-origin`, and `Cross-Origin-Resource-Policy: same-origin`. Blocks the whole clickjack-the-authenticated-UI class of attacks. `connect-src` intentionally allows `ws:` / `wss:` / `http:` / `https:` schemes so a TLS-terminating reverse proxy (Tailscale Serve, an operator-run front) can upgrade the scheme without breaking `new WebSocket(...)` — the load-bearing origin gate is the proxy's own `ALLOWED_CLIENT_ORIGINS` exact-match check on the WS upgrade, not CSP. Path-traversal filter on static requests, symlinks refused, malformed URIs return 400 (they do not crash the process).
- **Env-scrub in the CLI.** Before spawning the proxy, `bin/codex-web-remote.mjs` deletes `UPSTREAM_TOKEN`, `UPSTREAM_TOKEN_FILE`, `UPSTREAM_URL`, `PUBLIC_DIR`, `LISTEN_HOST`, `LISTEN_PORT`, `HOSTS_CONFIG_FILE`, `DEFAULT_HOST_LABEL`, `DEFAULT_HOST_RUN_MODE`, `ALLOWED_CLIENT_ORIGINS`, `CWR_BASIC_AUTH`, and `CWR_BASIC_AUTH_FILE` from the inherited environment, so a shell-leaked env cannot silently override what the CLI flags advertised. The **launcher** and the codex process it spawns get a separately-scrubbed env — `CWR_BASIC_AUTH{_FILE}`, `UPSTREAM_TOKEN*`, `ALLOWED_CLIENT_ORIGINS`, `HOSTS_CONFIG_FILE` are removed before launcher spawn, keeping the credential blast radius to the proxy child only.
- **`PUBLIC_DIR` = bundled dist, not `cwd`.** The static server serves the packaged `web/dist` (resolved via `import.meta.url`), so a `cd` into a random directory cannot exfiltrate arbitrary local files.
- **Upstream URL fixed at startup.** The browser cannot redirect the proxy to a different app-server via query params or frames.
- **Log scrubbing.** Tokens are never printed. Frame payloads are never printed. `PROXY_TRACE=1` prints `method + id + size` only. Attacker-controlled fields (method name, origin) are stripped of control chars before logging.

## "Bypass approvals" is a UI toggle, not a flag

`--codex "..."` sets which codex binary the CLI spawns — **not its permissions**. There is no `--dangerously-bypass-approvals-and-sandbox` on `codex app-server`; that flag exists on `codex exec` / the TUI, not this transport.

The web app has a **DANGER BYPASS** toggle in the titlebar (checkbox with a red border when ON, red hairline under the titlebar). When active it sends the following on every new `thread/start`:

```
approvalPolicy: "never"                 // AskForApproval::Never
sandbox:        "danger-full-access"    // SandboxMode::DangerFullAccess
```

The toggle is per-thread, off by default, persisted in `localStorage.cwr.runMode`, cannot be flipped via a URL/query-string (only a click on the local page), and cannot be flipped mid-thread — turning it on affects the *next* thread you start. The proxy's method allowlist is never widened for it.

## Remote access (Tailscale is the only recommended path)

> **Threat model, in one sentence:** access to this proxy is equivalent to local codex control, which on a developer machine is functionally equivalent to shell access. Treat the credential like you'd treat an SSH key. The RPC method allowlist is defense-in-depth against a *misbehaving browser page*; it is **not** a restriction on what an authenticated client can do.

The CLI **fails closed** on any non-loopback bind that would leak that authority:

- `--host 0.0.0.0` (or any non-loopback host) WITHOUT `--basic-auth`/`--basic-auth-file` → **refuses to start** (the Origin allowlist is CSRF protection, not authentication; a non-browser client trivially forges any Origin).
- Non-loopback bind WITH `--basic-auth` but WITHOUT TLS → **refuses to start** (Basic credentials are base64 on the wire; anyone on-path captures + replays them to reach the "no auth" state above).
- `--insecure-expose` is the escape hatch for **disposable dev networks only** (throwaway VM, isolated lab bench). It prints a loud warning and lets the CLI start.

### Recommended: Tailscale Serve terminates TLS in front

The proxy stays on loopback. Tailscale hands you an HTTPS URL on your tailnet with a magic-DNS cert; the browser speaks `wss` to Tailscale which terminates TLS and proxies to plain http on `127.0.0.1`.

```
# On the host machine — replace <host>.tailnet-name.ts.net with your tailnet URL,
# and /abs/path/cred with a file containing exactly "user:pass" (no trailing newline):
echo -n 'alice:s3cret' > /abs/path/cred && chmod 600 /abs/path/cred
npx @ai-nd-co/codex-web-remote \
  --allow-origin https://<host>.tailnet-name.ts.net \
  --basic-auth-file /abs/path/cred

# In another terminal:
tailscale serve --bg --https=443 http://127.0.0.1:8787
```

Your phone/laptop can now open `https://<host>.tailnet-name.ts.net/`. Basic Auth prompts on first load; browsers replay the credentials on the WS upgrade automatically (verified in `e2e/playwright/14-cv-basic-auth.spec.mjs`).

Prefer `--basic-auth-file` over `--basic-auth user:pass` — the inline form leaks the credential into shell history and the process's `argv`.

### Alternative: Proxy terminates TLS itself (LAN dev with a cert)

If you cannot use Tailscale, terminate TLS in the proxy directly. Set the two env vars before starting the CLI; the CLI will pick `https` for the health-gate, the default allowed-origin, and the "open URL" automatically:

```
echo -n 'alice:s3cret' > /abs/path/cred && chmod 600 /abs/path/cred
TLS_CERT_FILE=/abs/path/cert.pem \
  TLS_KEY_FILE=/abs/path/key.pem \
  npx @ai-nd-co/codex-web-remote \
    --host 0.0.0.0 \
    --allow-origin https://192.168.1.10:8787 \
    --basic-auth-file /abs/path/cred
```

Self-signed is fine for LAN dev (the browser will warn once and remember). All security invariants (Origin strip, host-side upstream token when configured, method allowlist, one-WS-per-conn, Basic Auth on both HTTP and the WS upgrade, hardening headers on every response) are unchanged.

### Disposable dev network only: `--insecure-expose`

Use this ONLY on a network you own end to end. The credential (if any) may still be sent in cleartext; more importantly, without auth **any device on the network can drive codex**.

```
# Understand what you're doing before running this.
npx @ai-nd-co/codex-web-remote --host 0.0.0.0 --insecure-expose
```

### Why Basic Auth **and** the origin allowlist?

The client-Origin allowlist is a **browser-only** defense: it stops a hostile web page from opening a WebSocket to your loopback proxy from a different origin. It does **not** stop a non-browser client on the same network (`curl`, another `node` process) — that client forges any Origin string trivially. Basic Auth is the outer gate that protects **both** the served UI and the WS upgrade. Credential compare is constant-time; credentials are never logged, never returned in `/proxy/healthz` bodies, never on the launcher's or codex's env. The browser holds them (that's what makes the WS upgrade work) and does not expose them to the page's JavaScript. Off by default; the loopback-only default is unchanged.

Bypass ("DANGER BYPASS") is still a UI-only toggle inside the web app — never a CLI flag, never a query-string switch. See ["Bypass approvals" is a UI toggle, not a flag](#bypass-approvals-is-a-ui-toggle-not-a-flag).

## Contributing & releases

- Development, tests, local dev-stack, and manual dry-runs: see [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- Releases are fully automated. On every push to `main`, `semantic-release` reads the conventional-commit prefixes since the last tag, picks the next SemVer, updates `CHANGELOG.md`, publishes to npm via **tokenless OIDC trusted publishing** (there is no `NPM_TOKEN` in the repo, workflow, or secret store), and creates the GitHub Release + tag. Merges that contain only `chore:`/`docs:`/`test:`/`refactor:` commits produce no release.

Preview a release without publishing:

```
npx semantic-release --dry-run --no-ci --branches main
```

## License

Apache-2.0 — see [`LICENSE`](./LICENSE).
