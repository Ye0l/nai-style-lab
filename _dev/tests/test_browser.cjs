// Integration against real HTTP + seeded fake images; never calls NovelAI.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
(async () => {
  const server = spawn(process.env.NAI_PYTHON || 'python3', ['_dev/tests/browser_server.py'], { cwd: root });
  let browser;
  const errors = [];
  server.stderr.on('data', (data) => process.stderr.write(data));
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Fixture startup timed out')), 15000);
      server.stdout.once('data', (data) => { clearTimeout(timer); resolve(data.toString().trim()); });
      server.once('exit', (code) => { clearTimeout(timer); reject(Error('Fixture exited: ' + code)); });
    });
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const context = await browser.newContext({ acceptDownloads: true });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const route of ['home', 'arena', 'evolution', 'refine', 'styles', 'library', 'free', 'settings']) {
        await page.goto(url + '/#/' + route);
        await page.waitForLoadState('networkidle');
        const size = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
        assert(size.scroll <= size.width + 1, `${route} overflows at ${width}px: ${size.scroll}`);
        if (process.env.NAI_SCREENSHOT_DIR && width === 390) {
          fs.mkdirSync(process.env.NAI_SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: path.join(process.env.NAI_SCREENSHOT_DIR, route + '.png'), fullPage: true });
        }
      }
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(url + '/#/home'); await page.waitForLoadState('networkidle');
    await page.locator('#nav-toggle').click();
    assert.equal(await page.locator('#nav-toggle').getAttribute('aria-expanded'), 'true');
    await page.locator('.nav-item').filter({ hasText: '자유 생성' }).click();
    await page.waitForURL('**/#/free');
    assert.equal(await page.locator('#nav-toggle').getAttribute('aria-expanded'), 'false');
    await page.goto(url + '/#/arena'); await page.waitForLoadState('networkidle');
    const vote = page.locator('.fighter .pick').first();
    await vote.waitFor();
    await page.waitForFunction(() => !document.querySelector('.fighter .pick')?.disabled);
    const voted = page.waitForResponse((response) => response.url().endsWith('/api/vote') && response.request().method() === 'POST');
    await vote.click(); assert.equal((await (await voted).json()).ok, true);
    // Selecting a style updates the detail panel and touch selection controls.
    await page.goto(url + '/#/styles'); await page.waitForLoadState('networkidle');
    await page.locator('.gallery .tile').first().click();
    await page.locator('.selection-bar').waitFor();
    // Native browser backup and restore flow (real ZIP; fake isolated state only).
    await page.goto(url + '/#/settings'); await page.waitForLoadState('networkidle');
    await page.getByRole('tab', { name: '데이터', exact: true }).click();
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: '데이터 내보내기' }).click();
    const download = await downloading; const file = await download.path();
    assert(download.suggestedFilename().endsWith('.zip'));
    const picking = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: '데이터 불러오기' }).click();
    await (await picking).setFiles({ name: 'backup.zip', mimeType: 'application/zip', buffer: fs.readFileSync(file) });
    const imported = page.waitForResponse((response) => response.url().endsWith('/api/data/import'));
    await page.getByRole('button', { name: '불러오기', exact: true }).click();
    assert.equal((await (await imported).json()).ok, true);
    assert.deepEqual(errors, []);
    console.log('PASS: 32 responsive route/viewport checks, drawer, image vote, selection and browser ZIP backup/restore.');
  } finally {
    if (browser) await browser.close();
    const exited = new Promise((resolve) => server.once('exit', resolve));
    server.kill(); await exited;
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
