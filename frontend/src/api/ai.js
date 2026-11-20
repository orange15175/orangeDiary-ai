import http, { ensureAccessToken } from './http'

// 历史由服务端按登录账号维护，浏览器不参与拼接上下文。
export const chatWithAi = ({ message }) => http.post('/ai/chat', { message }, { timeout: 180_000 })

// 服务端持有完整历史；刷新/换标签后从这里恢复界面，不再只依赖 sessionStorage。
export const fetchAiHistory = (limit = 40) =>
  http.get('/ai/conversation/messages', { params: { limit }, timeout: 10_000 })

/**
 * 流式聊天：SSE over fetch。
 *
 * 不用 EventSource 的原因：它不能设置请求头，JWT 只能塞进 query，等于把凭证写进访问日志。
 * 也不用 axios：浏览器端拿不到增量流，只能等整个响应结束。
 *
 * @returns {Promise<{reply: string, finished: boolean}>}
 */
export async function chatStream({ message, signal, onDelta, onSources, onKnowledge, onDone }) {
  const token = await ensureAccessToken()
  const response = await fetch('/api/ai/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message }),
    signal,
  })

  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = body?.message || ''
    } catch {
      // 非 JSON 错误体（网关超时页）没有可展示信息，走下面的兜底文案
    }
    const error = new Error(
      detail || (response.status === 429 ? '请求太频繁或额度已用完，请稍后再试' : 'AI 服务暂时不可用'),
    )
    error.status = response.status
    throw error
  }
  if (!response.body) throw new Error('当前浏览器不支持流式响应')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let reply = ''
  let finished = false
  let actionResult = {}

  const dispatch = (frame) => {
    let event = 'message'
    const dataLines = []
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
    }
    const data = dataLines.join('\n')
    if (event === 'message') {
      reply += data
      onDelta?.(data)
    } else if (event === 'sources') {
      try { onSources?.(JSON.parse(data)) } catch { /* 引用解析失败不影响正文 */ }
    } else if (event === 'knowledge') {
      try { onKnowledge?.(JSON.parse(data)?.status) } catch { /* 同上 */ }
    } else if (event === 'action') {
      const result = JSON.parse(data)
      if (result && typeof result === 'object') actionResult = result
    } else if (event === 'done') {
      finished = true
      onDone?.(data)
    } else if (event === 'error') {
      throw new Error(data || 'AI 服务调用失败')
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r\n/g, '\n')
    let index = buffer.indexOf('\n\n')
    while (index >= 0) {
      const frame = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      if (frame.trim()) dispatch(frame.replace(/\r\n/g, '\n'))
      index = buffer.indexOf('\n\n')
    }
  }
  if (buffer.trim()) dispatch(buffer.replace(/\r\n/g, '\n'))
  if (!finished) throw new Error('回复连接中断，请刷新查看会话后重试')
  return { ...actionResult, reply, finished }
}
