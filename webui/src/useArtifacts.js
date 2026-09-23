import { useEffect, useState } from 'react'
import { api } from './api'

export default function useArtifacts(sessionId, busy, revision) {
  const [state, setState] = useState({ sessionId: null, files: [], error: '', warnings: [] })
  useEffect(() => {
    if (!sessionId) return
    let alive = true
    let timer
    async function load() {
      try {
        const data = await api(`/api/sessions/${sessionId}/artifacts`)
        if (alive) setState({ sessionId, files: data.artifacts, error: '', warnings: data.warnings || [] })
      } catch (error) {
        if (alive) setState(previous => ({
          sessionId,
          files: previous.sessionId === sessionId ? previous.files : [],
          error: error.message,
          warnings: previous.sessionId === sessionId ? previous.warnings : [],
        }))
      }
      if (alive) timer = setTimeout(load, busy ? 1000 : 5000)
    }
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [sessionId, busy, revision])
  return state.sessionId === sessionId ? state : { files: [], error: '', warnings: [] }
}
