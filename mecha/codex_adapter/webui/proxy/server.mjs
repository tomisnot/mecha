// Codex Web Remote — host-side security-boundary proxy + static file server.
//
// Why this exists (verified against openai/codex `main`, 2026-07-29):
//   * codex app-server accepts `Authorization: Bearer <token>` on the WS
//     handshake — browsers cannot set custom headers on `new WebSocket(...)`.
//   * app-server 403s ANY request that carries an `Origin` header, BEFORE auth,
//     regardless of value. Browsers always send Origin. See upstream
//     `codex-rs/app-server-transport/src/transport/websocket.rs`
//     (`reject_requests_with_origin_header`). We therefore STRIP Origin
//     outright rather than substituting a "non-browser" value — a value would
//     still be 403'd.
//
// This proxy is the REAL security boundary. A dumb pass-through would give any
// browser full app-server authority: `fs/*`, `process/spawn`, config writes,
// plugin installs, `account/logout`, raw MCP. We enforce:
//
//   * Fixed host-side upstream URL + bearer token (NEVER from query / frame).
//     Multi-host (C10): the browser picks an OPAQUE `host` id; the proxy maps
//     that id -> a host-side config record with the real upstream URL + token.
//     The browser NEVER supplies a URL or token; the safe `/proxy/hosts`
//     listing NEVER returns a URL, token, or token-file path.
//   * Client-origin allowlist (`ALLOWED_CLIENT_ORIGINS`). Any browser at an
//     unlisted origin is refused at the WS upgrade — this is what keeps
//     `evil.example` from operating a loopback-bound proxy.
//   * OPTIONAL HTTP Basic Auth (`CWR_BASIC_AUTH`, `user:pass`). When set,
//     BOTH the static site AND the WS upgrade require valid Basic creds
//     (browsers replay the Basic header on same-origin WS upgrades). This
//     is the outer gate for LAN / Tailscale deployment. ADDITIVE — Origin
//     allowlist + host-side bearer token stay enforced. Credentials are
//     constant-time compared and NEVER printed.
//   * Per-connection isolation: ONE inbound browser ws -> ONE upstream ws.
//     Request-id spaces never mix across clients.
//   * RPC method allowlist for client -> upstream traffic (see ALLOWED_METHODS).
//     Unknown / unlisted methods get a JSON-RPC error and are NOT forwarded.
//   * Client responses (`{id,result|error}`) only forwarded when they answer
//     a pending upstream server-request for that same connection — no
//     unsolicited responses.
//   * Log scrubbing: token never printed; frame payloads only when
//     PROXY_TRACE=1 and even then method + id + size only (not params).
//   * Correct text vs. binary vs. control frame handling. codex-app-server
//     JSON-RPC is always TEXT; binary frames are refused.
//   * Bounded resources: max concurrent connections, upstream handshake
//     timeout, capped pre-open queue, high-water `bufferedAmount` check.
//   * Malformed HTTP paths → 400, not process crash.

import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, WebSocket } from 'ws';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');
const PUBLIC_DIR = path.resolve(REPO_ROOT, process.env.PUBLIC_DIR ?? 'public');

const LISTEN_HOST = process.env.LISTEN_HOST ?? '127.0.0.1';
const LISTEN_PORT = Number.parseInt(process.env.LISTEN_PORT ?? '8787', 10);
const WS_PATH = process.env.WS_PATH ?? '/ws';

// ---------- C11 TLS ---------------------------------------------------------
//
// Optional TLS mode for the browser-facing HTTP+WS listener. Two deployment
// shapes are supported:
//
//   1. TLS TERMINATED BY THIS PROXY (dev-only / self-signed).
//      Set both TLS_CERT_FILE and TLS_KEY_FILE. When set, this proxy serves
//      https:// on LISTEN_PORT and accepts wss:// on WS_PATH. The
//      browser-facing scheme becomes wss and the App's `buildHostWsUrl` emits
//      wss automatically (it reads location.protocol). Use for LAN dev where
//      a self-signed cert is acceptable; browsers will warn once and remember.
//
//   2. TLS TERMINATED BY TAILSCALE SERVE (recommended for phone-over-tailnet).
//      Leave TLS_CERT_FILE unset — proxy stays http on 127.0.0.1. Run
//      `tailscale serve --bg --https=443 http://127.0.0.1:8787` on the host
//      machine and Tailscale terminates HTTPS with a magic-DNS cert. The
//      browser sees https://<host>.tailnet-name.ts.net and speaks wss over
//      Tailscale's terminated TLS; Tailscale proxies to plain http on the
//      local port. This is what the C11 README recipe uses.
//
// The proxy NEVER weakens any security invariant when TLS is on:
//   - Origin is still stripped upstream.
//   - Bearer token still host-side (loaded from HOSTS_CONFIG_FILE / env).
//   - Method allowlist is unchanged.
//   - One-ws-per-conn is unchanged.
//   - Origin allowlist auto-expands to include https://<LISTEN_HOST>:<PORT>
//     variants when TLS is on so a browser hitting the https origin isn't
//     403'd by the allowlist. Any additional origins still require
//     ALLOWED_CLIENT_ORIGINS explicitly.
const TLS_CERT_FILE = process.env.TLS_CERT_FILE ?? '';
const TLS_KEY_FILE = process.env.TLS_KEY_FILE ?? '';
const TLS_ENABLED = TLS_CERT_FILE.length > 0 && TLS_KEY_FILE.length > 0;
if ((TLS_CERT_FILE.length > 0) !== (TLS_KEY_FILE.length > 0)) {
  console.error('[proxy] TLS_CERT_FILE and TLS_KEY_FILE must be set together');
  process.exit(2);
}
let TLS_OPTIONS = null;
if (TLS_ENABLED) {
  try {
    TLS_OPTIONS = {
      cert: fs.readFileSync(TLS_CERT_FILE),
      key: fs.readFileSync(TLS_KEY_FILE),
    };
  } catch (err) {
    // Only emit `err.code` (a short symbolic code like ENOENT/EACCES). Never
    // `err.message` — that echoes the absolute file path and can contain
    // control chars. `safeLogString` scrubs the code just in case.
    console.error(
      `[proxy] failed to read TLS_CERT_FILE / TLS_KEY_FILE: ${
        typeof err.code === 'string' ? err.code : 'EUNKNOWN'
      }`,
    );
    process.exit(2);
  }
}

