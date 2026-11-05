// Codex Web Remote — CL dev-stack shared helpers.
//
// Pure functions used by scripts/dev-up.mjs and scripts/dev-down.mjs. Kept in
// its own module so the unit tests in e2e/tests/11-cl-dev-stack-helpers.test.mjs
// can exercise them without spawning real subprocesses.
//
// NO SIDE EFFECTS AT IMPORT TIME. All fs / net access is behind explicit
// injected `probes` so tests can drive the picker deterministically.

import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

// ------------------------------------------------------------------
// Codex binary auto-detect
// ------------------------------------------------------------------
//
// This session broke because `scripts/launch-appserver.mjs` defaulted to a
// worktree binary that had been cleaned up. `npx @ai-nd-co/codex` also fails
// because the launcher uses `spawn(..., {shell:false})` on Windows and would
// need a `.cmd` shim. We therefore auto-detect a REAL executable file — in the
// order below — and hand the resolved absolute path to the launcher via
// CODEX_APP_SERVER_CMD. First candidate that actually exists on disk wins.
//
// Order:
//   1. Local build in the codex-upstream worktree (if a dev built it).
//   2. Installed nvm-managed vendor exe (Windows x64 — the one the user has).
//   3. Installed vendor exe under APPDATA/npm-global roots (belt & suspenders).
//   4. `codex` on PATH — accepted only if `.exe` on Windows or executable on
//      POSIX. We never accept `npx …`: the launcher's shell:false spawn breaks
//      that on Windows.
//
// We DELIBERATELY do NOT hardcode ONLY the worktree path — that's the failure
// mode this task was created to eliminate.

const IS_WIN = process.platform === 'win32';
const EXE = IS_WIN ? '.exe' : '';

/**
 * Build the ordered list of codex binary candidates for a given repo root and
 * user home. Pure — returns a list; does not touch the filesystem.
 *
 * @param {{ repoRoot: string, homeDir?: string, extraCandidates?: string[] }} opts
 * @returns {Array<{ path: string, source: 'worktree'|'vendor-nvm'|'vendor-appdata'|'vendor-npm-global'|'extra' }>}
 */
export function codexBinaryCandidates(opts) {
  const repoRoot = opts.repoRoot;
  const homeDir = opts.homeDir ?? os.homedir();
  const extras = Array.isArray(opts.extraCandidates) ? opts.extraCandidates : [];
  const list = [];

  // 1. Local worktree build (was the launch-appserver default; still preferred
  //    if actually present because it's the version a dev iterated on).
  list.push({
    path: path.resolve(
      repoRoot,
      '..', '..',
      'worktrees',
      'codex-upstream-port-20260722',
      'codex-rs',
      'target',
      'debug',
      `codex${EXE}`,
    ),
    source: 'worktree',
  });

  // 2. Installed nvm-managed vendor exe (what actually works on this box).
  //    Path is only meaningful on Windows; on POSIX we still emit it so the
  //    unit tests can verify order — the exists() probe filters it out.
  if (IS_WIN) {
    list.push({
      path: 'C:\\nvm4w\\nodejs\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\bin\\codex.exe',
      source: 'vendor-nvm',
    });
  }

  // 3. Installed vendor exe under APPDATA/npm-global roots (Windows) or the
  //    user's global npm prefix (POSIX). Belt & suspenders.
  if (IS_WIN) {
    const appdata = process.env.APPDATA ?? path.join(homeDir, 'AppData', 'Roaming');
    list.push({
      path: path.join(
        appdata,
        'npm',
        'node_modules',
        '@openai',
        'codex',
        'node_modules',
        '@openai',
        'codex-win32-x64',
        'vendor',
        'x86_64-pc-windows-msvc',
        'bin',
        'codex.exe',
      ),
      source: 'vendor-appdata',
    });
  } else {
    // POSIX vendor path — arch varies, but the linux-x64 arrangement is
    // usually here. Kept as a best-effort second-choice.
    list.push({
      path: path.join(
        homeDir,
        '.npm-global',
        'lib',
        'node_modules',
        '@openai',
        'codex',
        'node_modules',
        '@openai',
        'codex-linux-x64',
        'vendor',
        'x86_64-unknown-linux-gnu',
        'bin',
        'codex',
      ),
      source: 'vendor-npm-global',
    });
  }

  // 4. Extras (test hook + caller override).
  for (const p of extras) list.push({ path: p, source: 'extra' });

  return list;
}

