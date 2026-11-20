const PREFIX = 'orangeDiary:pending:'
const MAX_PENDING_AGE_MS = 6 * 24 * 60 * 60 * 1000

function stableJson(value) {
  return JSON.stringify(value)
}

function load(operation) {
  try {
    const pending = JSON.parse(sessionStorage.getItem(`${PREFIX}${operation}`) || 'null')
    if (!pending || Date.now() - pending.createdAt > MAX_PENDING_AGE_MS) {
      sessionStorage.removeItem(`${PREFIX}${operation}`)
      return null
    }
    return pending
  } catch {
    sessionStorage.removeItem(`${PREFIX}${operation}`)
    return null
  }
}

function save(operation, pending) {
  // Persist before the HTTP call so a refresh after a lost response can reuse this requestId.
  sessionStorage.setItem(`${PREFIX}${operation}`, JSON.stringify(pending))
}

export function clearPendingSubmission(operation) {
  sessionStorage.removeItem(`${PREFIX}${operation}`)
}

/**
 * One unchanged user intent reuses one requestId and its original payload.
 * A changed intent creates a new id so legitimate repeated records remain possible.
 */
export async function submitIdempotently(operation, intent, payload, submit) {
  const intentJson = stableJson(intent)
  const saved = load(operation)
  const pending = saved?.intentJson === intentJson
    ? saved
    : { requestId: crypto.randomUUID(), intentJson, payload, createdAt: Date.now() }

  if (pending !== saved) save(operation, pending)

  try {
    const result = await submit(pending.payload, pending.requestId)
    clearPendingSubmission(operation)
    return result
  } catch (error) {
    // A corrected request is a new user intent. Transient failures retain the id for replay.
    if (error.status === 400 || (error.status === 409 && error.message?.includes('不一致'))) {
      clearPendingSubmission(operation)
    }
    throw error
  }
}