const UPSTREAM_UA = process.env.UPSTREAM_USER_AGENT ?? 'codex-web-remote-proxy/0.0.1';
const MAX_FRAME_BYTES = Number.parseInt(
  process.env.MAX_FRAME_BYTES ?? String(4 * 1024 * 1024),
  10,
);
const MAX_PENDING_FRAMES = 16;
const MAX_PENDING_BYTES = Number.parseInt(
  process.env.MAX_PENDING_BYTES ?? String(1 * 1024 * 1024),
  10,
);
const MAX_CONNECTIONS = Number.parseInt(process.env.MAX_CONNECTIONS ?? '32', 10);
const CONNECTION_IDLE_TIMEOUT_MS = Number.parseInt(
  process.env.CONNECTION_IDLE_TIMEOUT_MS ?? String(5 * 60 * 1000),
  10,
);
const HEARTBEAT_INTERVAL_MS = Number.parseInt(
  process.env.HEARTBEAT_INTERVAL_MS ?? '30000',
  10,
);
const UPSTREAM_HANDSHAKE_TIMEOUT_MS = Number.parseInt(
  process.env.UPSTREAM_HANDSHAKE_TIMEOUT_MS ?? '10000',
  10,
);
const CLIENT_BUFFERED_HIGH_WATER = Number.parseInt(
  process.env.CLIENT_BUFFERED_HIGH_WATER ?? String(8 * 1024 * 1024),
  10,
);
const UPSTREAM_BUFFERED_HIGH_WATER = Number.parseInt(
  process.env.UPSTREAM_BUFFERED_HIGH_WATER ?? String(8 * 1024 * 1024),
  10,
);
const TRACE = process.env.PROXY_TRACE === '1';

// Client-side origin allowlist. Comma-separated, exact-match.
//
// Defaults trust ONLY same-host browser origins on the proxy port. We do NOT
// include:
//   * `'null'` — sandboxed iframes / file:// origins serialize as `Origin: null`.
//     A hostile site can create a sandboxed iframe and thus forge that origin.
//   * `''`     — the sentinel that means "no Origin header at all", i.e. a
//     non-browser client (Node CLI, our smoke test). Node clients must opt in
//     explicitly by setting `ALLOW_NO_ORIGIN=1`. Real browsers always send
//     Origin on WS upgrades.
//
// Override with `ALLOWED_CLIENT_ORIGINS` (comma-separated exact strings) to
// permit additional origins (e.g. a locally-served UI on a different port).
// C11: when TLS terminates HERE (not behind Tailscale), the browser origin
// becomes `https://...`, so the default allowlist must include the https
// variants too. When TLS terminates in front (Tailscale Serve), the operator
// sets ALLOWED_CLIENT_ORIGINS to the tailnet host explicitly — we intentionally
// do NOT auto-include arbitrary https origins.
// IPv6-safe: raw IPv6 hosts must be bracketed in origin strings per RFC 3986
// §3.2.2. Already-bracketed forms pass through unchanged.
function _bracketHostForOrigin(h) {
  const s = String(h);
  if (s.startsWith('[')) return s;
  if (s.includes(':')) return `[${s}]`;
  return s;
}
const _LISTEN_HOST_URL = _bracketHostForOrigin(LISTEN_HOST);
// A wildcard bind (`0.0.0.0` / `::`) is a LISTEN instruction, not a valid
// CONNECT target. Browsers hit the loopback equivalent (`127.0.0.1` / `::1`),
// so the wildcard form must never appear in the default allowlist — otherwise
// a browser at the loopback origin gets 403'd unnecessarily. Add the
// loopback equivalent (bracketed for IPv6) alongside the localhost + 127.0.0.1
// entries.
const _WILDCARD_LOOPBACK_ORIGIN =
  LISTEN_HOST === '0.0.0.0' ? '127.0.0.1'
  : LISTEN_HOST === '::' || LISTEN_HOST === '[::]' ? '[::1]'
  : null;
const _urlScheme = TLS_ENABLED ? 'https' : 'http';
const DEFAULT_CLIENT_ORIGINS = (() => {
  const set = new Set([
    // Only include the LISTEN_HOST origin when it's not a wildcard — a
    // wildcard bind's origin is a listen shorthand and no real browser
    // client uses it as its own Origin.
    ..._WILDCARD_LOOPBACK_ORIGIN == null
      ? [`${_urlScheme}://${_LISTEN_HOST_URL}:${LISTEN_PORT}`]
      : [`${_urlScheme}://${_WILDCARD_LOOPBACK_ORIGIN}:${LISTEN_PORT}`],
    `${_urlScheme}://localhost:${LISTEN_PORT}`,
    `${_urlScheme}://127.0.0.1:${LISTEN_PORT}`,
    // Also include [::1] so an IPv6-preferring browser hitting the wildcard
    // is not silently 403'd.
    `${_urlScheme}://[::1]:${LISTEN_PORT}`,
  ]);
  return [...set];
})();
const ALLOWED_CLIENT_ORIGINS = new Set(
  (process.env.ALLOWED_CLIENT_ORIGINS ?? DEFAULT_CLIENT_ORIGINS.join(','))
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0),
);
const ALLOW_NO_ORIGIN = process.env.ALLOW_NO_ORIGIN === '1';

// ---------- CV: HTTP Basic Auth ---------------------------------------------
//
// Optional Basic Auth on the browser-facing surface. When `CWR_BASIC_AUTH`
// is set (`user:pass`), BOTH the static site / HTTP endpoints AND the WS
// upgrade require valid `Authorization: Basic <base64(user:pass)>`. Protecting
// only the page would leave the WS reachable to any non-browser client on the
// LAN / tailnet, which defeats the point on an exposed proxy.
//
// SECURITY INVARIANTS (audited):
//   * The credentials are HOST-SIDE ONLY. Never in a log line. Never in a
//     safe listing. Never surfaced in `/proxy/healthz`. The startup banner
//     prints only `basicAuth=on|off`.
//   * ADDITIVE — the Origin allowlist, the RPC method allowlist, and the
//     host-side bearer token are all still enforced. Basic Auth is an outer
//     gate; the existing invariants are unchanged.
//   * Credential compare is constant-time on the pre-computed base64 header
//     to avoid timing leaks and Buffer-length asymmetry.
//   * Off by default. When unset the proxy behaves exactly as before.
//   * The realm string is fixed to a safe ASCII literal so a hostile
//     environment variable cannot inject CRLF into `WWW-Authenticate`.
const BASIC_AUTH_RAW = process.env.CWR_BASIC_AUTH ?? '';
let BASIC_AUTH_EXPECTED_HEADER = null;
let BASIC_AUTH_EXPECTED_BUF = null;
if (BASIC_AUTH_RAW.length > 0) {
  const colon = BASIC_AUTH_RAW.indexOf(':');
  if (colon <= 0 || colon === BASIC_AUTH_RAW.length - 1) {
    console.error('[proxy] CWR_BASIC_AUTH must be "user:pass" with non-empty user AND pass');
    process.exit(2);
  }
  BASIC_AUTH_EXPECTED_HEADER =
    'Basic ' + Buffer.from(BASIC_AUTH_RAW, 'utf8').toString('base64');
  BASIC_AUTH_EXPECTED_BUF = Buffer.from(BASIC_AUTH_EXPECTED_HEADER, 'utf8');
}
const BASIC_AUTH_ENABLED = BASIC_AUTH_EXPECTED_HEADER != null;
const BASIC_AUTH_REALM = 'codex-web-remote';

