// 只读探测：问 codex app-server 要「账号 + 额度」，把结果按 JSON 打到 stdout。
//
// 为什么要一个 Node 脚本：mecha 核心是零依赖的 Python，而 app-server 只说 ws。
// 界面已经要求 node，vendor 的 `ws` 就在 `webui/node_modules` 里，复用它即可，
// 不必往 Python 环境里塞一个 websocket 客户端。
//
// 用法：node quota_probe.mjs --url ws://127.0.0.1:<port> --token-file <abs>
// 输出：一行 JSON（ok/email/plan/windows/credits/resetCredits）
//
// ⚠ 只调 account/read 与 account/rateLimits/read —— 都是只读；不登录、不登出、
// 不改配置。失败一律走 stderr + 非零退出码，由 Python 侧翻成"读不到 + 原因"。

import fs from 'node:fs';
import { WebSocket } from './webui/node_modules/ws/wrapper.mjs';

function arg(name, fallback = '') {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const url = arg('url');
const tokenFile = arg('token-file');
if (!url || !tokenFile) {
  console.error('缺少参数：--url 与 --token-file 都必需');
  process.exit(2);
}

let token = '';
try {
  token = fs.readFileSync(tokenFile, 'utf8').trim();
} catch (exc) {
  console.error(`读不到 token 文件：${exc.message}`);
  process.exit(2);
}

const timer = setTimeout(() => {
  console.error('探测超时（6s）：app-server 没有回应');
  process.exit(3);
}, 6000);

const ws = new WebSocket(url, { headers: { Authorization: `Bearer ${token}` } });
let nextId = 0;
const pending = new Map();

function rpc(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

function finish(payload, code = 0) {
  clearTimeout(timer);
  try { ws.close(); } catch { /* 已经关了 */ }
  if (code === 0) process.stdout.write(JSON.stringify(payload));
  process.exit(code);
}

ws.on('message', (raw) => {
  let msg;
  try { msg = JSON.parse(raw.toString()); } catch { return; }
  const slot = msg.id != null ? pending.get(msg.id) : null;
  if (!slot) return;
  pending.delete(msg.id);
  if (msg.error) slot.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
  else slot.resolve(msg.result ?? {});
});

ws.on('error', (exc) => {
  console.error(`连不上 ${url}：${exc.message}`);
  finish({}, 1);
});

ws.on('open', async () => {
  try {
    await rpc('initialize', {
      clientInfo: { name: 'mecha-quota', title: 'mecha quota', version: '0.1.0' },
    });
    ws.send(JSON.stringify({ method: 'initialized', params: {} }));
    const account = await rpc('account/read');
    const limits = await rpc('account/rateLimits/read');
    const acct = account.account || {};
    const rl = (limits.rateLimits) || {};
    finish({
      ok: true,
      email: acct.email || '',
      plan: acct.planType || rl.planType || '',
      windows: [
        rl.primary && {
          label: windowLabel(rl.primary.windowDurationMins),
          usedPercent: rl.primary.usedPercent,
          resetsAt: rl.primary.resetsAt || null,
        },
        rl.secondary && {
          label: windowLabel(rl.secondary.windowDurationMins),
          usedPercent: rl.secondary.usedPercent,
          resetsAt: rl.secondary.resetsAt || null,
        },
      ].filter(Boolean),
      credits: (rl.credits && rl.credits.balance) || '0',
      resetCredits: (limits.rateLimitResetCredits && limits.rateLimitResetCredits.availableCount) || 0,
      resetExpires: ((limits.rateLimitResetCredits && limits.rateLimitResetCredits.credits) || [])
        .map((c) => c.expiresAt).filter(Boolean).sort((a, b) => a - b)[0] || null,
    });
  } catch (exc) {
    console.error(`查询失败：${exc.message}`);
    finish({}, 1);
  }
});

function windowLabel(mins) {
  if (!mins) return '';
  if (mins % 1440 === 0) return `${mins / 1440} 天`;
  if (mins % 60 === 0) return `${mins / 60} 小时`;
  return `${mins} 分钟`;
}
