import { Context } from 'koishi'
import { HTTP } from '@koishijs/plugin-http'
import OneBot from 'koishi-plugin-adapter-onebot'
import * as forwarder from './index'

function required(name: string): string {
  const value = process.env[name]?.trim()
  if (!value) throw new Error(`${name} is required in .env`)
  return value
}

const selfId = required('QQ_ACCOUNT_ID')
const bridgeToken = required('MESSAGE_INGEST_TOKEN')
const oneBotEndpoint = process.env.NAPCAT_ONEBOT_WS_URL?.trim() || 'ws://127.0.0.1:3001'
const oneBotToken = process.env.ONEBOT_ACCESS_TOKEN?.trim()

const app = new Context()
app.plugin(HTTP)
// Adapter bot classes are valid Koishi plugins at runtime, but the adapter's
// published declaration currently exposes its schema with an incompatible
// overload to Context.plugin(). Keep the cast local to that upstream mismatch.
app.plugin(OneBot as any, {
  selfId,
  protocol: 'ws',
  endpoint: oneBotEndpoint,
  token: oneBotToken || undefined,
})
app.plugin(forwarder, {
  token: bridgeToken,
  endpoint: 'http://127.0.0.1:8765/api/v1/messages',
  statusEndpoint: 'http://127.0.0.1:8765/api/v1/bridge/status',
  groupsEndpoint: 'http://127.0.0.1:8765/api/v1/bridge/groups',
  activityEndpoint: 'http://127.0.0.1:8765/api/v1/bridge/activity',
  retries: 3,
})

async function stop() {
  await app.stop()
  process.exit(0)
}

process.once('SIGINT', stop)
process.once('SIGTERM', stop)

app.start().catch((error) => {
  console.error(error)
  process.exit(1)
})
