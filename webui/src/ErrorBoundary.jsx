import React from 'react'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Jelly UI crashed', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="fatal-screen" role="alert">
        <span className="jelly-cube fatal-cube" aria-hidden="true" />
        <span className="eyebrow">JELLY RECOVERY</span>
        <h1>界面暂时没有正常打开</h1>
        <p>任务仍保存在本地。重新加载后可以继续刚才的工作。</p>
        <div className="fatal-actions">
          <button className="solid-button" onClick={() => window.location.reload()}>重新加载</button>
          <button className="quiet-button" onClick={() => this.setState({ error: null })}>尝试返回</button>
        </div>
        <details><summary>错误详情</summary><pre>{this.state.error.message}</pre></details>
      </main>
    )
  }
}
