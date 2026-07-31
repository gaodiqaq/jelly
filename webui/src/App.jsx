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
  const wsRef = useRef(null)
  const messagesRef = useRef(null)

  useEffect(() => {
    api('/api/health')
      .then((data) => setModel(data.model || ''))
      .catch(() => {})
  }, [])

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
            const tc = turn.tool_calls.find((c) => c.name === msg.name && !c.status)
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
              setStatus('')
              ws.close()
              wsRef.current = null
              refreshSessions()
            }
            break
          default:
            break
        }
      }
      ws.onerror = () => {
        setBusy(false)
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
      setHistory((prev) => [...prev, { role: 'user', content }])
      send(content)
    },
    [current, busy, send],
  )

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="logo">果冻</span>
        </div>
        <button className="new-chat" onClick={newSession} disabled={busy}>
          <span className="new-chat-plus">+</span> 新会话
        </button>
        <div className="token-box">
          <input
            type="password"
            placeholder="AGENT_WEB_TOKEN"
            value={token}
            onChange={(e) => {
              setToken(e.target.value)
              setError('')
              localStorage.setItem('agent_web_token', e.target.value)
              if (e.target.value) refreshSessions()
            }}
          />
        </div>
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
          {model && <span className="model-badge">{model}</span>}
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
          {history.map((m, i) => (
            <Message key={i} msg={m} />
          ))}
          {pending && <Message msg={pending} />}
          {status && <div className="status">{status}</div>}
        </div>
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
            <button
              className="send-btn"
              disabled={!current || busy || !draft.trim()}
              onClick={() => submit(draft)}
            >
              ➤
            </button>
          </div>
        </div>
      </main>
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

function Message({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`msg ${isUser ? 'user' : 'assistant'}`}>
      <div className={`avatar ${isUser ? 'me' : 'ai'}`}>{isUser ? '我' : 'AI'}</div>
      <div className="msg-body">
        {msg.content && (
          <div className="bubble">
            {isUser ? msg.content : <Markdown text={msg.content} />}
          </div>
        )}
        {msg.tool_calls &&
          msg.tool_calls.map((tc, i) => (
            <div key={i} className={`tool-card ${tc.status || ''}`}>
              <div className="tool-head">
                <span className="tool-name">🔧 {tc.name}</span>
                <span className="tool-status">{tc.status || '运行中…'}</span>
              </div>
              <pre className="tool-args">
                {typeof tc.arguments === 'string'
                  ? tc.arguments
                  : JSON.stringify(tc.arguments, null, 2)}
              </pre>
              {tc.output != null && <pre className="tool-output">{tc.output}</pre>}
            </div>
          ))}
      </div>
    </div>
  )
}
