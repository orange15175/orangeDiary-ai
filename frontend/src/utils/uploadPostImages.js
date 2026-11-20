// Bound parallel uploads and wait for active requests before allowing a retry.
// Successful uploads stay on each item so a retry only sends missing images.
export async function uploadPostImages(items, upload, concurrency = 3) {
  const pending = items.filter(item => !item.uploaded)
  let cursor = 0
  let failure
  async function worker() {
    while (!failure && cursor < pending.length) {
      const item = pending[cursor++]
      item.state = 'uploading'
      item.progress = 0
      try {
        item.uploaded = await upload(item.file, event => {
          const progress = event.progress ?? (event.total ? event.loaded / event.total : 0)
          item.progress = Math.min(99, Math.round(progress * 100))
        })
        item.state = 'uploaded'
        item.progress = 100
      } catch (error) {
        item.state = 'failed'
        failure ||= error
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, pending.length) }, worker))
  if (failure) throw failure
}
