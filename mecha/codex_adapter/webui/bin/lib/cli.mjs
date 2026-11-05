// Codex Web Remote — CLI argument parser + smart-upstream decision logic.
//
// PURE. No fs / net / process side effects. Everything the bin runtime needs
// to decide "connect vs launch" is derived from parsed args + an injected
// probe. Unit tests wire fake probes; runtime wires the real TCP probe.
//
// Security invariants this file participates in:
//   * `--codex "<cmd>"` and env `CODEX_APP_SERVER_CMD` are HOST-SIDE ONLY
//     (nothing on the wire can influence them). This module never reads
//     them; it just decides whether the launcher should be spawned at all.
//   * `--upstream <url>` supplied by the operator ⇒ CONNECT-ONLY, never
//     launch. We refuse to auto-spawn a codex app-server against a URL the
//     operator explicitly pointed us at — that URL might belong to another
//     stack (a real remote, a scratch pair, someone else's session).
//   * The default `ws://127.0.0.1:4222` when unreachable ⇒ launch is
//     allowed because the operator did not name a foreign target.
//
// Default codex command when we DO launch: `npx @ai-nd-co/codex@alpha`.
// This is exactly the whitespace-tokenized string the launcher expects
// (`@` is not ASCII whitespace so `@ai-nd-co/codex@alpha` is one token).

export const DEFAULT_UPSTREAM_URL = 'ws://127.0.0.1:4222';
export const DEFAULT_CODEX_CMD = 'npx @ai-nd-co/codex@alpha';
export const DEFAULT_PROXY_PORT = 8787;
export const DEFAULT_PROXY_HOST = '127.0.0.1';

// Loopback bind hosts. Non-loopback binds are the trigger for the
// exposed-proxy `--allow-origin` WARNING.
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '[::1]']);
export function isLoopbackHost(h) { return LOOPBACK_HOSTS.has(String(h)); }

// Format a bind host for embedding in an origin/URL. IPv6 literals must be
// bracketed per RFC 3986 §3.2.2 — otherwise `http://::1:8787` is a parse
// error (three colons). Already-bracketed forms pass through unchanged.
// Non-IPv6 hosts (IPv4 literals + DNS names + hostnames) return as-is.
export function formatHostForUrl(h) {
  const s = String(h);
  if (s.startsWith('[')) return s;                 // already bracketed
  if (s.includes(':')) return `[${s}]`;            // raw IPv6 -> bracket
  return s;
}

// Given a bind host as passed via --host, return the host the CLI should USE
// when opening the browser / probing health. Wildcard binds resolve to their
// loopback equivalent (0.0.0.0 -> 127.0.0.1, :: -> ::1). Everything else is
// passed through. This is a display+probe helper, NOT a bind-time helper.
export function connectHostFor(h) {
  const s = String(h);
  if (s === '0.0.0.0') return '127.0.0.1';
  if (s === '::' || s === '[::]') return '::1';
  return s;
}