// Constant-time string compare. Buffers of unequal length can't be handed to
// `crypto.timingSafeEqual` (it throws) — so we compare a same-length "sink"
// against the expected first (to keep the branch time roughly constant) and
// then return false. When lengths DO match, we defer to the real primitive.
function timingSafeEqualStr(actual, expectedBuf) {
  const a = Buffer.from(String(actual), 'utf8');
  if (a.length !== expectedBuf.length) {
    // Still touch a same-length compare against the expected buffer so a
    // simple length-difference doesn't short-circuit measurably.
    try { crypto.timingSafeEqual(expectedBuf, expectedBuf); } catch { /* unreachable */ }
    return false;
  }
  return crypto.timingSafeEqual(a, expectedBuf);
}

/**
 * Validate the request's Basic Auth header against the configured secret.
 * Returns true when Basic Auth is disabled OR the header matches EXACTLY the
 * pre-computed `Authorization: Basic <b64>` value. Uses constant-time compare
 * on the whole header string; the base64 shape means a length mismatch is
 * NOT confidential and the same-length compare handles the sensitive part.
 */
function checkBasicAuth(req) {
  if (!BASIC_AUTH_ENABLED) return true;
  const hdr = req.headers['authorization'];
  if (typeof hdr !== 'string' || hdr.length === 0) return false;
  return timingSafeEqualStr(hdr, BASIC_AUTH_EXPECTED_BUF);
}

// Method allowlist enforced server-side. Everything the browser client is
// permitted to call in this foundation phase. Anything not in the set is
// answered with a JSON-RPC error and NOT forwarded upstream.
//
// Growable as later phases (approvals, exec, attachments) come online, but
// stays deny-by-default. NEVER add `fs/*`, `process/*`, `account/logout`,
// `config/write`, `plugin/install*`, or raw MCP passthrough here.
const ALLOWED_METHODS = new Set([
  // handshake
  'initialize',
  'initialized',
  // thread lifecycle + read (C0-C1/C2)
  'thread/list',
  'thread/read',
  'thread/start',
  'thread/resume',
  'thread/loaded/list',
  // thread management + paginated history (C3)
  //   * name/archive/unarchive/delete: write-endpoints per the task allowlist
  //   * search + searchOccurrences: experimental read of stored threads
  //   * turns/list + items/list: cursor-paginated experimental history
  // NOTHING outside this exact set was added in C3.
  'thread/name/set',
  'thread/archive',
  'thread/unarchive',
  'thread/delete',
  'thread/search',
  'thread/searchOccurrences',
  'thread/turns/list',
  'thread/items/list',
  // turn lifecycle (C4 adds ONLY 'turn/steer'; everything else was already here)
  'turn/start',
  'turn/interrupt',
  'turn/steer',
  // C6 model + per-thread settings + compaction. EXACTLY these 4 methods;
  // NOTHING else. The task file froze this list. Notifications
  // `thread/settings/updated`, `model/rerouted`, `thread/tokenUsage/updated`,
  // `thread/compacted` are server->client (no allowlist entry needed).
  //
  // Deny-by-default preserved: none of these methods can touch fs/*,
  // process/*, account/logout, config/write, plugin/*, or raw MCP; each is a
  // narrowly-scoped per-thread mutation that the app-server itself enforces.
  // The mid-thread `thread/settings/update` is what carries the tagged-union
  // `sandbox_policy: {type: "dangerFullAccess"}` shape (distinct from C5b's
  // start-time `sandbox: "danger-full-access"` string — see kernel/runMode.ts
  // and the C6 report for the two exact shapes and why they differ).
  'model/list',
  'modelProvider/capabilities/read',
  'thread/settings/update',
  'thread/compact/start',
  // C7 fuzzy-file search — session lifecycle only (start / update / stop).
  // The one-shot `fuzzyFileSearch` variant is included because the composer's
  // debounce could fall back to it if the session flow is unavailable; still
  // narrowly-scoped read (returns files under `roots` matching a query). NO
  // `fs/*` methods were added — mention previews would require `fs/readFile`
  // and we intentionally deferred that (see C7 report §"why no preview").
  // Deny-by-default preserved: none of these methods can touch fs write /
  // process spawn / config write / plugin install / raw MCP / account.
  //
  // Grew allowlist from 22 (C6) to 26. Update the exact-count regression test
  // in `e2e/tests/05-c6-settings-compact.test.mjs` when this changes.
  'fuzzyFileSearch',
  'fuzzyFileSearch/sessionStart',
  'fuzzyFileSearch/sessionUpdate',
  'fuzzyFileSearch/sessionStop',
  // C8 native "Branch from here" — ONLY the non-deprecated `thread/fork`.
  // Splits history at the picked turn into a NEW thread. Deprecated
  // `thread/rollback` is intentionally NOT added. NO file/git/restore
  // methods sneak in — fork is a pure conversation-branch on the server
  // (codex has no file-restore capability, and per the Architecture
  // principle the web app is view+transport only over the codex API).
  'thread/fork',
  // C9 background terminals — experimental. ONLY the three list/terminate/
  // clean methods; nothing else. `command/exec*`, `process/*`, and
  // `fs/*` STAY DENIED — background terminals are managed by the app-server;
  // the client just enumerates + terminates by processId. No spawn on the
  // wire from the browser. The panel is capability-gated in the UI so an
  // upstream that doesn't support experimentalApi hides gracefully.
  //
  // Grew allowlist from 27 (C8) to 30. Update the exact-count regression
  // test in `e2e/tests/05-c6-settings-compact.test.mjs` when this changes.
  'thread/backgroundTerminals/list',
  'thread/backgroundTerminals/terminate',
  'thread/backgroundTerminals/clean',
]);

// Upstream methods that carry a server-issued request the client is expected
// to answer (with a response frame). Only IDs seen from upstream requests are
// eligible destinations for a client response — no unsolicited responses.
//
// C5 populates the four approvals + tool user-input request methods. The
// corresponding CLIENT->UPSTREAM responses do NOT need `method` allowlisting
// (responses carry only `{id, result|error}`), but they DO need to answer a
// pending server-request id — that gate lives in `isAllowedForClient()` below
// (see the `unsolicited-response` denial reason) and is populated exclusively
// from this set on receipt of an upstream request. Deny-by-default preserved:
// we grew the surface by EXACTLY these 4 read-shaped methods and NOTHING else,
// so a compromised client still cannot call `fs/*`, `process/*`, `config/*`,
// `plugin/*`, or `account/logout`.
//
// `mcpServer/elicitation/request` is intentionally NOT added here — MCP
// elicitation polish is deferred to a later phase (see the C5 task file's
// out-of-scope list); if it arrives from upstream we drop it on the floor
// per deny-by-default rather than silently forwarding.
const UPSTREAM_SERVER_REQUEST_METHODS = new Set([
  'item/commandExecution/requestApproval',
  'item/fileChange/requestApproval',
  'item/permissions/requestApproval',
  'item/tool/requestUserInput',
]);

