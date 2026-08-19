import { Context, Schema, Session, h } from 'koishi'

export const name = 'qq-group-summary-forwarder'

export interface Config {
  endpoint: string
  statusEndpoint: string
  token: string
  groupIds: string[]
  retries: number
}

export const Config: Schema<Config> = Schema.object({
  endpoint: Schema.string().default('http://127.0.0.1:8765/api/v1/messages'),
  statusEndpoint: Schema.string().default('http://127.0.0.1:8765/api/v1/bridge/status'),
  token: Schema.string().role('secret').required(),
  groupIds: Schema.array(String).default([]),
  retries: Schema.number().min(0).max(5).default(3),
})

function segmentsOf(session: Session) {
  const elements = session.elements || h.parse(session.content || '')
  return elements.map((element) => {
    const attrs = element.attrs || {}
    if (element.type === 'text') {
      return { type: 'text', data: { text: String(attrs.content || '') } }
    }
    if (element.type === 'at') {
      return { type: 'at', data: { id: String(attrs.id || ''), name: String(attrs.name || '') } }
    }
    if (element.type === 'a') {
      return { type: 'link', data: { url: String(attrs.href || ''), title: String(attrs.content || '链接') } }
    }
    if (element.type === 'file') {
      return { type: 'file', data: { name: String(attrs.name || attrs.file || '未命名文件') } }
    }
    if (element.type === 'img') {
      return { type: 'image', data: {} }
    }
    return {
      type: element.type,
      data: Object.fromEntries(Object.entries(attrs).map(([key, value]) => [key, String(value)])),
    }
  })
}

function payloadOf(session: Session) {
  return {
    qq_message_id: String(session.messageId || session.event.message?.id || ''),
    qq_group_id: String(session.guildId || ''),
    sender_id: String(session.userId || session.event.user?.id || ''),
    sender_name: String(session.username || session.event.user?.name || session.userId || '未知成员'),
    sent_at: new Date(session.timestamp || Date.now()).toISOString(),
    received_at: new Date().toISOString(),
    segments: segmentsOf(session),
  }
}

export function apply(ctx: Context, config: Config) {
  const allowed = new Set(config.groupIds.map(String))
  const postStatus = async () => {
    const bot = ctx.bots.find((item) => item.status === 1)
    try {
      await ctx.http.post(config.statusEndpoint, {
        connected: Boolean(bot),
        qq_id: String(bot?.user?.id || bot?.selfId || ''),
        nickname: String(bot?.user?.name || ''),
        platform: String(bot?.platform || 'onebot'),
      }, { headers: { Authorization: `Bearer ${config.token}` }, timeout: 10_000 })
    } catch (error) {
      ctx.logger(name).warn('Could not update QQ connection status: %s', error)
    }
  }
  ctx.on('ready', postStatus)
  ctx.setInterval(postStatus, 30_000)
  ctx.on('message', async (session) => {
    if (!session.guildId || !allowed.has(String(session.guildId))) return
    const payload = payloadOf(session)
    if (!payload.qq_message_id || !payload.sender_id || !payload.segments.length) {
      ctx.logger(name).warn('忽略缺少必要字段的群消息 group=%s', session.guildId)
      return
    }
    for (let attempt = 0; attempt <= config.retries; attempt += 1) {
      try {
        await ctx.http.post(config.endpoint, payload, {
          headers: { Authorization: `Bearer ${config.token}` },
          timeout: 10_000,
        })
        return
      } catch (error) {
        if (attempt === config.retries) {
          ctx.logger(name).error('消息转发失败 group=%s message=%s error=%s', session.guildId, payload.qq_message_id, error)
          return
        }
        await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt))
      }
    }
  })
}
