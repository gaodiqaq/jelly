import React from 'react'
import Markdown from './Markdown'
import Artifacts from './Artifacts'

const TOOL_LABELS = { read: '阅读文件', write: '创建或更新文件', edit: '修改文件', ls: '浏览目录', glob: '查找文件', grep: '搜索内容', bash: '执行命令', web_fetch: '读取网页', open: '打开文件', todo_add: '规划工作', todo_done: '完成步骤' }

export function ExecutionList({ calls }) {
  return <div className="execution-list">{calls.map((call, i) => <details className={`execution-row ${call.status || 'done'}`} key={`${call.call_id || call.name}-${i}`}>
    <summary><span className="execution-symbol">{call.status === 'running' ? '◌' : call.status === 'error' ? '!' : '✓'}</span><strong>{TOOL_LABELS[call.name] || call.name}</strong><span className="execution-target">{call.arguments?.path || call.arguments?.url || call.arguments?.command || ''}</span><span className="execution-state">{call.status === 'running' ? '进行中' : call.status === 'error' ? '未完成' : '完成'}</span><span className="execution-chevron">⌄</span></summary>
    <div className="execution-detail"><span className="eyebrow">{call.name}</span><pre>{JSON.stringify(call.arguments || {}, null, 2)}</pre>{call.output != null && <pre>{call.output}</pre>}</div>
  </details>)}</div>
}

export default function Conversation({ history, pending, artifacts, onOpenFile, activeFile, busy }) {
  const turns = []
  for (const message of [...history, ...(pending ? [pending] : [])]) {
    if (message.role === 'tool') continue
    if (message.role === 'assistant' && turns.at(-1)?.role === 'assistant') turns.at(-1).messages.push(message)
    else turns.push({ role: message.role, messages: [message] })
  }
  return <>{turns.map((turn, index) => {
    const user = turn.role === 'user'
    const last = index === turns.length - 1
    const calls = turn.messages.flatMap(m => m.tool_calls || [])
    return <article key={index} className={`conversation-turn ${user ? 'from-user' : 'from-agent'}`}>
      <div className="turn-heading"><span className={`turn-avatar${user ? '' : ' agent'}`}>{user ? '你' : <span className="jelly-cube" />}</span><strong>{user ? '你' : '果冻'}</strong>{!user && <span className="agent-label">AGENT</span>}{last && busy && !user && <span className="turn-working">正在处理</span>}</div>
      <div className="turn-content">
        {calls.length > 0 && <div className="execution-overview"><span className={busy && last ? 'live-indicator' : 'done-indicator'} />{busy && last ? '工作进行中' : '执行记录'}<span>{calls.filter(c => c.status === 'done').length} / {calls.length} 步完成</span></div>}
        {turn.messages.map((message, i) => <React.Fragment key={i}>
          {message.content && <div className={user ? 'user-note' : 'agent-prose'}>{user ? message.content : <Markdown text={message.content} onOpenFile={onOpenFile} />}</div>}
          {!!message.tool_calls?.length && <ExecutionList calls={message.tool_calls} />}
        </React.Fragment>)}
        {last && !user && artifacts.length > 0 && <Artifacts artifacts={artifacts} onOpenFile={onOpenFile} activeFile={activeFile} compact />}
      </div>
    </article>
  })}</>
}