// ---------- host registry (C10) ----------
//
// A registry of host-side records the proxy owns. The browser NEVER supplies
// an upstream URL or token; it picks a host by an OPAQUE id and the proxy
// maps that id to the real record here. If `HOSTS_CONFIG_FILE` is unset we
// synthesize a single record from `UPSTREAM_URL` + `UPSTREAM_TOKEN[_FILE]` so
// pre-C10 single-host deployments keep working unchanged (this is what every
// test in the repo runs against).
//
// Config file shape (JSON):
//   {
//     "hosts": [
//       {
//         "id": "opaque-id",           REQUIRED — opaque token the browser sends
//         "label": "Human label",      REQUIRED — safe to show to the browser
//         "upstreamUrl": "ws://...",   REQUIRED — HOST-SIDE ONLY, never emitted
//         "token": "raw",              OPTIONAL — inline bearer (discouraged)
//         "tokenFile": "/abs/path",    OPTIONAL — absolute path to token file
//         "defaultRunMode": "default"  OPTIONAL — "default" | "danger-bypass"
//       },
//       ...
//     ]
//   }
//
// `token` / `tokenFile` are HOST-SIDE ONLY: never emitted to `/proxy/hosts`
// and never in a log line (only `hasAuth: bool` is safe to expose).
function loadTokenFromRecord(rec, sourceLabel) {
  if (typeof rec.token === 'string' && rec.token.length > 0) return rec.token.trim();
  if (typeof rec.tokenFile === 'string' && rec.tokenFile.length > 0) {
    try {
      return fs.readFileSync(rec.tokenFile, 'utf8').trim();
    } catch (err) {
      // Do NOT log err.message — could echo path/mode; keep it terse.
      console.error(
        `[proxy] host ${safeLogString(sourceLabel, 64)} — failed to read tokenFile: ${err.code ?? 'ENOENT'}`,
      );
      process.exit(2);
    }
  }
  return null;
}

const VALID_ID = /^[a-zA-Z0-9_.:-]{1,64}$/;
const VALID_RUN_MODE = new Set(['default', 'danger-bypass']);

function validateAndFreezeHost(rec, sourceLabel) {
  if (!rec || typeof rec !== 'object' || Array.isArray(rec)) {
    throw new Error(`host record must be an object (${sourceLabel})`);
  }
  if (typeof rec.id !== 'string' || !VALID_ID.test(rec.id)) {
    throw new Error(
      `host record ${sourceLabel} — invalid id (must match ${VALID_ID}; got type=${typeof rec.id})`,
    );
  }
  if (typeof rec.label !== 'string' || rec.label.length === 0 || rec.label.length > 128) {
    throw new Error(`host record ${sourceLabel} — invalid label (1-128 chars required)`);
  }
  if (typeof rec.upstreamUrl !== 'string' || !/^wss?:\/\//i.test(rec.upstreamUrl)) {
    throw new Error(`host record ${sourceLabel} — upstreamUrl must be ws:// or wss://`);
  }
  const defaultRunMode =
    typeof rec.defaultRunMode === 'string' && VALID_RUN_MODE.has(rec.defaultRunMode)
      ? rec.defaultRunMode
      : 'default';
  // Optional per-host launcher command (ties C5b): a shell-free argv string
  // the operator uses to bring up THIS host's app-server. Retained on the
  // record so operators can key their launcher-manager off it; NEVER emitted
  // to the browser (not in the safe listing, not in healthz). Bounded to
  // 2048 chars defensively; typeof-only shape check because the launcher
  // itself is a separate process outside the proxy's control.
  let launchCmd = null;
  if (rec.launchCmd != null) {
    if (typeof rec.launchCmd !== 'string' || rec.launchCmd.length === 0 || rec.launchCmd.length > 2048) {
      throw new Error(`host record ${sourceLabel} — launchCmd must be a 1-2048 char string when set`);
    }
    launchCmd = rec.launchCmd;
  }
  const token = loadTokenFromRecord(rec, sourceLabel);
  return Object.freeze({
    id: rec.id,
    label: rec.label,
    upstreamUrl: rec.upstreamUrl,
    token,
    defaultRunMode,
    launchCmd,
  });
}

function loadHostRegistry() {
  const file = process.env.HOSTS_CONFIG_FILE;
  if (file && file.length > 0) {
    let raw;
    try {
      raw = fs.readFileSync(file, 'utf8');
    } catch (err) {
      console.error(`[proxy] failed to read HOSTS_CONFIG_FILE: ${err.code ?? 'ENOENT'}`);
      process.exit(2);
    }
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      console.error('[proxy] HOSTS_CONFIG_FILE is not valid JSON');
      process.exit(2);
    }
    const list = Array.isArray(parsed?.hosts) ? parsed.hosts : null;
    if (!list || list.length === 0) {
      console.error('[proxy] HOSTS_CONFIG_FILE must contain a non-empty "hosts" array');
      process.exit(2);
    }
    const byId = new Map();
    for (let i = 0; i < list.length; i++) {
      const frozen = validateAndFreezeHost(list[i], `hosts[${i}]`);
      if (byId.has(frozen.id)) {
        console.error(`[proxy] duplicate host id "${safeLogString(frozen.id, 64)}" in HOSTS_CONFIG_FILE`);
        process.exit(2);
      }
      byId.set(frozen.id, frozen);
    }
    return { byId, source: 'HOSTS_CONFIG_FILE' };
  }
  // No config file — synthesize a single default host from the pre-C10 env
  // vars so single-host deployments keep working unchanged. If UPSTREAM_URL
  // is unset we default to loopback 4222 (matches pre-C10 behavior); the
  // synthesized id is fixed as "default" so browsers can always target
  // `?host=default` without knowing whether multi-host is enabled.
  const upstreamUrl = process.env.UPSTREAM_URL ?? 'ws://127.0.0.1:4222';
  const label = process.env.DEFAULT_HOST_LABEL ?? 'default';
  const defaultRunMode =
    typeof process.env.DEFAULT_HOST_RUN_MODE === 'string' &&
    VALID_RUN_MODE.has(process.env.DEFAULT_HOST_RUN_MODE)
      ? process.env.DEFAULT_HOST_RUN_MODE
      : 'default';
  const token = loadTokenFromRecord(
    {
      token: process.env.UPSTREAM_TOKEN,
      tokenFile: process.env.UPSTREAM_TOKEN_FILE,
    },
    'env(default)',
  );
  return {
    byId: new Map([
      [
        'default',
        Object.freeze({
          id: 'default',
          label,
          upstreamUrl,
          token,
          defaultRunMode,
          launchCmd: null,
        }),
      ],
    ]),
    source: 'env',
  };
}

const HOST_REGISTRY = loadHostRegistry();
const IS_SINGLE_HOST = HOST_REGISTRY.byId.size === 1;
// Backward-compat queryless connect is allowed ONLY when the registry was
// SYNTHESIZED from env (source === 'env'). A HOSTS_CONFIG_FILE with a single
// record still requires an explicit opaque `?host=<id>` — the operator opted
// into strict routing by writing the config file at all.
const SYNTHESIZED_DEFAULT_HOST =
  HOST_REGISTRY.source === 'env' && IS_SINGLE_HOST
    ? [...HOST_REGISTRY.byId.values()][0]
    : null;

