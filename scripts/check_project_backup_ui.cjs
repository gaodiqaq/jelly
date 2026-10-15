// Run against preview_fixture.py --import-delay 2 to inspect real upload/restore states.
const path = require('node:path');
const playwright = process.env.JELLY_PLAYWRIGHT || require.resolve('playwright', { paths: [path.join(__dirname, '../webui')] });
const { chromium } = require(playwright);
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const url = process.env.JELLY_PREVIEW_URL || 'http://127.0.0.1:8012';
  const browser = await chromium.launch({ headless: true, ...(process.env.JELLY_CHROME ? { executablePath: process.env.JELLY_CHROME } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  fs.mkdirSync('scratch/screenshots', { recursive: true });
  await page.goto(url);
  await page.locator('.workspace-selector').waitFor();
  await page.locator('.project-list button[title="产品探索"]').click();
  await page.locator('.workspace-selector').click();
  const settings = page.getByRole('dialog', { name: '项目设置' });

  // Emulate the platform file writer; no ZIP-sized Blob may be allocated.
  await page.evaluate(() => {
    window.savedBackup = { bytes: 0, closed: false, name: '' };
    window.showSaveFilePicker = async options => {
      window.savedBackup.name = options.suggestedName;
      return { createWritable: async () => ({
        write: async chunk => { window.savedBackup.bytes += chunk.byteLength ?? chunk.size; },
        close: async () => { window.savedBackup.closed = true; },
        abort: async () => {},
      }) };
    };
  });
  await settings.getByRole('button', { name: '↓ 下载备份' }).click();
  await page.waitForFunction(() => window.savedBackup.closed);
  const saved = await page.evaluate(() => window.savedBackup);
  assert.ok(saved.bytes > 100);
  assert.match(saved.name, /^Jelly-产品探索-v1-\d{4}-\d{2}-\d{2}\.zip$/);
  await settings.getByText('项目备份已保存，可以随时从新建项目中恢复。').waitFor();
  await settings.getByRole('button', { name: '关闭项目设置' }).click();

  const projects = (await (await page.request.get(`${url}/api/projects`)).json()).projects;
  const research = projects.find(project => project.name === '产品探索');
  const backup = await page.request.get(`${url}/api/projects/${research.id}/backup`, { headers: { 'X-Jelly-Intent': 'project-backup' } });
  assert.equal(backup.status(), 200);
  const zip = await backup.body();

  await page.getByRole('button', { name: '新建项目', exact: true }).click();
  await page.getByRole('tab', { name: '新建项目', exact: true }).press('ArrowRight');
  assert.equal(await page.getByRole('tab', { name: '导入备份', exact: true }).getAttribute('aria-selected'), 'true');
  const dialog = page.getByRole('dialog', { name: '带回一个完整项目' });
  assert.equal(await dialog.getByText('项目名称', { exact: true }).count(), 0);
  assert.equal(await dialog.getByLabel('选择项目备份 ZIP').isVisible(), false);

  // A completed upload must no longer promise to cancel a server-side restore.
  await dialog.getByLabel('选择项目备份 ZIP').setInputFiles({ name: '项目备份.zip', mimeType: 'application/zip', buffer: zip });
  await dialog.getByRole('button', { name: '正在恢复…', exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '取消上传', exact: true }).count(), 0);
  assert.equal(await dialog.getByRole('button', { name: '关闭项目设置' }).isDisabled(), true);
  const progress = dialog.getByRole('progressbar');
  assert.match(await progress.getAttribute('aria-label'), /校验并恢复/);
  assert.notEqual(await progress.locator('i').evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)');
  await dialog.screenshot({ path: 'scratch/screenshots/project-import-restoring.png' });
  await page.locator('.project-notice').filter({ hasText: '已恢复' }).waitFor();

  // Losing the response after upload must reconcile the actual project list.
  await page.getByRole('button', { name: '新建项目', exact: true }).click();
  await page.getByRole('tab', { name: '导入备份', exact: true }).click();
  const disconnectedDialog = page.getByRole('dialog', { name: '带回一个完整项目' });
  const responseConnection = await page.context().newCDPSession(page);
  await responseConnection.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/projects/import', requestStage: 'Response' }] });
  responseConnection.on('Fetch.requestPaused', async event => {
    await responseConnection.send('Fetch.failRequest', { requestId: event.requestId, errorReason: 'ConnectionClosed' });
  });
  await disconnectedDialog.getByLabel('选择项目备份 ZIP').setInputFiles({ name: '断线恢复.zip', mimeType: 'application/zip', buffer: zip });
  await disconnectedDialog.getByRole('button', { name: '正在恢复…', exact: true }).waitFor();
  await page.locator('.project-notice').filter({ hasText: '项目恢复已完成' }).waitFor();
  await responseConnection.send('Fetch.disable');
  await responseConnection.detach();

  // A cancelled upload must stay cancelled and release the server operation gate.
  const beforeCancel = (await (await page.request.get(`${url}/api/projects`)).json()).projects.length;
  await page.getByRole('button', { name: '新建项目', exact: true }).click();
  await page.getByRole('tab', { name: '导入备份', exact: true }).click();
  const cancelDialog = page.getByRole('dialog', { name: '带回一个完整项目' });
  const network = await page.context().newCDPSession(page);
  await network.send('Network.enable');
  await network.send('Network.emulateNetworkConditions', { offline: false, latency: 100, downloadThroughput: 1024 * 1024, uploadThroughput: 32 * 1024 });
  await cancelDialog.getByLabel('选择项目备份 ZIP').setInputFiles({ name: '取消上传.zip', mimeType: 'application/zip', buffer: Buffer.alloc(1024 * 1024) });
  await cancelDialog.getByRole('button', { name: '取消上传', exact: true }).click();
  await cancelDialog.getByText('已取消上传。', { exact: true }).waitFor();
  await network.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await network.detach();
  const afterCancel = await (await page.request.get(`${url}/api/projects`)).json();
  assert.equal(afterCancel.projects.length, beforeCancel);
  await cancelDialog.getByRole('button', { name: '关闭项目设置' }).click();

  // Corrupt archives leave the import form usable with a visible error.
  await page.getByRole('button', { name: '新建项目', exact: true }).click();
  await page.getByRole('tab', { name: '导入备份', exact: true }).click();
  const retryDialog = page.getByRole('dialog', { name: '带回一个完整项目' });
  await retryDialog.getByLabel('选择项目备份 ZIP').setInputFiles({ name: '损坏.zip', mimeType: 'application/zip', buffer: Buffer.from('broken ZIP') });
  await retryDialog.getByRole('alert').waitFor();
  assert.equal(await retryDialog.getByRole('button', { name: '选择备份文件' }).isEnabled(), true);
  await retryDialog.screenshot({ path: 'scratch/screenshots/project-import-error.png' });

  await page.setViewportSize({ width: 360, height: 800 });
  for (const theme of ['dark', 'light']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await retryDialog.screenshot({ path: `scratch/screenshots/project-import-mobile-${theme}.png` });
    assert.equal(await retryDialog.evaluate(el => el.scrollWidth <= el.clientWidth), true);
    assert.ok(await retryDialog.getByRole('button', { name: '选择备份文件' }).evaluate(el => el.getBoundingClientRect().height) >= 44);
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ streamBytes: saved.bytes, restoreState: 'verified', mobile: ['dark', 'light'], errors }));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
