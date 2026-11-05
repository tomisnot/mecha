#!/usr/bin/env node
// Codex Web Remote — configurable app-server launcher (C5b).
//
// SECURITY BOUNDARY:
//   * This script is HOST-SIDE ONLY. It reads its command + args + auth from
//     the PROCESS ENVIRONMENT of the shell that starts it. Nothing the browser
//     (or the proxy, downstream of the browser) can send is consulted here.
//   * The launcher DOES NOT read from stdin, does NOT open any network socket,
//     and does NOT accept CLI flags — so no `--cmd=...` style injection surface
//     exists. Configuration lives ONLY in env vars listed below.
//   * `CODEX_APP_SERVER_CMD` MAY be a multi-token string ("npx @ai-nd-co/codex")
//     but is split on ASCII whitespace only. There is no shell interpolation:
//     the tokens are passed to `child_process.spawn(..., {shell: false})`, so
//     `; rm -rf /`, backticks, `$VAR`, etc. are literal argv strings to the
//     spawned process, not shell metacharacters.
//   * CM update: on Windows, when the first token is a BARE command whose real
//     binary is a `.cmd`/`.bat` shim (npm, npx, yarn, codex.cmd, …), the naive
//     `shell:false` spawn ENOENT's. We resolve it via PATH+PATHEXT and either
//     (a) prefer a real `.exe` — still shell:false, (b) un-shim well-known
//     npm-shaped `.cmd` shims by rewriting the argv to `node <cli.js> …` —
//     STILL shell:false (the shim body is NOT executed), or (c) LAST RESORT
//     spawn the RESOLVED absolute `.cmd` with shell:true, argv still an
//     array. Branches (a) and (b) preserve the no-shell property. Branch (c)
//     is documented + printed to stderr. See scripts/lib/launcher-resolve.mjs.
//   * `CODEX_APP_SERVER_LISTEN` MUST start with `ws://127.0.0.1:` or
//     `ws://localhost:` to keep app-server bound to loopback. The proxy is
//     the ONLY exposed surface.
//
// The upstream `codex app-server` accepts exactly these args (verified against
// openai/codex `main`, `app-server/src/main.rs`, 2026-07-29):
//   --listen <ws-url>
//   --session-source <string>            [optional]
//   --ws-auth <mode>                     [optional: capability-token | signed-bearer-token]
//   --ws-token-file <path>               [required with --ws-auth capability-token]
//   --strict-config                      [optional]
//
// There is NO `--dangerously-bypass-approvals-and-sandbox` on app-server. The
// bypass equivalent is per-thread: send `approval_policy:"never"` +
// `sandbox_policy:"danger-full-access"` on `thread/start` (see the client
// toggle in `web/src/kernel/runMode.ts`). This launcher does NOT try to
// simulate that flag.
//
// Env vars (all optional except CODEX_HOME defaults to a repo-relative sandbox):
//   CODEX_APP_SERVER_CMD   Command + optional prefix args, e.g.:
//                            "C:\\path\\to\\codex.exe"
//                            "npx @ai-nd-co/codex"
//                            "npx --package=@ai-nd-co/codex codex"
//                          Whitespace-split. Default = the bundled Windows
//                          debug binary from the codex worktree (see below).
//   CODEX_APP_SERVER_LISTEN  ws-url. Default: ws://127.0.0.1:4222
//                            MUST be loopback.
//   CODEX_APP_SERVER_AUTH  "capability-token" | "signed-bearer-token" | ""
//                          Default: "" (no auth — loopback only).
//   CODEX_APP_SERVER_TOKEN_FILE  Path to the shared secret. Required with auth.
//   CODEX_APP_SERVER_STRICT_CONFIG  "1" to add --strict-config.
//   CODEX_APP_SERVER_SESSION_SOURCE  Optional --session-source value.
//   CODEX_HOME             Isolated CODEX home dir (config.toml + auth).
//                          Default: repo-local `.codex-home-scratch/` (gitignored).
//   CODEX_APP_SERVER_EXTRA_ARGS  Additional argv tokens appended AFTER the
//                          fixed args (whitespace-split). Escape hatch for
//                          new upstream flags; still shell-safe.
//   CODEX_APP_SERVER_DRY_RUN  If "1", print the resolved argv + env keys and
//                          exit 0 without spawning. Used by tests / audit.

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveLaunchCommand, defaultReadFile, firstUnsafeShellToken } from './lib/launcher-resolve.mjs';
import { fsProbes } from './lib/dev-stack.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');

