import React, { useCallback, useEffect, useState } from 'react'
import Markdown from './Markdown'
import { api, authToken } from './api'
import Changes from './Changes'
import Recipes from './Recipes'

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

function FileTree({ onOpenFile, activeFile }) {
  const [dirs, setDirs] = useState({})
  const [expanded, setExpanded] = useState(() => new Set(['.']))
  const [cwdName, setCwdName] = useState('')

  const fetchDir = useCallback((rel) => {
    setDirs((prev) => ({ ...prev, [rel]: prev[rel] || { loading: true } }))
    api(`/api/files?path=${encodeURIComponent(rel)}`)
      .then((data) => {
        setCwdName(data.cwd || '')
        setDirs((prev) => ({ ...prev, [rel]: { entries: data.entries || [] } }))
      })
      .catch((e) => {
        setDirs((prev) => ({ ...prev, [rel]: { error: e.message } }))
      })
  }, [])

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
      return <div className="ws-hint" style={{ paddingLeft: 14 + depth * 14 }}>无法读取：{state.error}</div>
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

function Preview({ path, onBack, onOpenFile }) {
  const [state, setState] = useState({ loading: true })

  useEffect(() => {
    let alive = true
    setState({ loading: true })
    api(`/api/file?path=${encodeURIComponent(path)}`)
      .then((data) => {
        if (alive) setState({ data })
      })
      .catch((e) => {
        if (alive) setState({ error: e.message })
      })
    return () => {
      alive = false
    }
  }, [path])

  const data = state.data
  const isImage = IMAGE_RE.test(path)
  const isMd = data && MD_RE.test(data.name)
  const rawUrl = `/api/file/raw?path=${encodeURIComponent(path)}${authToken() ? `&token=${encodeURIComponent(authToken())}` : ''}`

  let body
  if (state.loading) {
    body = <div className="ws-hint ws-center">加载中…</div>
  } else if (state.error) {
    body = <div className="ws-hint ws-center">文件不存在或无法读取<br /><span className="ws-hint-sub">{path}</span></div>
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
        <a className="ws-raw-link" href={rawUrl} target="_blank" rel="noreferrer">打开原始文件</a>
      </div>
    )
  } else if (/\.html?$/i.test(path) && !data.truncated) {
    body = <iframe className="studio-web-preview" title={`网页预览：${data.name}`} sandbox="" referrerPolicy="no-referrer" srcDoc={'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; font-src data:;">' + data.content} />
  } else if (isMd) {
    body = <div className="ws-md"><Markdown text={data.content} onOpenFile={onOpenFile} /></div>
  } else {
    body = <pre className="ws-code">{data.content}</pre>
  }

  return (
    <div className="ws-preview">
      <div className="ws-preview-head">
        <button className="ws-back" onClick={onBack} title="返回文件列表">
          <ChevronIcon /> 文件列表
        </button>
        <span className="ws-preview-path" title={data ? data.path : path}>
          {data ? data.path : path}
        </span>
        {data && !data.is_binary && (
          <a className="ws-raw-link" href={rawUrl} target="_blank" rel="noreferrer" title="打开原始文件">
            原始文件
          </a>
        )}
      </div>
      {data && data.truncated && (
        <div className="ws-hint ws-truncated">内容过长，仅显示前一部分</div>
      )}
      <div className="ws-preview-body">{body}</div>
    </div>
  )
}

// ---------- 面板主体 ----------

export default function Workspace({ file, onOpenFile, onCloseFile, onClose, sessionId, busy, onUseRecipe, recipeSeed }) {
  const [tab, setTab] = useState('changes')
  const [revision, setRevision] = useState(0)
  useEffect(() => { if (!busy) setRevision(n => n + 1) }, [busy, sessionId])
  useEffect(() => { if (file) setTab('files') }, [file])
  return (
    <>
      <div className="ws-head">
        <span className="ws-title">
          <FolderIcon />
          {file ? file.split('/').pop() : '作品空间'}
        </span>
        <button className="ws-close" aria-label="关闭工作台" title="关闭工作台" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="studio-tabs" role="tablist" aria-label="作品空间">
        <button role="tab" aria-selected={tab === 'changes'} onClick={() => setTab('changes')}>成果与版本</button>
        <button role="tab" aria-selected={tab === 'files'} onClick={() => setTab('files')}>文件预览</button>
        <button role="tab" aria-selected={tab === 'recipes'} onClick={() => setTab('recipes')}>工作配方</button>
      </div>
      {tab === 'recipes' ? <Recipes onUse={onUseRecipe} seed={recipeSeed} /> : tab === 'changes' ? <Changes key={sessionId} sessionId={sessionId} busy={busy} onOpenFile={path => { setTab('files'); onOpenFile(path) }} onRestored={() => setRevision(n => n + 1)} /> : <>
        <div className="ws-tree-wrap" hidden={!!file}>
          <FileTree key={`${sessionId}-${revision}`} onOpenFile={onOpenFile} activeFile={file} />
        </div>
        {file && <Preview key={`${file}-${revision}`} path={file} onBack={onCloseFile} onOpenFile={onOpenFile} />}
      </>}
    </>
  )
}
