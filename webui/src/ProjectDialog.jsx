import React, { useEffect, useRef, useState } from 'react'
import { api } from './api'

export default function ProjectDialog({ project, onClose, onSaved }) {
  const [draft, setDraft] = useState(project || { name: '', cwd: '', model: '', permission: 'ask' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const dialog = useRef(null)
  useEffect(() => { dialog.current.showModal() }, [])
  async function save(e) {
    e.preventDefault(); setSaving(true); setError('')
    try {
      const body = { name: draft.name, model: draft.model, permission: draft.permission }
      if (!project) body.cwd = draft.cwd
      const saved = await api(`/api/projects${project ? '/' + project.id : ''}`, { method: project ? 'PUT' : 'POST', body: JSON.stringify(body) })
      onSaved(saved)
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }
  return <dialog className="project-dialog" ref={dialog} onCancel={onClose} aria-labelledby="project-dialog-title">
    <form onSubmit={save}>
      <div className="dialog-heading"><div><span className="eyebrow">JELLY PROJECT</span><h2 id="project-dialog-title">{project ? '项目设置' : '给想法一个工作空间'}</h2></div><button type="button" className="quiet-button" aria-label="关闭项目设置" onClick={onClose}>×</button></div>
      <p className="dialog-intro">目录、任务与成果归属同一个项目，随时回来接着做。</p>
      <label>项目名称<input autoFocus required maxLength={64} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="例如：品牌设计" /></label>
      <label>工作目录<input required disabled={!!project} value={draft.cwd} onChange={e => setDraft({ ...draft, cwd: e.target.value })} placeholder="D:\Projects\my-project" /></label>
      <p className="field-note">选择已存在的本地目录。项目创建后固定此目录，让历史任务始终指向原来的文件。</p>
      <label>默认模型<input value={draft.model} maxLength={128} onChange={e => setDraft({ ...draft, model: e.target.value })} placeholder="留空使用全局默认模型" /></label>
      <label>执行权限<select aria-label="执行权限" value={draft.permission} onChange={e => setDraft({ ...draft, permission: e.target.value })}><option value="ask">手动审批 · 修改前确认</option><option value="readonly">只读 · 仅浏览与分析</option><option value="auto">自动授权 · 允许执行工具</option><option value="deny">禁止工具执行</option></select></label>
      {error && <p role="alert" className="form-error">{error}</p>}
      <div className="dialog-actions"><button type="button" className="quiet-button" onClick={onClose}>取消</button><button className="solid-button" disabled={saving}>{saving ? '保存中…' : project ? '保存设置' : '创建项目'}</button></div>
    </form>
  </dialog>
}
