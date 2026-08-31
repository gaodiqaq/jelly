import React, { useCallback, useEffect, useRef, useState } from 'react'
import Markdown from './Markdown'
import Workspace from './Workspace'
import { api } from './api'

const EMPTY_HISTORY = []

const STROKE = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }

const Icon = {
  plus: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M12 5v14M5 12h14" /></svg>
  ),
  gear: () => (
    <svg viewBox="0 0 24 24" {...STROKE}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
  send: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" /></svg>
  ),
  stop: () => (
    <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2.5" /></svg>
  ),
  pencil: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z" /></svg>
  ),
  trash: () => (
    <svg viewBox="0 0 24 24" {...STROKE}>
      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14M10 11v6M14 11v6" />
    </svg>
  ),
  menu: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M3 6h18M3 12h18M3 18h18" /></svg>
  ),
  close: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M18 6 6 18M6 6l12 12" /></svg>
  ),
  zap: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M13 2 3 14h9l-1 8 10-12h-9z" /></svg>
  ),
  chevron: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="m6 9 6 6 6-6" /></svg>
  ),
  check: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M20 6 9 17l-5-5" /></svg>
  ),
  shield: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>
  ),
  folder: () => (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>
  ),
  eye: () => (
    <svg viewBox="0 0 24 24" {...STROKE}>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
}

const PERMISSION_MODES = [
  { value: 'readonly', label: '只读', icon: 'eye', desc: '仅放行查看类操作，修改性工具直接拒绝' },
  { value: 'ask', label: '手动审批', icon: 'shield', desc: '每次修改性操作先向你确认' },
  { value: 'auto', label: '自动授权', icon: 'zap', desc: '全部工具自动放行，无需确认' },
]

const TOOL_ICONS = {
  web_fetch: 'globe',
  bash: 'terminal',
  read: 'file',
  write: 'file',
  edit: 'file',
  ls: 'folder',
  glob: 'search',
  grep: 'search',
  todo_add: 'check-square',
  todo_done: 'check-square',
  todo_list: 'list',
}

const TOOL_SVGS = {
  globe: (
    <svg viewBox="0 0 24 24" {...STROKE}>
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  ),
  terminal: (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="m4 17 6-6-6-6M12 19h8" /></svg>
  ),
  file: (
    <svg viewBox="0 0 24 24" {...STROKE}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6M9 13h6M9 17h6" />
    </svg>
  ),
  folder: (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>
  ),
  search: (
    <svg viewBox="0 0 24 24" {...STROKE}><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></svg>
  ),
  'check-square': (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="m9 11 3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>
  ),
  list: (
    <svg viewBox="0 0 24 24" {...STROKE}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>
  ),
}

function ToolIcon({ name }) {
  return <span className="tool-icon">{TOOL_SVGS[TOOL_ICONS[name]] || TOOL_SVGS.terminal}</span>
}

const PERM_ICONS = { eye: Icon.eye, shield: Icon.shield, zap: Icon.zap }

function PermCurrentIcon({ mode }) {
  const item = PERMISSION_MODES.find((m) => m.value === mode)
  const IconCmp = PERM_ICONS[item?.icon] || Icon.shield
  return (
    <span className="perm-icon">
      <IconCmp />
    </span>
  )
}