// Resolve an opaque host id to its record. Returns null on any refusal
// (absent, empty string, malformed shape, unknown id) — the caller is
// responsible for the appropriate 4xx.
//
// The ONE queryless exception is the SYNTHESIZED single-host default (no
// HOSTS_CONFIG_FILE present). A configured single-host registry still
// REQUIRES an explicit id — operators writing a config file are opting into
// strict routing. Empty-string `?host=` is treated as malformed (not absent)
// so a URL like `/ws?host=` cannot slip through.
function resolveHost(rawId) {
  // Absent param: only the synthesized default is allowed to fall back.
  if (rawId == null) return SYNTHESIZED_DEFAULT_HOST;
  // Empty-string param: always malformed. Never the same as "absent".
  if (rawId === '') return null;
  if (!VALID_ID.test(rawId)) return null;
  return HOST_REGISTRY.byId.get(rawId) ?? null;
}

// Safe host listing — NEVER emits upstreamUrl, token, or tokenFile. The
// browser reads this to populate its picker; everything the browser can
// safely see is here, everything else stays host-side.
function safeHostList() {
  return {
    hosts: [...HOST_REGISTRY.byId.values()].map((h) => ({
      id: h.id,
      label: h.label,
      hasAuth: !!h.token,
      defaultRunMode: h.defaultRunMode,
    })),
  };
}

// ---------- helpers ----------

function safeLogString(s, max = 200) {
  // Prevent CRLF injection / ANSI escapes / other control chars leaking into
  // shared logs. Replace any C0/C1 control byte with `?`, cap length.
  if (typeof s !== 'string') s = String(s ?? '');
  const cleaned = s.replace(/[\x00-\x1f\x7f-\x9f]/g, '?');
  return cleaned.length > max ? cleaned.slice(0, max) + '...' : cleaned;
}

// CV audit r2: static-response security headers. The web UI is entirely
// self-hosted from web/dist — no third-party scripts, no external images,
// no XHR to third parties, no OAuth-style redirects — so a strict CSP is
// safe. `frame-ancestors 'none'` + `X-Frame-Options: DENY` blocks the whole
// class of "iframe the authenticated UI and clickjack an approval" attacks.
// `Referrer-Policy: no-referrer` keeps host/paths off outbound requests.
// `connect-src` deliberately allows both http(s) and ws(s) same-origin so a
// TLS-terminating front (Tailscale Serve, an operator-run reverse proxy) can
// upgrade the scheme without breaking the app.
const STATIC_CSP = [
  "default-src 'self'",
  // Vite emits inline module preloads and small runtime shims; the built
  // bundle uses `unsafe-inline` for style, which the fresh install ships
  // with. Keep script tight; allow inline for style only.
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  // WS/HTTP destinations: 'self' plus scheme sources so a TLS-terminating
  // reverse proxy (Tailscale Serve, an operator-run front) can upgrade the
  // scheme without breaking `new WebSocket(...)`. Scheme sources are broader
  // than 'self' — noted as intentional; the load-bearing gate is the
  // proxy's OWN Origin allowlist, not CSP.
  "connect-src 'self' ws: wss: http: https:",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
  "form-action 'none'",
].join('; ');

function commonSecurityHeaders(extra = {}) {
  return {
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'DENY',
    'referrer-policy': 'no-referrer',
    'content-security-policy': STATIC_CSP,
    'cross-origin-opener-policy': 'same-origin',
    'cross-origin-resource-policy': 'same-origin',
    ...extra,
  };
}

// ---------- static file server ----------

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.png': 'image/png',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
};

function replyError(res, status, msg) {
  res.writeHead(status, commonSecurityHeaders({ 'content-type': 'text/plain; charset=utf-8' }));
  res.end(msg);
}

function serveStatic(req, res) {
  let urlPath;
  try {
    urlPath = decodeURIComponent((req.url ?? '/').split('?')[0]);
  } catch {
    replyError(res, 400, 'bad request');
    return;
  }
  const safeSuffix = path
    .normalize(urlPath)
    .replace(/^([/\\])+/, '')
    .replace(/\.\.[/\\]/g, '');
  let target = path.join(PUBLIC_DIR, safeSuffix);
  const rel = path.relative(PUBLIC_DIR, target);
  if (rel.startsWith('..') || path.isAbsolute(rel)) {
    replyError(res, 403, 'forbidden');
    return;
  }
  fs.stat(target, (err, stat) => {
    if (err) return replyError(res, 404, 'not found');
    if (stat.isDirectory()) target = path.join(target, 'index.html');
    // Reject symlinks under PUBLIC_DIR — defense in depth against an
    // attacker-planted link even though the tree currently has none.
    fs.lstat(target, (err0, lstat) => {
      if (err0 || !lstat.isFile() || lstat.isSymbolicLink()) {
        return replyError(res, 404, 'not found');
      }
      const ext = path.extname(target).toLowerCase();
      res.writeHead(
        200,
        commonSecurityHeaders({ 'content-type': MIME[ext] ?? 'application/octet-stream' }),
      );
      fs.createReadStream(target).pipe(res);
    });
  });
}

// C11 — pick http or https based on TLS_OPTIONS. https.createServer inherits
// the same request-handling contract as http.createServer, and the ws upgrade
// path is identical (WebSocketServer runs in noServer mode; we hand the
// upgraded socket to it manually).
const createServer = TLS_ENABLED
  ? (h) => https.createServer(TLS_OPTIONS, h)
  : (h) => http.createServer(h);

// Send a 401 with a WWW-Authenticate: Basic challenge. Use safeLogString on
// the realm defensively even though it's a fixed literal.
function replyBasicAuthChallenge(res) {
  res.writeHead(
    401,
    commonSecurityHeaders({
      'content-type': 'text/plain; charset=utf-8',
      'www-authenticate': `Basic realm="${safeLogString(BASIC_AUTH_REALM, 64)}", charset="UTF-8"`,
    }),
  );
  res.end('Unauthorized');
}

