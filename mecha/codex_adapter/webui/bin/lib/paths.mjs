// Codex Web Remote — packaged path resolver.
//
// Resolves runtime files (the prebuilt web/dist bundle, proxy/server.mjs,
// scripts/launch-appserver.mjs) relative to the INSTALLED package location
// (`import.meta.url` at call time), NEVER relative to `process.cwd()`.
//
// This is why the CLI works from ANY working directory once installed via
// `npx @ai-nd-co/codex-web-remote` — it uses its own on-disk siblings, not
// whatever files happen to be next to the operator's shell prompt.

import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

/**
 * Compute all runtime paths this CLI needs. `binFileUrl` is `import.meta.url`
 * of the caller (the bin entrypoint) so this stays PURE — no ambient magic.
 * The paths returned are all absolute.
 *
 * Layout when published:
 *   <install-prefix>/node_modules/@ai-nd-co/codex-web-remote/
 *     bin/codex-web-remote.mjs        <-- binFileUrl points here
 *     bin/lib/paths.mjs               <-- THIS file
 *     proxy/server.mjs
 *     scripts/launch-appserver.mjs
 *     scripts/lib/**
 *     web/dist/index.html
 *
 * Layout when running from the repo (npm link / npx . / direct node run):
 *   <repo>/
 *     bin/codex-web-remote.mjs
 *     bin/lib/paths.mjs
 *     proxy/server.mjs
 *     scripts/launch-appserver.mjs
 *     web/dist/index.html
 *
 * Both layouts have the same tree shape so a single resolver works for both.
 *
 * @param {string} binFileUrl - `import.meta.url` of the bin entrypoint.
 * @returns {{
 *   packageRoot: string,
 *   proxyEntry: string,
 *   launcherEntry: string,
 *   publicDir: string,
 * }}
 */
export function computeRuntimePaths(binFileUrl) {
  const binFile = fileURLToPath(binFileUrl);
  const binDir = path.dirname(binFile);            // .../bin
  const packageRoot = path.dirname(binDir);        // package root
  const proxyEntry = path.join(packageRoot, 'proxy', 'server.mjs');
  const launcherEntry = path.join(packageRoot, 'scripts', 'launch-appserver.mjs');
  const publicDir = path.join(packageRoot, 'web', 'dist');
  return { packageRoot, proxyEntry, launcherEntry, publicDir };
}

/**
 * Assert the runtime paths actually exist. Called BEFORE spawning anything
 * so the operator gets a single clear diagnostic instead of a cascading
 * spawn ENOENT + proxy static 404 storm.
 *
 * @param {ReturnType<typeof computeRuntimePaths>} paths
 * @returns {{ ok: true } | { ok: false, missing: string[] }}
 */
export function verifyRuntimePaths(paths) {
  const missing = [];
  if (!fs.existsSync(paths.proxyEntry)) missing.push(paths.proxyEntry);
  if (!fs.existsSync(paths.launcherEntry)) missing.push(paths.launcherEntry);
  const indexHtml = path.join(paths.publicDir, 'index.html');
  if (!fs.existsSync(indexHtml)) missing.push(indexHtml);
  return missing.length === 0 ? { ok: true } : { ok: false, missing };
}
