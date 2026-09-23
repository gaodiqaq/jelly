import React, { useCallback, useEffect, useRef, useState } from 'react'

import Workspace from './Workspace'
import useTaskRun from './useTaskRun'
import ProjectDialog from './ProjectDialog'
import Artifacts from './Artifacts'
import Conversation, { ExecutionList } from './Conversation'
import useArtifacts from './useArtifacts'
import { api, authToken } from './api'
import { focusMenuItem, handleMenuKeyDown, handleTabListKeyDown } from './a11y'

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
  { value: 'deny', label: '禁用工具', icon: 'shield', desc: '只进行对话，拒绝所有工具调用' },
]

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
  const denyRef = useRef(null)
  const [deciding, setDeciding] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const argsText =
    typeof approval.arguments === 'string'
      ? approval.arguments
      : JSON.stringify(approval.arguments, null, 2)
  useEffect(() => {
    const previousFocus = document.activeElement
    setDecisionError('')
    setDeciding(false)
    denyRef.current?.focus()
    return () => {
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus()
    }
  }, [approval.id])

  const decide = async decision => {
    if (deciding) return
    setDeciding(true); setDecisionError('')
    try { await onDecide(approval.id, decision) }
    catch (error) { setDecisionError(error.message); setDeciding(false) }
  }

  return (
    <div className="approval-card" role="alertdialog" aria-modal="true" aria-labelledby="approval-title" aria-describedby="approval-hint" aria-busy={deciding} onKeyDown={event => keepFocusInside(event, event.currentTarget)}>
      <div className="approval-head">
        <span className="approval-icon"><Icon.shield /></span>
        <span className="approval-title" id="approval-title">权限确认</span>
        <span className="approval-tool">
          <span className={`approval-badge ${approval.read_only ? 'ro' : 'mut'}`}>
            {approval.read_only ? '只读' : '修改'}
          </span>
          <span className="approval-name">{approval.name}</span>
        </span>
      </div>
      {argsText !== '{}' && <pre className="approval-args">{argsText}</pre>}
      {decisionError && <div className="approval-error" role="alert">{decisionError}</div>}
      <div className="approval-actions">
        <button className="approval-btn allow" disabled={deciding} onClick={() => decide('approve')}>
          允许
        </button>
        <button ref={denyRef} className="approval-btn deny" disabled={deciding} onClick={() => decide('deny')}>
          拒绝
        </button>
        <button className="approval-btn ghost" disabled={deciding} onClick={() => decide('approve_all')}>
          本轮始终允许
        </button>
        <button className="approval-btn ghost" disabled={deciding} onClick={() => decide('deny_all')}>
          本轮始终拒绝
        </button>
      </div>
      <div className="approval-hint" id="approval-hint">Agent 已暂停，等待你的决定后继续</div>
    </div>
  )
}

function draftProviders(providers) {
  const out = {}
  for (const p of providers || []) {
    out[p.name] = { api_key: '', api_base: p.api_base || '' }
  }
  return out
}

function keepFocusInside(event, container) {
  if (event.key !== 'Tab') return
  const focusable = Array.from(container?.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
  ) || []).filter(element => !element.hidden)
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable.at(-1)
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault(); last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus()
  }
}