// Bundled default: the codex.exe built out of the local codex-upstream worktree.
// This mirrors what C0/C1 already documented. Overridden by CODEX_APP_SERVER_CMD.
const DEFAULT_BUNDLED_CMD = path.resolve(
  REPO_ROOT,
  '..',
  '..',
  'worktrees',
  'codex-upstream-main-20260722',
  'codex-rs',
  'target',
  'debug',
  'codex.exe',
);

const RAW_CMD = (process.env.CODEX_APP_SERVER_CMD ?? '').trim();
const LISTEN = (process.env.CODEX_APP_SERVER_LISTEN ?? 'ws://127.0.0.1:4222').trim();
const AUTH = (process.env.CODEX_APP_SERVER_AUTH ?? '').trim();
const TOKEN_FILE = (process.env.CODEX_APP_SERVER_TOKEN_FILE ?? '').trim();
const STRICT_CONFIG = process.env.CODEX_APP_SERVER_STRICT_CONFIG === '1';
const SESSION_SOURCE = (process.env.CODEX_APP_SERVER_SESSION_SOURCE ?? '').trim();
const EXTRA_ARGS_RAW = (process.env.CODEX_APP_SERVER_EXTRA_ARGS ?? '').trim();
const DRY_RUN = process.env.CODEX_APP_SERVER_DRY_RUN === '1';

// --- Validation -----------------------------------------------------------

function bail(msg) {
  process.stderr.write(`[launch-appserver] ${msg}\n`);
  process.exit(2);
}

// Loopback-only listen. app-server can bind non-loopback in principle, but
// the proxy is the ONLY exposed surface in this project. Refuse to launch
// unless we're on 127.0.0.1 or localhost.
if (!/^ws:\/\/(127\.0\.0\.1|localhost):\d+(\/.*)?$/i.test(LISTEN)) {
  bail(
    `CODEX_APP_SERVER_LISTEN must be a loopback ws:// URL (127.0.0.1 or ` +
      `localhost with an explicit port). Got: ${JSON.stringify(LISTEN)}`,
  );
}

if (AUTH && AUTH !== 'capability-token' && AUTH !== 'signed-bearer-token') {
  bail(
    `CODEX_APP_SERVER_AUTH must be "", "capability-token", or ` +
      `"signed-bearer-token". Got: ${JSON.stringify(AUTH)}`,
  );
}

