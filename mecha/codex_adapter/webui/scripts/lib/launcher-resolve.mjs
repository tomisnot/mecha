// Codex Web Remote — CM: launcher command resolver.
//
// PROBLEM SOLVED
// --------------
// `scripts/launch-appserver.mjs` originally did
// `spawn(firstToken, argv, { shell: false })`. On Windows that ENOENT's when
// the first token is a bare command name whose real binary is a `.cmd`/`.bat`
// shim (npm, npx, yarn, most anything installed by npm-global). This is what
// broke `CODEX_APP_SERVER_CMD="npx @ai-nd-co/codex@alpha"` in this session.
//
// SECURITY BOUNDARY (KEEP)
// ------------------------
// The launcher's C5b property is that `spawn` is `shell: false`, so tokens in
// `CODEX_APP_SERVER_CMD` are LITERAL argv strings — no shell metachar is
// interpreted. This module preserves that property in the common paths:
//
//   * absolute-path first-token          -> spawn shell:false, unchanged.
//   * bare `.exe` resolved via PATH      -> spawn shell:false, unchanged.
//   * npm-family `.cmd` shim (`npx.cmd`, `npm.cmd`, `codex.cmd`, …)
//                                        -> UN-SHIM: find the sibling `.js`
//                                           CLI the shim delegates to and
//                                           spawn `<node.exe> <cli.js> …`.
//                                           STILL `shell: false`. The shim
//                                           file is never executed.
//   * other `.cmd`/`.bat` we can't un-shim
//                                        -> LAST RESORT: `shell: true` with
//                                           the RESOLVED absolute path as the
//                                           command. Note (r1 audit fix):
//                                           with `shell:true` on Windows,
//                                           Node concatenates cmd + argv into
//                                           a SINGLE `cmd.exe /d /s /c "…"`
//                                           string. cmd.exe DOES interpret
//                                           metacharacters (`&`, `|`, `>`,
//                                           `<`, `^`, `%`, `!`, `(`, `)`,
//                                           backtick, newline) in that
//                                           string — we do NOT get one-arg-
//                                           per-token isolation here. To
//                                           keep the fallback SAFE we
//                                           REJECT any argv token or command
//                                           path containing a cmd.exe
//                                           metacharacter and downgrade to
//                                           `unresolved` with a clear
//                                           diagnostic (the operator must
//                                           either install the `.exe` or
//                                           pick an un-shimmable tool).
//                                           When accepted, `argv` is still
//                                           HOST-SIDE-only — the browser has
//                                           NEVER been able to influence
//                                           `CODEX_APP_SERVER_CMD` and this
//                                           fix does not change that.
//
// Everything here is PURE. All filesystem / PATH access is behind an injected
// `probes` object so unit tests can drive the resolver deterministically.

import fs from 'node:fs';
import path from 'node:path';

// ------------------------------------------------------------------
// Path-flavor selection (HOST-INDEPENDENT — fixes Linux-CI regression)
// ------------------------------------------------------------------
//
// A `.cmd`/`.bat` shim file is ALWAYS Windows-format regardless of the host
// running these tests (its body uses `\` separators, `%dp0%\...`, and its
// sibling `node.exe` lookup is a Windows filename). If we used the ambient
// `path` module we would silently fall through to POSIX rules on a Linux
// runner: `path.dirname('C:\\nvm4w\\nodejs\\npx.cmd')` returns `.`,
// `path.resolve` refuses to split on `\`, and `path.relative(...).startsWith('../')`
// misses a `..\..\..\` traversal.
//
// Real Windows runtime behavior is UNCHANGED: on Windows `path === path.win32`,
// so every call below is bit-identical to the pre-fix behavior. Only Linux
// (i.e. CI) changes — from broken to correct. Similarly the `!isWin` PATH
// pass-through does not use ambient `path` at all, but we still normalize by
// deriving the flavor from `isWin` so the resolver is provably host-agnostic.
const WIN_PATH = path.win32;
const POSIX_PATH = path.posix;
function pathFor(isWin) { return isWin ? WIN_PATH : POSIX_PATH; }

// ------------------------------------------------------------------
// cmd.exe metacharacter guard (used by the `cmd-shim-shell` fallback)
// ------------------------------------------------------------------
//
// When Node spawns with `shell:true` on Windows it joins `cmd` + `argv` into a
// single string and hands it to `cmd.exe /d /s /c`. cmd.exe interprets these
// characters as metacharacters:
//
//   & | < > ^ % ! ( ) `
//   and CR/LF line terminators
//
// Quoting inside the joined string is best-effort; a `%FOO%` inside a token
// still gets expanded, a `&` still chains commands. We therefore REFUSE to
// take the shell-true branch when any token contains such a character. The
// operator sees a hard diagnostic and must install the `.exe` or use an
// un-shimmable tool. This closes the r1-audit finding.

