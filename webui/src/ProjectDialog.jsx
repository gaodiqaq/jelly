import React, { useEffect, useRef, useState } from 'react'
import { api } from './api'

function directoryName(name) {
  let safe = name.trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '-').replace(/\s+/g, ' ').replace(/^[ .]+|[ .]+$/g, '').slice(0, 64).replace(/[ .]+$/g, '')
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(safe)) safe = `${safe.slice(0, 60)}-项目`
  return safe
}

export default function ProjectDialog({ project, onClose, onSaved, onArchive }) {
  const [draft, setDraft] = useState(project || { name: '', cwd: '', model: '', permission: 'ask' })
  const [directoryMode, setDirectoryMode] = useState('new')
  const [managedRoot, setManagedRoot] = useState('')
  const [rootError, setRootError] = useState('')
  const [saving, setSaving] = useState(false)
  const [lifecycleBusy, setLifecycleBusy] = useState(false)
  const [archiveConfirm, setArchiveConfirm] = useState(false)
  const [error, setError] = useState('')
  const dialog = useRef(null)
  useEffect(() => { dialog.current.showModal() }, [])
  useEffect(() => {
    if (project) return
    let active = true
    api('/api/projects/managed-root')
      .then(data => { if (active) { setManagedRoot(data.root); setRootError('') } })
      .catch(error => { if (active) setRootError(`无法获取新项目位置：${error.message}`) })
    return () => { active = false }
  }, [project])
  const managedName = directoryName(draft.name)
  const managedPath = managedRoot && managedName ? `${managedRoot.replace(/[\\/]$/, '')}${managedRoot.includes('\\') ? '\\' : '/'}${managedName}` : ''
  async function save(e) {
    e.preventDefault(); setSaving(true); setError('')
    try {
      const body = { name: draft.name, model: draft.model, permission: draft.permission }
      if (!project && directoryMode === 'existing') body.cwd = draft.cwd
      const endpoint = project ? `/api/projects/${project.id}` : directoryMode === 'new' ? '/api/projects/managed' : '/api/projects'
      const saved = await api(endpoint, { method: project ? 'PUT' : 'POST', body: JSON.stringify(body) })
      onSaved(saved)
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }
  async function changeArchive(archived) {
    setLifecycleBusy(true); setError('')
    try {
      const saved = await onArchive(project.id, archived)
      onSaved(saved)
    } catch (e) { setError(e.message) } finally { setLifecycleBusy(false) }
  }
  return <dialog className="project-dialog" ref={dialog} onCancel={onClose} aria-labelledby="project-dialog-title">
    <form onSubmit={save}>
      <div className="dialog-heading"><div><span className="eyebrow">JELLY PROJECT</span><h2 id="project-dialog-title">{project ? '项目设置' : '给想法一个工作空间'}</h2></div><button type="button" className="quiet-button" aria-label="关闭项目设置" onClick={onClose}>×</button></div>
      <p className="dialog-intro">目录、任务与成果归属同一个项目，随时回来接着做。</p>
      <label>项目名称<input autoFocus required maxLength={64} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="例如：品牌设计" /></label>
      {project ? <><label>工作目录<input disabled value={draft.cwd} readOnly /></label><p className="field-note">项目目录固定，历史任务始终指向原来的文件。</p></> : <>
        <div className="directory-choice" role="group" aria-label="工作目录方式">
          <button type="button" aria-pressed={directoryMode === 'new'} onClick={() => setDirectoryMode('new')}>创建新目录</button>
          <button type="button" aria-pressed={directoryMode === 'existing'} onClick={() => setDirectoryMode('existing')}>绑定现有目录</button>
        </div>
        {directoryMode === 'new' ? <div className="managed-location" aria-live="polite">
          <span>你的专属工作目录</span>
          <strong title={managedPath}>{rootError || (managedPath || (managedRoot ? '输入项目名称后显示目录' : '正在获取目录位置…'))}</strong>
          <small>创建项目时自动建立；任务和成果文件保存在此处。</small>
        </div> : <>
          <label>工作目录<input required value={draft.cwd} onChange={e => setDraft({ ...draft, cwd: e.target.value })} placeholder="粘贴已有文件夹的绝对路径" /></label>
          <p className="field-note">绑定已有文件夹；项目创建后将固定使用此目录。</p>
        </>}
      </>}
      <label>默认模型<input value={draft.model} maxLength={128} onChange={e => setDraft({ ...draft, model: e.target.value })} placeholder="留空使用全局默认模型" /></label>
      <label>执行权限<select aria-label="执行权限" value={draft.permission} onChange={e => setDraft({ ...draft, permission: e.target.value })}><option value="ask">手动审批 · 修改前确认</option><option value="readonly">只读 · 仅浏览与分析</option><option value="auto">自动授权 · 允许执行工具</option><option value="deny">禁止工具执行</option></select></label>
      {error && <p role="alert" className="form-error">{error}</p>}
      {project && <section className="project-lifecycle" aria-label="项目生命周期">
        {project.archived_at ? <>
          <div><strong>这个项目已归档</strong><p>任务和文件都保留着。恢复后即可继续工作。</p></div>
          <button type="button" className="quiet-button lifecycle-restore" disabled={lifecycleBusy} onClick={() => changeArchive(false)}>{lifecycleBusy ? '恢复中…' : '恢复项目'}</button>
        </> : archiveConfirm ? <div className="archive-confirm" role="group" aria-live="polite" aria-label="确认归档项目">
          <div><strong>归档「{project.name}」？</strong><p>项目会移到已归档列表，任务与工作目录不会删除。</p></div>
          <div><button type="button" className="quiet-button" disabled={lifecycleBusy} onClick={() => setArchiveConfirm(false)}>取消</button><button type="button" className="archive-button" disabled={lifecycleBusy} onClick={() => changeArchive(true)}>{lifecycleBusy ? '归档中…' : '确认归档'}</button></div>
        </div> : <>
          <div><strong>暂时不再使用？</strong><p>归档会收起项目，保留全部任务与文件。</p></div>
          <button type="button" className="quiet-button lifecycle-archive" onClick={() => setArchiveConfirm(true)}>归档项目</button>
        </>}
      </section>}
      <div className="dialog-actions"><button type="button" className="quiet-button" onClick={onClose}>取消</button><button className="solid-button" disabled={saving || (!project && directoryMode === 'new' && (!managedRoot || !managedName))}>{saving ? '保存中…' : project ? '保存设置' : '创建项目'}</button></div>
    </form>
  </dialog>
}