if (AUTH === 'capability-token' && !TOKEN_FILE) {
  bail(`CODEX_APP_SERVER_AUTH=capability-token requires CODEX_APP_SERVER_TOKEN_FILE`);
}
if (AUTH === 'signed-bearer-token' && !TOKEN_FILE) {
  bail(`CODEX_APP_SERVER_AUTH=signed-bearer-token requires CODEX_APP_SERVER_TOKEN_FILE`);
}
if (TOKEN_FILE) {
  if (!path.isAbsolute(TOKEN_FILE)) {
    bail(`CODEX_APP_SERVER_TOKEN_FILE must be an absolute path. Got: ${TOKEN_FILE}`);
  }
  try {
    // R1-a1 fix: use lstatSync (no symlink follow) and reject symlinks / Windows
    // reparse points. The launcher previously used statSync which follows links,
    // opening a TOCTOU where an attacker with write access to the containing
    // dir could swap the target between our check and app-server's read.
    // Absolute-path requirement plus link-refusal keeps the checked file and
    // the file app-server later opens the same on-disk object without us having
    // to memory-copy the token (which would introduce its own risks).
    //
    // Accepted residual (R2 audit finding #2): a "same pathname, different
    // file by time of open" swap is still possible if an attacker with write
    // access to the token-file's directory replaces the file between our
    // check and app-server's open. Fully closing this on Windows would
    // require the launcher to open a HANDLE and pass it to the child (not
    // supported by app-server's CLI, which takes a path). The threat model
    // for this launcher is a single-user loopback session; a peer with write
    // access to the user's token-file dir already has broader escalation
    // paths. This is documented so a future signed-bearer-token mode can
    // revisit it if the threat model widens.
    const stat = fs.lstatSync(TOKEN_FILE);
    if (stat.isSymbolicLink()) {
      bail(
        `CODEX_APP_SERVER_TOKEN_FILE must not be a symlink (TOCTOU risk). ` +
          `Point it at a regular file: ${TOKEN_FILE}`,
      );
    }
    // Windows reparse points (junctions, symbolic-link-like objects) report
    // isSymbolicLink()===true above for most kinds; belt-and-suspenders check
    // via the file mode's UF_REPARSE bit isn't portable in Node, so we rely
    // on lstatSync + a defensive isFile check + realpath === original check.
    if (!stat.isFile()) bail(`CODEX_APP_SERVER_TOKEN_FILE is not a regular file: ${TOKEN_FILE}`);
    const real = fs.realpathSync(TOKEN_FILE);
    if (path.resolve(real) !== path.resolve(TOKEN_FILE)) {
      bail(
        `CODEX_APP_SERVER_TOKEN_FILE resolves through a link to a different path: ` +
          `${TOKEN_FILE} -> ${real}. Pass the resolved path directly.`,
      );
    }
  } catch (err) {
    bail(`CODEX_APP_SERVER_TOKEN_FILE not readable (${err.code ?? 'ENOENT'}): ${TOKEN_FILE}`);
  }
}

// --- Command resolution ---------------------------------------------------

// Whitespace-only split (no shell). Empty string => default.
// Note: `@alpha` in `npx @ai-nd-co/codex@alpha` is a single token because
// `@` is not ASCII whitespace — the version tag survives tokenization intact.
const tokens = RAW_CMD.length > 0 ? RAW_CMD.split(/\s+/).filter((s) => s.length > 0) : [];
const cmdTokens = tokens.length > 0 ? tokens : [DEFAULT_BUNDLED_CMD];
const rawFirstToken = cmdTokens[0];
const cmdPrefix = cmdTokens.slice(1);

// If the command looks like an absolute path, verify it exists — bail early
// with a clear message rather than letting spawn ENOENT bubble up cryptically.
if (path.isAbsolute(rawFirstToken)) {
  try {
    fs.statSync(rawFirstToken);
  } catch {
    bail(
      `CODEX_APP_SERVER_CMD points to a non-existent path: ${rawFirstToken}\n` +
        `(if the bundled default is missing, set CODEX_APP_SERVER_CMD to your codex binary)`,
    );
  }
}

// Windows-safe command resolution. On POSIX + absolute-path Windows this is a
// pure passthrough (source=absolute|posix-bare, useShell=false). Fixes the
// `spawn npx ENOENT` on Windows without introducing shell interpretation on
// the common paths. See scripts/lib/launcher-resolve.mjs for the truth table.
const resolved = resolveLaunchCommand(rawFirstToken, {
  isWin: process.platform === 'win32',
  probes: {
    exists: fsProbes.exists,
    whichAll: fsProbes.whichAll,
    readFile: defaultReadFile,
  },
});
if (resolved.source === 'unresolved') {
  bail(
    `Could not resolve CODEX_APP_SERVER_CMD first token to a spawnable target: ${rawFirstToken}\n` +
      (resolved.error && resolved.error.tried ? resolved.error.tried.map((t) => `  - ${t}`).join('\n') + '\n' : '') +
      `Fix: pass an absolute path in CODEX_APP_SERVER_CMD, or install the tool on PATH.`,
  );
}

// If the resolver un-shimmed to a node cli, materialise the node.exe path.
// It uses the SIBLING node.exe when present (nvm layout) — matches whichever
// Node the shim targets. Falls back to the current process's execPath so the
// same major-version semantics hold.
const cmd = resolved.cmd === '__PROCESS_EXECPATH__' ? process.execPath : resolved.cmd;
const resolvedPrefix = resolved.prefixArgs;

