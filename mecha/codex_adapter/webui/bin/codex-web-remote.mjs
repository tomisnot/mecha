#!/usr/bin/env node
// Codex Web Remote — `npx @ai-nd-co/codex-web-remote` entrypoint.
//
// Serves the BUNDLED web/dist + the host-side security-boundary proxy on a
// local port, and either CONNECTS to a running codex app-server or LAUNCHES
// one (via scripts/launch-appserver.mjs, defaulting the command to
// `npx @ai-nd-co/codex@alpha`).
//
// SECURITY BOUNDARY — read carefully.
//   * `PUBLIC_DIR` for the proxy is set to the BUNDLED web/dist (resolved
//     via `import.meta.url`, NOT `process.cwd()`), so a `cd` into a random
//     directory cannot exfiltrate arbitrary local files via the static
//     server. See bin/lib/paths.mjs.
//   * The upstream URL + bearer token remain HOST-SIDE ONLY: we set
//     `UPSTREAM_URL` + optional `UPSTREAM_TOKEN_FILE` on the proxy's env
//     BEFORE spawning it. The browser never sees either — it only knows
//     "there is a proxy on 127.0.0.1:<port>".
//   * Auto-launching codex is ALLOWED only when the default upstream URL
//     is not reachable AND the operator did NOT pass `--upstream`. An
//     explicit `--upstream <url>` is CONNECT-ONLY, regardless of
//     reachability — we never spawn a codex against a URL the operator
//     specifically named (it might belong to another stack).
//   * The launcher (`scripts/launch-appserver.mjs`) already enforces its
//     own loopback-only, shell-safe, un-shim invariants; this CLI does not
//     bypass or weaken any of them.

import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import {
  parseArgs,
  decideRunMode,
  HELP_TEXT,
  DEFAULT_CODEX_CMD,
  isLoopbackHost,
  formatHostForUrl,
  connectHostFor,
  validateAllowOrigin,
} from './lib/cli.mjs';
import { computeRuntimePaths, verifyRuntimePaths } from './lib/paths.mjs';

const IS_WIN = process.platform === 'win32';

// -------------------------------------------------------------------------
// Version — read package.json at runtime so we don't fork the version string.
// -------------------------------------------------------------------------
function readPackageVersion(packageRoot) {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
    return typeof pkg.version === 'string' ? pkg.version : '(unknown)';
  } catch {
    return '(unknown)';
  }
}

// -------------------------------------------------------------------------
// Small helpers
// -------------------------------------------------------------------------

function log(msg)  { process.stdout.write(`[codex-web-remote] ${msg}\n`); }
function warn(msg) { process.stderr.write(`[codex-web-remote] WARN: ${msg}\n`); }
function die(msg, code = 1) {
  process.stderr.write(`[codex-web-remote] ERROR: ${msg}\n`);
  process.exit(code);
}

// Shape check for a Basic Auth credential using FIRST-COLON delimiter
// semantics: the password may itself contain colons (`alice:se:cret`), so
// the invariant is that a colon exists and is neither the first nor the
// last character. This matches the proxy's own `colon <= 0 || colon ===
// length - 1` check in `proxy/server.mjs`. (r5 blocker fix.)
function _looksLikeUserPass(s) {
  if (typeof s !== 'string') return false;
  const colon = s.indexOf(':');
  return colon > 0 && colon < s.length - 1;
}

/**
 * Probe a TCP host:port. Resolves { listening: true } if something answered
 * within `timeoutMs`, else { listening: false }. Used to decide whether to
 * launch a fresh codex or connect to a running one.
 */
function probeTcpPort({ host, port, timeoutMs = 500 }) {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    let done = false;
    const finish = (listening) => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch {}
      resolve({ listening });
    };
    sock.setTimeout(timeoutMs);
    sock.once('connect', () => finish(true));
    sock.once('timeout', () => finish(false));
    sock.once('error', () => finish(false));
    sock.connect(port, host);
  });
}

