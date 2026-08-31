// 共享的 REST 请求封装（自动附带访问令牌）
export function api(path, options = {}) {
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

export function authToken() {
  return localStorage.getItem('agent_web_token') || ''
}
