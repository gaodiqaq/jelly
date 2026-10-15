// Apply the saved appearance before React paints the workspace.
(() => {
  let theme = 'dark'
  try { if (localStorage.getItem('jelly_theme') === 'light') theme = 'light' } catch { /* private storage */ }
  document.documentElement.dataset.theme = theme
  const color = document.querySelector('meta[name="theme-color"]')
  if (color) color.content = theme === 'light' ? '#faf9f6' : '#20221f'
})()
