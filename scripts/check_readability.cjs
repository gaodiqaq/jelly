// Read-only typography and responsive acceptance against preview_fixture.py on 8012.
// JELLY_PLAYWRIGHT may point to an installed Playwright directory.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const playwright = process.env.JELLY_PLAYWRIGHT || require.resolve('playwright', { paths: [path.join(__dirname, '../webui')] });
const { chromium } = require(playwright);

(async () => {
  const base = process.env.JELLY_PREVIEW_URL || 'http://127.0.0.1:8012';
  assert.equal(new URL(base).port, '8012', 'Use the offline preview fixture on port 8012.');
  const output = path.join(__dirname, '../scratch/screenshots');
  fs.mkdirSync(output, { recursive: true });
  const failures = [];
  const results = [];
  const errors = [];
  const browser = await chromium.launch({ headless: true, ...(process.env.JELLY_CHROME ? { executablePath: process.env.JELLY_CHROME } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(10000);
    const projects = (await (await page.request.get(`${base}/api/projects`)).json()).projects;
    const research = projects.find(project => project.name === '产品探索');
    const sessions = (await (await page.request.get(`${base}/api/sessions`)).json()).sessions;
    const session = sessions.find(item => item.project_id === research?.id && item.title.startsWith('整理小团队'));
    assert.ok(session && session.model === 'preview/offline', 'A seeded offline research task is required.');
    await page.goto(base);
    await page.locator('.project-list button[title="产品探索"]').click();
    await page.locator('.session-item').filter({ hasText: session.title }).locator('.session-open').click();
    await page.locator('.document-sheet h1').waitFor();
    await page.evaluate(() => document.fonts.ready);

    function check(ok, message, data) {
      if (!ok) failures.push({ message, ...(data ? { data } : {}) });
    }
    async function shot(name) {
      await page.screenshot({ path: path.join(output, `readability-${name}.png`), fullPage: true });
    }
    async function inViewport(locator, label) {
      const box = await locator.boundingBox();
      const view = page.viewportSize();
      check(box && box.x >= -1 && box.y >= -1 && box.x + box.width <= view.width + 1 && box.y + box.height <= view.height + 1, `${label} stays inside the viewport`, box);
      return box;
    }
    async function minFont(selector, minimum, label) {
      const fonts = await page.locator(selector).evaluateAll(elements => elements.map(element => ({
        text: (element.textContent || element.getAttribute('placeholder') || '').trim().slice(0, 55),
        size: parseFloat(getComputedStyle(element).fontSize),
      })));
      check(fonts.length > 0, `${label} exists`);
      for (const font of fonts) check(font.size >= minimum, `${label} is at least ${minimum}px`, font);
      return fonts;
    }
    async function visibleSmallText(label) {
      const small = await page.evaluate(() => {
        const visible = element => {
          const style = getComputedStyle(element);
          if (style.visibility !== 'visible' || style.display === 'none' || Number(style.opacity) === 0 || element.closest('.sr-only,[hidden]')) return false;
          let rect = element.getBoundingClientRect();
          let left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right);
          let top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
          for (let parent = element.parentElement; parent; parent = parent.parentElement) {
            const css = getComputedStyle(parent);
            if (css.display === 'none' || css.visibility !== 'visible' || Number(css.opacity) === 0) return false;
            const bound = parent.getBoundingClientRect();
            if (/hidden|clip|auto|scroll/.test(css.overflowX)) { left = Math.max(left, bound.left); right = Math.min(right, bound.right); }
            if (/hidden|clip|auto|scroll/.test(css.overflowY)) { top = Math.max(top, bound.top); bottom = Math.min(bottom, bound.bottom); }
          }
          return right - left > 2 && bottom - top > 2;
        };
        return [...document.querySelectorAll('body *')].filter(element => !['SCRIPT', 'STYLE', 'SVG', 'PATH'].includes(element.tagName) && visible(element)).map(element => ({
          selector: `${element.tagName.toLowerCase()}.${String(element.className || '').trim().replace(/\s+/g, '.')}`,
          text: [...element.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim(),
          size: parseFloat(getComputedStyle(element).fontSize),
        })).filter(item => /[A-Za-z0-9\u3400-\u9fff]/.test(item.text) && item.size < 12);
      });
      check(small.length === 0, `${label}: visible descriptive text is at least 12px`, small);
    }
    async function noHorizontalOverflow(label) {
      const overflow = await page.evaluate(() => ({ width: innerWidth, html: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
      check(overflow.html <= overflow.width + 1 && overflow.body <= overflow.width + 1, `${label}: no page horizontal overflow`, overflow);
    }
    async function usable(locator, label) {
      await inViewport(locator, label);
      const hit = await locator.evaluate(element => {
        const box = element.getBoundingClientRect();
        const target = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        return { hit: target === element || element.contains(target), control: element.getAttribute('aria-label') || element.closest('label')?.textContent.trim().slice(0, 50), target: target?.outerHTML.slice(0, 160), width: box.width, height: box.height };
      });
      check(hit.hit && hit.width >= 24 && hit.height >= 24, `${label} is visible and not covered`, hit);
      if (!hit.hit) await shot(`${label.replace(/\s+/g, '-')}-covered`);
    }
    async function closeWorkspace() {
      const close = page.getByRole('button', { name: '关闭工作台', exact: true });
      if (await close.count()) await close.click();
      await page.waitForTimeout(180);
    }
    async function openWorkspace() {
      if (!await page.locator('.workspace-panel').count()) await page.locator('.ws-toggle').click();
      await page.locator('.document-sheet h1').waitFor();
      await page.waitForTimeout(180);
    }
    async function openNavigation() {
      if (await page.locator('.menu-btn').isVisible()) {
        await page.locator('.menu-btn').click();
        await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().left >= -1);
      }
    }
    async function closeNavigation() {
      const close = page.getByRole('button', { name: '关闭项目与任务导航', exact: true });
      if (await close.isVisible()) await close.click({ position: { x: page.viewportSize().width - 10, y: 20 } }).catch(() => page.keyboard.press('Escape'));
      await page.waitForTimeout(180);
    }
    async function menu(selector, button, name, scenario, screenshot) {
      await button.click();
      const popup = page.locator(selector);
      await popup.waitFor({ state: 'visible' });
      await page.waitForTimeout(180);
      await inViewport(popup, `${scenario} ${name} menu`);
      await visibleSmallText(`${scenario} ${name} menu`);
      await noHorizontalOverflow(`${scenario} ${name} menu`);
      if (screenshot) await shot(`${scenario}-${name}`);
      await page.keyboard.press('Escape');
      await popup.waitFor({ state: 'detached' });
    }
    async function dialogs(scenario) {
      await openNavigation();
      await page.getByRole('button', { name: '新建项目', exact: true }).click();
      let dialog = page.getByRole('dialog', { name: '新建项目', exact: true });
      // The project form title is intentionally product copy, not a fixed test ID.
      if (!await dialog.count()) dialog = page.locator('.project-dialog');
      await inViewport(dialog, `${scenario} project dialog`);
      await minFont('.project-dialog label', 14, `${scenario} project labels`);
      await minFont('.project-dialog input:not([type="file"]), .project-dialog select', 16, `${scenario} project inputs`);
      await visibleSmallText(`${scenario} project dialog`);
      const projectInputs = dialog.locator('input:not([type="file"]), select');
      for (let i = 0; i < await projectInputs.count(); i++) {
        await projectInputs.nth(i).scrollIntoViewIfNeeded();
        await usable(projectInputs.nth(i), `${scenario} project input ${i}`);
      }
      await dialog.evaluate(element => { element.scrollTop = 0; });
      await shot(`${scenario}-project`);
      await page.keyboard.press('Escape');
      await closeNavigation();
      await page.getByRole('button', { name: '模型与 API Key 设置' }).click();
      dialog = page.getByRole('dialog', { name: '模型与 API Key' });
      await inViewport(dialog, `${scenario} settings dialog`);
      await dialog.getByRole('button', { name: '+ 添加提供商', exact: true }).click();
      await minFont('.settings-modal input', 16, `${scenario} settings inputs`);
      await minFont('.settings-modal .field-label', 14, `${scenario} settings labels`);
      await visibleSmallText(`${scenario} settings dialog`);
      const fields = dialog.locator('input');
      for (let i = 0; i < await fields.count(); i++) {
        await fields.nth(i).scrollIntoViewIfNeeded();
        await usable(fields.nth(i), `${scenario} settings input ${i}`);
      }
      const close = dialog.getByRole('button', { name: '关闭', exact: true });
      await close.scrollIntoViewIfNeeded();
      await usable(close, `${scenario} settings close action`);
      await shot(`${scenario}-settings`);
      await page.keyboard.press('Escape');
      await noHorizontalOverflow(`${scenario} dialogs closed`);
    }

    const cdp = await page.context().newCDPSession(page);
    await cdp.send('DOM.enable');
    await cdp.send('CSS.enable');
    const { root } = await cdp.send('DOM.getDocument');
    const fontEvidence = [];
    for (const selector of ['.agent-prose .markdown p', '.user-note', '.document-sheet .markdown p', '.document-sheet .markdown h1', '.main-navigation button']) {
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector });
      const computed = await page.locator(selector).first().evaluate(element => ({ family: getComputedStyle(element).fontFamily, size: getComputedStyle(element).fontSize, text: element.textContent.slice(0, 80) }));
      const platform = await cdp.send('CSS.getPlatformFontsForNode', { nodeId });
      fontEvidence.push({ selector, computed, ...platform });
    }

    // Platform families differ between Windows, macOS and Linux. Record actual
    // glyph fonts for visual review without requiring a Windows-only font.
    const layering = [];
    async function settingsAboveWorkspace(label) {
      const dialog = page.getByRole('dialog', { name: '模型与 API Key' });
      await dialog.waitFor();
      check(await page.locator('.workspace-panel').count() === 1, `${label}: document workspace remains open`);
      await inViewport(dialog, `${label} settings dialog`);
      const input = dialog.getByLabel('当前模型', { exact: true });
      await input.scrollIntoViewIfNeeded();
      await usable(input, `${label} model input above workspace`);
      const closeAction = dialog.getByRole('button', { name: '关闭', exact: true });
      await closeAction.scrollIntoViewIfNeeded();
      await usable(closeAction, `${label} close action above workspace`);
      const closeIcon = dialog.getByRole('button', { name: '关闭设置', exact: true });
      await closeIcon.scrollIntoViewIfNeeded();
      await usable(closeIcon, `${label} close icon above workspace`);
      await shot(label);
      await closeIcon.click();
      await dialog.waitFor({ state: 'detached' });
      layering.push({ name: label, width: page.viewportSize().width });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace();
    await page.getByRole('button', { name: '模型与 API Key 设置' }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(250);
    await settingsAboveWorkspace('desktop-settings-resized-to-phone');
    // After closing the dialog, real keyboard navigation can return to its
    // settings trigger while the mobile document drawer is still open.
    let reachedSettings = false;
    for (let i = 0; i < 50; i++) {
      reachedSettings = await page.evaluate(() => document.activeElement?.matches('.settings-btn'));
      if (reachedSettings) break;
      await page.keyboard.press('Tab');
    }
    check(reachedSettings, 'Mobile open document workspace can reach settings by keyboard');
    if (reachedSettings) {
      await page.keyboard.press('Enter');
      await settingsAboveWorkspace('phone-document-settings-keyboard');
    }
    await closeWorkspace();

    const scenarios = [
      { name: 'desktop-1440', width: 1440, height: 900 },
      { name: 'desktop-1280', width: 1280, height: 800 },
      { name: 'tablet-1080', width: 1080, height: 810 },
      { name: 'phone-390', width: 390, height: 844 },
      { name: 'phone-360', width: 360, height: 800 },
      { name: 'short-900', width: 900, height: 600 },
      // 1440x900 at browser zoom 200% has a 720x450 CSS viewport.
      { name: 'zoom-200', width: 720, height: 450 },
    ];
    for (const scenario of scenarios) {
      await page.setViewportSize({ width: scenario.width, height: scenario.height });
      await page.waitForTimeout(250);
      await openWorkspace();
      const document = await minFont('.document-sheet .markdown', 16, `${scenario.name} document`);
      await inViewport(page.locator('.workspace-panel'), `${scenario.name} workspace`);
      await noHorizontalOverflow(`${scenario.name} workspace`);
      await visibleSmallText(`${scenario.name} workspace`);
      await shot(`${scenario.name}-document`);
      await closeWorkspace();
      const conversation = await minFont('.agent-prose .markdown', 17, `${scenario.name} conversation`);
      const input = await minFont('.composer textarea', 16, `${scenario.name} input`);
      await page.locator('.composer textarea').fill('继续优化这份产品方案');
      await usable(page.locator('.composer textarea'), `${scenario.name} composer`);
      await usable(page.getByRole('button', { name: '发送', exact: true }), `${scenario.name} send`);
      await noHorizontalOverflow(`${scenario.name} chat`);
      await visibleSmallText(`${scenario.name} chat`);
      await shot(`${scenario.name}-chat`);
      await openNavigation();
      await minFont('.main-navigation button, .project-list button, .session-title', 14, `${scenario.name} navigation`);
      if (scenario.width <= 760) await inViewport(page.locator('.sidebar'), `${scenario.name} navigation`);
      await closeNavigation();
      const captureMenus = ['desktop-1440', 'phone-390', 'zoom-200'].includes(scenario.name);
      await menu('.model-dropdown', page.locator('.model-picker-btn'), 'model', scenario.name, captureMenus);
      await menu('.perm-popup', page.getByRole('button', { name: '权限模式', exact: true }), 'permission', scenario.name, captureMenus);
      await menu('.skill-popup', page.getByRole('button', { name: 'Skill 命令', exact: true }), 'skill', scenario.name, captureMenus);
      await dialogs(scenario.name);
      results.push({ ...scenario, document, conversation, input });
    }
    check(errors.length === 0, 'No uncaught browser errors', errors);
    const report = { base, scenarios: results, fontEvidence, layering, errors, failures };
    fs.writeFileSync(path.join(output, 'readability-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
    assert.deepEqual(failures, [], 'Readability acceptance failed; see readability-report.json.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
