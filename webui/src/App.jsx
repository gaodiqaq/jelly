import React, { useCallback, useEffect, useRef, useState } from 'react'
import Markdown from './Markdown'

const EMPTY_HISTORY = []

function api(path, options = {}) {
  const token = localStorage.getItem('agent_web_token') || ''
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }
  return fetch(path, { ...options, headers }).then(async (res) => {
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      const err = new Error(body.detail || `HTTP ${res.status}`)
      err.status = res.status
      throw err
    }
    return res.json()
  })
}

function wsUrl(sessionId) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/${sessionId}`
}

function buildWsUrl(sessionId, token) {
  const url = wsUrl(sessionId)
  if (token) return `${url}?token=${encodeURIComponent(token)}`
  return url
}

function draftProviders(providers) {
  const out = {}
  for (const p of providers || []) {
    out[p.name] = { api_key: '', api_base: p.api_base || '' }
  }
  return out
}

export default function App() {
  const [sessions, setSessions] = useState([])
  const [current, setCurrent] = useState(null)
  const [history, setHistory] = useState(EMPTY_HISTORY)
  const [token, setToken] = useState(localStorage.getItem('agent_web_token') || '')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(null)
  const [draft, setDraft] = useState('')
  const [model, setModel] = useState('')
  const [providers, setProviders] = useState([])
  const [allModels, setAllModels] = useState({})
  const [config, setConfig] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showModelPicker, setShowModelPicker] = useState(false)
  const [propsDraft, setPropsDraft] = useState({ model: '', providers: {} })
  const [testing, setTesting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [usage, setUsage] = useState(null)
  const [skills, setSkills] = useState([])
  const [activeSkill, setActiveSkill] = useState(null)
  const wsRef = useRef(null)
  const messagesRef = useRef(null)
  const modelPickerRef = useRef(null)

  useEffect(() => {
    api('/api/health')
      .then((data) => setModel(data.model || ''))
      .catch(() => {})
  }, [])

  const loadProviders = useCallback(() => {
    api('/api/providers')
      .then((data) => {
        setProviders(data.providers || [])
        // 收集所有可用模型
        const modelsMap = {}
        const promises = (data.providers || []).map((p) =>
          api(`/api/providers/${p.name}/models`)
            .then((m) => { modelsMap[p.name] = m.models || [] })
            .catch(() => { modelsMap[p.name] = [] })
        )
        return Promise.all(promises).then(() => setAllModels(modelsMap))
      })
      .catch(() => {})
  }, [])

  const loadConfig = useCallback(() => {
    api('/api/config')
      .then((data) => {
        setConfig(data)
        setPropsDraft({ model: data.model || '', providers: draftProviders(data.providers) })
        loadProviders()
      })
      .catch(() => {})
  }, [loadProviders])

  const loadSkills = useCallback(() => {
    api('/api/skills')
      .then((data) => setSkills(data.skills || []))
      .catch(() => {})
  }, [])

  // 点击外部关闭模型选择器
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) {
        setShowModelPicker(false)
      }
    }
    if (showModelPicker) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [showModelPicker])

  useEffect(() => {
    if (token) {
      loadConfig()
      loadSkills()
    }
  }, [token, loadConfig, loadSkills])

  useEffect(() => {
    const el = messagesRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [history, pending, status])

  const refreshSessions = useCallback(() => {
    api('/api/sessions')
      .then((data) => setSessions(data.sessions || []))
      .catch((e) => {
        setError(
          e.status === 401
            ? '认证失败：请确认右上角 AGENT_WEB_TOKEN 输入框已填写正确口令'
            : `加载会话列表失败: ${e.message}`,
        )
      })
  }, [])

  useEffect(() => {
    refreshSessions()
  }, [refreshSessions])

  const newSession = useCallback(async () => {
    setError('')
    try {
      const data = await api('/api/sessions', { method: 'POST' })
      setCurrent(data.session_id)
      setHistory(EMPTY_HISTORY)
      setPending(null)
      refreshSessions()
    } catch (e) {
      setError(
        e.status === 401
          ? '认证失败：请确认右上角 AGENT_WEB_TOKEN 输入框已填写正确口令'
          : `创建会话失败: ${e.message}`,
      )
    }
  }, [refreshSessions])

  const openSession = useCallback(async (id) => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setCurrent(id)
    setHistory(EMPTY_HISTORY)
    setPending(null)
    setStatus('')
    setError('')
    try {
      const data = await api(`/api/sessions/${id}/messages`)
      setHistory(data.messages || [])
    } catch (e) {
      setError(
        e.status === 401
          ? '认证失败：请确认右上角 AGENT_WEB_TOKEN 输入框已填写正确口令'
          : `加载会话失败: ${e.message}`,
      )
    }
  }, [])

  const renameSession = useCallback(async (id, title) => {
    try {
      const data = await api(`/api/sessions/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ title }),
      })
      setSessions((prev) =>
        prev.map((s) => (s.session_id === id ? { ...s, title: data.title } : s)),
      )
    } catch (e) {
      setError(`重命名失败: ${e.message}`)
    }
  }, [])

  const deleteSession = useCallback(async (id) => {
    try {
      await api(`/api/sessions/${id}`, { method: 'DELETE' })
      setSessions((prev) => prev.filter((s) => s.session_id !== id))
      if (current === id) {
        setCurrent(null)
        setHistory(EMPTY_HISTORY)
        setPending(null)
      }
    } catch (e) {
      setError(`删除失败: ${e.message}`)
    }
  }, [current])

  const send = useCallback(
    async (text) => {
      if (!current || busy) return
      const ws = new WebSocket(buildWsUrl(current, token))
      wsRef.current = ws
      const turn = { role: 'assistant', content: '', tool_calls: [] }
      setPending({ ...turn })
      setBusy(true)
      setStatus('连接中…')
      setUsage(null)

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data)
        switch (msg.type) {
          case 'status':
            setStatus(msg.message || '')
            break
          case 'token':
            turn.content += msg.text
            setPending({ ...turn })
            break
          case 'tool_call':
            turn.tool_calls.push({ name: msg.name, arguments: msg.arguments, status: 'running' })
            setPending({ ...turn })
            break
          case 'tool_result': {
            const tc = turn.tool_calls.find((c) => c.name === msg.name && c.status === 'running')
            if (tc) {
              tc.status = msg.is_error ? 'error' : 'done'
              tc.output = msg.content
            }
            setPending({ ...turn })
            break
          }
          case 'error':
            setStatus(`错误: ${msg.message}`)
            break
          case 'done':
            if (wsRef.current === ws) {
              setHistory((prev) => [...prev, turn])
              setPending(null)
              setBusy(false)
              setStopping(false)
              setStatus('')
              ws.close()
              wsRef.current = null
              refreshSessions()
            }
            break
          case 'usage':
            setUsage({
              prompt_tokens: msg.prompt_tokens || 0,
              completion_tokens: msg.completion_tokens || 0,
              total_tokens: msg.total_tokens || 0,
              cache_creation_tokens: msg.cache_creation_tokens || 0,
              cache_read_tokens: msg.cache_read_tokens || 0,
              model: msg.model || '',
            })
            break
          case 'skill_activated':
            setActiveSkill({ name: msg.name, description: msg.description })
            break
          default:
            break
        }
      }
      ws.onerror = () => {
        setBusy(false)
        setStopping(false)
        setError('连接失败（请确认已启动服务且网络正常）')
      }
      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'user_message', content: text }))
      }
    },
    [current, busy, token, refreshSessions],
  )

  const submit = useCallback(
    (text) => {
      if (!current || busy || !text.trim()) return
      const content = text.trim()
      setDraft('')
      // 如果有activeSkill，附加skill命令
      const finalContent = activeSkill
        ? `${activeSkill.trigger} ${content}`
        : content
      setHistory((prev) => [...prev, { role: 'user', content }])
      send(finalContent)
    },
    [current, busy, send, activeSkill],
  )

  const stop = useCallback(() => {
    if (wsRef.current && busy) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }))
      setStopping(true)
      setStatus('正在停止…')
    }
  }, [busy])

  const saveConfig = useCallback(async () => {
    setError('')
    try {
      const { model: modelName } = propsDraft
      if (modelName && modelName.trim() && modelName.trim() !== config?.model) {
        const provider = modelName.trim().split('/')[0]
        await api('/api/config', {
          method: 'PUT',
          body: JSON.stringify({ provider, model: modelName.trim() }),
        })
      }
      for (const [name, p] of Object.entries(propsDraft.providers)) {
        const body = { provider: name }
        if (p.api_key && p.api_key.trim()) body.api_key = p.api_key.trim()
        if (p.api_base && p.api_base.trim()) body.api_base = p.api_base.trim()
        await api('/api/config', { method: 'PUT', body: JSON.stringify(body) })
      }
      setShowSettings(false)
      loadConfig()
      setModel(propsDraft.model.trim() || model)
    } catch (e) {
      setError(`保存配置失败: ${e.message}`)
    }
  }, [propsDraft, config, loadConfig, model])

  const switchModel = useCallback(async (modelName) => {
    if (!modelName || modelName === model) {
      setShowModelPicker(false)
      return
    }
    setError('')
    try {
      await api('/api/model/switch', {
        method: 'POST',
        body: JSON.stringify({ model: modelName }),
      })
      setModel(modelName)
      setShowModelPicker(false)
    } catch (e) {
      setError(`切换模型失败: ${e.message}`)
    }
  }, [model])

  const addProvider = useCallback(async (name, apiKey, apiBase) => {
    setError('')
    try {
      await api('/api/providers', {
        method: 'POST',
        body: JSON.stringify({ name, api_key: apiKey, api_base: apiBase }),
      })
      loadProviders()
      loadConfig()
    } catch (e) {
      setError(`添加提供商失败: ${e.message}`)
    }
  }, [loadProviders, loadConfig])

  const removeProvider = useCallback(async (name) => {
    setError('')
    try {
      await api(`/api/providers/${name}`, { method: 'DELETE' })
      loadProviders()
      loadConfig()
    } catch (e) {
      setError(`删除提供商失败: ${e.message}`)
    }
  }, [loadProviders, loadConfig])

  const testConfig = useCallback(async () => {
    setTesting(true)
    setError('')
    try {
      const body = { model: propsDraft.model.trim() || config?.model }
      const data = await api('/api/config/test', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setConfig((c) => ({ ...c, test: data }))
      if (!data.ok) setError(`连接测试失败: ${data.error}`)
    } catch (e) {
      setError(`连接测试失败: ${e.message}`)
    } finally {
      setTesting(false)
    }
  }, [propsDraft, config])

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="logo">果冻</span>
        </div>
        <button className="new-chat" onClick={newSession} disabled={busy}>
          <span className="new-chat-plus">+</span> 新会话
        </button>
        <div className="session-label">会话 · 悬停重命名 / 删除</div>
        <ul className="session-list">
          {sessions.map((s) => (
            <SessionItem
              key={s.session_id}
              s={s}
              active={s.session_id === current}
              onOpen={openSession}
              onRename={renameSession}
              onDelete={deleteSession}
            />
          ))}
        </ul>
        {sessions.length === 0 && <div className="empty-sessions">暂无会话，点击上方新建</div>}
      </aside>

      <main className="chat">
        <header className="chat-header">
          <span className="chat-title">
            {current ? `会话 ${current.slice(0, 8)}` : '未选择会话'}
          </span>
          <div className="header-right">
            {model && (
              <div className="model-picker" ref={modelPickerRef}>
                <button
                  className="model-picker-btn"
                  onClick={() => setShowModelPicker(!showModelPicker)}
                  title="点击切换模型"
                >
                  <span className="model-name">{model.split('/').pop() || model}</span>
                  <span className="model-provider">{model.split('/')[0]}</span>
                  <span className="dropdown-arrow">▾</span>
                </button>
                {showModelPicker && (
                  <div className="model-dropdown">
                    {providers.length === 0 ? (
                      <div className="dropdown-empty">暂无提供商，请先配置 API Key</div>
                    ) : (
                      providers.map((p) => (
                        <div className="dropdown-group" key={p.name}>
                          <div className="dropdown-group-label">{p.name}</div>
                          {(allModels[p.name] || []).map((m) => (
                            <button
                              key={m}
                              className={`dropdown-item ${m === model ? 'active' : ''}`}
                              onClick={() => switchModel(m)}
                            >
                              <span className="item-model">{m.split('/').pop()}</span>
                              {m === model && <span className="item-check">✓</span>}
                            </button>
                          ))}
                          {(allModels[p.name] || []).length === 0 && (
                            <div className="dropdown-empty-small">输入自定义模型名</div>
                          )}
                          <div className="dropdown-custom">
                            <input
                              type="text"
                              placeholder="自定义模型名 (如 qwen3.5:latest)"
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  const v = e.target.value.trim()
                                  if (v) switchModel(`${p.name}/${v}`)
                                }
                              }}
                            />
                          </div>
                        </div>
                      ))
                    )}
                    <div className="dropdown-footer">
                      <button onClick={() => { setShowModelPicker(false); setShowSettings(true) }}>
                        管理提供商 ⚙
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
            <button className="settings-btn" title="模型与 API Key 设置" onClick={() => setShowSettings(true)}>
              ⚙
            </button>
            {usage && (
              <div className="usage-badge" title={`模型: ${usage.model}\n缓存命中率: ${usage.cache_read_tokens}/${usage.prompt_tokens}`}>
                <span className="usage-tokens">
                  ↑{usage.prompt_tokens} ↓{usage.completion_tokens} ({usage.total_tokens})
                </span>
                {usage.cache_read_tokens > 0 && (
                  <span className="usage-cache">
                    ⚡{Math.round(usage.cache_read_tokens / Math.max(usage.prompt_tokens, 1) * 100)}%
                  </span>
                )}
              </div>
            )}
          </div>
        </header>
        {error && <div className="error-banner">{error}</div>}
        <div className="messages" ref={messagesRef}>
          {history.length === 0 && !pending && (
            <div className="empty-state">
              <div className="empty-icon">✦</div>
              <div className="empty-title">你好，我是果冻</div>
              <div className="empty-sub">可以让我读写文件、执行命令、搜索网络、管理待办</div>
            </div>
          )}
          {history
            .filter((m) => m.role !== 'tool')
            .map((m, i) => (
              <Message key={i} msg={m} />
            ))}
          {pending && <Message msg={pending} />}
          {status && <div className="status">{status}</div>}
        </div>
        {activeSkill && (
          <div className="skill-active">
            <span className="skill-badge">
              🔧 {activeSkill.name}
              <button className="skill-clear" onClick={() => {
                setActiveSkill(null)
                // 重新加载会话以清除skill
                if (current) refreshSessions().then(() => {
                  // 重置skill状态
                })
              }}>✕</button>
            </span>
            <span className="skill-desc">{activeSkill.description}</span>
          </div>
        )}
        <div className="composer">
          <div className="composer-box">
            <input
              type="text"
              placeholder={current ? '输入消息，Enter 发送' : '请先新建或选择会话'}
              disabled={!current || busy}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submit(draft)
              }}
            />
            {busy ? (
              <button className="stop-btn" onClick={stop} disabled={stopping}>
                ■
              </button>
            ) : (
              <button
                className="send-btn"
                disabled={!current || !draft.trim()}
                onClick={() => submit(draft)}
              >
                ➤
              </button>
            )}
          </div>
        </div>
      </main>
      {showSettings && (
        <SettingsModal
          config={config}
          draft={propsDraft}
          testing={testing}
          onChange={setPropsDraft}
          onSave={saveConfig}
          onTest={testConfig}
          onClose={() => setShowSettings(false)}
          onAddProvider={addProvider}
          onRemoveProvider={removeProvider}
        />
      )}
    </div>
  )
}

