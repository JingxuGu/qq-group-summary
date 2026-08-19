const $ = (id) => document.getElementById(id)
const typeLabels = { course: 'Course', academic: 'Academic', casual: 'Casual' }
let messageOffset = 0
const messagePageSize = 50

function formatTime(value) {
  if (!value) return 'No messages yet'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Time unavailable'
  return new Intl.DateTimeFormat('en-GB', { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
function empty(container, text) { container.replaceChildren(); const node = document.createElement('p'); node.className = 'empty-state'; node.textContent = text; container.append(node) }

function renderGroups(groups) {
  const list = $('group-list'); list.replaceChildren(); $('group-count').textContent = `${groups.length} ${groups.length === 1 ? 'group' : 'groups'}`
  if (!groups.length) return empty(list, 'No groups are enabled. Ask the developer to add groups in config.yaml.')
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
  try { const response = await fetch('/api/v1/dashboard', { cache: 'no-store' }); if (!response.ok) throw new Error(); const data = await response.json(); $('metric-received').textContent = data.metrics.received_24h; $('metric-pending').textContent = data.metrics.pending_messages; $('metric-summaries').textContent = data.metrics.summaries_24h; $('metric-delivery').textContent = data.deliveries[0]?.status === 'sent' ? 'Sent' : data.deliveries[0]?.status === 'failed' ? 'Failed' : 'None'; $('pending-note').textContent = `${data.metrics.pending_notifications} ${data.metrics.pending_notifications === 1 ? 'notice' : 'notices'} pending`; $('last-refresh').textContent = `Updated ${new Date().toLocaleTimeString('en-GB', { hour12: false })}`; renderGroups(data.groups); renderDeliveries(data.deliveries); state.classList.add('online'); state.lastElementChild.textContent = 'Service online' }
  catch (_) { state.classList.remove('online'); state.lastElementChild.textContent = 'Status unavailable'; $('last-refresh').textContent = 'Connection failed. Check the Python service.' }
}

function addSummaryLine(host, label, value) { if (!value || (Array.isArray(value) && !value.length)) return; const section = document.createElement('section'); const title = document.createElement('strong'); title.textContent = label; section.append(title); const values = Array.isArray(value) ? value : [value]; for (const item of values) { const p = document.createElement('p'); p.textContent = typeof item === 'object' ? `${item.member}: ${item.view}` : item; section.append(p) } host.append(section) }
function renderSummaries(items) {
  const list = $('summary-list'); list.replaceChildren(); if (!items.length) return empty(list, 'No summaries yet. New summaries will appear after a group reaches its configured threshold.')
  for (const item of items) { const card = $('summary-template').content.firstElementChild.cloneNode(true); card.querySelector('.summary-type').textContent = typeLabels[item.group_type] || item.group_type; card.querySelector('.summary-type').classList.add(item.group_type); card.querySelector('h3').textContent = item.group_name; card.querySelector('time').textContent = formatTime(item.ended_at); const content = card.querySelector('.summary-content'); const data = item.summary
    if (item.group_type === 'course') { addSummaryLine(content, 'Q&A summary', data.qa_summary); for (const notice of data.notifications || []) addSummaryLine(content, notice.title, notice.update_text ? [notice.original_text, `Update: ${notice.update_text}`] : notice.original_text) }
    if (item.group_type === 'academic') { addSummaryLine(content, 'Overview', data.overview); addSummaryLine(content, 'Member views', data.member_views); addSummaryLine(content, 'Disagreements', data.disagreements); addSummaryLine(content, 'Consensus', data.consensus); addSummaryLine(content, 'Open questions', data.unresolved_questions) }
    if (item.group_type === 'casual') { addSummaryLine(content, 'Overview', data.overview); addSummaryLine(content, 'Worth noting', data.noteworthy); addSummaryLine(content, 'Plans', data.plans); addSummaryLine(content, 'Resources', data.resources) }
    const footer = card.querySelector('footer'); footer.textContent = item.knowledge_tags?.length ? item.knowledge_tags.join(' · ') : `${item.message_count} source messages`; list.append(card) }
}
async function loadSummaries() { const type = $('summary-filter').value; const query = type ? `?group_type=${encodeURIComponent(type)}` : ''; const response = await fetch(`/api/v1/summaries${query}`, { cache: 'no-store' }); const data = await response.json(); renderSummaries(data.items || []) }

async function loadMessages(reset = false) { if (reset) messageOffset = 0; const q = $('message-query').value.trim(); const params = new URLSearchParams({ limit: String(messagePageSize), offset: String(messageOffset) }); if (q) params.set('q', q); const response = await fetch(`/api/v1/messages?${params}`, { cache: 'no-store' }); const data = await response.json(); const body = $('message-list'); body.replaceChildren(); for (const item of data.items) { const row = document.createElement('tr'); for (const value of [formatTime(item.sent_at), item.group_name, item.sender_name, item.text || `[${item.message_type}]`, item.summary_batch_id ? 'Summarized' : item.is_noise ? 'Filtered' : 'Pending']) { const cell = document.createElement('td'); cell.textContent = value; row.append(cell) } body.append(row) } if (!data.items.length) { const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 5; cell.className = 'empty-state'; cell.textContent = 'No matching messages.'; row.append(cell); body.append(row) } $('message-count').textContent = `${data.total} ${data.total === 1 ? 'message' : 'messages'}`; $('messages-prev').disabled = messageOffset === 0; $('messages-next').disabled = messageOffset + messagePageSize >= data.total }

async function loadSubscription() { const response = await fetch('/api/v1/subscription', { cache: 'no-store' }); const data = await response.json(); $('subscription-email').value = data.email; $('subscription-time').value = data.daily_time }
async function loadQQStatus() { const response = await fetch('/api/v1/qq/status', { cache: 'no-store' }); const data = await response.json(); $('qq-login-link').href = data.login_url; $('qq-signal').classList.toggle('connected', data.connected); $('qq-title').textContent = data.connected ? `${data.nickname || 'QQ account'} is connected` : 'QQ is not connected'; $('qq-description').textContent = data.connected ? `Account ${data.qq_id || 'unknown'} is online through ${data.platform}.` : 'Open the local NapCat login page, scan the QR code with QQ, then return here. Connection status refreshes automatically.'; $('qq-login-link').textContent = data.connected ? 'Open NapCat account page' : 'Open QQ login' }

document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', async () => { document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item === button)); document.querySelectorAll('.view').forEach((view) => { const active = view.id === `view-${button.dataset.view}`; view.hidden = !active; view.classList.toggle('active-view', active) }); if (button.dataset.view === 'summaries') await loadSummaries(); if (button.dataset.view === 'messages') await loadMessages(true); if (button.dataset.view === 'email') await loadSubscription(); if (button.dataset.view === 'qq') await loadQQStatus() }))
$('summary-filter').addEventListener('change', loadSummaries); $('message-search').addEventListener('submit', (event) => { event.preventDefault(); loadMessages(true) }); $('messages-prev').addEventListener('click', () => { messageOffset = Math.max(0, messageOffset - messagePageSize); loadMessages() }); $('messages-next').addEventListener('click', () => { messageOffset += messagePageSize; loadMessages() })
$('email-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = event.submitter; button.disabled = true; $('email-message').textContent = 'Saving…'; try { const response = await fetch('/api/v1/subscription', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: $('subscription-email').value.trim(), daily_time: $('subscription-time').value }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Could not save subscription'); $('email-message').textContent = 'Subscription updated.' } catch (error) { $('email-message').textContent = error.message } finally { button.disabled = false } })
$('qq-refresh').addEventListener('click', loadQQStatus)
function tick() { $('clock').textContent = new Date().toLocaleTimeString('en-GB', { hour12: false }) }
tick(); refreshDashboard(); setInterval(tick, 1000); setInterval(refreshDashboard, 30000); setInterval(() => { if (!$('view-qq').hidden) loadQQStatus() }, 30000)