/**
 * True if a path is executable enough for the launcher's `spawn(..., {shell: false})`.
 * On Windows: only `.exe` — never `.cmd`/`.bat` (spawn without shell can't exec shims).
 * On POSIX: must be a regular file with the X bit for the current user (`X_OK`).
 *
 * @param {string} p Absolute path (already known to exist).
 */
export function isLauncherExecutable(p) {
  if (typeof p !== 'string' || p.length === 0) return false;
  if (IS_WIN) {
    return /\.exe$/i.test(p);
  }
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Reject paths that contain ASCII whitespace. The launcher whitespace-splits
 * `CODEX_APP_SERVER_CMD`, so any space in the resolved path breaks it (e.g.
 * `C:\Program Files\...`). Fail fast with a clear message rather than let
 * the launcher spawn a garbage first-token.
 */
export function isWhitespaceSafe(p) {
  return typeof p === 'string' && !/\s/.test(p);
}

/**
 * Pick the first candidate whose file exists AND is spawn-usable. Also
 * resolves a bare `codex` on PATH by SCANNING (never bails on the first
 * shim) — the launcher's `shell: false` spawn can't execute `.cmd`/`.bat`,
 * so we continue past them until we find a real `.exe`. Any resolved path
 * containing whitespace is rejected (launcher whitespace-splits the env).
 *
 * @param {Array<{path:string, source:string}>} candidates
 * @param {{ exists: (p:string)=>boolean,
 *           executable?: (p:string)=>boolean,
 *           whichAll?: (name:string)=>string[],
 *           which?: (name:string)=>string|null }} probes
 * @returns {{ path: string, source: string }}
 * @throws {Error} with a diagnostic listing every candidate + PATH lookup result
 */
export function pickCodexBinary(candidates, probes) {
  if (!probes || typeof probes.exists !== 'function') {
    throw new TypeError('pickCodexBinary: probes.exists is required');
  }
  const isExec = probes.executable ?? isLauncherExecutable;
  const tried = [];

  const accept = (p, source) => {
    if (!isExec(p)) {
      tried.push(`  - [${source}] REJECTED (not spawn-usable, need ${IS_WIN ? '.exe' : 'X_OK'}): ${p}`);
      return null;
    }
    if (!isWhitespaceSafe(p)) {
      tried.push(`  - [${source}] REJECTED (path has whitespace — launcher splits on spaces): ${p}`);
      return null;
    }
    return { path: p, source };
  };

  for (const c of candidates) {
    if (!probes.exists(c.path)) {
      tried.push(`  - [${c.source}] MISSING: ${c.path}`);
      continue;
    }
    const ok = accept(c.path, c.source);
    if (ok) return ok;
  }

  // PATH walk: prefer whichAll (returns every match; we scan past shims);
  // fall back to `which` for callers that pre-date the walker.
  let pathHits = null;
  if (typeof probes.whichAll === 'function') {
    pathHits = probes.whichAll(IS_WIN ? 'codex' : 'codex');
  } else if (typeof probes.which === 'function') {
    const one = probes.which(IS_WIN ? 'codex.exe' : 'codex');
    pathHits = one ? [one] : [];
  }
  if (pathHits && pathHits.length > 0) {
    for (const hit of pathHits) {
      if (!probes.exists(hit)) continue;
      const ok = accept(hit, 'PATH');
      if (ok) return ok;
    }
    if (pathHits.length > 0) {
      tried.push(`  - [PATH] all ${pathHits.length} match(es) rejected — see reasons above`);
    }
  } else if (probes.whichAll || probes.which) {
    tried.push(`  - [PATH] not found on PATH`);
  }
  const err = new Error(
    `No usable codex binary found. Tried:\n${tried.join('\n')}\n\n` +
      `Fix: install @openai/codex (or point --codex-binary at a codex${IS_WIN ? '.exe' : ''}).\n` +
      `Note: on Windows the path must end in .exe AND contain no whitespace — the launcher's shell:false spawn cannot handle .cmd shims or spaces.`,
  );
  err.code = 'NO_CODEX_BINARY';
  err.tried = tried;
  throw err;
}

/** Default filesystem-backed probes for pickCodexBinary. */
export const fsProbes = {
  exists(p) {
    try {
      return fs.statSync(p).isFile();
    } catch {
      return false;
    }
  },
  executable: isLauncherExecutable,
  /**
   * Return EVERY PATH hit for `name`, in PATH-then-PATHEXT order, so the
   * picker can walk past `.cmd`/`.bat` shims and find the real `.exe`.
   */
  whichAll(name) {
    const PATH = process.env.PATH ?? '';
    const sep = IS_WIN ? ';' : ':';
    // Order matters: prefer .exe before .cmd/.bat so the picker sees the
    // real binary first when both live in the same dir. On POSIX we only
    // probe the bare name (no extension games).
    const exts = IS_WIN
      ? ['.EXE', '.CMD', '.BAT', ...((process.env.PATHEXT ?? '').split(';').filter(Boolean))]
      : [''];
    // Dedupe while preserving order.
    const seenExts = new Set();
    const finalExts = exts.filter((e) => {
      const k = e.toUpperCase();
      if (seenExts.has(k)) return false;
      seenExts.add(k);
      return true;
    });
    const base = name.replace(/\.exe$/i, '');
    const hits = [];
    for (const dir of PATH.split(sep)) {
      if (!dir) continue;
      for (const ext of finalExts) {
        const p = path.join(dir, base + (ext || ''));
        try {
          if (fs.statSync(p).isFile()) hits.push(p);
        } catch {
          /* keep scanning */
        }
      }
    }
    return hits;
  },
  which(name) {
    const hits = fsProbes.whichAll(name);
    return hits.length > 0 ? hits[0] : null;
  },
};

// ------------------------------------------------------------------
// Health-gate: distinguish a FRESH multi-host proxy from a STALE one
// ------------------------------------------------------------------
//
// The stale-proxy failure in this session: an older `proxy/server.mjs` was
// still running, so it 200'd on `/proxy/healthz` but its body lacked
// `hostCount`/`multiHost` and its `/proxy/hosts` 404'd. The bootstrap saw
// "not found" from the hosts endpoint and threw "not found is not valid JSON".
//
// Definition of a CURRENT proxy for our purposes:
//   - `ok:true`
//   - `hostCount` is a non-negative integer  (C10)
//   - `multiHost` is a boolean               (C10)
// Everything else (`tls`, `wsPath`, `activeConnections`, `allowlistSize`) is
// nice-to-have; the two C10 fields are the marker of a proxy that predates
// this task's own guarantees.

/**
 * @param {unknown} body Parsed JSON body from /proxy/healthz.
 * @returns {{ ok: boolean, current: boolean, missing: string[], stale: boolean, hostCount?: number, multiHost?: boolean }}
 */
export function parseHealthz(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, current: false, missing: ['not-an-object'], stale: false };
  }
  const missing = [];
  if (body.ok !== true) missing.push('ok:true');
  if (!(Number.isInteger(body.hostCount) && body.hostCount >= 0)) missing.push('hostCount');
  if (typeof body.multiHost !== 'boolean') missing.push('multiHost');
  const okShape = missing.length === 0;
  return {
    ok: body.ok === true,
    current: okShape,
    missing,
    // "stale" = the proxy is up (ok:true) but lacks the C10 fields.
    stale: body.ok === true && !okShape,
    hostCount: typeof body.hostCount === 'number' ? body.hostCount : undefined,
    multiHost: typeof body.multiHost === 'boolean' ? body.multiHost : undefined,
  };
}