// --- Build argv -----------------------------------------------------------

const fixedArgs = ['app-server', '--listen', LISTEN];
if (SESSION_SOURCE) fixedArgs.push('--session-source', SESSION_SOURCE);
if (AUTH) {
  fixedArgs.push('--ws-auth', AUTH);
  fixedArgs.push('--ws-token-file', TOKEN_FILE);
}
if (STRICT_CONFIG) fixedArgs.push('--strict-config');

const extraArgs = EXTRA_ARGS_RAW.length > 0
  ? EXTRA_ARGS_RAW.split(/\s+/).filter((s) => s.length > 0)
  : [];

// Order: [resolver un-shim prefix (e.g. cli.js)] + [user prefix tokens after
// the first] + [fixed args] + [extra args]. This ensures a `npx @pkg` becomes
// `node <npx-cli.js> @pkg app-server --listen …`.
const argv = [...resolvedPrefix, ...cmdPrefix, ...fixedArgs, ...extraArgs];

// r1-audit + r2-audit fix: if the resolver chose the shell:true fallback,
// EVERY argv token AND the command path must be free of cmd.exe
// metacharacters, because Node concatenates cmd + argv into a single
// cmd.exe /d /s /c string. The reject list is intentionally strict:
// `& | < > ^ ! ( ) \` %` and CR/LF — including a blanket `%` reject
// because env-var names may contain whitespace (`%CWR AUDIT%`) so a
// pair-detecting regex is insufficient. Never let a `%VAR%` or `&` in
// CODEX_APP_SERVER_CMD / CODEX_APP_SERVER_EXTRA_ARGS get interpreted.
if (resolved.useShell) {
  const unsafeCmd = firstUnsafeShellToken([cmd]);
  if (unsafeCmd) {
    bail(
      `CODEX_APP_SERVER_CMD resolved to a shell:true .cmd fallback (${cmd}) whose path contains a cmd.exe metacharacter. ` +
        `Refusing to spawn — install a .exe or use an un-shimmable tool.`,
    );
  }
  const unsafe = firstUnsafeShellToken(argv);
  if (unsafe) {
    bail(
      `The launcher would spawn ${cmd} with shell:true (Windows .cmd/.bat fallback), ` +
        `but argv token #${unsafe.index} contains a cmd.exe metacharacter (${JSON.stringify(unsafe.token)}). ` +
        `On Windows shell:true concatenates cmd + argv into a single cmd.exe /d /s /c string, ` +
        `so a metacharacter would be interpreted. Refusing to spawn.\n` +
        `Fix: (a) install the tool as a .exe on PATH, (b) point CODEX_APP_SERVER_CMD at an absolute .exe, ` +
        `or (c) remove the metacharacter from your CODEX_APP_SERVER_CMD / CODEX_APP_SERVER_EXTRA_ARGS.`,
    );
  }
}

// --- Isolated CODEX_HOME --------------------------------------------------

// R1-a2 fix: previously defaulted to `<repo>/.codex-home-scratch/`, which
// inherits the parent directory's ACL. If the repo sits under a permissive
// path (shared drive, `%PUBLIC%`, `C:\Users\*\Downloads` on multi-user boxes)
// another local principal could tamper with `config.toml` or auth artifacts.
// Default to the current user's home dir instead — on Windows this is
// `%USERPROFILE%`, ACL'd owner-only by default; on POSIX we mkdir with
// mode 0o700. Explicit `CODEX_HOME` overrides.
//
// Accepted residual (R2 audit finding #3): on Windows `os.homedir()` resolves
// via `USERPROFILE` which the invoking shell can override. A power user who
// deliberately points USERPROFILE at a permissive dir before running the
// launcher escalates their OWN threat model; we do not treat that as a
// launcher bug. Loopback-only + host-side config remain the trust boundary.
const DEFAULT_HOME = path.join(os.homedir(), '.codex-web-remote', 'codex-home-scratch');
const CODEX_HOME = process.env.CODEX_HOME ?? DEFAULT_HOME;
try {
  // R2 fix (audit finding #4): pass mode to mkdirSync so the leaf directory
  // is created with 0700 atomically. mode is a no-op on Windows (ACLs are
  // inherited from parent); on POSIX this closes the mkdir->chmod race
  // window a permissive umask would open.
  fs.mkdirSync(CODEX_HOME, { recursive: true, mode: 0o700 });
  // Defence in depth: if the leaf already existed with looser perms
  // (recursive mkdir does NOT re-tighten existing dirs), chmod it back to
  // 0700 on POSIX. Windows: no-op.
  if (process.platform !== 'win32') {
    try {
      fs.chmodSync(CODEX_HOME, 0o700);
    } catch {
      /* best effort — bail below if unreadable */
    }
  }
} catch (err) {
  bail(`failed to create CODEX_HOME=${CODEX_HOME}: ${err.code ?? err.message}`);
}

