import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

// Polls compact run snapshots independently of the lifetime of a browser connection.
export default function useTaskRun(sessionId, token, onSnapshot, onError) {
  const latest = useRef({ onSnapshot, onError, sessionId })
  latest.current = { onSnapshot, onError, sessionId }
  const [revision, refresh] = useState(0)
  const sending = useRef(false)
  useEffect(() => {
    if (!sessionId) return
    let alive = true
    let timer
    let previous = ''
    async function poll() {
      try {
        const state = await api(`/api/sessions/${sessionId}/run`)
        if (!alive) return
        const signature = JSON.stringify(state)
        if (signature !== previous) {
          previous = signature
          latest.current.onSnapshot(state)
        }
        timer = setTimeout(poll, state.busy ? 650 : 2500)
      } catch (error) {
        if (!alive) return
        previous = ''
        latest.current.onError(`连接暂时不可用：${error.message}。正在重新连接…`)
        timer = setTimeout(poll, 2500)
      }
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [sessionId, token, revision])

  const action = useCallback(async (suffix, body) => {
    if (!sessionId) return
    try {
      const result = await api(`/api/sessions/${sessionId}/run${suffix}`, {
        method: 'POST', body: JSON.stringify(body || {}),
      })
      if (latest.current.sessionId === sessionId) refresh(n => n + 1)
      return result
    } catch (error) {
      if (latest.current.sessionId === sessionId) latest.current.onError(error.message)
      throw error
    }
  }, [sessionId])
  const send = useCallback(async content => {
    if (sending.current) return
    sending.current = true
    try { return await action('', { type: 'user_message', content }) }
    finally { sending.current = false }
  }, [action])
  return { send, stop: () => action('/stop'), approve: (id, decision) => action('/approval', { id, decision }) }
}