// Validate a caller-supplied allow-origin string. Returns an error string on
// failure, or `null` on success. Deliberately narrow: must be a well-formed
// http(s) origin without a path/query/fragment; no wildcard, no bare host.
// Rejecting `*` here is the load-bearing invariant that keeps `--allow-origin`
// from silently disabling the client-origin allowlist.
export function validateAllowOrigin(v) {
  if (typeof v !== 'string' || v.length === 0) return `must be a non-empty string`;
  if (v.includes('*')) return `must be a specific origin (wildcard not supported): ${JSON.stringify(v)}`;
  if (!/^https?:\/\//i.test(v)) return `must start with http:// or https:// (got ${JSON.stringify(v)})`;
  let u;
  try { u = new URL(v); } catch { return `is not a valid URL (got ${JSON.stringify(v)})`; }
  if (u.username || u.password) return `must NOT include userinfo (browsers strip user:pass from the Origin header): ${JSON.stringify(v)}`;
  if (u.search || u.hash) return `must be an origin only (no query/fragment): ${JSON.stringify(v)}`;
  if (u.origin === 'null') return `origin serializes to "null" (unsupported): ${JSON.stringify(v)}`;
  // Browsers send `Origin: <scheme>://<host>[:<port>]` — canonical, NEVER a
  // trailing slash, NEVER userinfo. If the operator's string doesn't already
  // equal that canonical form, an exact-match allowlist can never accept the
  // real browser Origin (silent misconfiguration). Require canonicality.
  if (v !== u.origin) {
    return `must be a canonical origin (no trailing slash, no path). Got ${JSON.stringify(v)}; canonical form is ${JSON.stringify(u.origin)}`;
  }
  return null;
}

/**
 * Parse argv into a normalized options object.
 *
 * @param {string[]} argv - argv slice (without node + script name).
 * @param {{ env?: Record<string,string|undefined>, version?: string }} [opts]
 * @returns {{
 *   help: boolean,
 *   version: boolean,
 *   port: number,
 *   host: string,
 *   upstream: string,
 *   upstreamExplicit: boolean,
 *   codex: string|null,
 *   codexExplicit: boolean,
 *   tokenFile: string|null,
 *   open: boolean,
 *   error: string|null,
 * }}
 */
export function parseArgs(argv, opts = {}) {
  const env = opts.env ?? {};
  const out = {
    help: false,
    version: false,
    port: DEFAULT_PROXY_PORT,
    host: DEFAULT_PROXY_HOST,
    upstream: DEFAULT_UPSTREAM_URL,
    upstreamExplicit: false,
    upstreamSource: 'default',   // 'default' | 'env' | 'flag'
    codex: null,
    codexExplicit: false,
    codexSource: 'default',      // 'default' | 'env' | 'flag'
    tokenFile: null,
    tokenFileSource: 'default',  // 'default' | 'env' | 'flag'
    // CV: repeatable --allow-origin flag. Feeds ALLOWED_CLIENT_ORIGINS on the
    // proxy so a browser at a non-loopback origin (LAN IP, tailnet host) can
    // actually complete the WS upgrade. The loopback default is unchanged; we
    // ONLY add operator-named origins. No wildcards, no auto-any.
    allowOrigins: [],            // string[]
    allowOriginSource: 'default',
    // CV: HTTP Basic Auth (user:pass) — host-side ONLY, never logged. Feeds
    // CWR_BASIC_AUTH on the proxy, which then gates both HTTP and the WS
    // upgrade. Off by default. `--basic-auth-file <path>` is the preferred
    // form: --basic-auth on the command line leaks the secret into shell
    // history and process argv; the file form only stores the FILE PATH.
    basicAuth: null,             // string 'user:pass' or null (resolved value)
    basicAuthSource: 'default',
    basicAuthFile: null,         // absolute path or null
    basicAuthFileSource: 'default',
    // CV audit r2 (release-gating): the Origin allowlist is CSRF protection,
    // not authentication (a non-browser client can forge any Origin). On a
    // non-loopback bind we FAIL CLOSED unless the operator opts in explicitly.
    // Same for plaintext Basic Auth on a non-loopback bind — base64 on the wire
    // is captured by any on-path observer. `--insecure-expose` is the escape
    // hatch for disposable dev networks; it prints a loud warning and lets the
    // CLI start.
    insecureExpose: false,
    open: false,
    error: null,
  };

  // Env-var defaults are honoured but count as EXPLICIT if set — an operator
  // who put CODEX_APP_SERVER_CMD in their shell rc did opt in to that command.
  if (typeof env.CODEX_APP_SERVER_CMD === 'string' && env.CODEX_APP_SERVER_CMD.trim().length > 0) {
    out.codex = env.CODEX_APP_SERVER_CMD.trim();
    out.codexExplicit = true;
    out.codexSource = 'env';
  }
  if (typeof env.CODEX_WEB_REMOTE_UPSTREAM === 'string' && env.CODEX_WEB_REMOTE_UPSTREAM.trim().length > 0) {
    out.upstream = env.CODEX_WEB_REMOTE_UPSTREAM.trim();
    out.upstreamExplicit = true;
    out.upstreamSource = 'env';
  }
  if (typeof env.CODEX_WEB_REMOTE_TOKEN_FILE === 'string' && env.CODEX_WEB_REMOTE_TOKEN_FILE.trim().length > 0) {
    out.tokenFile = env.CODEX_WEB_REMOTE_TOKEN_FILE.trim();
    out.tokenFileSource = 'env';
  }
  // CV env-var defaults.
  //   * `CWR_BASIC_AUTH` / `CWR_BASIC_AUTH_FILE` — host-side ONLY; the CLI
  //     never logs them. VALUE shape validation is DEFERRED to
  //     `bin/codex-web-remote.mjs` post-precedence, so a malformed losing
  //     source can't abort startup (r4 blocker fix).
  //   * `CWR_ALLOW_ORIGIN` — comma-separated; each origin IS validated on
  //     parse because a malformed origin is never useful (it can never
  //     match a real browser Origin) and there's no precedence hazard here.
  if (typeof env.CWR_BASIC_AUTH === 'string' && env.CWR_BASIC_AUTH.length > 0) {
    out.basicAuth = env.CWR_BASIC_AUTH;
    out.basicAuthSource = 'env';
  }
  if (typeof env.CWR_BASIC_AUTH_FILE === 'string' && env.CWR_BASIC_AUTH_FILE.trim().length > 0) {
    out.basicAuthFile = env.CWR_BASIC_AUTH_FILE.trim();
    out.basicAuthFileSource = 'env';
  }
  // CV audit r6 fix: DO NOT validate env-origin values here. A `--allow-origin`
  // flag REPLACES the env list; validating env at parse time would abort
  // startup on a malformed env value even when the flag would have discarded
  // it entirely. Validation is deferred to `bin/codex-web-remote.mjs`, which
  // validates only the FINAL (winning) list.
  if (typeof env.CWR_ALLOW_ORIGIN === 'string' && env.CWR_ALLOW_ORIGIN.length > 0) {
    const parts = env.CWR_ALLOW_ORIGIN.split(',').map((s) => s.trim()).filter((s) => s.length > 0);
    if (parts.length > 0) {
      out.allowOrigins = parts;
      out.allowOriginSource = 'env';
    }
  }

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const need = (name) => {
      const v = argv[++i];
      if (v == null) {
        out.error = `flag ${name} requires a value`;
        return null;
      }
      return v;
    };
    if (a === '--help' || a === '-h') { out.help = true; return out; }
    else if (a === '--version' || a === '-v') { out.version = true; return out; }
    else if (a === '--port') {
      const v = need('--port'); if (out.error) return out;
      const n = Number.parseInt(v, 10);
      if (!Number.isInteger(n) || n <= 0 || n > 65535) {
        out.error = `--port must be 1..65535 (got ${JSON.stringify(v)})`;
        return out;
      }
      out.port = n;
    }
    else if (a === '--host') {
      const v = need('--host'); if (out.error) return out;
      out.host = v;
    }
    else if (a === '--upstream') {
      const v = need('--upstream'); if (out.error) return out;
      if (!/^wss?:\/\//i.test(v)) {
        out.error = `--upstream must be ws:// or wss:// (got ${JSON.stringify(v)})`;
        return out;
      }
      out.upstream = v;
      out.upstreamExplicit = true;
      out.upstreamSource = 'flag';
    }
    else if (a === '--codex') {
      const v = need('--codex'); if (out.error) return out;
      if (v.trim().length === 0) {
        out.error = `--codex requires a non-empty command string`;
        return out;
      }
      out.codex = v.trim();
      out.codexExplicit = true;
      out.codexSource = 'flag';
    }
    else if (a === '--token-file') {
      const v = need('--token-file'); if (out.error) return out;
      out.tokenFile = v;
      out.tokenFileSource = 'flag';
    }
    else if (a === '--allow-origin') {
      // Repeatable — every `--allow-origin` adds ONE origin. We deliberately
      // reject `*`, wildcards, and origins with paths — the browser sends the
      // exact origin string and the proxy compares exact-match, so anything
      // laxer than that would silently disable the allowlist. If env
      // populated the array, a flag REPLACES the env list on first use so
      // the operator's flag intent wins cleanly.
      const v = need('--allow-origin'); if (out.error) return out;
      const err = validateAllowOrigin(v);
      if (err) { out.error = `--allow-origin ${err}`; return out; }
      if (out.allowOriginSource === 'env') {
        out.allowOrigins = [];
      }
      out.allowOrigins.push(v);
      out.allowOriginSource = 'flag';
    }
    else if (a === '--basic-auth') {
      // Host-side ONLY. The value is `user:pass`; the CLI passes it to the
      // proxy child via CWR_BASIC_AUTH and NEVER prints it. Password may
      // contain colons; only the FIRST colon is the delimiter — but we don't
      // parse it here (proxy does), just enforce non-empty user AND pass.
      // `--basic-auth-file` is preferred: passing the secret on the command
      // line leaks it into shell history and the process's argv.
      //
      // CV audit r4 fix: shape validation of the VALUE is deferred to
      // `bin/codex-web-remote.mjs` post-precedence, so a losing source
      // (e.g. this flag when `--basic-auth-file` is ALSO given and wins)
      // doesn't abort startup because it happened to be malformed.
      const v = need('--basic-auth'); if (out.error) return out;
      out.basicAuth = v;
      out.basicAuthSource = 'flag';
    }
    else if (a === '--basic-auth-file') {
      const v = need('--basic-auth-file'); if (out.error) return out;
      if (v.length === 0) {
        out.error = `--basic-auth-file requires a path`;
        return out;
      }
      out.basicAuthFile = v;
      out.basicAuthFileSource = 'flag';
    }
    else if (a === '--insecure-expose') {
      out.insecureExpose = true;
    }
    else if (a === '--open') {
      out.open = true;
    }
    else {
      out.error = `unknown flag: ${JSON.stringify(a)}`;
      return out;
    }
  }
  return out;
}