// r2-audit fix: we flag EVERY `%`, not just `%<no-space>%`. Windows env-var
// names may contain whitespace (e.g. `%CWR AUDIT%`), and even without the
// pair, `cmd.exe` performs `%N` argument substitution and `%~<char>` variants.
// Because we're on the last-resort shell:true branch (already fringe), a
// blanket `%` reject is the correct trade-off: false positives are hard errors
// with a fix-me diagnostic; false negatives would be a shell-injection.
const CMD_META_RE = /[&|<>^!()`\r\n%]/;

/**
 * True iff `s` contains a cmd.exe metacharacter or a `%` (which could open
 * an environment-variable or positional-argument expansion). Over-strict on
 * purpose — this is only checked when we are ALREADY on the last-resort
 * shell:true `.cmd`/`.bat` fallback branch; every other branch is
 * `shell:false` where tokens are literal.
 */
export function containsCmdMetacharacter(s) {
  if (typeof s !== 'string') return false;
  return CMD_META_RE.test(s);
}

// ------------------------------------------------------------------
// Shim-detection helpers
// ------------------------------------------------------------------

/**
 * True if the file basename resolves to a Windows shim we recognise.
 * We accept `.cmd` and `.bat` — the npm-generated shims are usually `.cmd`,
 * but a defensive check for `.bat` costs nothing.
 */
function isShimExtension(p) {
  return /\.(cmd|bat)$/i.test(p);
}

/**
 * Parse a `.cmd` shim body and, if it matches the well-known npm-shim shape,
 * return the ABSOLUTE `.js` path it delegates to. Returns `null` when the
 * shim doesn't match — the caller must fall back to `shell:true`.
 *
 * Shape we match (npm/npx/codex shims all look like this):
 *
 *   "%_prog%"  "%dp0%\node_modules\<pkg>\bin\<name>-cli.js" %*
 *   "%_prog%"  "%dp0%\<any\rel\path.js>" %*
 *
 * `%dp0%` is set to the shim's own directory (with trailing backslash) by
 * `SET dp0=%~dp0`. We resolve the JS path RELATIVE to the shim's directory.
 *
 * SECURITY NOTE: we accept ONLY the strict tail pattern. A malicious shim
 * that does more work in the middle would not match and we'd fall back to
 * `shell:true`. We never `eval` or execute the shim body — this is a
 * SUBSTRING PARSE.
 *
 * @param {string} shimAbsPath  Absolute path to the resolved `.cmd`/`.bat`.
 * @param {(p:string)=>string|null} readFile  Injected reader (returns null on ENOENT).
 * @returns {{ cliJs: string, nodeCandidate: string | null } | null}
 */
export function parseNpmShim(shimAbsPath, readFile) {
  if (!isShimExtension(shimAbsPath)) return null;
  const body = readFile(shimAbsPath);
  if (typeof body !== 'string' || body.length === 0) return null;

  // Extract the `"%_prog%"  "%dp0%\<rel-path>" %*` tail line. The shim always
  // has ONE such tail line; match it strictly.
  //   - `"` = start of the CLI-path argument
  //   - `%dp0%` = literal (unescaped in the shim source)
  //   - `[^"\r\n]+` = the relative path (no quotes/newlines inside)
  //   - `"` = end
  //   - `\s+%\*` = the shim always forwards `%*` at the end
  const tail = /"%_prog%"\s+"%dp0%\\([^"\r\n]+)"\s+%\*/i.exec(body);
  if (!tail) return null;
  const relJs = tail[1].trim();
  if (!/\.js$/i.test(relJs)) return null;
  if (/[\r\n]/.test(relJs)) return null; // defensive — regex forbids newline already

  // A `.cmd`/`.bat` shim is Windows-format — always parse with `path.win32`
  // regardless of the host OS. On Windows this is a no-op (path === path.win32);
  // on Linux/macOS (CI, unit-test harness) it is what makes the parse actually
  // correct instead of silently mis-parsing backslash separators as filename
  // characters.
  const wp = WIN_PATH;
  const shimDir = wp.dirname(shimAbsPath);
  const cliJs = wp.resolve(shimDir, relJs);

  // Reject if the resolved JS path escapes the shim's directory tree — a
  // well-behaved npm shim points at a sibling `node_modules/...` file, never
  // at `..` up out of the tree. This makes shim-poisoning uninteresting: an
  // attacker who could replace the JS could also replace the .exe. What we
  // REFUSE is a shim pointing at a totally unrelated absolute path via
  // traversal.
  const rel = wp.relative(shimDir, cliJs);
  if (rel.startsWith('..' + wp.sep) || rel === '..' || wp.isAbsolute(rel)) {
    return null;
  }

  // Prefer a sibling `node.exe` in the same directory as the shim (nvm /
  // npm-global layout). Falls back to `null` — resolver caller uses
  // `process.execPath` in that case.
  const nodeCandidate = wp.join(shimDir, 'node.exe');
  return { cliJs, nodeCandidate };
}

