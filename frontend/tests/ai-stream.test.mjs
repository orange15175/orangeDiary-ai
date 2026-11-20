import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

// 替换认证依赖，直接验证生产 SSE 解析代码，不连接真实服务。
const source = (await readFile(new URL('../src/api/ai.js', import.meta.url), 'utf8'))
  .replace(/^import .* from '.\/http'$/m, "const http = {}; const ensureAccessToken = async () => 'test-token'")
const { chatStream } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

function mockStream(text) {
  const bytes = new TextEncoder().encode(text)
  globalThis.fetch = async () => ({
    ok: true,
    body: new ReadableStream({
      start(controller) {
        // 每字节分包，覆盖中文 UTF-8 和 SSE 边界被拆开的情况。
        for (const byte of bytes) controller.enqueue(Uint8Array.of(byte))
        controller.close()
      },
    }),
  })
}

test('分包文字和确认卡在 done 后一起交给页面', async () => {
  const original = globalThis.fetch
  try {
    mockStream('event: message\r\ndata: 请确认\r\n\r\n' +
      'event: action\r\ndata: {"action":"confirm_exercise_record","exercisePreview":{"durationMin":"30"}}\r\n\r\n' +
      'event: done\r\ndata: conversation-id\r\n\r\n')
    let shown = ''
    const result = await chatStream({ message: '跑步30分钟', onDelta: text => { shown += text } })
    assert.equal(shown, '请确认')
    assert.equal(result.reply, shown)
    assert.equal(result.action, 'confirm_exercise_record')
    assert.equal(result.exercisePreview.durationMin, '30')
    assert.equal(result.finished, true)
  } finally { globalThis.fetch = original }
})

test('缺少 done 的断线不能作为成功回答', async () => {
  const original = globalThis.fetch
  try {
    mockStream('event: message\ndata: 部分回答\n\n')
    await assert.rejects(chatStream({ message: '测试' }), /连接中断/)
  } finally { globalThis.fetch = original }
})