// ------------------------------------------------------------------
// PID-file read / write (atomic)
// ------------------------------------------------------------------
//
// Small JSON blob so dev-down can find EXACTLY the two processes we started.
// Written atomically (tmp + rename) so a crash mid-write can't leave a
// half-file that fools dev-down into killing random pids.

/**
 * @typedef {{
 *   version: 1,
 *   startedAt: string,
 *   startedPid: number,
 *   appserverPid: number,
 *   proxyPid: number,
 *   appserverPort: number,
 *   proxyPort: number,
 *   codexBinary: string,
 *   codexBinarySource: string,
 *   codexHome: string,
 *   publicDir: string
 * }} DevStackState
 */

/**
 * Read the PID file if present. Returns null when the file is missing (the
 * common "nothing to do" case). Throws when the file EXISTS but is corrupt
 * — the caller must distinguish "no state" from "unreadable state" so an
 * unexpected corruption doesn't get silently swallowed as "success".
 */
export function readPidFile(pidFilePath) {
  let raw;
  try {
    raw = fs.readFileSync(pidFilePath, 'utf8');
  } catch (err) {
    if (err && err.code === 'ENOENT') return null;
    throw err;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    const e = new Error(`pid-file at ${pidFilePath} is not valid JSON`);
    e.code = 'CORRUPT_PID_FILE';
    throw e;
  }
  if (!parsed || parsed.version !== 1) {
    const e = new Error(`pid-file at ${pidFilePath} has an unsupported version`);
    e.code = 'CORRUPT_PID_FILE';
    throw e;
  }
  const isPid = (n) => Number.isInteger(n) && n > 0;
  const isPort = (n) => Number.isInteger(n) && n > 0 && n <= 65535;
  if (!isPid(parsed.appserverPid) || !isPid(parsed.proxyPid)) {
    const e = new Error(`pid-file at ${pidFilePath} has invalid pids (must be positive integers)`);
    e.code = 'CORRUPT_PID_FILE';
    throw e;
  }
  if (!isPort(parsed.appserverPort) || !isPort(parsed.proxyPort)) {
    const e = new Error(`pid-file at ${pidFilePath} has invalid ports (must be 1-65535)`);
    e.code = 'CORRUPT_PID_FILE';
    throw e;
  }
  return parsed;
}