const httpServer = createServer((req, res) => {
  // CV: Basic Auth (when enabled) gates EVERY HTTP endpoint — including
  // `/proxy/healthz` and `/proxy/hosts`. The CLI's own health-gate probes
  // healthz with the same credentials it configured; nothing else here is
  // safe to leave unauthenticated on an exposed proxy (healthz leaks
  // hostCount / tls / activeConnections which are useful for scoping an
  // attack). Additive to the origin allowlist that gates the WS upgrade.
  if (BASIC_AUTH_ENABLED && !checkBasicAuth(req)) {
    replyBasicAuthChallenge(res);
    return;
  }
  if (req.url === '/proxy/healthz') {
    // Boolean flags + counts only. In single-host mode we keep the legacy
    // `upstream` (URL) and `auth` fields for backward-compat with pre-C10
    // callers (the raw index.html + external smoke tools). In multi-host
    // mode both are omitted so no per-host URL leaks; the browser reads
    // `/proxy/hosts` for the safe listing instead.
    const payload = {
      ok: true,
      allowlistSize: ALLOWED_METHODS.size,
      activeConnections,
      maxConnections: MAX_CONNECTIONS,
      wsPath: WS_PATH,
      hostCount: HOST_REGISTRY.byId.size,
      multiHost: !IS_SINGLE_HOST,
      // C11: browser-visible TLS status. Boolean-only — never emits cert
      // paths / SANs / key material. The browser uses it to render a
      // "TLS on" indicator; the App also reads `location.protocol` (which
      // is the true source when TLS sits in front via Tailscale Serve).
      tls: TLS_ENABLED,
      // CV: browser-visible Basic Auth status. Boolean-only. Never emits the
      // configured user or password. When true, the browser reader has
      // ALREADY passed the Basic Auth challenge to reach this endpoint.
      basicAuth: BASIC_AUTH_ENABLED,
    };
    // Legacy `upstream` (URL) + `auth` (bool) — kept ONLY for the synthesized
    // single-host default (pre-C10 deployments + `public/index.html`'s raw
    // fallback). A configured single-host registry (HOSTS_CONFIG_FILE with
    // exactly one entry) is treated as an explicit opt-in to strict semantics
    // and does NOT emit these fields (same principle as strict queryless
    // routing — see resolveHost / SYNTHESIZED_DEFAULT_HOST).
    if (SYNTHESIZED_DEFAULT_HOST) {
      payload.upstream = SYNTHESIZED_DEFAULT_HOST.upstreamUrl;
      payload.auth = !!SYNTHESIZED_DEFAULT_HOST.token;
    }
    res.writeHead(
      200,
      commonSecurityHeaders({ 'content-type': 'application/json; charset=utf-8' }),
    );
    res.end(JSON.stringify(payload));
    return;
  }
  if (req.url === '/proxy/hosts') {
    // Safe listing — id + label + hasAuth + defaultRunMode ONLY.
    // NEVER upstreamUrl, token, or tokenFile.
    res.writeHead(
      200,
      commonSecurityHeaders({ 'content-type': 'application/json; charset=utf-8' }),
    );
    res.end(JSON.stringify(safeHostList()));
    return;
  }
  serveStatic(req, res);
});

// Serialize the same hardening header set that `commonSecurityHeaders`
// applies to normal responses, so raw-socket rejections (WS upgrade path,
// clientError) don't degrade to a weaker header set. Bare-min set of extras
// beyond the CV headers: `content-length: 0` + `connection: close` so the
// socket closes cleanly.
function rawHttpHeaderLines(extra = {}) {
  const headers = {
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'DENY',
    'referrer-policy': 'no-referrer',
    'content-security-policy': STATIC_CSP,
    'cross-origin-opener-policy': 'same-origin',
    'cross-origin-resource-policy': 'same-origin',
    'content-length': '0',
    'connection': 'close',
    ...extra,
  };
  let lines = '';
  for (const [k, v] of Object.entries(headers)) {
    lines += `${k}: ${v}\r\n`;
  }
  return lines;
}

// Common raw-socket HTTP response for upgrade rejections / clientError, with
// the same hardening headers we set on normal HTTP responses.
function rawHttpReject(socket, status, statusText) {
  try {
    socket.end(
      `HTTP/1.1 ${status} ${statusText}\r\n` + rawHttpHeaderLines() + '\r\n',
    );
  } catch {}
}

// Raw-socket 401 with WWW-Authenticate: Basic challenge. Used to reject a WS
// upgrade when Basic Auth is enabled and the request lacks valid creds. The
// browser will retry with the Authorization header after the user auths to
// the page (or its own prompt) — same as any HTTP 401.
function rawHttpRejectBasicAuth(socket) {
  try {
    socket.end(
      'HTTP/1.1 401 Unauthorized\r\n' +
        rawHttpHeaderLines({
          'www-authenticate': `Basic realm="${safeLogString(BASIC_AUTH_REALM, 64)}", charset="UTF-8"`,
        }) +
        '\r\n',
    );
  } catch {}
}

// Catch handler-scoped exceptions rather than crashing the whole process.
httpServer.on('clientError', (_err, socket) => {
  rawHttpReject(socket, 400, 'Bad Request');
});

// ---------- ws proxy ----------

const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_FRAME_BYTES });

let activeConnections = 0;
let connSeq = 0;

httpServer.on('upgrade', (req, socket, head) => {
  let url;
  try {
    url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
  } catch {
    rawHttpReject(socket, 400, 'Bad Request');
    socket.destroy();
    return;
  }
  if (url.pathname !== WS_PATH) {
    rawHttpReject(socket, 404, 'Not Found');
    socket.destroy();
    return;
  }

  // Client-origin allowlist. Any Origin (even "null" from sandboxed iframes)
  // must be explicitly listed. An absent Origin header is only allowed when
  // ALLOW_NO_ORIGIN=1 — never trust an absent Origin from a real browser (all
  // browsers set it on ws upgrades; absence implies a non-browser client).
  const rawOrigin = req.headers.origin;
  const originPresent = rawOrigin != null;
  const originOk = originPresent
    ? ALLOWED_CLIENT_ORIGINS.has(rawOrigin)
    : ALLOW_NO_ORIGIN;
  if (!originOk) {
    console.warn(
      `[proxy] upgrade-denied origin=${originPresent ? safeLogString(rawOrigin, 128) : '(absent)'}`,
    );
    rawHttpReject(socket, 403, 'Forbidden');
    socket.destroy();
    return;
  }

  // CV: Basic Auth gate on the WS UPGRADE. Applies in addition to the origin
  // allowlist above. Protecting the page but not the WS would leave codex
  // reachable to any non-browser client on the LAN / tailnet, so the WS MUST
  // be gated too. Browsers replay the Basic `Authorization` header on
  // same-origin WS upgrades once the user has authed to the page. Log only
  // the fixed denial category — NEVER the header value.
  if (BASIC_AUTH_ENABLED && !checkBasicAuth(req)) {
    console.warn('[proxy] upgrade-denied basic-auth');
    rawHttpRejectBasicAuth(socket);
    socket.destroy();
    return;
  }

  // C10: resolve the opaque `host` id to its host-side record. Absent id is
  // accepted only in single-host mode (backward compat); every other case
  // MUST supply a known id or we refuse the upgrade. NEVER trust anything
  // in the query string beyond the id itself — no URL, no token, no host
  // metadata comes from the browser.
  const rawHostParam = url.searchParams.get('host');
  const host = resolveHost(rawHostParam);
  if (!host) {
    console.warn(
      `[proxy] upgrade-denied host=${rawHostParam == null ? '(absent)' : safeLogString(rawHostParam, 64)} reason=${rawHostParam == null ? 'host-required-multi-host' : 'unknown-host-id'}`,
    );
    rawHttpReject(socket, rawHostParam == null ? 400 : 404, 'Bad Request');
    socket.destroy();
    return;
  }

  if (activeConnections >= MAX_CONNECTIONS) {
    console.warn(`[proxy] upgrade-denied max-connections=${MAX_CONNECTIONS}`);
    rawHttpReject(socket, 503, 'Service Unavailable');
    socket.destroy();
    return;
  }

  // Stash the resolved record on the request so `wss.on('connection', ...)`
  // can pick it up without re-parsing (avoids TOCTOU on the query string).
  req.__cwrHost = host;
  wss.handleUpgrade(req, socket, head, (client) => wss.emit('connection', client, req));
});