// ------------------------------------------------------------------
// Main resolver
// ------------------------------------------------------------------

/**
 * @typedef {Object} ResolveResult
 * @property {string} cmd            Absolute (or PATH-resolvable) command to spawn.
 * @property {string[]} prefixArgs   Additional argv tokens the launcher must
 *                                   prepend BEFORE its own fixed args + user
 *                                   prefix. Example: for a `npx.cmd` un-shim
 *                                   this is `[cliJsPath]`.
 * @property {boolean} useShell      Node `spawn`'s `shell` option. `false` in
 *                                   every safe path; `true` ONLY on the
 *                                   documented `.cmd`/`.bat` fallback.
 * @property {'absolute' | 'posix-bare' | 'exe' | 'npm-shim-unshim' | 'cmd-shim-shell' | 'unresolved'} source
 * @property {string | null} notes   Human-readable diagnostic. Non-null when
 *                                   the fallback branch is taken; the launcher
 *                                   PRINTS it to stderr so the operator sees
 *                                   the security-relevant deviation.
 * @property {{tried: string[]} | null} error  When `source === 'unresolved'`.
 */

/**
 * Resolve `firstToken` from `CODEX_APP_SERVER_CMD` into a spawnable command.
 *
 * Pure — no `fs`, `process.env`, `child_process` at import time; every
 * filesystem or PATH read goes through `probes`. Callers wire the real
 * `defaultProbes` (below) in production; tests wire fakes.
 *
 * @param {string} firstToken
 * @param {{
 *   isWin: boolean,
 *   probes: {
 *     exists: (p:string) => boolean,
 *     whichAll: (name:string) => string[],
 *     readFile?: (p:string) => string | null,
 *   },
 * }} opts
 * @returns {ResolveResult}
 */