/** Atomically write the PID file (tmp + rename). */
export function writePidFile(pidFilePath, state) {
  const dir = path.dirname(pidFilePath);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = pidFilePath + '.tmp.' + process.pid + '.' + Date.now();
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tmp, pidFilePath);
}

export function removePidFile(pidFilePath) {
  try {
    fs.unlinkSync(pidFilePath);
  } catch {
    /* already gone */
  }
}

// ------------------------------------------------------------------
// Process + port introspection
// ------------------------------------------------------------------

/** True if the given pid is alive (works cross-platform via signal 0). */
export function isPidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    // ESRCH = no such process; EPERM = process exists but not ours (still alive).
    return err.code === 'EPERM';
  }
}

/**
 * Fetch a process command-line fingerprint for identity-verification before
 * killing. Returns null when unavailable (best-effort). Uses `wmic` on
 * Windows (with a hard timeout — wmic is deprecated and may be missing on
 * newer builds) and `ps` on POSIX.
 *
 * The purpose is NOT security — it's to defeat OS PID recycling on a single-
 * user dev box. If a crash left a PID file behind and the OS handed pid=17234
 * to a totally unrelated process (say, a text editor), a naive `taskkill /F
 * /T /PID 17234` would nuke it. We refuse to kill unless the fingerprint
 * still contains one of the expected markers we recorded at startup, AND
 * (stronger, see verifyPortOwner) the port we care about is still owned by
 * the recorded pid.
 *
 * @param {number} pid
 * @param {{ timeoutMs?: number }} [opts]
 * @returns {Promise<string|null>} lowercase command string, or null on failure/timeout
 */
async function runProbe({ cmd, args, timeoutMs, parse }) {
  const { spawn } = await import('node:child_process');
  return new Promise((resolve) => {
    let child;
    let done = false;
    const chunks = [];
    const finish = (v) => { if (!done) { done = true; try { child && child.kill(); } catch {} resolve(v); } };
    try {
      child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true, shell: false });
    } catch {
      finish(null); return;
    }
    const timer = setTimeout(() => finish(null), timeoutMs);
    child.stdout.on('data', (c) => chunks.push(c));
    child.on('error', () => { clearTimeout(timer); finish(null); });
    child.on('exit', (code) => {
      clearTimeout(timer);
      if (code !== 0) return finish(null);
      finish(parse(Buffer.concat(chunks).toString('utf8')));
    });
  });
}

