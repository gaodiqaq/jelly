import React, { useEffect, useState } from 'react'
import { api } from './api'

export default function Changes({ sessionId, busy, onOpenFile, onRestored }) {
  const [changes, setChanges] = useState([])
  const [error, setError] = useState('')
  const [warnings, setWarnings] = useState([])
  const [restoreNotice, setRestoreNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const [confirm, setConfirm] = useState(null)
  const [restoring, setRestoring] = useState(false)
  useEffect(() => setRestoreNotice(''), [sessionId])
  useEffect(() => {
    setChanges([]); setError(''); setWarnings([]); setConfirm(null)
    if (!sessionId) return
    let alive = true
    let timer
    async function load() {
      setLoading(true)
      try {
        const data = await api(`/api/sessions/${sessionId}/changes`)
        if (alive) { setChanges(data.changes); setWarnings(data.warnings || []); setError('') }
      } catch (e) { if (alive) setError(e.message) }
      finally { if (alive) { setLoading(false); if (busy) timer = setTimeout(load, 1800) } }
    }
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [sessionId, busy, revision])

  async function restore(change) {
    setRestoring(true); setError(''); setRestoreNotice('')
    try {
      const result = await api(`/api/sessions/${sessionId}/changes/${change.id}/restore`, { method: 'POST' })
      if (result.warning) setRestoreNotice(result.warning)
      setConfirm(null); setRevision(n => n + 1); onRestored()
    } catch (e) { setError(e.message) }
    finally { setRestoring(false) }
  }
  return <div className="changes-view">
    <div className="studio-eyebrow">VERSION HISTORY</div>
    <h2>每一步，都有迹可循。</h2>
    <p className="studio-muted">记录文件工具的修改前后版本。命令行及外部操作不包含在恢复范围内。</p>
    {error && <div role="alert" className="error-banner">{error}</div>}
    {restoreNotice && <div role="status" className="error-banner">{restoreNotice}</div>}
    {warnings.length > 0 && <div role="alert" className="error-banner">{warnings.length} 条版本记录需要核对：{warnings.map(item => item.file).join('、')}。其余版本仍可使用。</div>}
    {!changes.length && <div className="studio-empty"><span>◈</span><h3>{loading ? '读取版本…' : warnings.length ? '版本记录需要核对' : '等待第一份成果'}</h3><p>{warnings.length ? '目前没有可显示的有效版本；请检查上方提示的记录文件。' : '让果冻创建或修改一个文件，版本和差异将出现在这里。'}</p></div>}
    {changes.map(change => <article className="change-card" key={change.id}>
      <div className="change-heading"><button onClick={() => onOpenFile(change.path)}>{change.path}</button><span>{change.kind === 'created' ? '新建' : '修改'}</span></div>
      <div className="studio-muted">{new Date(change.created_at).toLocaleString()} · {({ ready: '版本已保存', restored: '已恢复', restoring: '恢复状态待核对', pending: '快照待完成', failed: '执行失败', snapshot_failed: '文件已处理，版本未保存' })[change.state]}</div>
      <details><summary>查看修改差异</summary><pre className="change-diff">{change.diff || '没有可显示的文本差异'}</pre></details>
      {change.state === 'ready' && (confirm === change.id ? <div className="restore-confirm"><p>{change.kind === 'created' ? '恢复将删除本次新建的文件。' : '恢复到这次修改之前的内容。'}如果文件已有后续修改，将拒绝覆盖。</p><button disabled={busy || restoring} onClick={() => restore(change)}>{restoring ? '恢复中…' : '确认恢复'}</button><button disabled={restoring} onClick={() => setConfirm(null)}>保留现状</button></div> : <button className="restore-button" disabled={busy} onClick={() => setConfirm(change.id)}>恢复此修改</button>)}
    </article>)}
  </div>
}