function AccessGate({ onAuthenticate }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const cardRef = useRef(null)
  async function submit(event) {
    event.preventDefault()
    if (!value.trim()) return
    setBusy(true)
    setError('')
    try { await onAuthenticate(value.trim()) }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  return <div className="access-gate" role="dialog" aria-modal="true" aria-labelledby="access-title" onKeyDown={event => keepFocusInside(event, cardRef.current)}><form ref={cardRef} className="access-card" onSubmit={submit}>
    <span className="jelly-cube access-cube" aria-hidden="true" />
    <span className="eyebrow">PRIVATE WORKSPACE</span>
    <h1 id="access-title">回到你的 Jelly 工作台</h1>
    <p>这个工作台启用了访问保护。输入服务启动时配置的访问口令。</p>
    <label>访问口令<input autoFocus name="access-token" type="password" autoComplete="off" value={value} onChange={event => setValue(event.target.value)} placeholder="AGENT_WEB_TOKEN" /></label>
    {error && <p className="form-error" role="alert">{error}</p>}
    <button className="solid-button" disabled={busy || !value.trim()}>{busy ? '正在验证…' : '进入工作台'}</button>
  </form></div>
}

export default function App() {
  const followOutput = useRef(true)
  const [theme, setTheme] = useState(() => localStorage.getItem('jelly_theme') || 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('jelly_theme', theme)
  }, [theme])
  const [sessions, setSessions] = useState([])
  const [sessionsLoaded, setSessionsLoaded] = useState(false)
  const [projects, setProjects] = useState([])
  const [projectsLoaded, setProjectsLoaded] = useState(false)
  const [projectsLoadError, setProjectsLoadError] = useState('')
  const [projectWarnings, setProjectWarnings] = useState([])
  const [projectId, setProjectId] = useState(() => localStorage.getItem('jelly_project') || null)
  const [projectDialog, setProjectDialog] = useState(null)
  const [showArchived, setShowArchived] = useState(false)
  const [projectAction, setProjectAction] = useState(null)
  const [taskFilter, setTaskFilter] = useState('all')
  const [taskSearch, setTaskSearch] = useState('')
  const [conversationTab, setConversationTab] = useState('chat')
  const [artifactRevision, setArtifactRevision] = useState(0)
  const [openedFiles, setOpenedFiles] = useState([])
  const [current, setCurrent] = useState(() => localStorage.getItem('jelly_current') || null)
  const [sessionsLoadError, setSessionsLoadError] = useState('')
  const [history, setHistory] = useState(EMPTY_HISTORY)
  const [token, setToken] = useState(authToken())
  const [authRequired, setAuthRequired] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(null)
  const [draft, setDraft] = useState('')
  const [recovery, setRecovery] = useState(null)
  const [model, setModel] = useState('')
  const [providers, setProviders] = useState([])
  const [allModels, setAllModels] = useState({})
  const [config, setConfig] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showModelPicker, setShowModelPicker] = useState(false)
  const [showSkillMenu, setShowSkillMenu] = useState(false)
  const [propsDraft, setPropsDraft] = useState({ model: '', cwd: '', providers: {} })
  const [testing, setTesting] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [providerAction, setProviderAction] = useState(null)
  const [stopping, setStopping] = useState(false)
  const [usage, setUsage] = useState(null)
  const [skills, setSkills] = useState([])
  const [activeSkill, setActiveSkill] = useState(null)
  const [permMode, setPermMode] = useState('ask')
  const [showPermMenu, setShowPermMenu] = useState(false)
  const [pendingApproval, setPendingApproval] = useState(null)
  const [wsOpen, setWsOpen] = useState(() => localStorage.getItem('jelly_ws_open') === '1' || (localStorage.getItem('jelly_ws_open') === null && window.innerWidth > 1100))
  const [wsWidth, setWsWidth] = useState(() => Number(localStorage.getItem('jelly_ws_width')) || 480)
  const [wsFile, setWsFile] = useState(null)
  const messagesRef = useRef(null)
  const modelPickerRef = useRef(null)
  const permMenuRef = useRef(null)
  const skillMenuRef = useRef(null)
  const draftRef = useRef(null)
  const [navOpen, setNavOpen] = useState(false)
  const activeSession = sessions.find(s => s.session_id === current)
  const activeProject = projects.find(p => p.id === (activeSession?.project_id || projectId))
  const recoverableInput = recovery?.sessionId === current ? recovery.content : ''
  const activeProjects = projects.filter(project => !project.archived_at)
  const archivedProjects = projects.filter(project => project.archived_at)
  const projectArchived = !!activeProject?.archived_at
  const effectiveModel = activeProject?.model || model
  const effectivePermission = activeProject?.permission || permMode
  const artifactState = useArtifacts(current, busy, artifactRevision)
  const artifacts = artifactState.files
  const scope = current ? `session_id=${encodeURIComponent(current)}` : projectId ? `project_id=${encodeURIComponent(projectId)}` : ''
  const visibleSessions = sessions.filter(s => (!projectId || s.project_id === projectId) && (taskFilter !== 'running' || s.running || (s.session_id === current && busy)) && (s.title || '').toLowerCase().includes(taskSearch.toLowerCase()))
  const allCalls = [...history, ...(pending ? [pending] : [])].flatMap(m => m.tool_calls || [])
  const knownArtifacts = useRef(new Set())

  const lockWorkspace = useCallback(() => {
    sessionStorage.removeItem('agent_web_token')
    localStorage.removeItem('agent_web_token')
    setToken(''); setAuthRequired(true); setSessions([]); setProjects([])
    setSessionsLoaded(false); setProjectsLoaded(false)
    setSessionsLoadError(''); setProjectsLoadError(''); setProjectWarnings([])
    setCurrent(null); setProjectId(null); setHistory(EMPTY_HISTORY); setPending(null)
    setOpenedFiles([]); setWsFile(null); setError('')
  }, [])
  useEffect(() => {
    window.addEventListener('jelly:unauthorized', lockWorkspace)
    return () => window.removeEventListener('jelly:unauthorized', lockWorkspace)
  }, [lockWorkspace])
  useEffect(() => { if (showSettings) setSettingsError('') }, [showSettings])

  const loadProjects = useCallback(async () => {
    try {
      const data = await api('/api/projects')
      setProjects(data.projects)
      setProjectWarnings(data.warnings || [])
      setProjectsLoadError('')
    } catch (e) {
      setProjectsLoadError(e.message)
      setError(`加载项目失败: ${e.message}`)
    } finally {
      setProjectsLoaded(true)
    }
  }, [])
  useEffect(() => { loadProjects() }, [loadProjects, token])
  useEffect(() => {
    if (activeSession) setProjectId(activeSession.project_id || null)
  }, [current, activeSession?.project_id])
  useEffect(() => {
    if (projectId) localStorage.setItem('jelly_project', projectId)
    else localStorage.removeItem('jelly_project')
  }, [projectId])
  useEffect(() => {
    knownArtifacts.current = new Set()
    setWsFile(null); setOpenedFiles([]); setConversationTab('chat'); setActiveSkill(null)
    setPendingApproval(null); setHistory(EMPTY_HISTORY); setPending(null); setStatus(''); setBusy(false)
  }, [current])

  const SUGGESTIONS = ['帮我梳理这个项目的目录结构', '写一个贪吃蛇网页小游戏', '查一下今天的科技新闻']

  useEffect(() => {
    api('/api/health')
      .then((data) => setModel(data.model || ''))
      .catch(() => {})
  }, [])

  const loadProviders = useCallback(() => {
    return api('/api/providers')
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
    return api('/api/config')
      .then((data) => {
        setConfig(data)
        setPropsDraft({ model: data.model || '', cwd: data.cwd || '', providers: draftProviders(data.providers) })
        return loadProviders()
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
    if (mode === effectivePermission) return
    setError('')
    try {
      if (activeProject) {
        await api(`/api/projects/${activeProject.id}`, { method: 'PUT', body: JSON.stringify({ ...activeProject, permission: mode }) })
        await loadProjects()
        return
      }
      const data = await api('/api/permissions', {
        method: 'PUT',
        body: JSON.stringify({ mode }),
      })
      setPermMode(data.mode || mode)
    } catch (e) {
      setError(`切换权限模式失败: ${e.message}`)
    }
  }, [effectivePermission, activeProject, loadProjects])

  // 点击外部关闭弹出菜单
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) {
        setShowModelPicker(false)
      }
      if (permMenuRef.current && !permMenuRef.current.contains(e.target)) {
        setShowPermMenu(false)
      }
      if (skillMenuRef.current && !skillMenuRef.current.contains(e.target)) {
        setShowSkillMenu(false)
      }
    }
    if (showModelPicker || showPermMenu || showSkillMenu) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [showModelPicker, showPermMenu, showSkillMenu])

  useEffect(() => {
    if (!navOpen) return
    requestAnimationFrame(() => document.querySelector('.sidebar .workspace-selector')?.focus())
    const closeOnEscape = event => {
      if (event.key === 'Escape') {
        setNavOpen(false)
        document.querySelector('.menu-btn')?.focus()
      }
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [navOpen])

  useEffect(() => {
    loadConfig()
    loadSkills()
    loadPermissions()
  }, [token, loadConfig, loadSkills, loadPermissions])

  useEffect(() => {
    const el = messagesRef.current
    if (el && followOutput.current) el.scrollTop = el.scrollHeight
  }, [history, pending, status, pendingApproval])

  // ---------- 工作台面板 ----------
  useEffect(() => {
    localStorage.setItem('jelly_ws_open', wsOpen ? '1' : '0')
  }, [wsOpen])

  useEffect(() => {
    localStorage.setItem('jelly_ws_width', String(wsWidth))
  }, [wsWidth])

  const openFile = useCallback((path) => {
    const root = (activeSession?.cwd || activeProject?.cwd || config?.cwd || '').replaceAll('\\', '/').replace(/\/$/, '')
    let relative = path.replaceAll('\\', '/')
    if (root && relative.toLowerCase().startsWith(root.toLowerCase() + '/')) relative = relative.slice(root.length + 1)
    setWsFile(relative)
    setOpenedFiles(files => files.includes(relative) ? files : [...files, relative])
    setWsOpen(true)
  }, [activeSession?.cwd, activeProject?.cwd, config?.cwd])

  useEffect(() => {
    const newFiles = artifacts.filter(file => !knownArtifacts.current.has(file.path))
    const next = newFiles.find(file => /\.(md|markdown)$/i.test(file.path)) || newFiles.find(file => /\.html?$/i.test(file.path)) || newFiles[0]
    for (const file of artifacts) knownArtifacts.current.add(file.path)
    const wideScreen = window.matchMedia('(min-width: 1101px)').matches
    if (next && wideScreen && !wsFile) openFile(next.path)
  }, [artifacts, openFile, wsFile])

  const closeTab = path => {
    const remaining = openedFiles.filter(file => file !== path)
    setOpenedFiles(remaining)
    if (wsFile === path) setWsFile(remaining.at(-1) || null)
  }

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
      .then((data) => {
        setSessions(data.sessions || [])
        setSessionsLoadError('')
      })
      .catch((e) => {
        setSessionsLoadError(e.message)
        setError(
          e.status === 401
            ? '访问口令已失效，请重新登录'
            : `加载会话列表失败: ${e.message}`,
        )
      })
      .finally(() => setSessionsLoaded(true))
  }, [])

  useEffect(() => {
    if (sessionsLoaded && !sessionsLoadError && current && !sessions.some(session => session.session_id === current)) {
      setCurrent(null)
    }
  }, [sessionsLoaded, sessionsLoadError, sessions, current])
  useEffect(() => {
    if (projectsLoaded && !projectsLoadError && projectId && !projects.some(project => project.id === projectId)) {
      setProjectId(null)
    }
  }, [projectsLoaded, projectsLoadError, projects, projectId])

  useEffect(() => {
    refreshSessions()
  }, [refreshSessions, token])
  useEffect(() => {
    const timer = setInterval(refreshSessions, 4000)
    return () => clearInterval(timer)
  }, [refreshSessions])

  const newSession = useCallback(async () => {
    setError('')
    if (projectArchived) {
      setError('项目已归档，请先恢复项目再新建任务')
      return
    }
    try {
      const data = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ project_id: projectId }) })
      setSessions(previous => [{
        session_id: data.session_id,
        project_id: data.project_id,
        cwd: data.cwd,
        title: '',
        message_count: 0,
        updated_at: new Date().toISOString(),
        running: false,
      }, ...previous.filter(session => session.session_id !== data.session_id)])
      setCurrent(data.session_id)
      setNavOpen(false)
      setHistory(EMPTY_HISTORY)
      setPending(null)
      refreshSessions()
      requestAnimationFrame(() => draftRef.current?.focus())
    } catch (e) {
      setError(
          e.status === 401
          ? '访问口令已失效，请重新登录'
          : `创建会话失败: ${e.message}`,
      )
    }
  }, [refreshSessions, projectId, projectArchived])

  const startWith = useCallback(
    async (text) => {
      if (busy || projectArchived) return
      if (!current) await newSession()
      setDraft(text)
      requestAnimationFrame(() => draftRef.current && draftRef.current.focus())
    },
    [busy, current, newSession, projectArchived],
  )

  const openSession = useCallback(async (id) => {
    followOutput.current = true
    setNavOpen(false)
    setCurrent(id)
    setHistory(EMPTY_HISTORY)
    setPending(null)
    setStatus('')
    setError('')
    setBusy(false)
    setStopping(false)
    setPendingApproval(null)
  }, [])

  const selectProject = id => {
    setProjectId(id); setCurrent(null); setHistory([]); setPending(null); setWsFile(null)
    setTaskFilter('all'); setTaskSearch(''); setNavOpen(false)
  }

  const setProjectArchived = useCallback(async (id, archived) => {
    setProjectAction(id)
    try {
      const saved = await api(`/api/projects/${id}/archive`, {
        method: 'PATCH',
        body: JSON.stringify({ archived }),
      })
      setProjects(previous => previous.map(project => project.id === id ? saved : project))
      return saved
    } catch (e) {
      throw new Error(`${archived ? '归档' : '恢复'}项目失败: ${e.message}`)
    } finally {
      setProjectAction(null)
    }
  }, [])

  const restoreProject = useCallback(async id => {
    setError('')
    try { await setProjectArchived(id, false) }
    catch (e) { setError(e.message) }
  }, [setProjectArchived])

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

  const run = useTaskRun(current, token, state => {
    setHistory(state.history || [])
    setPending(state.pending || null)
    setBusy(state.busy)
    setStopping(!!state.stopping && state.busy)
    setStatus(state.status || '')
    setPendingApproval(state.approval || null)
    setUsage(state.usage || null)
    if (state.skill) setActiveSkill(state.skill)
    if (state.error) setError(state.error)
    setRecovery(state.retry_content && !state.busy ? { sessionId: current, content: state.retry_content } : null)
  }, setError)

  useEffect(() => {
    if (current) localStorage.setItem('jelly_current', current)
    else localStorage.removeItem('jelly_current')
  }, [current])
  useEffect(() => { if (!busy) refreshSessions() }, [busy, refreshSessions])
  const submit = async text => {
    if (!current || busy || !text.trim()) return
    if (projectArchived) {
      setError('项目已归档，请先恢复项目再继续任务')
      return
    }
    setError('')
    setBusy(true)
    try { await run.send(text.trim()); setDraft('') }
    catch { setBusy(false) }
  }
  const stop = async () => {
    setStopping(true); setStatus('正在停止，等待执行器退出…')
    try { await run.stop() } catch { setStopping(false) }
  }
  const decideApproval = async (id, decision) => {
    await run.approve(id, decision)
    setPendingApproval(null)
  }

  const saveConfig = useCallback(async () => {
    if (settingsSaving) return
    setSettingsSaving(true); setSettingsError('')
    try {
      const { model: modelName, cwd: cwdDraft } = propsDraft
      if (modelName && modelName.trim() && modelName.trim() !== config?.model) {
        const provider = modelName.trim().split('/')[0]
        await api('/api/config', {
          method: 'PUT',
          body: JSON.stringify({ provider, model: modelName.trim() }),
        })
      }
      if (cwdDraft && cwdDraft.trim() && cwdDraft.trim() !== config?.cwd) {
        await api('/api/config', {
          method: 'PUT',
          body: JSON.stringify({ provider: 'openai', cwd: cwdDraft.trim() }),
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
      setSettingsError(`保存配置失败: ${e.message}`)
    } finally {
      setSettingsSaving(false)
    }
  }, [settingsSaving, propsDraft, config, loadConfig, model])

  const switchModel = useCallback(async (modelName) => {
    if (!modelName || modelName === effectiveModel) {
      setShowModelPicker(false)
      return
    }
    setError('')
    try {
      if (activeProject) {
        await api(`/api/projects/${activeProject.id}`, { method: 'PUT', body: JSON.stringify({ ...activeProject, model: modelName }) })
        await loadProjects(); setShowModelPicker(false)
        return
      }
      await api('/api/model/switch', {
        method: 'POST',
        body: JSON.stringify({ model: modelName }),
      })
      setModel(modelName)
      setShowModelPicker(false)
    } catch (e) {
      setError(`切换模型失败: ${e.message}`)
    }
  }, [effectiveModel, activeProject, loadProjects])

  const addProvider = useCallback(async (provider) => {
    if (providerAction) return
    setProviderAction('add'); setSettingsError('')
    try {
      await api('/api/providers', {
        method: 'POST',
        body: JSON.stringify(provider),
      })
      await Promise.all([loadProviders(), loadConfig()])
    } catch (e) {
      const message = `添加提供商失败: ${e.message}`
      setSettingsError(message)
      throw new Error(message)
    } finally {
      setProviderAction(null)
    }
  }, [providerAction, loadProviders, loadConfig])

  const authenticate = useCallback(async (candidate) => {
    sessionStorage.setItem('agent_web_token', candidate)
    localStorage.removeItem('agent_web_token')
    try {
      const [sessionData, projectData] = await Promise.all([
        api('/api/sessions'),
        api('/api/projects'),
      ])
      setSessions(sessionData.sessions || [])
      setProjects(projectData.projects || [])
      setSessionsLoaded(true)
      setProjectsLoaded(true)
      setToken(candidate)
      setAuthRequired(false)
      setError('')
    } catch (e) {
      sessionStorage.removeItem('agent_web_token')
      throw e
    }
  }, [])

  const removeProvider = useCallback(async (name) => {
    if (providerAction) return
    setProviderAction(name); setSettingsError('')
    try {
      await api(`/api/providers/${name}`, { method: 'DELETE' })
      await Promise.all([loadProviders(), loadConfig()])
    } catch (e) {
      setSettingsError(`删除提供商失败: ${e.message}`)
    } finally {
      setProviderAction(null)
    }
  }, [providerAction, loadProviders, loadConfig])

  const testConfig = useCallback(async () => {
    setTesting(true)
    setSettingsError('')
    try {
      const body = { model: propsDraft.model.trim() || config?.model }
      const data = await api('/api/config/test', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setConfig((c) => ({ ...c, test: data }))
      if (!data.ok) setSettingsError(`连接测试失败: ${data.error}`)
    } catch (e) {
      setSettingsError(`连接测试失败: ${e.message}`)
    } finally {
      setTesting(false)
    }
  }, [propsDraft, config])

  if (authRequired) return <AccessGate onAuthenticate={authenticate} />
  if (!sessionsLoaded || !projectsLoaded) {
    return <div className="boot-screen" role="status" aria-live="polite"><span className="jelly-cube wobble" aria-hidden="true" /><span>正在准备工作台…</span></div>
  }
  if ((projectsLoadError && projects.length === 0) || (sessionsLoadError && sessions.length === 0)) {
    return <div className="fatal-screen load-failure" role="alert"><span className="jelly-cube fatal-cube" aria-hidden="true" /><span className="eyebrow">WORKSPACE UNAVAILABLE</span><h1>工作台暂时没有准备好。</h1><p>{projectsLoadError ? `项目读取失败：${projectsLoadError}` : `任务读取失败：${sessionsLoadError}`}</p><div className="fatal-actions"><button className="solid-button" onClick={() => { setProjectsLoaded(false); setSessionsLoaded(false); loadProjects(); refreshSessions() }}>重新读取</button>{token && <button className="quiet-button" onClick={lockWorkspace}>重新输入访问口令</button>}</div></div>
  }

  return (
    <div className={`layout${navOpen ? ' nav-open' : ''}${wsOpen ? ' ws-open' : ''}`}>
      <aside className="sidebar" id="workspace-navigation" aria-label="项目与任务" onKeyDown={event => { if (navOpen && window.matchMedia('(max-width: 760px)').matches) keepFocusInside(event, event.currentTarget) }}>
        <div className="sidebar-header">
          <div className="brand">
            <span className="jelly-cube brand-cube" aria-hidden="true" />
            <span className="brand-text">
              <span className="brand-name">果冻</span>
              <span className="brand-sub">Jelly · Studio</span>
            </span>
          </div>
        </div>
        <button className={`workspace-selector${projectArchived ? ' archived' : ''}${activeProject?.available === false ? ' unavailable' : ''}`} onClick={() => activeProject ? setProjectDialog(activeProject) : setProjectDialog({})}>
          <span className="workspace-monogram">{(activeProject?.name || 'J').slice(0, 1)}</span><span>{activeProject?.name || '我的工作空间'}</span>{projectArchived ? <small>已归档</small> : activeProject?.available === false ? <small>目录不可用</small> : null}<Icon.chevron />
        </button>
        <button className="new-chat" disabled={projectArchived} title={projectArchived ? '恢复项目后即可新建任务' : '新建任务'} onClick={() => projectsLoaded && !activeProjects.length ? setProjectDialog({}) : newSession()}>
          <Icon.plus /> {projectArchived ? '项目已归档' : '新建任务'} <kbd>＋</kbd>
        </button>
        <nav className="main-navigation" aria-label="工作空间导航">
          <button className={!projectId && taskFilter === 'all' ? 'active' : ''} onClick={() => selectProject(null)}><Icon.folder /><span>工作台</span><small>{sessions.length}</small></button>
          <button className={taskFilter === 'running' ? 'active' : ''} onClick={() => setTaskFilter(f => f === 'running' ? 'all' : 'running')}><span className="nav-spinner">◌</span><span>进行中的任务</span><small>{sessions.filter(s => s.running).length}</small></button>
        </nav>
        <label className="task-search"><span>⌕</span><input aria-label="搜索任务" placeholder="搜索任务" value={taskSearch} onChange={e => setTaskSearch(e.target.value)} /></label>
        <div className="nav-section-heading"><span>项目</span><button className="quiet-button" aria-label="新建项目" onClick={() => setProjectDialog({})}>＋</button></div>
        <nav className="project-list" aria-label="项目">
          {activeProjects.map(project => <button key={project.id} className={`${projectId === project.id ? 'active' : ''}${project.available === false ? ' unavailable' : ''}`} title={project.available === false ? '绑定的工作目录当前不可用' : project.name} onClick={() => selectProject(project.id)}><Icon.folder /><span>{project.name}</span>{project.available === false && <i aria-label="目录不可用">!</i>}<small>{sessions.filter(s => s.project_id === project.id).length}</small></button>)}
          {!activeProjects.length && <button className="create-project-link" onClick={() => setProjectDialog({})}>创建新项目 ↗</button>}
        </nav>
        {!!projectWarnings.length && <div className="catalog-warning" role="status">已隔离 {projectWarnings.length} 个损坏的项目记录，其他项目不受影响。</div>}
        {!!archivedProjects.length && <div className="archived-projects">
          <button className="archived-toggle" aria-expanded={showArchived} aria-controls="archived-project-list" onClick={() => setShowArchived(value => !value)}><span>已归档</span><small>{archivedProjects.length}</small><Icon.chevron /></button>
          {showArchived && <div id="archived-project-list" className="archived-project-list">
            {archivedProjects.map(project => <div key={project.id} className={projectId === project.id ? 'active' : ''}>
              <button className="archived-project-open" onClick={() => selectProject(project.id)}><Icon.folder /><span>{project.name}</span></button>
              <button className="archived-project-restore" disabled={projectAction === project.id} aria-label={`恢复项目 ${project.name}`} onClick={() => restoreProject(project.id)}>{projectAction === project.id ? '…' : '恢复'}</button>
            </div>)}
          </div>}
        </div>}
        <div className="nav-section-heading"><span>{taskFilter === 'running' ? '进行中' : '最近任务'}</span><span>{visibleSessions.length}</span></div>
        <ul className="session-list">
          {visibleSessions.map((s) => (
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
        {visibleSessions.length === 0 && <div className="empty-sessions">这里还没有任务<br />从一个想法开始。</div>}
        <div className="sidebar-bottom"><span className="sidebar-status"><i /> 本地工作空间</span><button onClick={() => setShowSettings(true)}><Icon.gear /> 设置与连接</button></div>
      </aside>
      <button type="button" className="nav-backdrop" aria-label="关闭项目与任务导航" onClick={() => { setNavOpen(false); requestAnimationFrame(() => document.querySelector('.menu-btn')?.focus()) }} />

      <main className="chat">
        <header className="chat-header">
          <div className="header-left">
            <button className="menu-btn" aria-label="打开会话列表" aria-expanded={navOpen} aria-controls="workspace-navigation" onClick={() => setNavOpen(true)}>
              <Icon.menu />
            </button>
            <div className="task-heading"><span className="task-breadcrumb">{activeProject?.name || '工作空间'} <span> / </span> 任务</span><span className="chat-title">{activeSession?.title || (activeProject ? '开始一件新的作品' : '我的工作室')}</span></div>
          </div>
          <div className="header-right">
            <span className={`task-state ${pendingApproval ? 'awaiting' : busy ? 'working' : ''}`}><i />{pendingApproval ? '等待确认' : stopping ? '正在停止' : busy ? '进行中' : current && history.length ? '已就绪' : '准备开始'}</span>
            <button className="theme-button" aria-label="切换明暗主题" title="切换明暗主题" onClick={() => setTheme(t => t === 'light' ? 'dark' : 'light')}>{theme === 'light' ? '◐' : '☼'}</button>

            <button
              className={`ws-toggle${wsOpen ? ' active' : ''}`}
              title="工作台：文件树与预览"
              aria-label="工作台：文件树与预览"
              aria-expanded={wsOpen}
              aria-controls="artifact-workspace"
              onClick={() => setWsOpen(!wsOpen)}
            >
              <Icon.folder />
            </button>
            <button className="settings-btn" title="模型与 API Key 设置" aria-label="模型与 API Key 设置" onClick={() => setShowSettings(true)}>
              <Icon.gear />
            </button>
            {token && <button className="auth-button" title="锁定工作台并更换访问口令" aria-label="锁定工作台并更换访问口令" onClick={lockWorkspace}>钥</button>}
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
        <div className="conversation-tabs" role="tablist" aria-label="任务内容" onKeyDown={handleTabListKeyDown}>
          <button id="task-tab-chat" role="tab" tabIndex={conversationTab === 'chat' ? 0 : -1} aria-selected={conversationTab === 'chat'} aria-controls="task-panel" onClick={() => setConversationTab('chat')}>对话</button>
          <button id="task-tab-execution" role="tab" tabIndex={conversationTab === 'execution' ? 0 : -1} aria-selected={conversationTab === 'execution'} aria-controls="task-panel" onClick={() => setConversationTab('execution')}>执行记录 <small>{allCalls.length}</small></button>
          <button id="task-tab-files" role="tab" tabIndex={conversationTab === 'files' ? 0 : -1} aria-selected={conversationTab === 'files'} aria-controls="task-panel" onClick={() => setConversationTab('files')}>文件 <small>{artifacts.length}</small></button>
          <span className="project-context" title={activeSession?.cwd || activeProject?.cwd || config?.cwd}><Icon.folder />{activeProject?.name || '本地目录'}</span>
        </div>
        {error && <div className="error-banner" role="alert"><span>{error}</span><button aria-label="关闭错误提示" onClick={() => setError('')}>×</button></div>}
        <div
          className="messages"
          id="task-panel"
          role="tabpanel"
          aria-labelledby={`task-tab-${conversationTab}`}
          aria-busy={busy}
          ref={messagesRef}
          onScroll={e => { const el = e.currentTarget; followOutput.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100 }}
        >
          {projectsLoaded && activeProjects.length === 0 && !projectId && !current && conversationTab === 'chat' ? (
            <div className="empty-state onboarding-state">
              <div className="onboarding-card">
                <span className="eyebrow">WELCOME TO JELLY</span>
                <h1>{archivedProjects.length ? '工作台已经收拾好了。' : '先给工作一个安静的空间。'}</h1>
                <p>{archivedProjects.length ? '已归档项目仍完整保留。恢复一个继续打磨，或为新想法创建空间。' : '绑定一个本地目录。之后的任务、对话、文件与版本都会留在这个项目里。'}</p>
                <ol>
                  <li><span>01</span><div><strong>创建项目</strong><small>选择已有目录，文件始终由你保管</small></div></li>
                  <li><span>02</span><div><strong>说清目标</strong><small>果冻会边执行边留下可检查的记录</small></div></li>
                  <li><span>03</span><div><strong>直接看成果</strong><small>文档、代码与网页会在右侧自动打开</small></div></li>
                </ol>
                <div className="onboarding-actions">
                  <button className="solid-button" onClick={() => setProjectDialog({})}>{archivedProjects.length ? '创建新项目' : '创建第一个项目'}</button>
                  {!!archivedProjects.length && <button className="quiet-button" onClick={() => { setShowArchived(true); setNavOpen(true) }}>查看已归档项目</button>}
                  <button className="quiet-button" onClick={() => setShowSettings(true)}>设置模型连接</button>
                </div>
              </div>
            </div>
          ) : history.length === 0 && !pending && !recoverableInput && conversationTab === 'chat' && (
            <div className="empty-state">
              <div>
                <span className="jelly-cube empty-cube" aria-hidden="true" />
                <div className="empty-title">把想法，慢慢做成作品。</div>
                <div className="empty-sub">
                  和果冻一起写文档、打磨代码、制作网页。
                  <br />
                  成果在旁边生长，每次文件修改都留下版本。
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
            {conversationTab === 'chat' && <Conversation history={history} pending={pending} artifacts={artifacts} busy={busy} onOpenFile={openFile} activeFile={wsFile} />}
            {conversationTab === 'chat' && recoverableInput && !busy && <div className="prompt-recovery" role="status">
              <div><strong>输入尚未写入任务历史</strong><p>任务在启动前中断了。原文仍保存在运行记录中，可调整设置后继续。</p><pre>{recoverableInput}</pre></div>
              <button type="button" disabled={projectArchived} onClick={() => { setDraft(previous => previous.includes(recoverableInput) ? previous : previous ? `${previous}\n\n${recoverableInput}` : recoverableInput); requestAnimationFrame(() => draftRef.current?.focus()) }}>放回输入框</button>
            </div>}
            {conversationTab === 'execution' && <><div className="view-heading"><span className="eyebrow">ACTIVITY</span><h2>每一步，清晰可见。</h2><p>展开一条记录，查看实际输入和执行结果。</p></div>{allCalls.length ? <ExecutionList calls={allCalls} /> : <p className="pane-empty">还没有工具执行记录。</p>}</>}
            {conversationTab === 'files' && <><div className="view-heading"><span className="eyebrow">DELIVERABLES</span><h2>这次合作的成果</h2><p>选择一个文件，在旁边继续查看和打磨。</p></div><Artifacts artifacts={artifacts} onOpenFile={openFile} activeFile={wsFile} />{!artifacts.length && <p className="pane-empty">任务创建或修改的文件会出现在这里。</p>}{artifactState.error && <p role="alert" className="form-error">{artifactState.error}</p>}</>}
            {pendingApproval && <ApprovalCard approval={pendingApproval} onDecide={decideApproval} />}
            {status && (
              <div className="status" aria-hidden="true">
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
          {projectArchived && <div className="archived-project-notice" role="status"><span><strong>项目已归档</strong> · 当前内容仅供查看</span><button type="button" disabled={projectAction === activeProject.id} onClick={() => restoreProject(activeProject.id)}>{projectAction === activeProject.id ? '恢复中…' : '恢复项目'}</button></div>}
          <div className="composer-box">
            <textarea
              ref={draftRef}
              placeholder={projectArchived ? '恢复项目后即可继续对话' : current ? '描述你想做的作品，或继续打磨眼前的成果…' : '请先新建或选择会话'}
              disabled={!current || busy || projectArchived}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                  e.preventDefault()
                  submit(draft)
                }
              }}
              rows={2}
            />
            <div className="composer-toolbar">
              <div className="toolbar-left">
                <div className={`perm-picker perm-mode-${effectivePermission}`} ref={permMenuRef}>
                  <button
                    className="perm-btn"
                    title="权限模式"
                    aria-label="权限模式"
                    aria-haspopup="menu"
                    aria-expanded={showPermMenu}
                    aria-controls="permission-menu"
                    onClick={() => setShowPermMenu(!showPermMenu)}
                    onKeyDown={event => {
                      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                        event.preventDefault()
                        setShowPermMenu(true)
                        focusMenuItem(permMenuRef.current, event.key === 'ArrowUp' ? 'last' : 'first')
                      } else if (event.key === 'Escape') {
                        setShowPermMenu(false)
                      }
                    }}
                  >
                    <PermCurrentIcon mode={effectivePermission} />
                    <span className="perm-btn-label">
                      {PERMISSION_MODES.find((m) => m.value === effectivePermission)?.label || '手动审批'}
                    </span>
                    <span className="perm-arrow"><Icon.chevron /></span>
                  </button>
                  {showPermMenu && (
                    <div
                      className="perm-popup"
                      id="permission-menu"
                      role="menu"
                      aria-label="执行权限"
                      onKeyDown={event => handleMenuKeyDown(event, () => {
                        setShowPermMenu(false)
                        permMenuRef.current?.querySelector('.perm-btn')?.focus()
                      })}
                    >
                      {PERMISSION_MODES.map((m) => {
                        const IconCmp = PERM_ICONS[m.icon] || Icon.shield
                        return (
                          <button
                            key={m.value}
                            className={`perm-option ${m.value === effectivePermission ? 'active' : ''}`}
                            role="menuitemradio"
                            aria-checked={m.value === effectivePermission}
                            tabIndex={m.value === effectivePermission ? 0 : -1}
                            onClick={() => {
                              changePermission(m.value)
                              requestAnimationFrame(() => permMenuRef.current?.querySelector('.perm-btn')?.focus())
                            }}
                          >
                            <span className="perm-option-icon"><IconCmp /></span>
                            <span className="perm-option-text">
                              <span className="perm-option-label">{m.label}</span>
                              <span className="perm-option-desc">{m.desc}</span>
                            </span>
                            {m.value === effectivePermission && (
                              <span className="perm-option-check"><Icon.check /></span>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
            {effectiveModel && (
              <div className="model-picker" ref={modelPickerRef}>
                <button
                  className="model-picker-btn"
                  onClick={() => setShowModelPicker(!showModelPicker)}
                  title="点击切换模型"
                  aria-haspopup="dialog"
                  aria-expanded={showModelPicker}
                  aria-controls="model-picker-dialog"
                  onKeyDown={event => {
                    if (event.key === 'ArrowDown') {
                      event.preventDefault()
                      setShowModelPicker(true)
                      requestAnimationFrame(() => modelPickerRef.current?.querySelector('.dropdown-item, .dropdown-custom input, .dropdown-footer button')?.focus())
                    } else if (event.key === 'Escape') {
                      setShowModelPicker(false)
                    }
                  }}
                >
                  <span className="model-name">{effectiveModel.split('/').pop() || effectiveModel}</span>
                  <span className="model-provider">{effectiveModel.split('/')[0]}</span>
                  <span className="dropdown-arrow"><Icon.chevron /></span>
                </button>
                {showModelPicker && (
                  <div
                    className="model-dropdown"
                    id="model-picker-dialog"
                    role="dialog"
                    aria-label="选择模型"
                    onKeyDown={event => {
                      if (event.key === 'Escape') {
                        event.preventDefault()
                        setShowModelPicker(false)
                        modelPickerRef.current?.querySelector('.model-picker-btn')?.focus()
                      }
                    }}
                  >
                    {providers.length === 0 ? (
                      <div className="dropdown-empty">暂无提供商，请先配置 API Key</div>
                    ) : (
                      providers.map((p) => (
                        <div className="dropdown-group" key={p.name}>
                          <div className="dropdown-group-label">{p.name}</div>
                          {(allModels[p.name] || []).map((m) => (
                            <button
                              key={m}
                              className={`dropdown-item ${m === effectiveModel ? 'active' : ''}`}
                              onClick={() => switchModel(m)}
                            >
                              <span className="item-model">{m.split('/').pop()}</span>
                              {m === effectiveModel && <span className="item-check"><Icon.check /></span>}
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
                <div className="skill-btn-wrapper" ref={skillMenuRef}>
                  <button
                    className="toolbar-btn skill-btn"
                    title="Skill 命令"
                    aria-label="Skill 命令"
                    aria-haspopup="menu"
                    aria-expanded={showSkillMenu}
                    aria-controls="skill-menu"
                    disabled={skills.length === 0}
                    onClick={() => setShowSkillMenu(value => !value)}
                    onKeyDown={event => {
                      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                        event.preventDefault()
                        setShowSkillMenu(true)
                        focusMenuItem(skillMenuRef.current, event.key === 'ArrowUp' ? 'last' : 'first')
                      } else if (event.key === 'Escape') {
                        setShowSkillMenu(false)
                      }
                    }}
                  >
                    <Icon.zap />
                  </button>
                  {skills.length > 0 && showSkillMenu && (
                  <div
                    className="skill-popup"
                    id="skill-menu"
                    role="menu"
                    aria-label="Skill 命令"
                    onKeyDown={event => handleMenuKeyDown(event, () => {
                      setShowSkillMenu(false)
                      skillMenuRef.current?.querySelector('.skill-btn')?.focus()
                    })}
                  >
                    {skills.map((s, index) => (
                      <button
                        type="button"
                        key={s.name}
                        className="skill-item"
                        role="menuitem"
                        tabIndex={index === 0 ? 0 : -1}
                        onClick={() => {
                          setDraft(s.triggers[0] + ' ')
                          setShowSkillMenu(false)
                          requestAnimationFrame(() => draftRef.current?.focus())
                      }}
                      >
                        <span className="skill-item-name">{s.name}</span>
                        <span className="skill-item-desc">{s.description}</span>
                      </button>
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
                    disabled={!current || !draft.trim() || projectArchived}
                    onClick={() => submit(draft)}
                  >
                    <Icon.send />
                  </button>
                )}
              </div>
            </div>
          </div>
          <div className="composer-footnote"><span>{activeProject?.name || 'Jelly Studio'} · {projectArchived ? '已归档，内容完整保留' : '你的工作，由你掌控'}</span><span>{projectArchived ? '恢复后继续' : 'Enter 发送 · Shift + Enter 换行'}</span></div>
        </div>
        <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {pendingApproval ? '任务已暂停，等待权限确认。' : stopping ? '正在停止任务。' : busy ? (status || '任务正在进行。') : current && history.length ? '任务已完成，可以继续输入。' : ''}
        </div>
        </main>
      {wsOpen && (
        <>
          <button type="button" className="ws-backdrop" aria-label="关闭文件预览" onClick={() => setWsOpen(false)} />
          <aside className="workspace-panel" id="artifact-workspace" aria-label="文件预览与版本" style={{ width: wsWidth }}>
            <div className="ws-resizer" onPointerDown={startWsResize} aria-hidden="true" />
            <Workspace
              key={`${current || projectId || 'default'}:${token}`}
              scope={scope}
              artifacts={artifacts}
              openedFiles={openedFiles}
              onCloseTab={closeTab}
              onRestored={() => setArtifactRevision(n => n + 1)}
              sessionId={current}
              onUseRecipe={startWith}
              recipeSeed={history.filter(m => m.role === 'user').at(-1)?.content || ''}
              busy={busy}
              file={wsFile}
              onOpenFile={openFile}
              onCloseFile={closeFile}
              onClose={() => setWsOpen(false)}
            />
          </aside>
        </>
      )}
      {projectDialog && <ProjectDialog project={projectDialog.id ? projectDialog : null} onClose={() => setProjectDialog(null)} onArchive={setProjectArchived} onSaved={async project => { setProjectDialog(null); await loadProjects(); if (!projectDialog.id) selectProject(project.id) }} />}
      {showSettings && (
        <SettingsModal
          config={config}
          draft={propsDraft}
          testing={testing}
          saving={settingsSaving}
          error={settingsError}
          providerAction={providerAction}
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
    <li className={`session-item${active ? ' active' : ''}`}>
      {editing ? (
        <input
          ref={inputRef}
          className="session-edit"
          value={draft}
          maxLength={64}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <>
          <button
            type="button"
            className="session-open"
            aria-current={active ? 'page' : undefined}
            onClick={() => onOpen(s.session_id)}
          >
            <span className="session-title">{s.title || `会话 ${s.session_id.slice(0, 8)}`}</span>
            <span className="session-meta">{s.updated_at ? s.updated_at.slice(5, 16).replace('T', ' ') : ''} · {s.message_count} 条</span>
          </button>
          <span className="session-actions">
            <button
              type="button"
              className="rename-btn"
              title="重命名会话"
              aria-label={`重命名 ${s.title || '会话'}`}
              onClick={startEdit}
            >
              <Icon.pencil />
            </button>
            <button
              type="button"
              className="delete-btn"
              title="删除会话"
              aria-label={`删除 ${s.title || '会话'}`}
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

function SettingsModal({ config, draft, testing, saving, error, providerAction, onChange, onSave, onTest, onClose, onAddProvider, onRemoveProvider }) {
  const providers = draft.providers || {}
  const test = config?.test
  const [showAddProvider, setShowAddProvider] = useState(false)
  const [newProvider, setNewProvider] = useState({ name: '', api_key: '', api_base: '', default_model: '' })
  const modalRef = useRef(null)
  useEffect(() => {
    const previousFocus = document.activeElement
    const handleKeyDown = event => {
      if (event.key === 'Escape') onClose()
      else keepFocusInside(event, modalRef.current)
    }
    document.addEventListener('keydown', handleKeyDown)
    modalRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus()
    }
  }, [])

  const handleAddProvider = async () => {
    if (!newProvider.name.trim()) return
    try {
      await onAddProvider({
        name: newProvider.name.trim(),
        api_key: newProvider.api_key.trim() || undefined,
        api_base: newProvider.api_base.trim() || undefined,
        default_model: newProvider.default_model.trim() || undefined,
      })
      setNewProvider({ name: '', api_key: '', api_base: '', default_model: '' })
      setShowAddProvider(false)
    } catch { /* 父组件展示错误，保留表单便于修正 */ }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="settings-modal" ref={modalRef} role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title" id="settings-title"><Icon.gear /> 模型与 API Key</span>
          <button className="modal-close" aria-label="关闭设置" onClick={onClose}><Icon.close /></button>
        </div>

        {error && <div className="settings-error" role="alert">{error}</div>}

        <div className="modal-section">
          <div className="field-label">当前模型（litellm 格式，带提供商前缀）</div>
          <input
            type="text"
            className="field-input"
            aria-label="当前模型"
            placeholder="openai/gpt-4o-mini"
            value={draft.model}
            onChange={(e) => onChange({ ...draft, model: e.target.value })}
          />
        </div>

        <div className="modal-section">
          <div className="field-label">工作目录（agent 的工具执行基目录）</div>
          <input
            type="text"
            className="field-input"
            aria-label="全局工作目录"
            placeholder="D:/work/project"
            value={draft.cwd}
            onChange={(e) => onChange({ ...draft, cwd: e.target.value })}
          />
          <div className="field-hint">切换后立即对下一轮对话生效，并持久化到配置。</div>
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
                aria-label="提供商名称"
                placeholder="提供商名（如 openai, deepseek, anthropic）"
                value={newProvider.name}
                onChange={(e) => setNewProvider({ ...newProvider, name: e.target.value })}
              />
              <input
                type="password"
                className="field-input"
                aria-label="新提供商 API Key"
                placeholder="API Key"
                value={newProvider.api_key}
                onChange={(e) => setNewProvider({ ...newProvider, api_key: e.target.value })}
              />
              <input
                type="text"
                className="field-input"
                aria-label="新提供商 Base URL"
                placeholder="Base URL（可选，如 https://api.deepseek.com）"
                value={newProvider.api_base}
                onChange={(e) => setNewProvider({ ...newProvider, api_base: e.target.value })}
              />
              <input
                type="text"
                className="field-input"
                aria-label="新提供商默认模型"
                placeholder="默认模型（可选，如 gpt-4o-mini）"
                value={newProvider.default_model}
                onChange={(e) => setNewProvider({ ...newProvider, default_model: e.target.value })}
              />
              <button className="btn primary" disabled={providerAction === 'add'} onClick={handleAddProvider}>
                {providerAction === 'add' ? '添加中…' : '确认添加'}
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
                    aria-label={`删除提供商 ${name}`}
                    disabled={providerAction === name}
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
                  aria-label={`${name} API Key`}
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
                  aria-label={`${name} Base URL`}
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
          <button className="btn primary" onClick={onSave} disabled={saving || testing || !!providerAction}>{saving ? '保存中…' : '保存'}</button>
          <button className="btn plain" onClick={onTest} disabled={testing || saving || !!providerAction}>
            {testing ? '测试中…' : '测试连接'}
          </button>
          <button className="btn plain" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
