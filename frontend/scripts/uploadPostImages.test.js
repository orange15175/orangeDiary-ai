import { test } from 'node:test'
import assert from 'node:assert/strict'
import { uploadPostImages } from '../src/utils/uploadPostImages.js'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))

test('uploads three images at a time, preserves order and skips completed images', async () => {
  const items = Array.from({ length: 9 }, (_, file) => ({ file }))
  items[0].uploaded = { key: 'existing' }
  const requests = [], active = new Set()
  let peak = 0
  const running = uploadPostImages(items, async (file, progress) => {
    const request = deferred()
    requests.push({ file, ...request }); active.add(file); peak = Math.max(peak, active.size)
    progress({ progress: 0.5 })
    await request.promise
    active.delete(file)
    return { key: `image-${file}` }
  })
  assert.equal(requests.length, 3)
  assert.equal(items[1].progress, 50)
  while (requests.length < 8 || active.size) {
    requests.filter(request => active.has(request.file)).forEach(request => request.resolve())
    await tick()
  }
  await running
  assert.equal(peak, 3)
  assert.equal(requests.length, 8)
  assert.deepEqual(items.map(item => item.uploaded.key), ['existing', ...Array.from({ length: 8 }, (_, i) => `image-${i + 1}`)])
})

test('on failure waits for active requests, then retry only uploads missing images', async () => {
  const items = Array.from({ length: 5 }, (_, file) => ({ file }))
  const requests = []
  let finished = false
  const failure = new Error('timeout')
  const running = uploadPostImages(items, file => {
    const request = deferred(); requests.push({ file, ...request }); return request.promise
  }).then(() => { throw new Error('expected upload failure') }, error => { assert.equal(error, failure); finished = true })
  requests[0].reject(failure)
  await tick()
  assert.equal(finished, false)
  assert.equal(requests.length, 3)
  requests[1].resolve({ key: 'one' }); requests[2].resolve({ key: 'two' })
  await running
  assert.equal(items[0].state, 'failed')
  const retried = []
  await uploadPostImages(items, async file => { retried.push(file); return { key: `retry-${file}` } })
  assert.deepEqual(retried, [0, 3, 4])
  assert.equal(items[1].uploaded.key, 'one')
  assert.equal(items[2].uploaded.key, 'two')
})
