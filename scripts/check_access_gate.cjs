// Access-gate acceptance. Start preview_fixture.py with --token first.
const { chromium } = require(process.env.JELLY_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const url = process.env.JELLY_PREVIEW_URL || 'http://127.0.0.1:8013';
  const token = process.env.JELLY_PREVIEW_TOKEN || 'jelly-preview-token';
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.JELLY_CHROME ? { executablePath: process.env.JELLY_CHROME } : {}),
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await context.addInitScript(() => {
    localStorage.setItem('jelly_current', 'stale-session');
    localStorage.setItem('jelly_project', 'stale-project');
  });
  const page = await context.newPage();
  const errors = [];
  const requestedUrls = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => requestedUrls.push(request.url()));

  await page.goto(url);
  await page.getByRole('heading', { name: '回到你的 Jelly 工作台' }).waitFor();
  await page.getByLabel('访问口令').fill('wrong-token');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('alert').filter({ hasText: /无效|缺失|401/ }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '回到你的 Jelly 工作台' }).count(), 1);

  await page.getByLabel('访问口令').fill(token);
  const workspaceLoaded = page.waitForResponse(response => response.url().includes('/api/files?') && response.status() === 200);
  await page.getByRole('button', { name: '进入工作台' }).click();
  await workspaceLoaded;
  await page.locator('.layout').waitFor();
  await page.getByText('产品探索', { exact: true }).first().waitFor();
  assert.equal(await page.locator('.access-gate').count(), 0);
  assert.equal(await page.getByText(/无法读取：无效或缺失的访问令牌/).count(), 0);
  assert.deepEqual(await page.evaluate(() => ({
    current: localStorage.getItem('jelly_current'),
    project: localStorage.getItem('jelly_project'),
    persistentToken: localStorage.getItem('agent_web_token'),
    sessionToken: sessionStorage.getItem('agent_web_token'),
  })), { current: null, project: null, persistentToken: null, sessionToken: token });
  assert.equal(requestedUrls.some(requestUrl => requestUrl.includes('token=')), false);
  assert.deepEqual(errors, []);

  await page.getByRole('button', { name: '锁定工作台并更换访问口令' }).click();
  await page.getByRole('heading', { name: '回到你的 Jelly 工作台' }).waitFor();
  assert.equal(await page.getByText('产品探索', { exact: true }).count(), 0);

  await browser.close();
  console.log('PASS: access gate rejects invalid tokens and keeps tokens out of URLs');
})().catch(error => { console.error(error); process.exit(1); });
