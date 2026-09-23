import React, { useCallback, useEffect, useRef, useState } from 'react'
import DOMPurify from 'dompurify'
import Markdown from './Markdown'
import { api, authHeaders } from './api'
import Changes from './Changes'
import Recipes from './Recipes'
import Artifacts, { FileGlyph } from './Artifacts'
import { handleTabListKeyDown } from './a11y'

const STROKE = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }

const FolderIcon = () => (
  <svg viewBox="0 0 24 24" {...STROKE}><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>
)

const FileIcon = () => (
  <svg viewBox="0 0 24 24" {...STROKE}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
  </svg>
)

const ChevronIcon = () => (
  <svg viewBox="0 0 24 24" {...STROKE}><path d="m6 9 6 6 6-6" /></svg>
)

const IMAGE_RE = /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i
const MD_RE = /\.(md|markdown)$/i

// ---------- 文件树 ----------

function FileTree({ onOpenFile, activeFile, scope }) {
  const [dirs, setDirs] = useState({})
  const [expanded, setExpanded] = useState(() => new Set(['.']))
  const [cwdName, setCwdName] = useState('')

  const fetchDir = useCallback((rel) => {
    setDirs((prev) => ({ ...prev, [rel]: prev[rel] || { loading: true } }))
    api(`/api/files?path=${encodeURIComponent(rel)}&${scope}`)
      .then((data) => {
        setCwdName(data.cwd || '')
        setDirs((prev) => ({ ...prev, [rel]: { entries: data.entries || [] } }))
      })
      .catch((e) => {
        setDirs((prev) => ({ ...prev, [rel]: { error: e.message } }))
      })
  }, [scope])

  useEffect(() => {
    fetchDir('.')
  }, [fetchDir])

  const toggle = (rel) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(rel)) next.delete(rel)
      else {
        next.add(rel)
        if (!dirs[rel] || dirs[rel].error) fetchDir(rel)
      }
      return next
    })
  }

  const renderEntries = (rel, depth) => {
    const state = dirs[rel]
    if (!state || state.loading) {
      return <div className="ws-hint" style={{ paddingLeft: 14 + depth * 14 }}>加载中…</div>
    }
    if (state.error) {
      return <div className="ws-hint ws-tree-error" style={{ paddingLeft: 14 + depth * 14 }}>无法读取：{state.error} <button type="button" onClick={() => fetchDir(rel)}>重试</button></div>
    }
    return state.entries.map((entry) => {
      const childRel = rel === '.' ? entry.name : `${rel}/${entry.name}`
      if (entry.type === 'dir') {
        const open = expanded.has(childRel)
        return (
          <React.Fragment key={childRel}>
            <button
              className="ws-row"
              style={{ paddingLeft: 10 + depth * 14 }}
              onClick={() => toggle(childRel)}
            >
              <span className={`ws-chevron${open ? ' open' : ''}`}><ChevronIcon /></span>
              <span className="ws-row-icon"><FolderIcon /></span>
              <span className="ws-row-name">{entry.name}</span>
            </button>
            {open && renderEntries(childRel, depth + 1)}
          </React.Fragment>
        )
      }
      return (
        <button
          key={childRel}
          className={`ws-row file${activeFile === childRel ? ' active' : ''}`}
          style={{ paddingLeft: 10 + depth * 14 + 16 }}
          title={entry.size != null ? `${entry.size} 字节` : ''}
          onClick={() => onOpenFile(childRel)}
        >
          <span className="ws-row-icon"><FileIcon /></span>
          <span className="ws-row-name">{entry.name}</span>
        </button>
      )
    })
  }

  return (
    <div className="ws-tree">
      <div className="ws-tree-root">
        <span className="ws-row-icon"><FolderIcon /></span>
        <span className="ws-root-name">{cwdName || '工作区'}</span>
      </div>
      {renderEntries('.', 0)}
    </div>
  )
}

// ---------- 文件预览 ----------