// --- Spawn ----------------------------------------------------------------

const spawnEnv = { ...process.env, CODEX_HOME };

// R1-a3 fix: EXTRA_ARGS is an escape hatch for future upstream flags. It could
// carry secret-bearing tokens (`--api-key sk-...`, `--bearer …`). We do NOT
// know which extra flags are secret-shaped, so redact ALL of them from any
// logged argv. The child still receives the real values; only the logs are
// scrubbed. Fixed args are safe to log (no secret-shaped flag is fixed).
function redactedArgvForLog() {
  const safe = [...resolvedPrefix, ...cmdPrefix, ...fixedArgs];
  if (extraArgs.length > 0) {
    safe.push(`[EXTRA_ARGS_REDACTED count=${extraArgs.length}]`);
  }
  return safe;
}

if (DRY_RUN) {
  process.stdout.write(
    JSON.stringify(
      {
        cmd,
        argv: redactedArgvForLog(),
        extraArgsCount: extraArgs.length,
        env: {
          CODEX_HOME,
          CODEX_APP_SERVER_LISTEN: LISTEN,
          CODEX_APP_SERVER_AUTH: AUTH || null,
          CODEX_APP_SERVER_TOKEN_FILE: TOKEN_FILE || null,
          CODEX_APP_SERVER_STRICT_CONFIG: STRICT_CONFIG,
          CODEX_APP_SERVER_SESSION_SOURCE: SESSION_SOURCE || null,
        },
        source: RAW_CMD ? 'CODEX_APP_SERVER_CMD' : 'bundled-default',
        rawFirstToken,
        resolution: {
          source: resolved.source,
          useShell: resolved.useShell,
          notes: resolved.notes,
        },
      },
      null,
      2,
    ) + '\n',
  );
  process.exit(0);
}

if (resolved.notes) {
  // Print the resolver's diagnostic so the security-relevant deviation from
  // the shell:false default is visible in the log (e.g. cmd-shim-shell branch).
  process.stderr.write(`[launch-appserver] resolver: ${resolved.notes}\n`);
}
process.stderr.write(
  `[launch-appserver] cmd=${cmd} argv=${JSON.stringify(redactedArgvForLog())} ` +
    `CODEX_HOME=${CODEX_HOME} auth=${AUTH || 'none'} listen=${LISTEN} ` +
    `resolvedFrom=${resolved.source} shell=${resolved.useShell}\n`,
);

const child = spawn(cmd, argv, {
  shell: resolved.useShell,
  stdio: 'inherit',
  env: spawnEnv,
  windowsHide: true,
});

child.on('error', (err) => {
  process.stderr.write(
    `[launch-appserver] spawn error (${err.code ?? 'ERR'}): ${err.message}\n`,
  );
  process.exit(3);
});

const forward = (sig) => {
  try {
    child.kill(sig);
  } catch {
    /* already gone */
  }
};
process.on('SIGINT', () => forward('SIGINT'));
process.on('SIGTERM', () => forward('SIGTERM'));

child.on('exit', (code, signal) => {
  process.exit(signal ? 128 + (signal === 'SIGINT' ? 2 : signal === 'SIGTERM' ? 15 : 0) : (code ?? 0));
});
