/** Plugin-side refresh for mecha's aggregate MCP client. */
import { existsSync, readFileSync, watch } from 'node:fs'
import { basename } from 'node:path'

export const name = 'mecha-mcp-refresh'
export const inject = []
const OVERLAY_NAME = 'dsh-aggregate.patch.yml'
const DEFAULT_ENTRY_ID = 'mcp-mecha'

function overlayFromArgv(argv = process.argv) {
  for (let i = 0; i < argv.length - 1; i++) {
    if (argv[i] === '--patch' && basename(String(argv[i + 1] ?? '')) === OVERLAY_NAME) return String(argv[i + 1])
  }
}

function readTarget(file) {
  const text = readFileSync(file, 'utf8')
  const id = text.match(/^\s*- id:\s*([^\s#]+)/m)?.[1] ?? DEFAULT_ENTRY_ID
  const url = text.match(/^\s*url:\s*(\S+)\s*$/m)?.[1]
  if (!url) throw new Error(`overlay 没有 url（${basename(file)}）`)
  return { id, url }
}

export async function apply(ctx) {
  const file = overlayFromArgv()
  if (!file) return
  let lastUrl
  let inFlight
  let timer
  let watcher
  let stopped = false
  const refresh = async () => {
    if (stopped || !existsSync(file)) return
    let target
    try { target = readTarget(file) } catch (error) {
      ctx.logger?.warn?.(`[mecha-mcp-refresh] 读取 overlay 失败：${error.message}`)
      return
    }
    if (target.url === lastUrl || inFlight === target.url) return
    let entry
    try { entry = ctx.loader?.resolve(target.id) } catch { return }
    const fiber = entry?.fiber
    if (!fiber?.uid) return
    inFlight = target.url
    try {
      await fiber.update({ ...(entry.options?.config ?? {}), url: target.url }, true)
      lastUrl = target.url
      ctx.logger?.info?.(`[mecha-mcp-refresh] 已重新初始化 ${target.id}（${target.url}）`)
    } catch (error) {
      ctx.logger?.error?.(`[mecha-mcp-refresh] 重初始化 ${target.id} 失败：${error.message}`)
    } finally {
      if (inFlight === target.url) inFlight = undefined
    }
  }
  const start = () => {
    timer = setInterval(refresh, 250)
    watcher = watch(file, { persistent: false }, () => void refresh())
    void refresh()
    return () => {
      stopped = true
      if (timer) clearInterval(timer)
      watcher?.close?.()
    }
  }
  if (typeof ctx.effect === 'function') {
    await ctx.effect(start, 'mecha-mcp-refresh: watch aggregate overlay')
  } else start()
}