function Preview({ path, onBack, onOpenFile, scope, revision, artifact }) {
  const [state, setState] = useState({ loading: true })
  const [source, setSource] = useState(false)
  const [interactive, setInteractive] = useState(false)
  const [interactiveUrl, setInteractiveUrl] = useState('')
  const [interactiveError, setInteractiveError] = useState('')
  const [rawUrl, setRawUrl] = useState('')
  const documentRef = useRef(null)
  const rawEndpoint = `/api/file/raw?path=${encodeURIComponent(path)}${scope ? `&${scope}` : ''}`

  useEffect(() => {
    let alive = true
    setState({ loading: true })
    api(`/api/file?path=${encodeURIComponent(path)}&${scope}`)
      .then((data) => {
        if (alive) setState({ data })
      })
      .catch((e) => {
        if (alive) setState({ error: e.message })
      })
    return () => {
      alive = false
    }
  }, [path, scope, revision])

  useEffect(() => {
    if (!IMAGE_RE.test(path)) { setRawUrl(''); return }
    const controller = new AbortController()
    let objectUrl = ''
    fetch(rawEndpoint, { headers: authHeaders(), signal: controller.signal })
      .then(response => {
        if (response.status === 401) window.dispatchEvent(new Event('jelly:unauthorized'))
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.blob()
      })
      .then(blob => { objectUrl = URL.createObjectURL(blob); setRawUrl(objectUrl) })
      .catch(error => { if (error.name !== 'AbortError') setState({ error: error.message }) })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [path, rawEndpoint, revision])

  useEffect(() => {
    if (!interactive || !state.data || source) return
    let alive = true
    setInteractiveUrl(''); setInteractiveError('')
    const parameters = new URLSearchParams(scope)
    api('/api/file/preview', {
      method: 'POST',
      body: JSON.stringify({
        path,
        session_id: parameters.get('session_id') || null,
        project_id: parameters.get('project_id') || null,
      }),
    })
      .then(result => { if (alive) setInteractiveUrl(result.url) })
      .catch(error => { if (alive) setInteractiveError(error.message) })
    return () => { alive = false }
  }, [interactive, state.data, source, path, scope, revision])

  async function downloadRaw() {
    try {
      const response = await fetch(rawEndpoint, { headers: authHeaders() })
      if (response.status === 401) window.dispatchEvent(new Event('jelly:unauthorized'))
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const objectUrl = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = state.data?.name || path.split('/').pop()
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
    } catch (error) {
      setState(previous => ({ ...previous, error: `下载失败：${error.message}` }))
    }
  }

  const data = state.data
  const isImage = IMAGE_RE.test(path)
  const isMd = data && MD_RE.test(data.name)
  const headings = isMd ? (data.content || '').split('\n').filter(line => /^#{2,3} /.test(line)).slice(0, 12) : []
  const sectionStart = isMd ? (data.content || '').search(/^## /m) : -1
  const introduction = sectionStart > 0 ? data.content.slice(0, sectionStart) : ''
  const documentBody = sectionStart > 0 ? data.content.slice(sectionStart) : data?.content

  let body
  if (state.loading) {
    body = <div className="ws-hint ws-center">加载中…</div>
  } else if (state.error) {
    body = <div className="ws-hint ws-center">文件不存在或无法读取<br /><span className="ws-hint-sub">{path}</span></div>
  } else if (source && !data.is_binary) {
    body = <pre className="ws-code">{data.content}</pre>
  } else if (isImage) {
    body = (
      <div className="ws-image">
        <img src={rawUrl} alt={data.name} />
      </div>
    )
  } else if (data.is_binary) {
    body = (
      <div className="ws-hint ws-center">
        二进制文件，无法预览
        <br />
        <button className="ws-raw-link" onClick={downloadRaw}>下载原始文件</button>
      </div>
    )
  } else if (/\.html?$/i.test(path) && !data.truncated) {
    const policy = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:;"
    const staticHtml = DOMPurify.sanitize(data.content, { WHOLE_DOCUMENT: true })
    body = <div className="web-preview-shell">
      <div className="web-preview-safety"><span>{interactive ? '隔离交互预览 · 网络访问已关闭' : '安全静态预览 · 脚本未运行'}</span><button type="button" aria-pressed={interactive} onClick={() => setInteractive(value => !value)}>{interactive ? '停止交互' : '启用交互'}</button></div>
      {interactive
        ? interactiveError ? <div className="ws-hint ws-center" role="alert">交互预览无法打开：{interactiveError}</div> : interactiveUrl ? <iframe key={interactiveUrl} className="studio-web-preview" title={`网页预览：${data.name}`} sandbox="allow-scripts" referrerPolicy="no-referrer" src={interactiveUrl} /> : <div className="ws-hint ws-center">正在准备隔离预览…</div>
        : <iframe key="static" className="studio-web-preview" title={`网页预览：${data.name}`} sandbox="" referrerPolicy="no-referrer" srcDoc={`<meta http-equiv="Content-Security-Policy" content="${policy}">` + staticHtml} />}
    </div>
  } else if (isMd) {
    body = <div className="document-sheet" ref={documentRef}><div className="document-kicker">JELLY DOCUMENT <span>{artifact ? `v${artifact.version}` : '本地文件'}</span></div>
      {introduction && <Markdown text={introduction} onOpenFile={onOpenFile} />}
      {headings.length > 2 && <details className="document-outline" open><summary>目录 <span>CONTENTS</span></summary>{headings.map((heading, i) => <button key={i} onClick={() => documentRef.current?.querySelectorAll('.markdown h2, .markdown h3')[i]?.scrollIntoView({ behavior: 'smooth', block: 'start' })}><span>{String(i + 1).padStart(2, '0')}</span>{heading.replace(/^#+ /, '')}</button>)}</details>}
      <Markdown text={documentBody} onOpenFile={onOpenFile} />
    </div>
  } else {
    body = <pre className="ws-code">{data.content}</pre>
  }

  return (
    <div className="ws-preview">
      <div className="ws-preview-head">
        <button className="ws-back" onClick={onBack} title="返回文件列表">
          <ChevronIcon /> 文件列表
        </button>
        <span className="ws-preview-path" title={data ? data.path : path}>{artifact ? `v${artifact.version} · 已保存` : '本地文件'}</span>
        {data && !data.is_binary && <button className="preview-mode" onClick={() => { setSource(value => !value); setInteractive(false) }}>{source ? '预览' : '源文件'}</button>}
        {data && !data.is_binary && (
          <button className="ws-raw-link" onClick={downloadRaw} title="下载原始文件">
            ↓ 下载
          </button>
        )}
      </div>
      {data && data.truncated && (
        <div className="ws-hint ws-truncated">内容过长，仅显示前一部分</div>
      )}
      <div className="ws-preview-body">{body}</div>
      {data && <div className="preview-footer"><span><i />{artifact ? '成果已同步' : '本地预览'}</span><span>{isMd ? 'Markdown' : path.split('.').pop().toUpperCase()} · {data.is_binary ? '二进制' : 'UTF-8'}</span></div>}
    </div>
  )
}

// ---------- 面板主体 ----------

export default function Workspace({ file, onOpenFile, onCloseFile, onClose, sessionId, busy, onUseRecipe, recipeSeed, scope, artifacts = [], openedFiles = [], onCloseTab, onRestored }) {
  const [tab, setTab] = useState('files')
  const [revision, setRevision] = useState(0)
  const [focused, setFocused] = useState(false)
  const activeArtifact = artifacts.find(item => item.path === file)
  useEffect(() => { if (!busy) setRevision(n => n + 1) }, [busy, sessionId])
  useEffect(() => { if (file) setTab('files') }, [file])
  useEffect(() => {
    if (!focused) return
    const escape = event => { if (event.key === 'Escape') setFocused(false) }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [focused])
  return (
    <div className={`workspace-inner${focused ? ' focused-preview' : ''}`}>
      <div className="ws-head">
        <span className="ws-title">
          <FolderIcon />
          文件预览 <small>{artifacts.length}</small>
        </span>
        <button className="ws-close" aria-label={focused ? '退出专注阅读' : '专注阅读'} title="专注阅读" onClick={() => setFocused(value => !value)}>⤢</button>
        <button className="ws-close" aria-label="关闭工作台" title="关闭工作台" onClick={onClose}>
          ✕
        </button>
      </div>
      {!!openedFiles.length && <div className="file-tabs" role="tablist" aria-label="打开的文件" onKeyDown={handleTabListKeyDown}>{openedFiles.map(path => <div role="presentation" key={path} className={file === path ? 'active' : ''}><button role="tab" tabIndex={file === path ? 0 : -1} aria-selected={file === path} aria-controls="workspace-tab-panel" onClick={() => { setTab('files'); onOpenFile(path) }} title={path}><FileGlyph path={path} /><span>{path.split('/').pop()}</span></button><button className="file-tab-close" aria-label={`关闭 ${path}`} onClick={() => onCloseTab(path)}>×</button></div>)}</div>}
      <div className="studio-tabs" role="tablist" aria-label="作品空间" onKeyDown={handleTabListKeyDown}>
        <button id="workspace-tab-files" role="tab" tabIndex={tab === 'files' ? 0 : -1} aria-selected={tab === 'files'} aria-controls="workspace-tab-panel" onClick={() => setTab('files')}>预览</button>
        <button id="workspace-tab-changes" role="tab" tabIndex={tab === 'changes' ? 0 : -1} aria-selected={tab === 'changes'} aria-controls="workspace-tab-panel" onClick={() => setTab('changes')}>版本与恢复</button>
        <button id="workspace-tab-recipes" role="tab" tabIndex={tab === 'recipes' ? 0 : -1} aria-selected={tab === 'recipes'} aria-controls="workspace-tab-panel" onClick={() => setTab('recipes')}>工作配方</button>
      </div>
      <div id="workspace-tab-panel" role="tabpanel" aria-labelledby={`workspace-tab-${tab}`} className="workspace-tab-panel">
        {tab === 'recipes' ? <Recipes onUse={onUseRecipe} seed={recipeSeed} /> : tab === 'changes' ? <Changes key={sessionId} sessionId={sessionId} busy={busy} onOpenFile={path => { setTab('files'); onOpenFile(path) }} onRestored={() => { setRevision(n => n + 1); onRestored() }} /> : <>
        <div className="ws-tree-wrap" hidden={!!file}>
          {!!artifacts.length && <div className="workspace-artifacts"><span className="eyebrow">本次成果</span><Artifacts artifacts={artifacts} onOpenFile={onOpenFile} activeFile={file} /></div>}
          {!artifacts.length && <div className="preview-welcome"><span className="preview-orbit">◇</span><h3>让成果，留在眼前。</h3><p>文件生成后会自动在这里打开。<br />也可以从项目目录选择一份文件。</p></div>}
          <FileTree key={`${sessionId}-${revision}`} scope={scope} onOpenFile={onOpenFile} activeFile={file} />
        </div>
        {file && <Preview key={file} path={file} scope={scope} revision={`${revision}-${activeArtifact?.revision || ''}`} artifact={activeArtifact} onBack={onCloseFile} onOpenFile={onOpenFile} />}
        </>}
      </div>
    </div>
  )
}