/**
 * Given parsed args + a probe result, decide the run mode.
 *
 * Rules (as decided by the user; see the CQ task file):
 *   1. If `--upstream <url>` was EXPLICIT (CLI flag or env override): NEVER
 *      launch. connect-only. Reachability of that URL is the operator's
 *      concern — we just try to connect. (The launcher would target the
 *      DEFAULT loopback URL anyway, not this one; auto-launching for an
 *      unreachable operator-supplied URL would be actively wrong.)
 *   2. If the default upstream is REACHABLE: connect only.
 *   3. If the default upstream is NOT reachable: LAUNCH via the launcher,
 *      defaulting `CODEX_APP_SERVER_CMD` to `npx @ai-nd-co/codex@alpha`.
 *      A `--codex "<cmd>"` (or env) override wins.
 *
 * @param {ReturnType<typeof parseArgs>} args
 * @param {{ upstreamReachable: boolean }} probe
 * @returns {{
 *   mode: 'connect' | 'launch',
 *   upstreamUrl: string,
 *   codexCmd: string | null,     // present iff mode === 'launch'
 *   codexSource: 'flag' | 'env' | 'default' | null,
 *   reason: string,
 * }}
 */
export function decideRunMode(args, probe) {
  const reachable = !!probe.upstreamReachable;
  if (args.upstreamExplicit) {
    return {
      mode: 'connect',
      upstreamUrl: args.upstream,
      codexCmd: null,
      codexSource: null,
      reason: 'operator supplied --upstream (or CODEX_WEB_REMOTE_UPSTREAM); connect-only regardless of reachability',
    };
  }
  if (reachable) {
    return {
      mode: 'connect',
      upstreamUrl: args.upstream,
      codexCmd: null,
      codexSource: null,
      reason: `default upstream ${args.upstream} is reachable; connect-only`,
    };
  }
  const cmd = args.codex ?? DEFAULT_CODEX_CMD;
  return {
    mode: 'launch',
    upstreamUrl: args.upstream,
    codexCmd: cmd,
    codexSource: args.codexExplicit ? args.codexSource : 'default',
    reason: `default upstream ${args.upstream} is not reachable; launching codex via ${cmd}`,
  };
}