function ApprovalCard({ approval, onDecide }) {
  const argsText =
    typeof approval.arguments === 'string'
      ? approval.arguments
      : JSON.stringify(approval.arguments, null, 2)
  return (
    <div className="approval-card" role="alertdialog" aria-label="权限确认">
      <div className="approval-head">
        <span className="approval-icon"><Icon.shield /></span>
        <span className="approval-title">权限确认</span>
        <span className="approval-tool">
          <span className={`approval-badge ${approval.read_only ? 'ro' : 'mut'}`}>
            {approval.read_only ? '只读' : '修改'}
          </span>
          <span className="approval-name">{approval.name}</span>
        </span>
      </div>
      {argsText !== '{}' && <pre className="approval-args">{argsText}</pre>}
      <div className="approval-actions">
        <button className="approval-btn allow" onClick={() => onDecide(approval.id, 'approve')}>
          允许
        </button>
        <button className="approval-btn deny" onClick={() => onDecide(approval.id, 'deny')}>
          拒绝
        </button>
        <button className="approval-btn ghost" onClick={() => onDecide(approval.id, 'approve_all')}>
          本会话始终允许
        </button>
        <button className="approval-btn ghost" onClick={() => onDecide(approval.id, 'deny_all')}>
          本会话始终拒绝
        </button>
      </div>
      <div className="approval-hint">Agent 已暂停，等待你的决定后继续</div>
    </div>
  )
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
  const [permMode, setPermMode] = useState('ask')
  const [showPermMenu, setShowPermMenu] = useState(false)
  const [pendingApproval, setPendingApproval] = useState(null)
  const [wsOpen, setWsOpen] = useState(() => localStorage.getItem('jelly_ws_open') === '1')
  const [wsWidth, setWsWidth] = useState(() => Number(localStorage.getItem('jelly_ws_width')) || 420)
  const [wsFile, setWsFile] = useState(null)
  const wsRef = useRef(null)
  const messagesRef = useRef(null)
  const modelPickerRef = useRef(null)
  const permMenuRef = useRef(null)
  const stopTimeoutRef = useRef(null)
  const draftRef = useRef(null)
  const [navOpen, setNavOpen] = useState(false)

  const SUGGESTIONS = ['帮我梳理这个项目的目录结构', '写一个贪吃蛇网页小游戏', '查一下今天的科技新闻']

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

  const loadPermissions = useCallback(() => {
    api('/api/permissions')
      .then((data) => setPermMode(data.mode || 'ask'))
      .catch(() => {})
  }, [])

  const changePermission = useCallback(async (mode) => {
    setShowPermMenu(false)
    if (mode === permMode) return
    setError('')
    try {
      const data = await api('/api/permissions', {
        method: 'PUT',
        body: JSON.stringify({ mode }),
      })
      setPermMode(data.mode || mode)
    } catch (e) {
      setError(`切换权限模式失败: ${e.message}`)
    }
  }, [permMode])

  const decideApproval = useCallback((id, decision) => {
    const ws = wsRef.current
    setPendingApproval(null)
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'approval_decision', id, decision }))
    }
  }, [])

  // 点击外部关闭弹出菜单
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) {
        setShowModelPicker(false)
      }
      if (permMenuRef.current && !permMenuRef.current.contains(e.target)) {
        setShowPermMenu(false)
      }
    }
    if (showModelPicker || showPermMenu) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [showModelPicker, showPermMenu])

  useEffect(() => {
    if (token) {
      loadConfig()
      loadSkills()
      loadPermissions()
    }
  }, [token, loadConfig, loadSkills, loadPermissions])

  useEffect(() => {
    const el = messagesRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [history, pending, status, pendingApproval])

  // ---------- 工作台面板 ----------
  useEffect(() => {
    localStorage.setItem('jelly_ws_open', wsOpen ? '1' : '0')
  }, [wsOpen])

  useEffect(() => {
    localStorage.setItem('jelly_ws_width', String(wsWidth))
  }, [wsWidth])

  const openFile = useCallback((path) => {
    setWsFile(path)
    setWsOpen(true)
  }, [])

  const closeFile = useCallback(() => setWsFile(null), [])

  const startWsResize = useCallback((e) => {
    e.preventDefault()
    const panel = e.currentTarget.parentElement
    const onMove = (ev) => {
      const width = Math.min(720, Math.max(320, Math.round(panel.getBoundingClientRect().right - ev.clientX)))
      setWsWidth(width)
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [])

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

  const startWith = useCallback(
    async (text) => {
      if (busy) return
      if (!current) await newSession()
      setDraft(text)
      requestAnimationFrame(() => draftRef.current && draftRef.current.focus())
    },
    [busy, current, newSession],
  )

  const openSession = useCallback(async (id) => {
    setNavOpen(false)
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
          case 'approval_request':
            setPendingApproval({
              id: msg.id,
              name: msg.name,
              arguments: msg.arguments || {},
              read_only: !!msg.read_only,
            })
            break
          case 'error':
            setPendingApproval(null)
            setError(msg.message || '生成失败')
            break
          case 'done':
            if (wsRef.current === ws) {
              if (stopTimeoutRef.current) {
                clearTimeout(stopTimeoutRef.current)
                stopTimeoutRef.current = null
              }
              setHistory((prev) => [...prev, turn])
              setPending(null)
              setBusy(false)
              setStopping(false)
              setStatus('')
              setPendingApproval(null)
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
          case 'stopped':
            if (wsRef.current === ws) {
              if (stopTimeoutRef.current) {
                clearTimeout(stopTimeoutRef.current)
                stopTimeoutRef.current = null
              }
              setHistory((prev) => {
                if (turn.content || turn.tool_calls.length > 0) {
                  return [...prev, turn]
                }
                return prev
              })
              setPending(null)
              setBusy(false)
              setStopping(false)
              setStatus('')
              setPendingApproval(null)
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
      setError('')
      setHistory((prev) => [...prev, { role: 'user', content }])
      send(content)
    },
    [current, busy, send],
  )

  const stop = useCallback(() => {
    if (wsRef.current && busy) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }))
      setStopping(true)
      setStatus('正在停止…')
      // 超时保护：5秒后如果还没响应，强制重置状态
      stopTimeoutRef.current = setTimeout(() => {
        if (stopping || busy) {
          setBusy(false)
          setStopping(false)
          setStatus('')
          if (wsRef.current) {
            wsRef.current.close()
            wsRef.current = null
          }
          refreshSessions()
        }
      }, 5000)
    }
  }, [busy, stopping])

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
    <div className={`layout${navOpen ? ' nav-open' : ''}${wsOpen ? ' ws-open' : ''}`}>
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="brand">
            <span className="jelly-cube brand-cube" aria-hidden="true" />
            <span className="brand-text">
              <span className="brand-name">果冻</span>
              <span className="brand-sub">Jelly · Agent</span>
            </span>
          </div>
        </div>
        <button className="new-chat" onClick={newSession} disabled={busy}>
          <Icon.plus /> 新会话
        </button>
        <div className="session-label">历史会话</div>
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
        {sessions.length === 0 && <div className="empty-sessions">暂无会话<br />点击上方「新会话」开始</div>}
      </aside>
      <div className="nav-backdrop" onClick={() => setNavOpen(false)} />

      <main className="chat">
        <header className="chat-header">
          <div className="header-left">
            <button className="menu-btn" aria-label="打开会话列表" onClick={() => setNavOpen(true)}>
              <Icon.menu />
            </button>
            <span className="chat-title">
              {current ? `会话 ${current.slice(0, 8)}` : '未选择会话'}
            </span>
          </div>
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
                  <span className="dropdown-arrow"><Icon.chevron /></span>
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
                              {m === model && <span className="item-check"><Icon.check /></span>}
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
                                  const prefix =
                                    (allModels[p.name] || [])[0]?.split('/')[0] || p.name
                                  if (v) switchModel(`${prefix}/${v}`)
                                }
                              }}
                            />
                          </div>
                        </div>
                      ))
                    )}
                    <div className="dropdown-footer">
                      <button onClick={() => { setShowModelPicker(false); setShowSettings(true) }}>
                        管理提供商
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
            <button
              className={`ws-toggle${wsOpen ? ' active' : ''}`}
              title="工作台：文件树与预览"
              aria-label="工作台：文件树与预览"
              onClick={() => setWsOpen(!wsOpen)}
            >
              <Icon.folder />
            </button>
            <button className="settings-btn" title="模型与 API Key 设置" aria-label="模型与 API Key 设置" onClick={() => setShowSettings(true)}>
              <Icon.gear />
            </button>
            {usage && (
              <div className="usage-badge" title={`模型: ${usage.model}\n缓存命中率: ${usage.cache_read_tokens}/${usage.prompt_tokens}`}>
                <span className="usage-tokens">
                  ↑{usage.prompt_tokens} ↓{usage.completion_tokens} ({usage.total_tokens})
                </span>
                {usage.cache_read_tokens > 0 && (
                  <span className="usage-cache">
                    缓存 {Math.round(usage.cache_read_tokens / Math.max(usage.prompt_tokens, 1) * 100)}%
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
              <div>
                <span className="jelly-cube empty-cube" aria-hidden="true" />
                <div className="empty-title">你好，我是果冻</div>
                <div className="empty-sub">
                  可以让我读写文件、执行命令、搜索网络、管理待办
                  <br />
                  点击消息里的文件路径，可在右侧工作台直接预览
                </div>
                <div className="empty-chips">
                  {SUGGESTIONS.map((t) => (
                    <button key={t} className="chip" onClick={() => startWith(t)}>
                      {t}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
          <div className="thread">
            {history
              .filter((m) => m.role !== 'tool')
              .map((m, i) => (
                <Message key={i} msg={m} onOpenFile={openFile} />
              ))}
            {pending && <Message msg={pending} thinking={busy} onOpenFile={openFile} />}
            {pendingApproval && <ApprovalCard approval={pendingApproval} onDecide={decideApproval} />}
            {status && (
              <div className="status">
                <span className="status-dot" />
                {status}
              </div>
            )}
          </div>
        </div>
        {activeSkill && (
          <div className="skill-active">
            <span className="skill-badge">
              <Icon.zap /> {activeSkill.name}
              <button
                className="skill-clear"
                title="关闭该技能"
                aria-label="关闭该技能"
                onClick={async () => {
                  if (current) {
                    try {
                      await api(`/api/sessions/${current}/skill`, { method: 'DELETE' })
                    } catch (e) {
                      setError(`关闭技能失败: ${e.message}`)
                    }
                  }
                  setActiveSkill(null)
                }}
              >
                <Icon.close />
              </button>
            </span>
            <span className="skill-desc">{activeSkill.description}</span>
          </div>
        )}
        <div className="composer">
          <div className="composer-box">
            <textarea
              ref={draftRef}
              placeholder={current ? '输入消息，Enter 发送，Shift+Enter 换行' : '请先新建或选择会话'}
              disabled={!current || busy}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit(draft)
                }
              }}
              rows={2}
            />
            <div className="composer-toolbar">
              <div className="toolbar-left">
                <div className={`perm-picker perm-mode-${permMode}`} ref={permMenuRef}>
                  <button
                    className="perm-btn"
                    title="权限模式"
                    aria-label="权限模式"
                    onClick={() => setShowPermMenu(!showPermMenu)}
                  >
                    <PermCurrentIcon mode={permMode} />
                    <span className="perm-btn-label">
                      {PERMISSION_MODES.find((m) => m.value === permMode)?.label || '手动审批'}
                    </span>
                    <span className="perm-arrow"><Icon.chevron /></span>
                  </button>
                  {showPermMenu && (
                    <div className="perm-popup">
                      {PERMISSION_MODES.map((m) => {
                        const IconCmp = PERM_ICONS[m.icon] || Icon.shield
                        return (
                          <button
                            key={m.value}
                            className={`perm-option ${m.value === permMode ? 'active' : ''}`}
                            onClick={() => changePermission(m.value)}
                          >
                            <span className="perm-option-icon"><IconCmp /></span>
                            <span className="perm-option-text">
                              <span className="perm-option-label">{m.label}</span>
                              <span className="perm-option-desc">{m.desc}</span>
                            </span>
                            {m.value === permMode && (
                              <span className="perm-option-check"><Icon.check /></span>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
                <div className="skill-btn-wrapper">
                  <button className="toolbar-btn skill-btn" title="Skill 命令" aria-label="Skill 命令" disabled={skills.length === 0}>
                    <Icon.zap />
                  </button>
                  {skills.length > 0 && (
                  <div className="skill-popup">
                    {skills.map((s) => (
                      <div
                        key={s.name}
                        className="skill-item"
                        onClick={() => {
                          setDraft(s.triggers[0] + ' ')
                      }}
                      >
                        <span className="skill-item-name">{s.triggers[0]}</span>
                        <span className="skill-item-desc">{s.description}</span>
                      </div>
                    ))}
                  </div>
                  )}
                </div>
              </div>
              <div className="toolbar-right">
                {busy ? (
                  <button className="stop-btn" title="停止生成" aria-label="停止生成" onClick={stop} disabled={stopping}>
                    <Icon.stop />
                  </button>
                ) : (
                  <button
                    className="send-btn"
                    title="发送"
                    aria-label="发送"
                    disabled={!current || !draft.trim()}
                    onClick={() => submit(draft)}
                  >
                    <Icon.send />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
        </main>
      {wsOpen && (
        <>
          <div className="ws-backdrop" onClick={() => setWsOpen(false)} />
          <aside className="workspace-panel" style={{ width: wsWidth }}>
            <div className="ws-resizer" onPointerDown={startWsResize} aria-hidden="true" />
            <Workspace
              file={wsFile}
              onOpenFile={openFile}
              onCloseFile={closeFile}
              onClose={() => setWsOpen(false)}
            />
          </aside>
        </>
      )}
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
            {s.updated_at ? s.updated_at.slice(5, 16).replace('T', ' ') : ''} · {s.message_count} 条
            <button
              className="rename-btn"
              title="重命名会话"
              onClick={(e) => {
                e.stopPropagation()
                startEdit()
              }}
            >
              <Icon.pencil />
            </button>
            <button
              className="delete-btn"
              title="删除会话"
              onClick={handleDelete}
            >
              <Icon.trash />
            </button>
          </span>
        </>
      )}
    </li>
  )
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
          <span className="modal-title"><Icon.gear /> 模型与 API Key</span>
          <button className="modal-close" aria-label="关闭设置" onClick={onClose}><Icon.close /></button>
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
                    <Icon.close />
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

function Message({ msg, thinking, onOpenFile }) {
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
      <div className={`avatar ${isUser ? 'me' : 'ai'}`}>
        {isUser ? '我' : <span className={`jelly-cube${thinking ? ' wobble' : ''}`} aria-hidden="true" />}
      </div>
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
              const args = tc.arguments || {}
              const pathArg = args && typeof args.path === 'string' ? args.path : ''
              return (
                <div key={i} className={`tool-card ${cardClass}`}>
                  <div className="tool-head" onClick={() => toggle(i)}>
                    <div className="tool-head-left">
                      <span className={`tool-arrow ${open ? 'open' : ''}`}>▶</span>
                      <ToolIcon name={tc.name} />
                      <span className="tool-name">{tc.name}</span>
                      {pathArg && onOpenFile ? (
                        <button
                          className="file-link tool-path"
                          title="在右侧预览该文件"
                          onClick={(e) => {
                            e.stopPropagation()
                            onOpenFile(pathArg)
                          }}
                        >
                          {pathArg}
                        </button>
                      ) : (
                        <span className="tool-summary">{toolSummary(tc)}</span>
                      )}
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
            {isUser ? msg.content : <Markdown text={msg.content} onOpenFile={onOpenFile} />}
          </div>
        )}
      </div>
    </div>
  )
}
