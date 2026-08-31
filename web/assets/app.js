const $ = (id) => document.getElementById(id)
const typeLabels = { course: 'Course', academic: 'Academic', casual: 'Casual' }
let qqGroupsLoaded = false

function formatTime(value) {
  if (!value) return 'No messages yet'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Time unavailable'
  return new Intl.DateTimeFormat('en-GB', { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
function empty(container, text) { container.replaceChildren(); const node = document.createElement('p'); node.className = 'empty-state'; node.textContent = text; container.append(node) }

function renderGroups(groups) {
  const list = $('group-list'); list.replaceChildren(); $('group-count').textContent = `${groups.length} ${groups.length === 1 ? 'group' : 'groups'}`
  if (!groups.length) return empty(list, 'No groups selected yet. Choose groups from Connect QQ.')
  for (const group of groups) {
    const row = $('group-template').content.firstElementChild.cloneNode(true); const type = row.querySelector('.group-type'); type.classList.add(group.type); type.title = typeLabels[group.type] || group.type
    row.querySelector('.group-main strong').textContent = group.name; row.querySelector('.group-main small').textContent = `${typeLabels[group.type] || group.type} · Last message ${formatTime(group.last_message_at)}`; row.querySelector('.group-number strong').textContent = group.messages_24h
    const pending = row.querySelector('.group-pending'); pending.textContent = group.pending ? `${group.pending} waiting` : 'Up to date'; pending.classList.toggle('has-work', group.pending > 0); list.append(row)
  }
}
function renderDeliveries(deliveries) {
  const list = $('delivery-list'); list.replaceChildren(); if (!deliveries.length) return empty(list, 'No delivery records yet. Your first digest will appear here.')
  for (const delivery of deliveries) { const row = $('delivery-template').content.firstElementChild.cloneNode(true); row.querySelector('.delivery-status').classList.add(delivery.status); row.querySelector('strong').textContent = delivery.email_subject; row.querySelector('small').textContent = delivery.status === 'sent' ? `Sent · ${formatTime(delivery.sent_at)}` : delivery.status === 'failed' ? `Failed · ${delivery.error_message || 'Check logs'}` : 'Preparing'; list.append(row) }
}
async function refreshDashboard() {
  const state = $('service-state')
  try { const response = await fetch('/api/v1/dashboard', { cache: 'no-store' }); if (!response.ok) throw new Error(); const data = await response.json(); $('metric-received').textContent = data.metrics.received_24h; $('metric-pending').textContent = data.metrics.pending_messages; $('metric-summaries').textContent = data.metrics.summaries_24h; $('metric-delivery').textContent = data.deliveries[0]?.status === 'sent' ? 'Sent' : data.deliveries[0]?.status === 'failed' ? 'Failed' : 'None'; $('pending-note').textContent = `${data.metrics.pending_notifications} ${data.metrics.pending_notifications === 1 ? 'notice' : 'notices'} pending`; $('last-refresh').textContent = `Updated ${new Date().toLocaleTimeString('en-GB', { hour12: false })}`; renderGroups(data.groups); renderDeliveries(data.deliveries); state.classList.add('online'); state.querySelector('strong').textContent = 'Service online' }
  catch (_) { state.classList.remove('online'); state.querySelector('strong').textContent = 'Status unavailable'; $('last-refresh').textContent = 'Connection failed. Check the Python service.' }
}

function addSummaryLine(host, label, value) { if (!value || (Array.isArray(value) && !value.length)) return; const section = document.createElement('section'); const title = document.createElement('strong'); title.textContent = label; section.append(title); const values = Array.isArray(value) ? value : [value]; for (const item of values) { const p = document.createElement('p'); p.textContent = typeof item === 'object' ? `${item.member}: ${item.view}` : item; section.append(p) } host.append(section) }
function renderSummaries(items) {
  const list = $('summary-list'); list.replaceChildren(); if (!items.length) return empty(list, 'No summaries yet. New summaries will appear after a group reaches its configured threshold.')
  for (const item of items) { const card = $('summary-template').content.firstElementChild.cloneNode(true); card.querySelector('.summary-type').textContent = typeLabels[item.group_type] || item.group_type; card.querySelector('.summary-type').classList.add(item.group_type); card.querySelector('h2').textContent = item.group_name; card.querySelector('time').textContent = formatTime(item.ended_at); const content = card.querySelector('.summary-content'); const data = item.summary
    if (item.group_type === 'course') { addSummaryLine(content, 'Q&A summary', data.qa_summary); for (const notice of data.notifications || []) addSummaryLine(content, notice.title, notice.update_text ? [notice.original_text, `Update: ${notice.update_text}`] : notice.original_text) }
    if (item.group_type === 'academic') { addSummaryLine(content, 'Overview', data.overview); addSummaryLine(content, 'Member views', data.member_views); addSummaryLine(content, 'Disagreements', data.disagreements); addSummaryLine(content, 'Consensus', data.consensus); addSummaryLine(content, 'Open questions', data.unresolved_questions) }
    if (item.group_type === 'casual') { addSummaryLine(content, 'Overview', data.overview); addSummaryLine(content, 'Worth noting', data.noteworthy); addSummaryLine(content, 'Plans', data.plans); addSummaryLine(content, 'Resources', data.resources) }
    const footer = card.querySelector('footer'); footer.textContent = item.knowledge_tags?.length ? item.knowledge_tags.join(' · ') : `${item.message_count} source messages`; list.append(card) }
}
async function loadSummaries() { const type = $('summary-filter').value; const query = type ? `?group_type=${encodeURIComponent(type)}` : ''; const response = await fetch(`/api/v1/summaries${query}`, { cache: 'no-store' }); const data = await response.json(); renderSummaries(data.items || []) }

function renderMessageGroup(group, history) {
  const card = $('message-group-template').content.firstElementChild.cloneNode(true)
  const type = card.querySelector('.message-group-type'); type.textContent = typeLabels[group.type] || group.type; type.classList.add(group.type)
  card.querySelector('h2').textContent = group.name; card.querySelector('.message-group-count strong').textContent = group.message_count
  const scroll = card.querySelector('.message-scroll'); scroll.setAttribute('aria-label', `${group.name} message history`)
  const thread = card.querySelector('.message-thread')
  for (const item of history.items) {
    const row = document.createElement('li'); row.className = 'message-entry'
    const rail = document.createElement('time'); rail.dateTime = item.sent_at; rail.textContent = formatTime(item.sent_at)
    const content = document.createElement('div'); content.className = 'message-entry-content'
    const meta = document.createElement('div'); meta.className = 'message-entry-meta'
    const sender = document.createElement('strong'); sender.textContent = item.sender_name
    const state = document.createElement('span'); state.textContent = item.summary_batch_id ? 'Summarized' : item.is_noise ? 'Filtered' : 'Pending'; state.className = `message-state ${state.textContent.toLowerCase()}`
    const body = document.createElement('p'); body.textContent = item.text || item.attachment_title || `[${item.message_type}]`
    meta.append(sender, state); content.append(meta, body); row.append(rail, content); thread.append(row)
  }
  card.querySelector('footer').textContent = history.total > history.items.length ? `Showing the newest ${history.items.length} of ${history.total} messages` : `Complete saved history · ${history.total} messages`
  return card
}
async function loadMessages() {
  const board = $('message-groups'); const q = $('message-query').value.trim(); empty(board, 'Loading message history…')
  try {
    const groupParams = new URLSearchParams(); if (q) groupParams.set('q', q)
    const groupResponse = await fetch(`/api/v1/messages/groups?${groupParams}`, { cache: 'no-store' }); if (!groupResponse.ok) throw new Error('Could not load message groups')
    const groups = (await groupResponse.json()).items || []
    const histories = await Promise.all(groups.map(async (group) => { const params = new URLSearchParams({ group_id: group.qq_group_id, limit: '200', offset: '0' }); if (q) params.set('q', q); const response = await fetch(`/api/v1/messages?${params}`, { cache: 'no-store' }); if (!response.ok) throw new Error(`Could not load ${group.name}`); return response.json() }))
    board.replaceChildren(); const total = groups.reduce((sum, group) => sum + group.message_count, 0); $('message-count').textContent = `${total} ${total === 1 ? 'message' : 'messages'}`
    if (!groups.length) return empty(board, q ? 'No messages match this search.' : 'No saved messages yet. Select a group and let new messages arrive.')
    groups.forEach((group, index) => board.append(renderMessageGroup(group, histories[index])))
  } catch (error) { $('message-count').textContent = 'Unavailable'; empty(board, error.message) }
}

async function loadSubscription() { const response = await fetch('/api/v1/subscription', { cache: 'no-store' }); const data = await response.json(); $('subscription-email').value = data.email; $('subscription-time').value = data.daily_time }
function renderQQGroups(groups) {
  const list = $('qq-group-list'); list.replaceChildren(); $('qq-group-count').textContent = `${groups.length} ${groups.length === 1 ? 'group' : 'groups'}`
  if (!groups.length) return empty(list, 'No groups found yet. Keep Koishi connected while the group list synchronizes.')
  groups.forEach((group) => {
    const row = $('qq-group-template').content.firstElementChild.cloneNode(true)
    const enabled = row.querySelector('.group-enabled'); const select = row.querySelector('select')
    row.dataset.groupId = group.qq_group_id; enabled.checked = group.enabled; select.value = group.type; select.disabled = !group.enabled
    row.classList.toggle('selected', group.enabled); row.classList.toggle('recent', Boolean(group.last_message_at))
    row.querySelector('.group-choice strong').textContent = group.name
    row.querySelector('.group-choice small').textContent = `QQ group ${group.qq_group_id}`
    row.querySelector('time').textContent = group.last_message_at ? `Latest message ${formatTime(group.last_message_at)}` : 'No message observed yet'
    enabled.addEventListener('change', () => { select.disabled = !enabled.checked; row.classList.toggle('selected', enabled.checked) })
    list.append(row)
  })
}
async function loadQQGroups() {
  const response = await fetch('/api/v1/qq/groups', { cache: 'no-store' }); if (!response.ok) throw new Error('Could not load QQ groups')
  const data = await response.json(); renderQQGroups(data.items || []); qqGroupsLoaded = true
}
function renderQQLogin(login, connected) {
  const ticket = $('qq-qr-ticket'); const signal = $('qq-signal'); const image = $('qq-qr-image'); const button = $('qq-connect')
  signal.hidden = !connected && Boolean(login.qr_code); ticket.hidden = connected || !login.qr_code
  if (login.qr_code) image.src = login.qr_code; else image.removeAttribute('src')
  button.hidden = connected; button.textContent = login.qr_code ? 'Refresh login QR' : 'Show login QR'
  $('qq-login-message').textContent = connected ? 'Connection stays active on this server.' : login.error || (login.qr_code ? 'Open QQ on your phone and scan this code.' : '')
}
async function loadQQLogin(refresh = false) {
  const response = await fetch('/api/v1/qq/login' + (refresh ? '/refresh' : ''), { method: refresh ? 'POST' : 'GET', cache: 'no-store' })
  const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Could not start QQ login')
  return data
}
async function loadQQStatus() {
  const response = await fetch('/api/v1/qq/status', { cache: 'no-store' }); const data = await response.json()
  let login = { available: false, logged_in: false, qr_code: '', error: '' }
  try { login = await loadQQLogin() } catch (error) { login.error = error.message }
  const connected = Boolean(data.connected || login.logged_in)
  $('qq-signal').classList.toggle('connected', connected); $('qq-title').textContent = connected ? `${data.nickname || 'Your QQ account'} is connected` : 'Ready to connect'; $('qq-description').textContent = connected ? 'Choose which groups should become part of your daily digest.' : 'Scan once with the QQ mobile app. This server will stay online and collect messages only from groups you choose.'; $('qq-groups-form').hidden = !connected
  renderQQLogin(login, connected)
  if (connected && !qqGroupsLoaded) await loadQQGroups(); if (!connected) qqGroupsLoaded = false
}

document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', async () => { document.querySelectorAll('.nav-item').forEach((item) => { const active = item === button; item.classList.toggle('active', active); if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current') }); document.querySelectorAll('.view').forEach((view) => { const active = view.id === `view-${button.dataset.view}`; view.hidden = !active; view.classList.toggle('active-view', active) }); if (button.dataset.view === 'summaries') await loadSummaries(); if (button.dataset.view === 'messages') await loadMessages(); if (button.dataset.view === 'email') await loadSubscription(); if (button.dataset.view === 'qq') await loadQQStatus() }))
$('summary-filter').addEventListener('change', loadSummaries); $('message-search').addEventListener('submit', (event) => { event.preventDefault(); loadMessages() })
$('summary-run').addEventListener('click', async (event) => { const button = event.currentTarget; const message = $('summary-run-message'); button.disabled = true; button.textContent = 'Summarizing…'; message.textContent = 'Processing pending messages'; try { const response = await fetch('/api/v1/summaries/run', { method: 'POST' }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Could not generate summaries'); message.textContent = data.created ? `${data.created} ${data.created === 1 ? 'summary' : 'summaries'} created.` : 'Nothing new to summarize.'; await loadSummaries(); await refreshDashboard() } catch (error) { message.textContent = error.message } finally { button.disabled = false; button.textContent = 'Summarize now' } })
$('email-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = event.submitter; button.disabled = true; $('email-message').textContent = 'Sending confirmation…'; try { const response = await fetch('/api/v1/subscription', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: $('subscription-email').value.trim(), daily_time: $('subscription-time').value }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Could not save delivery'); $('email-message').textContent = 'Delivery saved. Check your inbox for the confirmation.' } catch (error) { $('email-message').textContent = error.message } finally { button.disabled = false } })
$('qq-groups-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = event.submitter; button.disabled = true; $('qq-groups-message').textContent = 'Saving…'; const groups = [...document.querySelectorAll('.qq-group-option')].filter((row) => row.querySelector('.group-enabled').checked).map((row) => ({ qq_group_id: row.dataset.groupId, type: row.querySelector('select').value })); try { const response = await fetch('/api/v1/qq/groups', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ groups }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Could not save group choices'); $('qq-groups-message').textContent = `${data.selected} ${data.selected === 1 ? 'group' : 'groups'} selected.`; await refreshDashboard() } catch (error) { $('qq-groups-message').textContent = error.message } finally { button.disabled = false } })
$('qq-refresh').addEventListener('click', async () => { qqGroupsLoaded = false; await loadQQStatus() })
$('qq-connect').addEventListener('click', async (event) => { const button = event.currentTarget; button.disabled = true; $('qq-login-message').textContent = 'Preparing a secure login code…'; try { const login = await loadQQLogin(true); renderQQLogin(login, false) } catch (error) { $('qq-login-message').textContent = error.message } finally { button.disabled = false } })
function tick() { $('clock').textContent = new Date().toLocaleTimeString('en-GB', { hour12: false }) }
tick(); refreshDashboard(); setInterval(tick, 1000); setInterval(refreshDashboard, 30000); setInterval(() => { if (!$('view-qq').hidden) loadQQStatus() }, 3000)