/** Parse a ws:// or wss:// URL into { host, port }. */
function parseWsUrl(url) {
  const u = new URL(url);
  const host = u.hostname;
  const port = Number.parseInt(u.port, 10);
  if (!host || !Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error(`could not extract host+port from ${JSON.stringify(url)}`);
  }
  return { host, port };
}

async function pollUntil(fn, { timeoutMs = 20000, intervalMs = 250, label = 'condition' } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  const err = new Error(`timed out waiting for ${label} after ${timeoutMs}ms${lastErr ? `: ${lastErr.message}` : ''}`);
  err.code = 'ETIMEDOUT';
  throw err;
}

function fetchJson(url, { timeoutMs = 1500, headers = {} } = {}) {
  // CV: pick http vs https from the URL scheme. When the proxy terminates TLS
  // itself (TLS_CERT_FILE + TLS_KEY_FILE set), the health-gate MUST speak
  // https to the local proxy or it self-deadlocks — the polling loop never
  // sees a 200 and eventually kills a perfectly healthy proxy. rejectUnauthorized
  // is FALSE here because the health probe is loopback-only against the proxy
  // WE just spawned — a self-signed dev cert is the whole point of TLS-direct
  // mode. (This does NOT relax TLS anywhere else — Tailscale Serve terminates
  // its own TLS in front and the CLI still probes plain http on loopback.)
  const isHttps = /^https:/i.test(url);
  const client = isHttps ? https : http;
  const opts = { timeout: timeoutMs, headers };
  if (isHttps) opts.rejectUnauthorized = false;
  return new Promise((resolve, reject) => {
    const req = client.get(url, opts, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        const body = { status: res.statusCode ?? 0, text, json: null };
        try { body.json = JSON.parse(text); } catch {}
        resolve(body);
      });
    });
    req.on('timeout', () => { req.destroy(new Error('timeout')); });
    req.on('error', reject);
  });
}

function openBrowser(url) {
  // Best-effort. Never blocks the CLI; failures are logged as a warning.
  try {
    let cmd, argv;
    if (IS_WIN) {
      // `cmd /c start "" <url>`: argv is host-side literal — url is derived
      // from our own bind params, not user-supplied.
      cmd = 'cmd';
      argv = ['/c', 'start', '""', url];
    } else if (process.platform === 'darwin') {
      cmd = 'open'; argv = [url];
    } else {
      cmd = 'xdg-open'; argv = [url];
    }
    const child = spawn(cmd, argv, { stdio: 'ignore', detached: true, windowsHide: true, shell: false });
    child.unref();
  } catch (err) {
    warn(`could not open browser: ${err.message}`);
  }
}

