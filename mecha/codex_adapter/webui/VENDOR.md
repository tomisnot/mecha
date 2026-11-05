# vendor: codex-web-remote 0.2.0

这个目录**不是我们的代码**，是整包 vendor 进来的第三方 Web UI + 代理，用来给 Codex 宿主
提供一个浏览器界面。上游更新不进这里——升级是「整目录替换 + 重跑判据」的显式动作。

## 来源

| 项 | 值 |
|---|---|
| 包名 | `@ai-nd-co/codex-web-remote` |
| 版本 | **0.2.0**（2026-10 发布） |
| 许可证 | Apache-2.0（见同目录 `LICENSE`） |
| tarball | `https://registry.npmmirror.com/@ai-nd-co/codex-web-remote/-/codex-web-remote-0.2.0.tgz` |
| 上游仓库 | https://github.com/ai-nd-co/codex-web-remote |

## 为什么 vendor 而不是 `npx`

用户要求：**别让上游更新把我们的东西搞坏**。`npx` 每次都解析最新版，等于把运行时交给
registry；vendor 之后版本固定在这个目录里，除非我们主动替换，行为不会变。

⚠ 代价要说清楚：**前端只有构建产物**（`web/dist` 的 js/css），没有 React 源码。
能改的是代理（`proxy/`、`bin/`，源码）；界面本身改不了，要改只能换包或自研。

## 保留了什么 / 裁掉了什么

保留：

- `bin/`、`proxy/`、`scripts/`：CLI 与代理源码（我们只用 `--upstream` 连接模式）
- `web/dist/`：UI 构建产物（`index.html` + js + css）
- `node_modules/ws/`：唯一运行时依赖 `ws@8.22.1`（上游 `dependencies` 只有它）
- `LICENSE`、`README.md`、`package.json`：溯源用

裁掉：

- `web/dist/assets/*.js.map`（3.3 MB，调试用，运行不需要）
- `devDependencies`（`semantic-release` 那条链，发布用，运行不需要）

## 怎么用（mecha 侧）

```bash
node webui/bin/codex-web-remote.mjs \
  --upstream ws://127.0.0.1:<app-server 端口> \
  --token-file <绝对路径的 ws token> \
  --port <UI 端口> --host 127.0.0.1
```

- `--upstream` ⇒ **只连接、绝不自动拉起** codex（上游 README 明写：给了这个参数就是
  connect-only）。生存域因此留在 mecha 的 supervisor 手里。
- `--token-file` ⇒ 代理在**服务端**给上游握手加 `Authorization: Bearer …`，token 不进浏览器。
- 代理会**剥掉浏览器的 `Origin` 头**——codex app-server 对带 Origin 的握手直接 403
  （本机实测），没有这一层浏览器根本连不上。
- 代理有 `deny-by-default` 的 RPC 方法白名单（30 条），浏览器调不到 `fs/*`、`process/*`、
  配置写入、原始 MCP。

## 升级怎么做

1. `npm pack @ai-nd-co/codex-web-remote@<新版本>`，解开；
2. 整目录替换（保留本文件与上面「保留/裁掉」的裁剪规则）；
3. `node -p "require('./node_modules/ws/package.json').version"` 记下实际版本；
4. 跑 `python checks/codex_ui_selfcheck.py`（会真起 app-server + 代理并取页面）。
