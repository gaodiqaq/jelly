import React, { useEffect, useState } from 'react'
import { api } from './api'

export default function Recipes({ onUse, seed = '' }) {
  const [items, setItems] = useState([])
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  async function load() {
    try { setItems((await api('/api/recipes')).recipes) }
    catch (e) { setError(e.message) }
  }
  useEffect(() => { load() }, [])
  async function save(e) {
    e.preventDefault(); setSaving(true); setError('')
    try {
      await api(`/api/recipes${draft.id ? '/' + draft.id : ''}`, { method: draft.id ? 'PUT' : 'POST', body: JSON.stringify({ title: draft.title, instruction: draft.instruction }) })
      setDraft(null); await load()
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }
  return <div className="changes-view">
    <div className="studio-eyebrow">YOUR WAY OF WORKING</div><h2>好方法，留下来。</h2>
    <p className="studio-muted">把满意的工作方式写成配方，下一次从这里开始。保存前可编辑，使用时先填入输入框。</p>
    <button className="restore-button" onClick={() => setDraft({ title: '', instruction: seed ? `目标：${seed}\n\n工作步骤：\n1. \n\n输出格式：\n\n完成标准：` : '目标：\n\n工作步骤：\n1. \n\n输出格式：\n\n完成标准：' })}>＋ 保存一个工作配方</button>
    {error && <div role="alert" className="error-banner">{error}</div>}
    {draft && <form className="recipe-form" onSubmit={save}>
      <label>配方名称<input required maxLength={80} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} placeholder="例如：我的项目周报" /></label>
      <label>工作方式<textarea aria-label="工作方式" required maxLength={20000} rows={12} value={draft.instruction} onChange={e => setDraft({ ...draft, instruction: e.target.value })} /></label>
      <p className="studio-muted">只保留可复用的方法；请移除密钥、临时资料和不需要长期保存的信息。</p>
      <button className="restore-button" disabled={saving}>{saving ? '保存中…' : '保存配方'}</button><button type="button" className="restore-button" disabled={saving} onClick={() => setDraft(null)}>取消</button>
    </form>}
    {!items.length && !draft && <div className="studio-empty"><span>✧</span><h3>你的方法库，从一次满意的合作开始。</h3><p>记录步骤、输出格式和完成标准，让下次少一些重复说明。</p></div>}
    {items.map(item => <article key={item.id} className="change-card"><div className="change-heading"><strong>{item.title}</strong><span>v{item.version}</span></div><details><summary>查看工作方式</summary><pre className="change-diff">{item.instruction}</pre></details><button className="restore-button" onClick={() => onUse(item.instruction)}>使用配方</button><button className="restore-button" onClick={() => setDraft(item)}>编辑</button></article>)}
  </div>
}