// -------------------------------------------------------------------------
// Main
// -------------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv.slice(2), { env: process.env });

  const paths = computeRuntimePaths(import.meta.url);
  const version = readPackageVersion(paths.packageRoot);

  if (args.help) { process.stdout.write(HELP_TEXT); return; }
  if (args.version) { process.stdout.write(`${version}\n`); return; }
  if (args.error) die(args.error, 2);

  const rt = verifyRuntimePaths(paths);
  if (!rt.ok) {
    die(
      `packaged runtime files missing (broken install?):\n  ` +
        rt.missing.join('\n  '),
      2,
    );
  }

  // CV audit r2: resolve --basic-auth-file BEFORE anything else. It affects
  // the fail-closed checks below and must be validated before we spawn any
  // child process. The resolved credential is held in memory as
  // `args.basicAuth`; the FILE PATH stays out of the proxy env.
  //
  // CV audit r3 fix (BLOCKER): precedence between the FOUR credential inputs
  // must be flag-beats-env, and file-beats-inline when equal. Without this,
  // `CWR_BASIC_AUTH_FILE=old --basic-auth new:cred` silently authenticated
  // with `old`, contradicting the documented "flag wins" rule.
  //   Rank: flag=2, env=1, none=0.
  //   The file form only overrides `basicAuth` when its rank is >= the
  //   inline form's rank. So:
  //     --basic-auth (flag, 2) + CWR_BASIC_AUTH_FILE (env, 1) → flag wins.
  //     --basic-auth-file (flag, 2) + --basic-auth (flag, 2)  → file wins.
  //     CWR_BASIC_AUTH_FILE (env, 1) + no inline               → file wins.
  const _srcRank = (s) => (s === 'flag' ? 2 : s === 'env' ? 1 : 0);
  if (args.basicAuthFile && _srcRank(args.basicAuthFileSource) >= _srcRank(args.basicAuthSource)) {
    if (!path.isAbsolute(args.basicAuthFile)) {
      die(`--basic-auth-file must be an absolute path (got ${JSON.stringify(args.basicAuthFile)})`, 2);
    }
    let contents;
    try {
      contents = fs.readFileSync(args.basicAuthFile, 'utf8');
    } catch (err) {
      die(`could not read --basic-auth-file: ${err.code ?? err.message}`, 2);
    }
    const trimmed = contents.replace(/\s+$/g, '');
    // CV audit r5 fix: first-colon delimiter semantics — a password may
    // itself contain colons (`alice:sec:ret`), so the invariant is "colon
    // exists AND is not the first or last char" ⇒ non-empty user AND
    // non-empty pass. `endsWith(':')` would have wrongly rejected
    // `alice:sec:`. Kept as the shared shape check.
    if (!_looksLikeUserPass(trimmed)) {
      die(`--basic-auth-file contents must be "user:pass" with non-empty user AND pass`, 2);
    }
    args.basicAuth = trimmed;
    args.basicAuthSource = args.basicAuthFileSource;
  }
  // CV audit r4 fix: shape-validate the WINNING credential source. parseArgs
  // deliberately does NOT validate auth VALUES so a malformed losing source
  // can't abort startup — the winning source (chosen by rank + file-wins-tie)
  // is what actually gates the proxy, so it's the only one that has to be
  // well-formed. If no source produced a winner, `args.basicAuth` is null
  // and Basic Auth stays off (subject to fail-closed checks below).
  if (args.basicAuth != null && !_looksLikeUserPass(args.basicAuth)) {
    die(
      `Basic Auth credential (source=${args.basicAuthSource}) must be "user:pass" with non-empty user AND pass.`,
      2,
    );
  }
  // CV audit r6 fix: validate the WINNING allow-origin list. `--allow-origin`
  // flags REPLACE the env list, so a malformed env value must not abort
  // startup when a flag would have discarded it — validation happens here,
  // once, on whatever list is actually going to be sent to the proxy.
  for (const o of args.allowOrigins) {
    const err = validateAllowOrigin(o);
    if (err) {
      const sourceLabel = args.allowOriginSource === 'env' ? 'CWR_ALLOW_ORIGIN' : '--allow-origin';
      die(`${sourceLabel} ${err}`, 2);
    }
  }

  // CV audit r2: detect TLS-direct mode from the inherited env NOW so the
  // fail-closed checks can see the effective scheme. TLS_CERT_FILE +
  // TLS_KEY_FILE are pass-through (the CLI never terminates TLS itself; it
  // just spawns a proxy that does).
  const tlsOn =
    typeof process.env.TLS_CERT_FILE === 'string' && process.env.TLS_CERT_FILE.length > 0 &&
    typeof process.env.TLS_KEY_FILE === 'string' && process.env.TLS_KEY_FILE.length > 0;
  const scheme = tlsOn ? 'https' : 'http';

  // CV audit r2: FAIL CLOSED on unsafe exposed-proxy configurations —
  // BEFORE we spawn anything. See the long comment further down for the
  // rationale; the short version: Origin allowlist is CSRF protection, not
  // authentication, and Basic Auth over plain HTTP on a shared network is a
  // credential-leak that hands over shell-equivalent control.
  const nonLoopback = !isLoopbackHost(args.host);
  const hostForWarn = args.host === '0.0.0.0' || args.host === '::' ? '<this-machine>' : formatHostForUrl(args.host);
  const boundOrigin = `${scheme}://${hostForWarn}:${args.port}`;
  if (nonLoopback && !args.basicAuth && !args.insecureExpose) {
    die(
      `refusing to serve on --host=${args.host} without authentication.\n` +
        `  Access to this proxy is equivalent to local codex control (approximately shell access).\n` +
        `  The Origin allowlist is CSRF protection, NOT authentication — any non-browser client\n` +
        `  on the network can forge an Origin string.\n` +
        `  Fix (recommended): keep --host 127.0.0.1 and put the proxy behind Tailscale Serve, e.g.\n` +
        `      npx @ai-nd-co/codex-web-remote --allow-origin https://<host>.ts.net --basic-auth-file /path/cred\n` +
        `      tailscale serve --bg --https=443 http://127.0.0.1:${args.port}\n` +
        `  Or set --basic-auth-file /path/to/cred (recommended over --basic-auth user:pass).\n` +
        `  Disposable dev network only: re-run with --insecure-expose to bypass this check.`,
      2,
    );
  }
  if (nonLoopback && args.basicAuth && !tlsOn && !args.insecureExpose) {
    die(
      `refusing to send Basic Auth credentials over plain HTTP on --host=${args.host}.\n` +
        `  Basic credentials are base64 on the wire — any on-path observer captures + replays them.\n` +
        `  Fix (recommended): keep --host 127.0.0.1 and let Tailscale Serve terminate TLS in front:\n` +
        `      npx @ai-nd-co/codex-web-remote --allow-origin https://<host>.ts.net --basic-auth-file /path/cred\n` +
        `      tailscale serve --bg --https=443 http://127.0.0.1:${args.port}\n` +
        `  Or terminate TLS in the proxy itself: TLS_CERT_FILE=... TLS_KEY_FILE=... npx @ai-nd-co/codex-web-remote ...\n` +
        `  Disposable dev network only: re-run with --insecure-expose to bypass this check.`,
      2,
    );
  }
  if (args.insecureExpose && nonLoopback) {
    warn(
      `--insecure-expose is set. Serving on ${boundOrigin} WITHOUT the standard ` +
        `authentication / TLS guarantees. Do NOT use this on any network you do ` +
        `not own end-to-end. Access to this proxy is equivalent to local codex ` +
        `control (approximately shell access on this machine).`,
    );
  }
  // The proxy auto-includes a concrete LISTEN_HOST origin in its default
  // allowlist (see DEFAULT_CLIENT_ORIGINS), so a concrete non-loopback bind
  // like `--host 192.168.1.10` doesn't actually need --allow-origin for its
  // OWN origin — only wildcard binds (0.0.0.0 / ::) or a distinct
  // externally-visible origin (a tailnet host, an operator's reverse proxy).
  const wildcardBind = args.host === '0.0.0.0' || args.host === '::' || args.host === '[::]';
  if (nonLoopback && args.allowOrigins.length === 0 && wildcardBind) {
    warn(
      `--host=${args.host} is a wildcard bind and no --allow-origin was given. Browsers ` +
        `at ${boundOrigin} may FAIL the WS upgrade (403) unless the origin matches the ` +
        `proxy's default loopback list. Pass --allow-origin ${boundOrigin} ` +
        `(and/or --allow-origin https://<host>.ts.net for Tailscale). Repeatable.`,
    );
  }
  if (nonLoopback && args.allowOrigins.length === 0 && !wildcardBind) {
    log(
      `note: --host=${args.host} — the proxy trusts its own ${boundOrigin} origin by ` +
        `default. If the site is reached via a DIFFERENT externally-visible origin ` +
        `(a reverse proxy, a tailnet host), pass --allow-origin <that-origin>. Repeatable.`,
    );
  }

  // Decide connect vs launch.
  let upstreamProbe = { listening: false };
  try {
    const { host, port } = parseWsUrl(args.upstream);
    upstreamProbe = await probeTcpPort({ host, port, timeoutMs: 700 });
  } catch (err) {
    die(err.message, 2);
  }
  const decision = decideRunMode(args, { upstreamReachable: upstreamProbe.listening });
  log(`version=${version}`);
  log(`upstream=${args.upstream} reachable=${upstreamProbe.listening} → mode=${decision.mode}`);
  log(`decision: ${decision.reason}`);

  // ---- Optional: launch app-server ----
  let launcherChild = null;
  if (decision.mode === 'launch') {
    // Parse listen host/port from the upstream URL. The launcher hard-refuses
    // anything non-loopback (see scripts/launch-appserver.mjs), which matches
    // our own trust boundary: the proxy is the exposed surface, never the
    // app-server. The default upstream URL is `ws://127.0.0.1:4222`; we
    // pass it through as CODEX_APP_SERVER_LISTEN unchanged.
    let listenUrl;
    try {
      const { host, port } = parseWsUrl(args.upstream);
      if (host !== '127.0.0.1' && host !== 'localhost') {
        die(
          `refusing to auto-launch codex for a non-loopback upstream URL (${args.upstream}). ` +
            `If you really want codex on a remote host, run it there and pass --upstream ws://... explicitly.`,
          2,
        );
      }
      listenUrl = `ws://${host}:${port}`;
    } catch (err) {
      die(err.message, 2);
    }

    const codexCmd = decision.codexCmd ?? DEFAULT_CODEX_CMD;
    log(`launching codex app-server: CODEX_APP_SERVER_CMD=${JSON.stringify(codexCmd)} listen=${listenUrl}`);
    // CV audit r2: strip proxy-owned secrets from the LAUNCHER env too. The
    // launcher spawns codex, which is a separate process tree and has no
    // business seeing CWR_BASIC_AUTH or CWR_BASIC_AUTH_FILE. Same for
    // UPSTREAM_TOKEN* — codex needs its own token via its own config, not
    // ours. Keeps the credential blast radius to the proxy child only.
    const launcherEnv = {
      ...process.env,
      CODEX_APP_SERVER_CMD: codexCmd,
      CODEX_APP_SERVER_LISTEN: listenUrl,
    };
    for (const k of [
      'CWR_BASIC_AUTH',
      'CWR_BASIC_AUTH_FILE',
      'UPSTREAM_TOKEN',
      'UPSTREAM_TOKEN_FILE',
      'ALLOWED_CLIENT_ORIGINS',
      'HOSTS_CONFIG_FILE',
    ]) {
      delete launcherEnv[k];
    }
    launcherChild = spawn(process.execPath, [paths.launcherEntry], {
      env: launcherEnv,
      stdio: ['ignore', 'inherit', 'inherit'],
      windowsHide: true,
      // Not detached — we WANT it to die when we die; SIGINT handler also
      // kill()s it explicitly for a graceful teardown.
      shell: false,
    });
    launcherChild.on('error', (err) => {
      warn(`launcher process error: ${err.code ?? err.message}`);
    });
    launcherChild.on('exit', (code, signal) => {
      if (code !== 0 && signal !== 'SIGTERM' && signal !== 'SIGINT') {
        warn(`launcher exited unexpectedly code=${code} signal=${signal ?? '-'}`);
      }
    });

    // Health-gate the app-server port.
    try {
      const { host, port } = parseWsUrl(args.upstream);
      await pollUntil(async () => {
        if (launcherChild && launcherChild.exitCode != null) {
          throw new Error(`launcher exited early (code=${launcherChild.exitCode}) — check the stderr above`);
        }
        const r = await probeTcpPort({ host, port, timeoutMs: 500 });
        return r.listening ? true : null;
      }, { timeoutMs: 30000, intervalMs: 400, label: `app-server ${listenUrl}` });
      log(`app-server is up on ${listenUrl}`);
    } catch (err) {
      teardownLauncher(launcherChild);
      die(err.message);
    }
  }

  // ---- Start proxy ----
  //
  // Audit R1 BLOCKER fix: strip every proxy-controlling env var from the
  // inherited environment BEFORE we set our own. Otherwise an inherited
  // `UPSTREAM_TOKEN` / `UPSTREAM_TOKEN_FILE` / `PUBLIC_DIR` / `HOSTS_CONFIG_FILE`
  // silently overrides the values this CLI's flags advertised. In particular
  // the proxy's `loadTokenFromRecord` prefers UPSTREAM_TOKEN over
  // UPSTREAM_TOKEN_FILE, so a leaked env token would ALWAYS shadow a
  // caller's `--token-file` — the exact class of "expected to be host-side
  // only" leakage the launcher already guards against.
  const proxyEnv = { ...process.env };
  for (const k of [
    'UPSTREAM_TOKEN',
    'UPSTREAM_TOKEN_FILE',
    'UPSTREAM_URL',
    'PUBLIC_DIR',
    'LISTEN_PORT',
    'LISTEN_HOST',
    'HOSTS_CONFIG_FILE',
    'DEFAULT_HOST_LABEL',
    'DEFAULT_HOST_RUN_MODE',
    // CV: proxy-owned auth / origin envs. Same rationale as UPSTREAM_TOKEN —
    // a leaked shell env would silently override what --allow-origin /
    // --basic-auth advertised on the command line. Strip first, then set our
    // own from the parsed args.
    'ALLOWED_CLIENT_ORIGINS',
    'CWR_BASIC_AUTH',
    'CWR_BASIC_AUTH_FILE',
  ]) {
    delete proxyEnv[k];
  }
  proxyEnv.LISTEN_HOST = args.host;
  proxyEnv.LISTEN_PORT = String(args.port);
  proxyEnv.PUBLIC_DIR = paths.publicDir;          // BUNDLED dist, not cwd
  proxyEnv.UPSTREAM_URL = args.upstream;
  // Token stays HOST-SIDE. We only pass a token-FILE path (never inline). The
  // proxy re-reads the file on startup.
  if (args.tokenFile) {
    if (!path.isAbsolute(args.tokenFile)) {
      teardownLauncher(launcherChild);
      die(`--token-file must be an absolute path (got ${JSON.stringify(args.tokenFile)})`, 2);
    }
    if (!fs.existsSync(args.tokenFile)) {
      teardownLauncher(launcherChild);
      die(`--token-file does not exist: ${args.tokenFile}`, 2);
    }
    proxyEnv.UPSTREAM_TOKEN_FILE = args.tokenFile;
  }
  // CV: forward --allow-origin (repeatable) as ALLOWED_CLIENT_ORIGINS. The
  // proxy replaces its default list when this env is set, so we prepend the
  // scheme-correct loopback defaults to keep "browser at the proxy's own
  // port" working.
  if (args.allowOrigins.length > 0) {
    // CV audit r4 tightening: mirror the proxy's own "wildcard resolves to
    // loopback" default. A wildcard bind is a listen instruction, not a
    // connect target — the browser hits `[::1]` / `127.0.0.1`. Emit the
    // loopback equivalent (never the bare `[::]` / `0.0.0.0` origin).
    const listenHostForOrigin = args.host === '0.0.0.0' || args.host === '::' || args.host === '[::]'
      ? formatHostForUrl(connectHostFor(args.host))
      : formatHostForUrl(args.host);
    const defaults = [
      `${scheme}://${listenHostForOrigin}:${args.port}`,
      `${scheme}://localhost:${args.port}`,
      `${scheme}://127.0.0.1:${args.port}`,
      // Always include [::1] so an IPv6-preferring browser at loopback is
      // not silently 403'd on any bind that might route it through.
      `${scheme}://[::1]:${args.port}`,
    ];
    // Deduplicate while preserving order.
    const seen = new Set();
    const merged = [];
    for (const o of [...defaults, ...args.allowOrigins]) {
      if (!seen.has(o)) { seen.add(o); merged.push(o); }
    }
    proxyEnv.ALLOWED_CLIENT_ORIGINS = merged.join(',');
  }
  // CV: forward --basic-auth as CWR_BASIC_AUTH. HOST-SIDE. Never logged.
  if (args.basicAuth) {
    proxyEnv.CWR_BASIC_AUTH = args.basicAuth;
  }

  log(
    `starting proxy: ${scheme}://${formatHostForUrl(args.host)}:${args.port}/ (public=${paths.publicDir})` +
      `  tls=${tlsOn ? 'on' : 'off'}` +
      `  allow-origin=${args.allowOrigins.length}` +
      `  basic-auth=${args.basicAuth ? 'on' : 'off'}` +
      `  insecure-expose=${args.insecureExpose ? 'YES' : 'no'}`,
  );
  const proxyChild = spawn(process.execPath, [paths.proxyEntry], {
    env: proxyEnv,
    stdio: ['ignore', 'inherit', 'inherit'],
    windowsHide: true,
    shell: false,
  });
  proxyChild.on('error', (err) => {
    warn(`proxy process error: ${err.code ?? err.message}`);
  });
  let proxyExited = false;
  proxyChild.on('exit', (code, signal) => {
    proxyExited = true;
    if (code !== 0 && signal !== 'SIGTERM' && signal !== 'SIGINT') {
      warn(`proxy exited unexpectedly code=${code} signal=${signal ?? '-'}`);
    }
    // If the proxy dies we tear down the launched codex and exit — no point
    // keeping a headless app-server running.
    teardownLauncher(launcherChild);
    process.exit(code ?? 1);
  });

  // CV: when --basic-auth is on, the CLI's own health-gate MUST send the
  // same credentials it configured on the proxy — /proxy/healthz is gated
  // (leaves nothing unauthenticated on an exposed proxy). Credentials are
  // constructed once and NEVER printed. Loopback host, so no wire risk.
  const healthzHeaders = args.basicAuth
    ? { authorization: 'Basic ' + Buffer.from(args.basicAuth, 'utf8').toString('base64') }
    : {};

  // Health-gate the proxy: wait for /proxy/healthz current-shape (hostCount +
  // multiHost fields, per parseHealthz's C10 marker).
  try {
    await pollUntil(async () => {
      if (proxyExited) throw new Error('proxy exited during startup');
      let r;
      try {
        // IPv6-safe URL: brackets around raw IPv6 hosts. Wildcard binds
        // (0.0.0.0 / ::) route to the loopback equivalent for the CLI's own
        // probe — the wildcard is a listen instruction, not a connect target.
        r = await fetchJson(`${scheme}://${formatHostForUrl(connectHostFor(args.host))}:${args.port}/proxy/healthz`, {
          headers: healthzHeaders,
        });
      } catch { return null; }
      if (r.status !== 200 || !r.json || r.json.ok !== true) return null;
      // Match the same shape gate dev-up uses (dev-stack.parseHealthz).
      if (!Number.isInteger(r.json.hostCount) || typeof r.json.multiHost !== 'boolean') return null;
      return r.json;
    }, { timeoutMs: 30000, intervalMs: 300, label: `proxy /proxy/healthz current shape` });
  } catch (err) {
    teardownLauncher(launcherChild);
    teardownProxy(proxyChild);
    die(err.message);
  }

  const openUrl = `${scheme}://${formatHostForUrl(connectHostFor(args.host))}:${args.port}/`;
  log(``);
  log(`READY — open: ${openUrl}`);
  log(``);
  if (args.open) openBrowser(openUrl);

  // ---- Signal handlers: tear down what we started ----
  const cleanup = (sig) => {
    log(`${sig} received — shutting down`);
    teardownProxy(proxyChild);
    teardownLauncher(launcherChild);
    // Give children a moment to exit; then hard-exit.
    setTimeout(() => process.exit(0), 500).unref();
  };
  process.on('SIGINT', () => cleanup('SIGINT'));
  process.on('SIGTERM', () => cleanup('SIGTERM'));
  if (!IS_WIN) process.on('SIGHUP', () => cleanup('SIGHUP'));
}

function teardownProxy(child) {
  if (!child || child.exitCode != null) return;
  try { child.kill('SIGTERM'); } catch {}
}
function teardownLauncher(child) {
  if (!child || child.exitCode != null) return;
  try {
    if (IS_WIN) {
      // The launcher spawned codex as a foreground child; kill the whole tree.
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        stdio: 'ignore', windowsHide: true, shell: false,
      });
    } else {
      child.kill('SIGTERM');
    }
  } catch {}
}

main().catch((err) => {
  die(err.stack ?? err.message ?? String(err));
});