export function resolveLaunchCommand(firstToken, opts) {
  if (typeof firstToken !== 'string' || firstToken.length === 0) {
    return { cmd: '', prefixArgs: [], useShell: false, source: 'unresolved', notes: null, error: { tried: ['(empty first token)'] } };
  }
  const isWin = !!opts.isWin;
  const probes = opts.probes;
  const readFile = probes.readFile ?? defaultReadFile;

  // Absolute-or-relative-path detection must not depend on the host OS: an
  // absolute Windows path (`C:\...`) is always absolute for the Windows spawn
  // branch, and `/...` is always absolute for the POSIX branch. Use the
  // flavor selected by `isWin` explicitly. The `includes('/')` and
  // `includes('\\')` checks below cover the relative-path shape (`./bin/foo`
  // on POSIX, `subdir\foo` on Windows) and are separator-literal so they
  // don't need a flavor.
  const flavor = pathFor(isWin);
  const looksAbsoluteOrRelative =
    flavor.isAbsolute(firstToken) ||
    firstToken.includes('/') ||
    firstToken.includes('\\');

  // Absolute or relative path: caller has already picked the exact file (or
  // will fail its own stat check). Nothing to resolve — pass through with
  // shell:false.
  if (looksAbsoluteOrRelative) {
    return {
      cmd: firstToken,
      prefixArgs: [],
      useShell: false,
      source: 'absolute',
      notes: null,
      error: null,
    };
  }

  // POSIX bare command: `spawn` on POSIX resolves PATH itself with shell:false,
  // and there are no `.cmd`/`.bat` shims. Pass through — the OS will ENOENT if
  // the command really is missing.
  if (!isWin) {
    return {
      cmd: firstToken,
      prefixArgs: [],
      useShell: false,
      source: 'posix-bare',
      notes: null,
      error: null,
    };
  }

  // Windows bare command: WALK every PATH hit for `<name>` + PATHEXT so we
  // can prefer a real `.exe` over a `.cmd`/`.bat` shim and, when only a shim
  // exists, un-shim it to its underlying `.js`.
  const hits = probes.whichAll(firstToken) ?? [];
  if (hits.length === 0) {
    return {
      cmd: firstToken,
      prefixArgs: [],
      useShell: false,
      source: 'unresolved',
      notes: null,
      error: { tried: [`PATH lookup for ${firstToken} found no hits`] },
    };
  }

  // Prefer any `.exe` — cleanest path. `whichAll` returns hits in PATH+PATHEXT
  // order (see `fsProbes.whichAll`), so this is O(n) and stable.
  for (const hit of hits) {
    if (/\.exe$/i.test(hit) && probes.exists(hit)) {
      return {
        cmd: hit,
        prefixArgs: [],
        useShell: false,
        source: 'exe',
        notes: null,
        error: null,
      };
    }
  }

  // No `.exe` — try to un-shim the first `.cmd`/`.bat` we find that matches
  // the well-known npm-shim shape.
  for (const hit of hits) {
    if (!isShimExtension(hit) || !probes.exists(hit)) continue;
    const parsed = parseNpmShim(hit, readFile);
    if (parsed && probes.exists(parsed.cliJs)) {
      // Prefer the sibling `node.exe` (matches whichever Node the user's
      // shim was written for — critical for nvm setups). If missing, the
      // launcher will substitute `process.execPath`.
      const nodeAbs = parsed.nodeCandidate && probes.exists(parsed.nodeCandidate)
        ? parsed.nodeCandidate
        : null;
      return {
        cmd: nodeAbs ?? '__PROCESS_EXECPATH__',
        prefixArgs: [parsed.cliJs],
        useShell: false,
        source: 'npm-shim-unshim',
        notes:
          `un-shimmed ${hit} -> node "${parsed.cliJs}" ` +
          `(shell:false preserved; shim body NOT executed)`,
        error: null,
      };
    }
  }

  // Last resort: pick the first existing `.cmd`/`.bat` hit and spawn with
  // shell:true. r1-audit fix: on Windows with shell:true Node concatenates
  // cmd + argv into a single cmd.exe command string, so we DO get
  // metacharacter interpretation. We therefore refuse this branch if the
  // resolved cmd PATH contains a cmd.exe metacharacter (the caller must
  // ALSO validate every argv token before spawning — see the
  // `validateShellSafe` helper below and its call-site in
  // scripts/launch-appserver.mjs).
  for (const hit of hits) {
    if (isShimExtension(hit) && probes.exists(hit)) {
      if (containsCmdMetacharacter(hit)) {
        // Very unusual for a resolved PATH entry, but refuse rather than
        // enter shell:true with an already-hostile cmd string.
        return {
          cmd: firstToken,
          prefixArgs: [],
          useShell: false,
          source: 'unresolved',
          notes: null,
          error: {
            tried: [
              `PATH hit resolves to a .cmd shim (${hit}) but the path itself contains a cmd.exe metacharacter; refusing shell:true fallback.`,
            ],
          },
        };
      }
      return {
        cmd: hit,
        prefixArgs: [],
        useShell: true,
        source: 'cmd-shim-shell',
        notes:
          `RESIDUAL: spawning ${hit} with shell:true (unable to un-shim). ` +
          `Windows shell:true CONCATENATES cmd + argv into a cmd.exe /d /s /c ` +
          `string — argv tokens are NOT per-argument isolated. Every argv ` +
          `token has been validated to contain no cmd.exe metacharacter ` +
          `(&, |, <, >, ^, !, (, ), \`, %, CR/LF); ` +
          `otherwise the launcher would have bailed. The argv is HOST-SIDE ` +
          `only (never browser-supplied), so the injection surface is the ` +
          `operator's own CODEX_APP_SERVER_CMD.`,
        error: null,
      };
    }
  }

  // whichAll returned hits but none were `.exe`/`.cmd`/`.bat` we could use.
  return {
    cmd: firstToken,
    prefixArgs: [],
    useShell: false,
    source: 'unresolved',
    notes: null,
    error: {
      tried: hits.map((h) => `PATH hit not usable: ${h}`),
    },
  };
}

// ------------------------------------------------------------------
// Argv metacharacter validation (for the shell:true fallback)
// ------------------------------------------------------------------

/**
 * Scan `argv` and return the first token containing a cmd.exe metacharacter,
 * or null when all tokens are safe. The launcher calls this ONLY when the
 * resolver returned `useShell:true` and refuses to spawn on any hit.
 *
 * @param {string[]} argv
 * @returns {{ index: number, token: string } | null}
 */
export function firstUnsafeShellToken(argv) {
  if (!Array.isArray(argv)) return null;
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (typeof t !== 'string') continue;
    if (containsCmdMetacharacter(t)) return { index: i, token: t };
  }
  return null;
}

// ------------------------------------------------------------------
// Default probes (real filesystem)
// ------------------------------------------------------------------

/** Read a file to a string; returns null on ENOENT so parseNpmShim can bail. */
export function defaultReadFile(p) {
  try {
    return fs.readFileSync(p, 'utf8');
  } catch {
    return null;
  }
}
