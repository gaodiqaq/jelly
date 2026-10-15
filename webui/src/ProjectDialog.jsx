import React, { useEffect, useRef, useState } from 'react'
import { api, authHeaders } from './api'

function directoryName(name) {
  let safe = name.trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '-').replace(/\s+/g, ' ').replace(/^[ .]+|[ .]+$/g, '').slice(0, 64).replace(/[ .]+$/g, '')
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(safe)) safe = `${safe.slice(0, 60)}-项目`
  return safe
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value <= 0) return ''
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function backupFilename(project) {
  const name = directoryName(project?.name || '') || 'Project'
  const date = new Date().toISOString().slice(0, 10)
  return `Jelly-${name}-v1-${date}.zip`
}

export default function ProjectDialog({ project, onClose, onSaved, onArchive, onImported, onImportUncertain }) {
  const [draft, setDraft] = useState(project || { name: '', cwd: '', model: '', permission: 'ask' })
  const [dialogMode, setDialogMode] = useState(project ? 'settings' : 'create')
  const [directoryMode, setDirectoryMode] = useState('new')
  const [managedRoot, setManagedRoot] = useState('')
  const [rootError, setRootError] = useState('')
  const [saving, setSaving] = useState(false)
  const [lifecycleBusy, setLifecycleBusy] = useState(false)
  const [archiveConfirm, setArchiveConfirm] = useState(false)
  const [error, setError] = useState('')
  const [backupBusy, setBackupBusy] = useState('')
  const [backupProgress, setBackupProgress] = useState(null)
  const [backupAnnouncement, setBackupAnnouncement] = useState('')
  const [backupCanCancel, setBackupCanCancel] = useState(false)
  const [backupNotice, setBackupNotice] = useState('')
  const dialog = useRef(null)
  const backupFile = useRef(null)
  const cancelBackup = useRef(null)
  const backupAction = useRef(null)
  const cancelButton = useRef(null)
  const importFinished = useRef(false)
  useEffect(() => { dialog.current.showModal() }, [])
  useEffect(() => () => cancelBackup.current?.(), [])
  useEffect(() => { if (backupCanCancel) cancelButton.current?.focus() }, [backupCanCancel])
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
    e.preventDefault()
    if (saving || backupBusy || dialogMode === 'import') return
    setSaving(true); setError('')
    try {
      const body = { name: draft.name, model: draft.model, permission: draft.permission }
      if (!project && directoryMode === 'existing') body.cwd = draft.cwd
      const endpoint = project ? `/api/projects/${project.id}` : directoryMode === 'new' ? '/api/projects/managed' : '/api/projects'
      const saved = await api(endpoint, { method: project ? 'PUT' : 'POST', body: JSON.stringify(body) })
      onSaved(saved)
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }
  async function changeArchive(archived) {
    if (backupBusy) return
    setLifecycleBusy(true); setError('')
    try {
      const saved = await onArchive(project.id, archived)
      onSaved(saved)
    } catch (e) { setError(e.message) } finally { setLifecycleBusy(false) }
  }
  async function exportBackup() {
    if (!project || backupBusy) return
    setBackupBusy('export'); setBackupProgress({ label: '正在准备项目备份…', percent: null }); setBackupAnnouncement('正在准备项目备份'); setBackupNotice(''); setError(''); setBackupCanCancel(false)
    try {
      const filename = backupFilename(project)
      if (typeof window.showSaveFilePicker !== 'function') {
        const response = await fetch(`/api/projects/${project.id}/backup-ticket`, {
          method: 'POST',
          headers: { ...authHeaders(), 'X-Jelly-Intent': 'project-backup' },
        })
        if (response.status === 401) window.dispatchEvent(new Event('jelly:unauthorized'))
        if (!response.ok) {
          const body = await response.json().catch(() => ({}))
          throw new Error(body.detail || `HTTP ${response.status}`)
        }
        const ticket = await response.json()
        const anchor = document.createElement('a')
        anchor.href = ticket.url
        anchor.download = ticket.filename || filename
        document.body.appendChild(anchor)
        anchor.click()
        anchor.remove()
        setBackupNotice('备份已经交给浏览器下载，可在下载列表查看进度。')
        return
      }

      const handle = await window.showSaveFilePicker({ suggestedName: filename, types: [{ description: 'Jelly 项目备份', accept: { 'application/zip': ['.zip'] } }] })
      const controller = new AbortController()
      const response = await fetch(`/api/projects/${project.id}/backup`, {
        headers: { ...authHeaders(), 'X-Jelly-Intent': 'project-backup' },
        signal: controller.signal,
      })
      if (response.status === 401) window.dispatchEvent(new Event('jelly:unauthorized'))
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${response.status}`)
      }
      cancelBackup.current = () => controller.abort()
      setBackupCanCancel(true)
      setBackupAnnouncement('备份已经生成，正在保存到所选位置')
      const total = Number(response.headers.get('content-length')) || 0
      const reader = response.body?.getReader()
      let received = 0
      let writable = null
      try {
        writable = await handle.createWritable()
        if (reader) {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            received += value.byteLength
            await writable.write(value)
            setBackupProgress({
              label: `正在保存备份${total ? ` · ${formatBytes(received)} / ${formatBytes(total)}` : ` · ${formatBytes(received)}`}`,
              percent: total ? Math.min(100, Math.round(received / total * 100)) : null,
            })
          }
        } else {
          const blob = await response.blob()
          received = blob.size
          await writable.write(blob)
        }
        await writable.close()
      } catch (cause) {
        if (writable) await writable.abort().catch(() => {})
        throw cause
      }
      setBackupNotice('项目备份已保存，可以随时从新建项目中恢复。')
    } catch (cause) {
      if (cause.name === 'AbortError') setBackupNotice('已停止保存备份。')
      else setError(`项目备份失败：${cause.message}`)
    } finally {
      cancelBackup.current = null
      setBackupCanCancel(false)
      setBackupAnnouncement('')
      setBackupProgress(null)
      setBackupBusy('')
      window.setTimeout(() => backupAction.current?.focus(), 0)
    }
  }
  async function importBackup(file) {
    if (!file || backupBusy) return
    if (file.size > 2_100_000_000) { setError('备份文件超过 2.1 GB 上传上限'); return }
    setBackupBusy('import'); setBackupProgress({ label: `正在上传 ${file.name}`, percent: 0 }); setBackupAnnouncement(`正在上传 ${file.name}`); setBackupNotice(''); setError(''); setBackupCanCancel(true)
    const xhr = new XMLHttpRequest()
    let uploadFinished = false
    cancelBackup.current = () => xhr.abort()
    try {
      const restored = await new Promise((resolve, reject) => {
        let announcedPercent = 0
        xhr.open('POST', '/api/projects/import')
        Object.entries(authHeaders()).forEach(([name, value]) => xhr.setRequestHeader(name, value))
        xhr.setRequestHeader('Content-Type', 'application/zip')
        xhr.setRequestHeader('X-Jelly-Intent', 'project-backup')
        xhr.upload.onprogress = event => {
          const total = event.lengthComputable ? event.total : file.size
          const percent = total ? Math.min(100, Math.round(event.loaded / total * 100)) : null
          setBackupProgress({
            label: `正在上传 ${file.name} · ${formatBytes(event.loaded)} / ${formatBytes(total)}`,
            percent,
          })
          if (percent != null && percent >= announcedPercent + 10) {
            announcedPercent = Math.floor(percent / 10) * 10
            setBackupAnnouncement(`项目备份已上传 ${announcedPercent}%`)
          }
        }
        xhr.upload.onload = () => {
          uploadFinished = true
          cancelBackup.current = null
          setBackupCanCancel(false)
          setBackupProgress({ label: '正在校验并恢复到独立目录…', percent: null })
          setBackupAnnouncement('上传完成，正在校验并恢复项目；此阶段请不要关闭页面')
        }
        xhr.onerror = () => reject(new Error('网络连接中断'))
        xhr.onabort = () => reject(new DOMException('已取消', 'AbortError'))
        xhr.onload = () => {
          let body = {}
          try { body = JSON.parse(xhr.responseText || '{}') } catch { /* use status below */ }
          if (xhr.status === 401) window.dispatchEvent(new Event('jelly:unauthorized'))
          if (xhr.status < 200 || xhr.status >= 300) reject(new Error(body.detail || `HTTP ${xhr.status}`))
          else resolve(body)
        }
        xhr.send(file)
      })
      cancelBackup.current = null
      importFinished.current = true
      await onImported(restored.project, restored.task_count)
    } catch (cause) {
      if (cause.name === 'AbortError') setBackupNotice('已取消上传。')
      else if (uploadFinished && (xhr.status === 0 || xhr.status >= 500)) {
        onImportUncertain()
      } else setError(`导入项目失败：${cause.message}`)
    } finally {
      cancelBackup.current = null
      setBackupCanCancel(false)
      setBackupAnnouncement('')
      setBackupProgress(null)
      setBackupBusy('')
      if (!importFinished.current) window.setTimeout(() => backupAction.current?.focus(), 0)
    }
  }

  function changeMode(mode) {
    if (backupBusy) return
    setDialogMode(mode)
    setError('')
    setBackupNotice('')
  }

  function handleModeKeyDown(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || backupBusy) return
    event.preventDefault()
    const mode = event.key === 'Home' ? 'create' : event.key === 'End' ? 'import' : dialogMode === 'create' ? 'import' : 'create'
    changeMode(mode)
    dialog.current.querySelector(`#project-entry-${mode}`)?.focus()
  }

  const title = project ? '项目设置' : dialogMode === 'import' ? '带回一个完整项目' : '给想法一个工作空间'
  const intro = project
    ? '目录、任务与成果归属同一个项目，随时回来接着做。'
    : dialogMode === 'import'
      ? '选择由 Jelly 导出的项目备份，校验通过后恢复到新的独立目录。'
      : '建立独立目录与执行边界，让对话持续沉淀为可交付成果。'
  const backupFeedback = <>
    {backupProgress && <div className="backup-progress">
      <span aria-hidden="true">{backupProgress.label}</span>
      <div
        className={`backup-progress-track ${backupProgress.percent == null ? 'indeterminate' : ''}`}
        role="progressbar"
        aria-label={backupProgress.label}
        aria-valuetext={backupProgress.label}
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={backupProgress.percent == null ? undefined : backupProgress.percent}
      ><i style={backupProgress.percent == null ? undefined : { width: `${backupProgress.percent}%` }} /></div>
      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">{backupAnnouncement}</span>
    </div>}
    {backupNotice && <p className="backup-notice" role="status">{backupNotice}</p>}
  </>

  return <dialog className="project-dialog" ref={dialog} onCancel={event => { if (backupBusy) event.preventDefault(); else onClose() }} aria-labelledby="project-dialog-title">
    <form onSubmit={save}>
      <div className="dialog-heading"><div><span className="eyebrow">JELLY PROJECT</span><h2 id="project-dialog-title">{title}</h2></div><button type="button" className="quiet-button" aria-label="关闭项目设置" disabled={!!backupBusy} onClick={onClose}>×</button></div>
      <p className="dialog-intro">{intro}</p>

      {!project && <div className="project-entry-choice" role="tablist" aria-label="添加项目方式" onKeyDown={handleModeKeyDown}>
        <button id="project-entry-create" type="button" role="tab" tabIndex={dialogMode === 'create' ? 0 : -1} disabled={!!backupBusy} aria-selected={dialogMode === 'create'} aria-controls="project-create-panel" onClick={() => changeMode('create')}>新建项目</button>
        <button id="project-entry-import" type="button" role="tab" tabIndex={dialogMode === 'import' ? 0 : -1} disabled={!!backupBusy} aria-selected={dialogMode === 'import'} aria-controls="project-import-panel" onClick={() => changeMode('import')}>导入备份</button>
      </div>}

      {(project || dialogMode === 'create') && <div id="project-create-panel" role={project ? undefined : 'tabpanel'} aria-labelledby={project ? undefined : 'project-entry-create'}>
        <label>项目名称<input autoFocus={!project} required maxLength={64} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="例如：品牌设计" /></label>
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
            <button type="button" className="quiet-button lifecycle-restore" disabled={lifecycleBusy || !!backupBusy} onClick={() => changeArchive(false)}>{lifecycleBusy ? '恢复中…' : '恢复项目'}</button>
          </> : archiveConfirm ? <div className="archive-confirm" role="group" aria-live="polite" aria-label="确认归档项目">
            <div><strong>归档「{project.name}」？</strong><p>项目会移到已归档列表，任务与工作目录不会删除。</p></div>
            <div><button type="button" className="quiet-button" disabled={lifecycleBusy || !!backupBusy} onClick={() => setArchiveConfirm(false)}>取消</button><button type="button" className="archive-button" disabled={lifecycleBusy || !!backupBusy} onClick={() => changeArchive(true)}>{lifecycleBusy ? '归档中…' : '确认归档'}</button></div>
          </div> : <>
            <div><strong>暂时不再使用？</strong><p>归档会收起项目，保留全部任务与文件。</p></div>
            <button type="button" className="quiet-button lifecycle-archive" disabled={!!backupBusy} onClick={() => setArchiveConfirm(true)}>归档项目</button>
          </>}
        </section>}
        {project && <section className="project-backup" aria-label="项目备份与恢复" aria-busy={!!backupBusy}>
          <div className="project-backup-copy"><strong>完整备份</strong><p>保存工作目录、任务、版本和运行记录，包括 .env、密钥与私人文档。全局 Provider 凭据不会包含在内。</p>{backupFeedback}</div>
          <div className="project-backup-actions">{backupCanCancel && <button ref={cancelButton} type="button" className="quiet-button" onClick={() => cancelBackup.current?.()}>停止保存</button>}<button ref={backupAction} type="button" className="quiet-button" disabled={!!backupBusy} onClick={exportBackup}>{backupBusy === 'export' ? '正在准备…' : '↓ 下载备份'}</button></div>
        </section>}
        <div className="dialog-actions"><button type="button" className="quiet-button" disabled={!!backupBusy} onClick={onClose}>取消</button><button className="solid-button" disabled={saving || !!backupBusy || (!project && directoryMode === 'new' && (!managedRoot || !managedName))}>{saving ? '保存中…' : project ? '保存设置' : '创建项目'}</button></div>
      </div>}

      {!project && dialogMode === 'import' && <div id="project-import-panel" role="tabpanel" aria-labelledby="project-entry-import" className="project-import-panel">
        <div className="import-privacy"><span aria-hidden="true">!</span><div><strong>备份可能包含私人内容</strong><p>项目备份会还原工作目录中的 .env、密钥和私人文档。只导入你信任的 Jelly 备份；全局 Provider 凭据不会被恢复。</p></div></div>
        <div className="import-destination"><span>恢复位置</span><strong>{rootError || managedRoot || '正在获取项目目录…'}</strong><small>Jelly 会创建新的独立目录、项目和任务 ID，不覆盖现有内容。</small></div>
        {error && <p role="alert" className="form-error">{error}</p>}
        {backupFeedback}
        <div className="import-actions"><button type="button" className="quiet-button" disabled={!!backupBusy} onClick={onClose}>取消</button>{backupCanCancel && <button ref={cancelButton} type="button" className="quiet-button" onClick={() => cancelBackup.current?.()}>取消上传</button>}<button ref={backupAction} type="button" className="solid-button" disabled={!!backupBusy || !!rootError || !managedRoot} onClick={() => backupFile.current?.click()}>{backupBusy === 'import' ? (backupCanCancel ? '正在上传…' : '正在恢复…') : '选择备份文件'}</button></div>
      </div>}
      <input ref={backupFile} type="file" accept=".zip,application/zip" aria-label="选择项目备份 ZIP" hidden onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) importBackup(file) }} />
    </form>
  </dialog>
}