export const HELP_TEXT = `Usage: codex-web-remote [options]

Serves the bundled web UI + the security-boundary WebSocket proxy locally, and
either CONNECTS to a running codex app-server or LAUNCHES one if the default
loopback port is idle. All bearer tokens and launcher commands stay host-side;
the browser never sees an upstream URL or a token.

Options:
  --port <n>              Local port for the UI + proxy (default ${DEFAULT_PROXY_PORT}).
  --host <host>           Bind interface (default ${DEFAULT_PROXY_HOST}; keep loopback
                          unless you know why).
  --upstream <ws-url>     Existing codex app-server to connect to. When supplied
                          the CLI will NEVER auto-launch codex (connect-only,
                          regardless of reachability). Default: ${DEFAULT_UPSTREAM_URL}
                          — if the default is not reachable the CLI launches
                          codex itself.
  --codex "<command>"     Codex command to spawn when auto-launching (host-side
                          only, shell:false; whitespace-split into argv).
                          Default: "${DEFAULT_CODEX_CMD}".
  --token-file <path>     Absolute path to a file whose contents are the bearer
                          token the proxy adds to the upstream WS handshake.
                          Host-side only — the browser never sees it.
  --allow-origin <origin> Additional client Origin to allow at the WS upgrade.
                          Repeatable. The proxy already trusts its own
                          concrete LISTEN_HOST origin by default, so a bare
                          --host 192.168.1.10 works out of the box. This flag
                          is required only for wildcard binds (--host 0.0.0.0)
                          or a DIFFERENT externally visible origin (tailnet
                          host, reverse proxy). Must be a full http(s)
                          origin — no wildcards, no paths.
  --basic-auth <user:pass>
                          Enable HTTP Basic Auth on the served UI AND the WS
                          upgrade. Host-side only; the value is never logged.
                          Additive to the origin allowlist + host-side bearer
                          token. Off by default. PREFER --basic-auth-file —
                          command-line credentials leak into shell history +
                          argv.
  --basic-auth-file <path>
                          Path to a file whose (trimmed) contents are the
                          "user:pass" credential. Preferred over --basic-auth.
  --insecure-expose       Opt-in escape hatch that lets the CLI serve on a
                          non-loopback host WITHOUT authentication or
                          WITHOUT TLS. Disposable dev networks only; prints
                          a loud warning at startup. Default = FAIL CLOSED.
  --open                  Open the served URL in the default browser.
  --version, -v           Print the package version and exit.
  --help, -h              Print this help and exit.

Environment (equivalent to flags):
  CODEX_APP_SERVER_CMD          same as --codex
  CODEX_WEB_REMOTE_UPSTREAM     same as --upstream
  CODEX_WEB_REMOTE_TOKEN_FILE   same as --token-file
  CWR_ALLOW_ORIGIN              comma-separated list; same as repeating
                                --allow-origin. A --allow-origin flag on the
                                command line REPLACES the env list.
  CWR_BASIC_AUTH                same as --basic-auth "user:pass"
  CWR_BASIC_AUTH_FILE           same as --basic-auth-file <path> (preferred)

Dangerous "bypass approvals" mode is a UI toggle inside the web app (per-thread
runMode). This CLI does NOT expose it and neither does the app-server flag.

TLS: keep the CLI on loopback and terminate TLS at Tailscale Serve (recommended
for phone-over-tailnet), or set TLS_CERT_FILE + TLS_KEY_FILE in the environment
to have the proxy serve https+wss directly.
`;
