import React, { useEffect, useMemo, useRef } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

// 文件路径样式文本：可选盘符 + 0~12 段目录 + 带已知扩展名的文件名。
// 误匹配无害（后端 404 时面板内提示"文件不存在"），故保持宽松。
const FILE_PATH_RE =
  /(?:[A-Za-z]:)?(?:[\w.-]+[/\\]){0,12}[\w.-]+\.(?:py|jsx?|tsx?|md|txt|json|jsonl|ya?ml|html?|css|toml|ini|cfg|sh|bat|ps1|log|csv|xml|sql|go|rs|java|c|cpp|h|hpp|env|svg|png|jpe?g|gif|webp|vue|lock|gitignore|d\.ts)\b/g

function linkifyFilePaths(root) {
  const doc = root.ownerDocument
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement
      if (!parent || parent.closest('a, pre, button')) return NodeFilter.FILTER_REJECT
      const value = node.nodeValue
      if (!value || !FILE_PATH_RE.test(value)) return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })
  const targets = []
  while (walker.nextNode()) targets.push(walker.currentNode)
  for (const textNode of targets) {
    const value = textNode.nodeValue
    FILE_PATH_RE.lastIndex = 0
    const frag = doc.createDocumentFragment()
    let last = 0
    let match
    let found = false
    while ((match = FILE_PATH_RE.exec(value))) {
      // 跳过 URL 的组成部分（前面是 :// 或 /）
      const before = value.slice(Math.max(0, match.index - 3), match.index)
      if (before.endsWith('://') || value[match.index - 1] === '/') continue
      found = true
      if (match.index > last) frag.appendChild(doc.createTextNode(value.slice(last, match.index)))
      const btn = doc.createElement('button')
      btn.type = 'button'
      btn.className = 'file-link'
      btn.dataset.path = match[0]
      btn.textContent = match[0]
      frag.appendChild(btn)
      last = match.index + match[0].length
    }
    if (!found) continue
    if (last < value.length) frag.appendChild(doc.createTextNode(value.slice(last)))
    textNode.replaceWith(frag)
  }
}

export default function Markdown({ text, onOpenFile }) {
  const html = useMemo(() => {
    const raw = marked.parse(text || '')
    return DOMPurify.sanitize(typeof raw === 'string' ? raw : '')
  }, [text])
  const ref = useRef(null)

  useEffect(() => {
    if (ref.current && onOpenFile) linkifyFilePaths(ref.current)
  }, [html, onOpenFile])

  const handleClick = (event) => {
    const btn = event.target.closest('.file-link')
    if (btn && onOpenFile) onOpenFile(btn.dataset.path)
  }

  return (
    <div
      className="markdown"
      ref={ref}
      onClick={handleClick}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