function SessionItem({ s, active, onOpen, onRename, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const inputRef = useRef(null)

  const startEdit = () => {
    setDraft(s.title || '')
    setEditing(true)
    requestAnimationFrame(() => inputRef.current && inputRef.current.focus())
  }

  const commit = () => {
    setEditing(false)
    const title = draft.trim()
    if (title && title !== s.title) onRename(s.session_id, title)
  }

  const handleDelete = (e) => {
    e.stopPropagation()
    const name = s.title || `会话 ${s.session_id.slice(0, 8)}`
    if (window.confirm(`确定删除会话「${name}」？此操作不可恢复。`)) {
      onDelete(s.session_id)
    }
  }

  return (
    <li className={active ? 'active' : ''} onClick={() => !editing && onOpen(s.session_id)}>
      {editing ? (
        <input
          ref={inputRef}
          className="session-edit"
          value={draft}
          maxLength={64}
          onChange={(e) => setDraft(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <>
          <span className="session-title">{s.title || `会话 ${s.session_id.slice(0, 8)}`}</span>
          <span className="session-meta">
            {s.updated_at ? s.updated_at.slice(5, 16) : ''} · {s.message_count} 条
            <button
              className="rename-btn"
              title="重命名会话"
              onClick={(e) => {
                e.stopPropagation()
                startEdit()
              }}
            >
              ✎
            </button>
            <button
              className="delete-btn"
              title="删除会话"
              onClick={handleDelete}
            >
              🗑
            </button>
          </span>
        </>
      )}
    </li>
  )
}

const TOOL_ICONS = {
  web_fetch: '🌐',
  bash: '💻',
  read: '📖',
  write: '✏️',
  edit: '✏️',
  ls: '📁',
  glob: '🔍',
  grep: '🔍',
  todo_add: '✅',
  todo_done: '☑️',
  todo_list: '📋',
}

function toolSummary(tc) {
  const args = tc.arguments || {}
  const s = (v) => (typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v))
  switch (tc.name) {
    case 'web_fetch':
      return s(args.url) || '抓取网页'
    case 'bash':
      return `$ ${s(args.command)}`
    case 'read':
      return s(args.path) || '读取文件'
    case 'write':
      return s(args.path) || '写入文件'
    case 'edit':
      return s(args.path) || '修改文件'
    case 'ls':
      return s(args.path) || '.'
    case 'glob':
    case 'grep':
      return args.path ? `${s(args.pattern)} @ ${s(args.path)}` : s(args.pattern)
    case 'todo_add':
      return s(args.content) || '添加待办'
    case 'todo_done':
      return s(args.todo_id) || '完成待办'
    case 'todo_list':
      return '查看待办列表'
    default:
      return Object.entries(args)
        .map(([k, v]) => `${k}=${s(v)}`)
        .join(' ')
  }
}

function SettingsModal({ config, draft, testing, onChange, onSave, onTest, onClose, onAddProvider, onRemoveProvider }) {
  const providers = draft.providers || {}
  const test = config?.test
  const [showAddProvider, setShowAddProvider] = useState(false)
  const [newProvider, setNewProvider] = useState({ name: '', api_key: '', api_base: '', default_model: '' })

  const handleAddProvider = () => {
    if (!newProvider.name.trim()) return
    onAddProvider({
      name: newProvider.name.trim(),
      api_key: newProvider.api_key.trim() || undefined,
      api_base: newProvider.api_base.trim() || undefined,
      default_model: newProvider.default_model.trim() || undefined,
    })
    setNewProvider({ name: '', api_key: '', api_base: '', default_model: '' })
    setShowAddProvider(false)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">⚙ 模型与 API Key</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-section">
          <div className="field-label">当前模型（litellm 格式，带提供商前缀）</div>
          <input
            type="text"
            className="field-input"
            placeholder="openai/gpt-4o-mini"
            value={draft.model}
            onChange={(e) => onChange({ ...draft, model: e.target.value })}
          />
        </div>

        <div className="modal-section">
          <div className="field-label">
            提供商
            <button
              className="btn-add-provider"
              onClick={() => setShowAddProvider(!showAddProvider)}
            >
              {showAddProvider ? '取消' : '+ 添加提供商'}
            </button>
          </div>

          {showAddProvider && (
            <div className="add-provider-form">
              <input
                type="text"
                className="field-input"
                placeholder="提供商名（如 openai, deepseek, anthropic）"
                value={newProvider.name}
                onChange={(e) => setNewProvider({ ...newProvider, name: e.target.value })}
              />
              <input
                type="password"
                className="field-input"
                placeholder="API Key"
                value={newProvider.api_key}
                onChange={(e) => setNewProvider({ ...newProvider, api_key: e.target.value })}
              />
              <input
                type="text"
                className="field-input"
                placeholder="Base URL（可选，如 https://api.deepseek.com）"
                value={newProvider.api_base}
                onChange={(e) => setNewProvider({ ...newProvider, api_base: e.target.value })}
              />
              <input
                type="text"
                className="field-input"
                placeholder="默认模型（可选，如 gpt-4o-mini）"
                value={newProvider.default_model}
                onChange={(e) => setNewProvider({ ...newProvider, default_model: e.target.value })}
              />
              <button className="btn primary" onClick={handleAddProvider}>
                确认添加
              </button>
            </div>
          )}

          {Object.keys(providers).length === 0 && !showAddProvider ? (
            <div className="field-hint">
              暂无已配置的提供商。填写上方模型后保存，即可用 /apikey（终端）或此处（Web）添加。
            </div>
          ) : (
            Object.entries(providers).map(([name, p]) => (
              <div className="provider-row" key={name}>
                <div className="provider-row-head">
                  <span className="provider-name">{name}</span>
                  <span className="provider-meta">
                    {config?.providers?.find((x) => x.name === name)?.api_key_masked || '未配置 Key'}
                  </span>
                  <button
                    className="btn-remove-provider"
                    title="删除提供商"
                    onClick={() => {
                      if (window.confirm(`确定删除提供商「${name}」？`)) {
                        onRemoveProvider(name)
                      }
                    }}
                  >
                    ✕
                  </button>
                </div>
                <input
                  type="password"
                  className="field-input"
                  placeholder="API Key（留空则不修改）"
                  value={p.api_key}
                  onChange={(e) =>
                    onChange({
                      ...draft,
                      providers: { ...providers, [name]: { ...p, api_key: e.target.value } },
                    })
                  }
                />
                <input
                  type="text"
                  className="field-input"
                  placeholder="Base URL（可选，如 https://api.deepseek.com）"
                  value={p.api_base}
                  onChange={(e) =>
                    onChange({
                      ...draft,
                      providers: { ...providers, [name]: { ...p, api_base: e.target.value } },
                    })
                  }
                />
              </div>
            ))
          )}
        </div>

        {test && (
          <div className={`test-result ${test.ok ? 'ok' : 'fail'}`}>
            {test.ok
              ? `✓ 连接成功，延迟 ${test.latency_ms}ms（${test.model}）`
              : `✗ 连接失败（${test.model}）: ${test.error}`}
          </div>
        )}

        <div className="modal-actions">
          <button className="btn primary" onClick={onSave}>保存</button>
          <button className="btn plain" onClick={onTest} disabled={testing}>
            {testing ? '测试中…' : '测试连接'}
          </button>
          <button className="btn plain" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}

function Message({ msg }) {
  const isUser = msg.role === 'user'
  const [expanded, setExpanded] = useState(() => new Set())
  const [userToggled, setUserToggled] = useState(() => new Set())
  const calls = msg.tool_calls || []

  const isOpen = (i) => {
    const tc = calls[i]
    return expanded.has(i) || (tc.status === 'running' && !userToggled.has(i))
  }

  const allOpen = calls.length > 0 && calls.every((_, i) => isOpen(i))

  const toggle = (i) => {
    setUserToggled((prev) => new Set(prev).add(i))
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }

  const toggleAll = () => {
    const all = new Set(calls.map((_, i) => i))
    setUserToggled(all)
    setExpanded(allOpen ? new Set() : all)
  }

  return (
    <div className={`msg ${isUser ? 'user' : 'assistant'}`}>
      <div className={`avatar ${isUser ? 'me' : 'ai'}`}>{isUser ? '我' : 'AI'}</div>
      <div className="msg-body">
        {calls.length > 0 && (
          <div className="tool-calls">
            {calls.length > 1 && (
              <button className="tool-toggle-all" onClick={toggleAll}>
                {allOpen ? '收起全部' : '展开全部'}
              </button>
            )}
            {calls.map((tc, i) => {
              const open = isOpen(i)
              const done = tc.status == null || tc.status === 'done'
              const statusText = tc.status === 'error' ? '出错' : done ? '完成' : '运行中'
              const cardClass = tc.status === 'error' ? 'error' : done ? 'done' : 'running'
              return (
                <div key={i} className={`tool-card ${cardClass}`}>
                  <div className="tool-head" onClick={() => toggle(i)}>
                    <div className="tool-head-left">
                      <span className={`tool-arrow ${open ? 'open' : ''}`}>▶</span>
                      <span className="tool-icon">{TOOL_ICONS[tc.name] || '🔧'}</span>
                      <span className="tool-name">{tc.name}</span>
                      <span className="tool-summary">{toolSummary(tc)}</span>
                    </div>
                    <span className="tool-status">{statusText}</span>
                  </div>
                  <div className={`tool-collapse ${open ? 'open' : ''}`}>
                    <div className="tool-collapse-inner">
                      <pre className="tool-args">
                        {typeof tc.arguments === 'string'
                          ? tc.arguments
                          : JSON.stringify(tc.arguments, null, 2)}
                      </pre>
                      {tc.output != null && <pre className="tool-output">{tc.output}</pre>}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {msg.content && (
          <div className="bubble">
            {isUser ? msg.content : <Markdown text={msg.content} />}
          </div>
        )}
      </div>
    </div>
  )
}
