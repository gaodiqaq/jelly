import React, { useEffect, useState } from 'react'
import { api } from './api'

export default function Changes({ sessionId, busy, onOpenFile, onRestored }) {
  const [changes, setChanges] = useState([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const [confirm, setConfirm] = useState(null)
  const [restoring, setRestoring] = useState(false)
  useEffect(() => {
    setChanges([]); setError(''); setConfirm(null)
    if (!sessionId) return
    let alive = true
    let timer
    async function load() {
      setLoading(true)
      try {
        const data = await api(`/api/sessions/${sessionId}/changes`)
        if (alive) { setChanges(data.changes); setError('') }
      } catch (e) { if (alive) setError(e.message) }
      finally { if (alive) { setLoading(false); if (busy) timer = setTimeout(load, 1800) } }
    }
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [sessionId, busy, revision])

  async function restore(change) {
    setRestoring(true); setError('')
    try {
      await api(`/api/sessions/${sessionId}/changes/${change.id}/restore`, { method: 'POST' })
      setConfirm(null); setRevision(n => n + 1); onRestored()
    } catch (e) { setError(e.message) }
    finally { setRestoring(false) }
  }
  return <div className="changes-view">
    <div className="studio-eyebrow">VERSION HISTORY</div>
    <h2>每一步，都有迹可循。</h2>
    <p className="studio-muted">记录文件工具的修改前后版本。命令行及外部操作不包含在恢复范围内。</p>
    {error && <div role="alert" className="error-banner">{error}</div>}
    {!changes.length && <div className="studio-empty"><span>◈</span><h3>{loading ? '读取版本…' : '等待第一份成果'}</h3><p>让果冻创建或修改一个文件，版本和差异将出现在这里。</p></div>}
    {changes.map(change => <article className="change-card" key={change.id}>
      <div className="change-heading"><button onClick={() => onOpenFile(change.path)}>{change.path}</button><span>{change.kind === 'created' ? '新建' : '修改'}</span></div>
      <div className="studio-muted">{new Date(change.created_at).toLocaleString()} · {({ ready: '版本已保存', restored: '已恢复', pending: '快照待完成', failed: '执行失败', snapshot_failed: '文件已处理，版本未保存' })[change.state]}</div>
      <details><summary>查看修改差异</summary><pre className="change-diff">{change.diff || '没有可显示的文本差异'}</pre></details>
      {change.state === 'ready' && (confirm === change.id ? <div className="restore-confirm"><p>{change.kind === 'created' ? '恢复将删除本次新建的文件。' : '恢复到这次修改之前的内容。'}如果文件已有后续修改，将拒绝覆盖。</p><button disabled={busy || restoring} onClick={() => restore(change)}>{restoring ? '恢复中…' : '确认恢复'}</button><button disabled={restoring} onClick={() => setConfirm(null)}>保留现状</button></div> : <button className="restore-button" disabled={busy} onClick={() => setConfirm(change.id)}>恢复此修改</button>)}
    </article>)}
  </div>
}