// Default probe timeout. Windows callers get more headroom because a
// PowerShell fallback cold-start can take 5-10s on top of profile + CIM
// load; wmic-less boxes rely on that fallback. POSIX `ps` is <100ms.
// Tests that need a strict upper bound must pass `timeoutMs` explicitly.
const DEFAULT_PROBE_TIMEOUT_MS = IS_WIN ? 15000 : 3000;

export async function processCommandLine(pid, opts = {}) {
  if (!Number.isInteger(pid) || pid <= 0) return null;
  const timeoutMs = Number.isInteger(opts.timeoutMs) ? opts.timeoutMs : DEFAULT_PROBE_TIMEOUT_MS;
  const parseWmic = (raw) => {
    const m = /commandline=(.*)/i.exec(raw.toLowerCase());
    const t = (m ? m[1] : raw.toLowerCase()).trim();
    return t.length > 0 ? t : null;
  };
  const parsePosix = (raw) => {
    const t = raw.toLowerCase().trim();
    return t.length > 0 ? t : null;
  };
  const parsePwsh = (raw) => {
    const t = raw.trim().toLowerCase();
    return t.length > 0 ? t : null;
  };
  if (IS_WIN) {
    // Try wmic first (fast + universally available on older builds).
    let v = await runProbe({
      cmd: 'wmic',
      args: ['process', 'where', `ProcessId=${pid}`, 'get', 'CommandLine', '/format:list'],
      timeoutMs, parse: parseWmic,
    });
    if (v) return v;
    // PowerShell fallback (Win11 removed wmic on some builds).
    v = await runProbe({
      cmd: 'powershell',
      args: ['-NoProfile', '-Command', `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`],
      timeoutMs, parse: parsePwsh,
    });
    return v;
  }
  return runProbe({
    cmd: 'ps', args: ['-o', 'command=', '-p', String(pid)], timeoutMs, parse: parsePosix,
  });
}

/**
 * Look up a process's ParentProcessId. Cross-platform, best-effort (returns
 * null when the probe can't answer).
 */