// One inbound browser ws => exactly one upstream ws. No sharing, no multiplex.
wss.on('connection', (client, req) => {
  const connId = ++connSeq;
  activeConnections++;
  const host = req.__cwrHost;
  const peer = `${req.socket.remoteAddress}:${req.socket.remotePort}`;
  const clientOrigin = req.headers.origin ?? '';
  console.log(
    `[proxy][#${connId}] client-connected peer=${peer} origin=${safeLogString(clientOrigin, 128)} host-id=${safeLogString(host.id, 64)} active=${activeConnections}`,
  );

  // Build upstream headers. Two invariants:
  //   * NO `Origin` header (app-server 403s any value; ws lib default is none).
  //   * NO client-supplied Authorization is ever inherited; if we have a token
  //     we set it ourselves; otherwise send none.
  const upstreamHeaders = { 'user-agent': UPSTREAM_UA };
  if (host.token) upstreamHeaders['authorization'] = `Bearer ${host.token}`;

  const upstream = new WebSocket(host.upstreamUrl, {
    headers: upstreamHeaders,
    maxPayload: MAX_FRAME_BYTES,
    origin: undefined, // belt-and-suspenders: never let ws attach an Origin
    handshakeTimeout: UPSTREAM_HANDSHAKE_TIMEOUT_MS,
  });

  let clientOpen = true;
  let upstreamOpen = false;
  const pendingFromClient = [];
  let pendingBytes = 0;
  let lastActivityAt = Date.now();
  const bump = () => { lastActivityAt = Date.now(); };

  // Track server-issued request IDs so client responses can only answer real
  // pending server requests. Bounded to protect against memory growth if
  // upstream ever emits an unbounded number of unresolved requests. Keys are
  // typed (`n:1` vs `s:"1"`) so numeric 1 and string "1" don't collide.
  const pendingServerRequestIds = new Set();
  const MAX_PENDING_SERVER_REQUESTS = 128;
  const idKey = (id) => `${typeof id === 'number' ? 'n' : 's'}:${String(id)}`;

  const closeBoth = (code, reason) => {
    try { client.close(code, safeLogString(reason ?? '', 120)); } catch {}
    try { upstream.close(code, safeLogString(reason ?? '', 120)); } catch {}
    // Terminate upstream if still connecting (close is a no-op pre-open).
    try {
      if (upstream.readyState === WebSocket.CONNECTING) upstream.terminate();
    } catch {}
    pendingFromClient.length = 0;
  };

  function sendClientJson(obj) {
    if (!clientOpen) return;
    try { client.send(JSON.stringify(obj), { binary: false }); } catch {}
  }

  function traceFrame(dir, method, id, bytes) {
    if (!TRACE) return;
    console.log(
      `[proxy][#${connId}] ${dir} m=${safeLogString(method ?? '(none)', 80)} id=${safeLogString(String(id ?? '-'), 80)} ${bytes}B`,
    );
  }

  function parseMaybe(data) {
    const text = typeof data === 'string' ? data : data.toString('utf8');
    try { return { text, obj: JSON.parse(text) }; } catch { return { text, obj: null }; }
  }

  function isAllowedForClient(msg) {
    if (!msg || typeof msg !== 'object' || Array.isArray(msg)) return { ok: false, reason: 'shape' };
    // Requests + notifications: method must be in the allowlist.
    if (typeof msg.method === 'string') {
      return ALLOWED_METHODS.has(msg.method)
        ? { ok: true }
        : { ok: false, reason: 'method-not-permitted' };
    }
    // Responses to upstream server-requests: id must match a pending server request.
    if (msg.id != null && (Object.hasOwn(msg, 'result') || Object.hasOwn(msg, 'error'))) {
      const key = idKey(msg.id);
      if (pendingServerRequestIds.has(key)) {
        pendingServerRequestIds.delete(key);
        return { ok: true };
      }
      return { ok: false, reason: 'unsolicited-response' };
    }
    return { ok: false, reason: 'shape' };
  }

  // ---- upstream lifecycle ----

  upstream.on('open', () => {
    if (!clientOpen) {
      // Client already gave up; do not flush queued requests upstream.
      try { upstream.close(1000, 'client left'); } catch {}
      pendingFromClient.length = 0;
      pendingBytes = 0;
      return;
    }
    upstreamOpen = true;
    console.log(`[proxy][#${connId}] upstream-open host-id=${safeLogString(host.id, 64)} auth=${!!host.token}`);
    for (const buf of pendingFromClient) upstream.send(buf, { binary: false });
    pendingFromClient.length = 0;
    pendingBytes = 0;
  });

  upstream.on('message', (data, isBinary) => {
    if (!clientOpen) return;
    if (isBinary) {
      console.warn(`[proxy][#${connId}] dropping unexpected binary upstream frame`);
      return;
    }
    bump();
    const { text, obj } = parseMaybe(data);
    // If this is a server-issued request the client is expected to answer,
    // record its id so the client's response is accepted later.
    if (
      obj &&
      typeof obj.method === 'string' &&
      obj.id != null &&
      UPSTREAM_SERVER_REQUEST_METHODS.has(obj.method)
    ) {
      if (pendingServerRequestIds.size >= MAX_PENDING_SERVER_REQUESTS) {
        console.warn(`[proxy][#${connId}] too-many-pending-server-requests — closing`);
        closeBoth(1013, 'server-request pressure');
        return;
      }
      pendingServerRequestIds.add(idKey(obj.id));
    }
    // The app-server emits `serverRequest/resolved` when a pending server
    // request is resolved WITHOUT a client response — turn interruption,
    // turn completion with the approval left unanswered, or any other
    // server-side path (see `codex-rs/app-server/README.md`). Without this
    // cleanup the proxy's pending-id set would leak on every such event
    // and, after 128 leaks, `MAX_PENDING_SERVER_REQUESTS` closes the ws.
    // The kernel dispatcher already handles this on its side; the proxy
    // must mirror it so the response-allowlist gate matches reality.
    if (
      obj &&
      obj.method === 'serverRequest/resolved' &&
      obj.params &&
      typeof obj.params === 'object' &&
      obj.params.requestId != null
    ) {
      pendingServerRequestIds.delete(idKey(obj.params.requestId));
    }
    traceFrame('u->c', obj?.method, obj?.id, text.length);

    // Slow-client protection: if the outbound buffer is piling up, close.
    if (client.bufferedAmount > CLIENT_BUFFERED_HIGH_WATER) {
      console.warn(`[proxy][#${connId}] client-slow-consumer bufferedAmount=${client.bufferedAmount}`);
      closeBoth(1013, 'client slow consumer');
      return;
    }
    client.send(text, { binary: false });
  });

  upstream.on('close', (code, reason) => {
    console.log(`[proxy][#${connId}] upstream-close code=${code}`);
    upstreamOpen = false;
    if (clientOpen) {
      // C11 r2 fix — forward authentication-shaped close codes to the browser
      // so the kernel's terminal-error detector reads the REAL upstream
      // failure (1008 policy violation / 4401 unauthorized / 4403 forbidden)
      // instead of a generic 1011. Non-auth upstream closes stay as 1011
      // ("upstream closed") so the kernel treats them as transient and
      // reconnects. Reason is scrubbed to a short generic string — we never
      // pass an upstream-provided reason verbatim to the browser (that would
      // widen the log-scrub surface to the wire).
      const isAuthClose = code === 1008 || code === 4401 || code === 4403;
      if (isAuthClose) {
        closeBoth(code, 'upstream auth failed');
      } else {
        closeBoth(1011, 'upstream closed');
      }
    }
  });

  upstream.on('error', (err) => {
    console.warn(`[proxy][#${connId}] upstream-error ${err.code ?? err.message}`);
    if (clientOpen) closeBoth(1011, 'upstream error');
  });

  // ---- client lifecycle ----

  client.on('message', (data, isBinary) => {
    if (isBinary) {
      console.warn(`[proxy][#${connId}] dropping unexpected binary client frame`);
      return;
    }
    bump();
    const { text, obj } = parseMaybe(data);
    if (text.length > MAX_FRAME_BYTES) {
      console.warn(`[proxy][#${connId}] oversized client frame ${text.length}B — closing`);
      closeBoth(1009, 'frame too large');
      return;
    }
    if (!obj) {
      sendClientJson({
        jsonrpc: '2.0',
        id: null,
        error: { code: -32700, message: 'proxy: parse error (JSON required)' },
      });
      return;
    }
    const verdict = isAllowedForClient(obj);
    if (!verdict.ok) {
      const method = typeof obj.method === 'string' ? obj.method : '(no-method)';
      // Fixed denial category + safe-logged method; never any params.
      console.warn(
        `[proxy][#${connId}] deny reason=${verdict.reason} method=${safeLogString(method, 80)}`,
      );
      if (obj.id != null) {
        sendClientJson({
          jsonrpc: '2.0',
          id: obj.id,
          error: {
            code: -32601,
            message: `proxy: method not permitted (${safeLogString(method, 80)})`,
          },
        });
      }
      return; // NEVER forward
    }
    traceFrame('c->u', obj.method, obj.id, text.length);

    // Slow-upstream protection.
    if (upstreamOpen && upstream.bufferedAmount > UPSTREAM_BUFFERED_HIGH_WATER) {
      console.warn(`[proxy][#${connId}] upstream-slow-consumer bufferedAmount=${upstream.bufferedAmount}`);
      closeBoth(1013, 'upstream slow consumer');
      return;
    }
    if (!upstreamOpen) {
      // UTF-8 byte length — matches wire size regardless of non-ASCII payload.
      const frameBytes = Buffer.byteLength(text, 'utf8');
      if (
        pendingFromClient.length >= MAX_PENDING_FRAMES ||
        pendingBytes + frameBytes > MAX_PENDING_BYTES
      ) {
        console.warn(
          `[proxy][#${connId}] pending-buffer-full frames=${pendingFromClient.length} bytes=${pendingBytes} — closing`,
        );
        closeBoth(1013, 'upstream not ready');
        return;
      }
      pendingFromClient.push(text);
      pendingBytes += frameBytes;
      return;
    }
    bump();
    upstream.send(text, { binary: false });
  });

  client.on('close', (code) => {
    console.log(`[proxy][#${connId}] client-close code=${code}`);
    clientOpen = false;
    // Cancel upstream regardless of ready state; drop any queued frames.
    closeBoth(1000, 'client closed');
  });

  client.on('error', (err) => {
    console.warn(`[proxy][#${connId}] client-error ${err.code ?? err.message}`);
    clientOpen = false;
    closeBoth(1011, 'client error');
  });

  // Heartbeat + idle-connection reap. Ping every HEARTBEAT_INTERVAL_MS; if
  // there's no APPLICATION traffic in either direction for
  // CONNECTION_IDLE_TIMEOUT_MS, terminate — well-behaved auto-pong must NOT
  // reset the idle timer (otherwise every browser silently keeps its slot
  // forever). Only real message frames call `bump()`.
  let heartbeatPongAt = Date.now();
  client.on('pong', () => { heartbeatPongAt = Date.now(); });
  const heartbeat = setInterval(() => {
    const now = Date.now();
    if (now - lastActivityAt > CONNECTION_IDLE_TIMEOUT_MS) {
      console.warn(`[proxy][#${connId}] idle-timeout — closing`);
      clearInterval(heartbeat);
      closeBoth(1000, 'idle timeout');
      return;
    }
    if (now - heartbeatPongAt > HEARTBEAT_INTERVAL_MS * 2) {
      console.warn(`[proxy][#${connId}] client heartbeat missed — terminating`);
      clearInterval(heartbeat);
      try { client.terminate(); } catch {}
      try { upstream.terminate(); } catch {}
      return;
    }
    try { client.ping(); } catch {}
  }, HEARTBEAT_INTERVAL_MS);

  // Track active-connection count and stop the heartbeat on teardown.
  const dec = () => {
    if (!dec.done) {
      dec.done = true;
      activeConnections--;
      clearInterval(heartbeat);
    }
  };
  client.on('close', dec);
  client.on('error', dec);
});

