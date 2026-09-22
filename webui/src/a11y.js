export function handleTabListKeyDown(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  const tabs = Array.from(event.currentTarget.querySelectorAll('[role="tab"]'))
    .filter(tab => !tab.disabled)
  const current = tabs.indexOf(document.activeElement)
  if (current < 0 || tabs.length === 0) return

  event.preventDefault()
  let next = current
  if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = tabs.length - 1
  else if (event.key === 'ArrowRight') next = (current + 1) % tabs.length
  else next = (current - 1 + tabs.length) % tabs.length

  tabs[next].focus()
  tabs[next].click()
}

export function handleMenuKeyDown(event, onEscape) {
  const items = Array.from(event.currentTarget.querySelectorAll('[role^="menuitem"]'))
    .filter(item => !item.disabled)

  if (event.key === 'Escape') {
    event.preventDefault()
    onEscape?.()
    return
  }
  if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key) || items.length === 0) return

  event.preventDefault()
  const current = items.indexOf(document.activeElement)
  let next
  if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = items.length - 1
  else if (event.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % items.length
  else next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length
  items[next].focus()
}

export function focusMenuItem(container, position = 'first') {
  requestAnimationFrame(() => {
    const items = Array.from(container?.querySelectorAll('[role^="menuitem"]') || [])
      .filter(item => !item.disabled)
    const target = position === 'last' ? items.at(-1) : items[0]
    target?.focus()
  })
}
