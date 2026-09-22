// 共享的 REST 请求封装（自动附带访问令牌）
export async function api(path, options = {}) {
  const token = authToken()
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), options.timeout ?? 45_000)
  const abortFromParent = () => controller.abort()
  if (options.signal) {
    if (options.signal.aborted) controller.abort()
    else options.signal.addEventListener('abort', abortFromParent, { once: true })
  }
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }
  const { timeout: _timeout, withMeta = false, ...fetchOptions } = options
  try {
    const res = await fetch(path, { ...fetchOptions, headers, signal: controller.signal })
    if (res.status === 304 && withMeta) return { data: null, response: res }
    if (!res.ok) {
      if (res.status === 401) window.dispatchEvent(new Event('jelly:unauthorized'))
      const body = await res.json().catch(() => ({}))
      const err = new Error(body.detail || `HTTP ${res.status}`)
      err.status = res.status
      throw err
    }
    const data = await res.json()
    return withMeta ? { data, response: res } : data
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请检查服务或网络后重试')
    throw error
  } finally {
    window.clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abortFromParent)
  }
}

export function authToken() {
  const current = sessionStorage.getItem('agent_web_token')
  if (current) return current
  const legacy = localStorage.getItem('agent_web_token') || ''
  if (legacy) {
    sessionStorage.setItem('agent_web_token', legacy)
    localStorage.removeItem('agent_web_token')
  }
  return legacy
}

export function authHeaders() {
  const token = authToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}
