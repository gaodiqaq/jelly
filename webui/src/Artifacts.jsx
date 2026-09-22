import React from 'react'

export function FileGlyph({ path = '' }) {
  const label = /\.md$/i.test(path) ? 'MD' : /\.html?$/i.test(path) ? '</>' : path.split('.').pop().slice(0, 3).toUpperCase()
  return <span className="file-glyph" aria-hidden="true">{label}</span>
}

export default function Artifacts({ artifacts, onOpenFile, activeFile, compact = false }) {
  return <div className={`artifact-grid${compact ? ' compact' : ''}`}>
    {artifacts.map(file => <button key={file.path} className={`artifact-card${activeFile === file.path ? ' selected' : ''}`} onClick={() => onOpenFile(file.path)}>
      <FileGlyph path={file.path} /><span className="artifact-info"><strong>{file.name}</strong><small>{file.version > 1 ? `第 ${file.version} 个版本` : '已生成'} · {file.size < 1024 ? `${file.size} B` : `${(file.size / 1024).toFixed(1)} KB`}</small></span><span className="artifact-arrow">↗</span>
    </button>)}
  </div>
}
