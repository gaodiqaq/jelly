import React, { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

export default function Markdown({ text }) {
  const html = useMemo(() => {
    const raw = marked.parse(text || '')
    return DOMPurify.sanitize(typeof raw === 'string' ? raw : '')
  }, [text])
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />
}