export async function parentPidOf(pid, opts = {}) {
  if (!Number.isInteger(pid) || pid <= 0) return null;
  const timeoutMs = Number.isInteger(opts.timeoutMs) ? opts.timeoutMs : DEFAULT_PROBE_TIMEOUT_MS;
  const parseWmic = (raw) => {
    const m = /parentprocessid=(\d+)/i.exec(raw);
    if (!m) return null;
    const n = Number.parseInt(m[1], 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  const parsePosix = (raw) => {
    const n = Number.parseInt(raw.trim(), 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  const parsePwsh = (raw) => {
    const n = Number.parseInt(raw.trim(), 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  if (IS_WIN) {
    let v = await runProbe({
      cmd: 'wmic',
      args: ['process', 'where', `ProcessId=${pid}`, 'get', 'ParentProcessId', '/format:list'],
      timeoutMs, parse: parseWmic,
    });
    if (v) return v;
    v = await runProbe({
      cmd: 'powershell',
      args: ['-NoProfile', '-Command', `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").ParentProcessId`],
      timeoutMs, parse: parsePwsh,
    });
    return v;
  }
  return runProbe({
    cmd: 'ps', args: ['-o', 'ppid=', '-p', String(pid)], timeoutMs, parse: parsePosix,
  });
}

/**
 * True if `descendantPid` is `ancestorPid` OR any parent-chain ancestor is
 * `ancestorPid`. Bounded to 8 hops so a broken parent chain cannot spin.
 * Returns null when the probe can't answer (unknown — caller decides).
 */
export async function isDescendantOf(descendantPid, ancestorPid) {
  if (!Number.isInteger(descendantPid) || !Number.isInteger(ancestorPid)) return null;
  if (descendantPid === ancestorPid) return true;
  let cur = descendantPid;
  for (let hop = 0; hop < 8; hop++) {
    const parent = await parentPidOf(cur);
    if (parent == null) return null; // probe unavailable
    if (parent === ancestorPid) return true;
    if (parent <= 1) return false; // hit init / no more parents
    cur = parent;
  }
  return false;
}

/**
 * Ask the OS which PID owns a listening loopback TCP port. Returns null when
 * the tool isn't available or the port isn't held. This is the STRONGER
 * ownership check than command-line matching: even if PIDs recycled, the
 * kernel-level socket owner is unforgeable within our threat model (single
 * user, local box).
 *
 * @param {number} port
 * @param {{ timeoutMs?: number }} [opts]
 * @returns {Promise<number|null>}
 */
export async function portOwnerPid(port, opts = {}) {
  if (!Number.isInteger(port) || port <= 0 || port > 65535) return null;
  const timeoutMs = Number.isInteger(opts.timeoutMs) ? opts.timeoutMs : DEFAULT_PROBE_TIMEOUT_MS;
  const { spawn } = await import('node:child_process');
  return new Promise((resolve) => {
    let cmd, args;
    if (IS_WIN) {
      cmd = 'netstat';
      args = ['-ano', '-p', 'TCP'];
    } else {
      // ss is present on modern linux; fall back to lsof if not available.
      cmd = 'ss';
      args = ['-lntpH'];
    }
    const chunks = [];
    let child;
    let done = false;
    const finish = (v) => { if (!done) { done = true; try { child && child.kill(); } catch {} resolve(v); } };
    try {
      child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true, shell: false });
    } catch {
      finish(null); return;
    }
    const timer = setTimeout(() => finish(null), timeoutMs);
    child.stdout.on('data', (c) => chunks.push(c));
    child.on('error', () => { clearTimeout(timer); finish(null); });
    child.on('exit', () => {
      clearTimeout(timer);
      const out = Buffer.concat(chunks).toString('utf8');
      // Match a LISTENING loopback row on the port and capture the PID.
      // Windows netstat sample (whitespace-separated):
      //   TCP    127.0.0.1:8787    0.0.0.0:0    LISTENING    12345
      // POSIX ss sample (line contains `users:(("proc",pid=1234,fd=3))`).
      let match = null;
      if (IS_WIN) {
        const re = new RegExp(
          `^\\s*TCP\\s+(?:127\\.0\\.0\\.1|0\\.0\\.0\\.0|\\[::1?\\])?:${port}\\b[^\\n]*\\bLISTENING\\s+(\\d+)`,
          'im',
        );
        match = re.exec(out);
      } else {
        const re = new RegExp(
          `LISTEN[^\\n]*(?:127\\.0\\.0\\.1|\\[::1?\\]|0\\.0\\.0\\.0|\\*):${port}\\b[^\\n]*pid=(\\d+)`,
          'i',
        );
        match = re.exec(out);
      }
      if (match) resolve(Number.parseInt(match[1], 10));
      else resolve(null);
    });
  });
}

/**
 * Verify a pid still looks like the process we started. Given a list of
 * expected substrings that MUST appear in the command line (e.g.
 * "launch-appserver.mjs" for our app-server child), returns true only if the
 * pid is alive AND its command line matches. Returns null when the
 * fingerprint cannot be read (caller decides: best-effort fall through, or
 * refuse to kill). The "null = unknown" tri-state is deliberate — never
 * conflate "identity unavailable" with "identity mismatch".
 *
 * @param {number} pid
 * @param {string[]} expectSubstrings
 * @returns {Promise<true|false|null>}
 */
export async function verifyPidIdentity(pid, expectSubstrings) {
  if (!isPidAlive(pid)) return false;
  const cmd = await processCommandLine(pid);
  if (cmd == null) return null;
  for (const sub of expectSubstrings) {
    if (sub && cmd.includes(String(sub).toLowerCase())) return true;
  }
  return false;
}

/**
 * Full kill-safety verification for a recorded pid on a known port. Combines
 * identity fingerprinting + port-owner + descendant check so `dev-down` uses
 * exactly the same gate as `dev-up`'s idempotency check.
 *
 * Returns:
 *   { safeToKill: true }
 *   { safeToKill: false, reason: 'not-alive' | 'identity-mismatch' | 'port-foreign' }
 *
 * "port-foreign" is when the port owner is a known non-matching pid AND
 * descendant probe proves it's NOT ours (or the probe is unavailable and
 * strict mode is on — see r3 fix). Identity=null is accepted (probe may be
 * missing on the box); the port-owner check is the stronger gate.
 *
 * @param {number} pid
 * @param {number} port
 * @param {string[]} markers
 */
export async function verifyKillSafety(pid, port, markers) {
  if (!isPidAlive(pid)) return { safeToKill: false, reason: 'not-alive' };
  const [identity, portOwner] = await Promise.all([
    verifyPidIdentity(pid, markers),
    portOwnerPid(port),
  ]);
  if (identity === false) {
    return { safeToKill: false, reason: 'identity-mismatch', identity, portOwner };
  }
  let descendant = null;
  if (portOwner != null && portOwner !== pid) {
    descendant = await isDescendantOf(portOwner, pid);
    if (descendant !== true) {
      // Proven-or-unknown foreign listener. Refuse.
      return { safeToKill: false, reason: 'port-foreign', identity, portOwner, descendantProbe: descendant };
    }
  }
  // r4 fix (fail-closed): require at least ONE positive ownership indicator.
  // If both identity and port-owner probes returned null (both wmic AND
  // powershell missing on Windows, or `ss`/`ps` missing on POSIX), we CANNOT
  // prove this pid is ours. A recycled OS pid could then be nuked. Refuse
  // and force the operator to intervene (or delete the pid-file manually).
  const proven = identity === true || portOwner === pid || descendant === true;
  if (!proven) {
    return {
      safeToKill: false,
      reason: 'unknown-ownership',
      identity,
      portOwner,
      descendantProbe: descendant,
    };
  }
  return { safeToKill: true, identity, portOwner, descendantProbe: descendant };
}

/**
 * Probe a loopback TCP port. Resolves { listening: true } if something answered
 * within `timeoutMs`, otherwise { listening: false }.
 */
export function probeTcpPort(port, { host = '127.0.0.1', timeoutMs = 500 } = {}) {
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

/**
 * Sentinel error class: throwing an instance of this inside `pollUntil`'s
 * producer aborts the poll IMMEDIATELY rather than retrying until timeout.
 * Used by the health-gate to reject a stale-shape proxy on first observation
 * (we don't want to wait 20s to notice the shape is wrong — it's proof of a
 * collision, not a transient boot delay).
 */
export class FatalPollError extends Error {
  constructor(msg) {
    super(msg);
    this.name = 'FatalPollError';
    this.code = 'EFATAL_POLL';
  }
}

/**
 * Poll a producer until it returns truthy or `timeoutMs` elapses.
 * Returns whatever the producer returned, or throws with `lastError` message.
 * If the producer throws a `FatalPollError`, the poll unwinds immediately.
 */
export async function pollUntil(fn, { timeoutMs = 20000, intervalMs = 250, label = 'condition' } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (err) {
      if (err instanceof FatalPollError) throw err;
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  const err = new Error(`timed out waiting for ${label} after ${timeoutMs}ms${lastErr ? `: ${lastErr.message}` : ''}`);
  err.code = 'ETIMEDOUT_POLL';
  throw err;
}

// ------------------------------------------------------------------
// Defaults
// ------------------------------------------------------------------

export const DEFAULTS = Object.freeze({
  appserverPort: 4222,
  proxyPort: 8787,
  pidFile: '.dev-stack.pids',
  logDir: '.dev-stack-logs',
});

/**
 * Command-line markers a legitimate stack process MUST include. Kept here
 * (not per-script duplicated) so dev:up and dev:down never drift. Case-
 * insensitive substring match against the OS-reported command line. Combined
 * with the port-owner check, this defeats OS PID recycling in the single-
 * user local-box threat model.
 */
export const APPSERVER_CMD_MARKERS = Object.freeze(['launch-appserver.mjs']);
export const PROXY_CMD_MARKERS = Object.freeze(['proxy/server.mjs', 'proxy\\server.mjs']);
