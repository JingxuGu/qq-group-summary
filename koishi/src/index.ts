import { Context, Schema, Session, h } from 'koishi'

export const name = 'qq-group-summary-forwarder'

export interface Config {
  endpoint: string
  statusEndpoint: string
  groupsEndpoint: string
  activityEndpoint: string
  token: string
  retries: number
}

export const Config: Schema<Config> = Schema.object({
  endpoint: Schema.string().default('http://127.0.0.1:8765/api/v1/messages'),
  statusEndpoint: Schema.string().default('http://127.0.0.1:8765/api/v1/bridge/status'),
  groupsEndpoint: Schema.string().default('http://127.0.0.1:8765/api/v1/bridge/groups'),
  activityEndpoint: Schema.string().default('http://127.0.0.1:8765/api/v1/bridge/activity'),
  token: Schema.string().role('secret').required(),
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
  const headers = { Authorization: `Bearer ${config.token}` }

  const syncBridge = async () => {
    const bot = ctx.bots.find((item) => item.status === 1)
    try {
      await ctx.http.post(config.statusEndpoint, {
        connected: Boolean(bot),
        qq_id: String(bot?.user?.id || bot?.selfId || ''),
        nickname: String(bot?.user?.name || ''),
        platform: String(bot?.platform || 'onebot'),
      }, { headers, timeout: 10_000 })
      if (!bot) return
      const groups: Array<{ qq_group_id: string, name: string }> = []
      let next: string | undefined
      do {
        const page = await bot.getGuildList(next)
        groups.push(...page.data.map((guild) => ({
          qq_group_id: String(guild.id),
          name: String(guild.name || guild.id),
        })))
        next = page.next
      } while (next)
      await ctx.http.post(config.groupsEndpoint, { groups }, { headers, timeout: 15_000 })
    } catch (error) {
      ctx.logger(name).warn('Could not synchronize QQ account and group list: %s', error)
    }
  }
  ctx.on('ready', syncBridge)
  ctx.setInterval(syncBridge, 30_000)
  ctx.on('message', async (session) => {
    if (!session.guildId) return
    const qqGroupId = String(session.guildId)
    const groupName = String(session.event.guild?.name || qqGroupId)
    let selected = false
    try {
      const activity = await ctx.http.post<{ selected: boolean }>(config.activityEndpoint, {
        qq_group_id: qqGroupId,
        name: groupName,
        occurred_at: new Date(session.timestamp || Date.now()).toISOString(),
      }, { headers, timeout: 10_000 })
      selected = Boolean(activity.selected)
    } catch (error) {
      ctx.logger(name).warn('Could not update group activity group=%s error=%s', qqGroupId, error)
      return
    }
    if (!selected) return
    const payload = payloadOf(session)
    if (!payload.qq_message_id || !payload.sender_id || !payload.segments.length) {
      ctx.logger(name).warn('忽略缺少必要字段的群消息 group=%s', session.guildId)
      return
    }
    for (let attempt = 0; attempt <= config.retries; attempt += 1) {
      try {
        await ctx.http.post(config.endpoint, payload, {
          headers,
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