httpServer.listen(LISTEN_PORT, LISTEN_HOST, () => {
  const anyAuth = [...HOST_REGISTRY.byId.values()].some((h) => !!h.token);
  const allAuth = [...HOST_REGISTRY.byId.values()].every((h) => !!h.token);
  const scheme = TLS_ENABLED ? 'https+wss' : 'http+ws';
  const urlScheme = TLS_ENABLED ? 'https' : 'http';
  console.log(
    `[proxy] listening ${scheme} ${urlScheme}://${_LISTEN_HOST_URL}:${LISTEN_PORT}${WS_PATH}` +
      `  tls=${TLS_ENABLED ? 'on' : 'off'}` +
      `  basic-auth=${BASIC_AUTH_ENABLED ? 'on' : 'off'}` +
      `  hosts=${HOST_REGISTRY.byId.size}(${HOST_REGISTRY.source})` +
      `  auth=${allAuth ? 'all' : anyAuth ? 'some' : 'none'}` +
      `  allowlist=${ALLOWED_METHODS.size}` +
      `  origins=${ALLOWED_CLIENT_ORIGINS.size}` +
      `  max-conn=${MAX_CONNECTIONS}` +
      `  public=${PUBLIC_DIR}`,
  );
  if (!anyAuth) {
    console.warn(
      '[proxy] WARNING: NO host has a bearer token — only safe if app-server is loopback-bound and only trusted local clients can reach this proxy.',
    );
  }
});

for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(sig, () => {
    console.log(`[proxy] ${sig} received, shutting down`);
    httpServer.close(() => process.exit(0));
    setTimeout(() => process.exit(1), 3000).unref();
  });
}
