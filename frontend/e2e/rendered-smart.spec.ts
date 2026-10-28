import { expect, test } from '@playwright/test';
import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const python = process.env.SIYAR_E2E_PYTHON ?? 'python';
let directory: string;
let server: ChildProcess;
let isolatedUrl: string;
let output = '';
const synthetic = '######OpenITI#\r\n#META#Header#End#\r\n### $ 1 - شخصية تجريبية أولى\r\n# نص مصطنع 🧪 فيه الإِخْلَاصُ مع علامات محفوظة (1) * و77.\r\n~~ هذا استمرار في السطر نفسه ms1234 </span> للاختبار فقط. PageV01P001\r\n# نص آخر عن طلب العلم، لا ينسب إلى الكتاب. PageV00P000\r\n### $$ 2 - شخصية تجريبية ثانية\r\n# نص مصطنع لاختبار القراءة. PageV01P002\r\n# <script>window.__siyarUnsafeExecuted = true</script>\r\n# <img src="invalid-test" onerror="window.__siyarUnsafeExecuted = true">\r\n';

async function freePort() {
  const socket = net.createServer();
  await new Promise<void>(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = (socket.address() as net.AddressInfo).port;
  await new Promise<void>(resolve => socket.close(() => resolve()));
  return port;
}

test.beforeAll(async () => {
  directory = fs.mkdtempSync(path.join(os.tmpdir(), 'siyar-smart-qa-'));
  const source = path.join(directory, 'synthetic-smart.mARkdown');
  const database = path.join(directory, 'smart.sqlite3');
  fs.writeFileSync(source, synthetic, 'utf8');
  execFileSync(python, ['-m', 'backend.importer', '--source', source, '--db', database], { cwd: root, windowsHide: true });
  const port = await freePort();
  isolatedUrl = `http://127.0.0.1:${port}`;
  server = spawn(python, ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: root, windowsHide: true, env: { ...process.env, SIYAR_DB: database, SIYAR_SOURCE: source, SIYAR_SEMANTIC_DISABLED: '1', SIYAR_SEMANTIC_DIR: path.join(directory, 'absent-index') }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout?.on('data', data => { output += String(data); });
  server.stderr?.on('data', data => { output += String(data); });
  const until = Date.now() + 20_000;
  while (Date.now() < until) {
    try { if ((await fetch(`${isolatedUrl}/api/health`)).ok) return; } catch {}
    if (server.exitCode !== null) throw new Error(`Synthetic smart server exited: ${output}`);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`Synthetic smart server failed to start: ${output}`);
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    // The smart endpoint owns a Python inference process. Windows termination
    // must include that child or its working directory remains locked.
    if (process.platform === 'win32' && server.pid) execFileSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else server.kill();
    await Promise.race([new Promise(resolve => server.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 5000))]);
  }
  if (directory && path.dirname(directory) === os.tmpdir() && path.basename(directory).startsWith('siyar-smart-qa-')) fs.rmSync(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
});

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: isolatedUrl + url.pathname + url.search });
    await route.fulfill({ response });
  });
});

test('Formatted reader is default, hides only known technical markers, and raw preference preserves the source', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const original = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  await page.goto('/reader/1');
  const formatted = page.locator('.reader-paper').getByTestId('formatted-text');
  await expect(formatted).toBeVisible();
  await expect(page.getByTestId('original-text')).toHaveCount(0);
  await expect(formatted).toContainText('الإِخْلَاصُ');
  await expect(formatted).toContainText('(1) * و77.');
  await expect(formatted).not.toContainText('###');
  await expect(formatted).not.toContainText('~~');
  await expect(formatted).not.toContainText('ms1234');
  await expect(formatted).not.toContainText('</span>');
  await expect(formatted.getByLabel('إحالة PageV01P001')).toBeVisible();
  await expect(formatted.getByLabel('إحالة PageV00P000')).toContainText('صفحة غير محددة');
  await formatted.getByLabel('إحالة PageV01P001').click();
  await expect(formatted.locator('.page-popover').first()).toContainText('غير محققة طباعيًا');
  const controls = page.getByRole('group', { name: 'وضع القراءة' });
  await controls.getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  expect(await page.getByTestId('original-text').textContent()).toBe(original.full_text);
  await page.reload();
  await expect(page.getByTestId('original-text')).toBeVisible();
  expect(await page.getByTestId('original-text').textContent()).toBe(original.full_text);
  await controls.getByRole('button', { name: 'قراءة منسّقة', exact: true }).click();
  await page.reload();
  await expect(formatted).toBeVisible();
  expect((await (await fetch(`${isolatedUrl}/api/biographies/1`)).json()).full_text).toBe(original.full_text);
  expect(errors).toEqual([]);
});

test('Formatted highlight after emoji maps to exact source and both copy actions use their advertised form', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const original = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  const view = await (await fetch(`${isolatedUrl}/api/biographies/1/reading-view`)).json();
  const prefix = original.full_text.slice(0, original.full_text.indexOf('الإِخْلَاصُ'));
  const start = original.source_char_start + Array.from(prefix).length;
  const end = start + Array.from('الإِخْلَاصُ').length;
  await page.goto(`/reader/1?start=${start}&end=${end}`);
  await expect(page.locator('.reader-paper').getByTestId('formatted-text').locator('mark')).toHaveText('الإِخْلَاصُ');
  await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
  const cleanCopy = await page.evaluate(() => navigator.clipboard.readText());
  // Windows' native clipboard turns LF into CRLF. Compare its plain characters
  // after removing only that platform newline transport conversion.
  expect(cleanCopy.replace(/\r\n/g, '\n')).toContain(view.plain_text.replace(/\r\n/g, '\n'));
  expect(cleanCopy).toContain(`النطاق [${original.source_char_start}, ${original.source_char_end})`);
  expect(cleanCopy).not.toContain('ms1234');
  await page.getByRole('button', { name: 'نسخ الأصل الحرفي', exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(original.full_text);
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  expect(await page.getByTestId('original-text').locator('mark').textContent()).toBe('الإِخْلَاصُ');
});

test('Description requires explicit meaning selection and execution before any source excerpts or result count', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let searches = 0;
  page.on('request', request => { if (new URL(request.url()).pathname === '/api/search/smart') searches += 1; });
  await page.goto('/search?kind=description');
  await page.getByRole('textbox', { name: 'صف ما تبحث عنه', exact: true }).fill('مواقف الإخلاص في العمل');
  await page.getByRole('button', { name: 'اقترح المعاني', exact: true }).click();
  await expect(page.getByRole('region', { name: 'المعاني المقترحة' })).toBeVisible();
  const cards = page.locator('.interpretation-card');
  expect(await cards.count()).toBeGreaterThanOrEqual(1);
  expect(await cards.count()).toBeLessThanOrEqual(3);
  await expect(page.locator('.evidence')).toHaveCount(0);
  await expect(page.locator('.smart-results')).toHaveCount(0);
  expect(searches).toBe(0);
  await cards.last().getByRole('button', { name: 'اختيار هذا المعنى', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'الوصف المعتمد', exact: true })).toBeVisible();
  await expect(page.locator('.evidence')).toHaveCount(0);
  expect(searches).toBe(0);
  await page.getByRole('textbox', { name: 'الوصف المعتمد', exact: true }).fill('العمل بإخلاص');
  await page.getByRole('textbox', { name: 'ألفاظ مساعدة للتوسيع', exact: true }).fill('الإخلاص، النية');
  await page.getByRole('textbox', { name: 'كلمات مطلوبة', exact: true }).fill('الإخلاص');
  await page.getByRole('textbox', { name: 'كلمات مستبعدة', exact: true }).fill('لفظغيرموجود');
  const response = page.waitForResponse(response => response.url().endsWith('/api/search/smart') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'ابحث بهذا المعنى', exact: true }).click();
  const result = await (await response).json();
  expect(result.mode).toBe('lexical_only');
  await expect(page.locator('.smart-results')).toContainText('نتائج لفظية فقط');
  await expect(page.locator('.evidence')).toHaveCount(result.items.length);
  expect(result.items.length).toBeGreaterThan(0);
  await expect(page.locator('.evidence').first()).toContainText('مرشّح');
  const first = result.items[0];
  expect(Array.from(synthetic).slice(first.source_char_start, first.source_char_end).join('')).toBe(first.text_original);
  await page.locator('.evidence').first().getByRole('button', { name: 'عرض الشاهد الخام', exact: true }).click();
  expect(await page.locator('.evidence .quote').first().textContent()).toBe(first.text_original);
  await page.locator('.evidence').first().getByRole('link', { name: 'اقرأ السياق', exact: true }).click();
  await expect(page.locator('.reader-paper').getByTestId('formatted-text').locator('mark').first()).toBeVisible();
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  expect(await page.getByTestId('original-text').locator('mark').textContent()).toBe(first.text_original);
  expect(errors).toEqual([]);
});

test('Formatted and smart surfaces fit mobile without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of ['/reader/1', '/search?kind=description']) {
    await page.goto(route);
    await expect(page.locator('.loading')).toHaveCount(0);
    if (route.includes('description')) {
      await page.getByRole('textbox', { name: 'صف ما تبحث عنه', exact: true }).fill('مواقف الإخلاص');
      await page.getByRole('button', { name: 'اقترح المعاني', exact: true }).click();
      await page.locator('.interpretation-card').last().getByRole('button', { name: 'اختيار هذا المعنى', exact: true }).click();
    }
    const widths = await page.evaluate(() => ({ viewport: innerWidth, scroll: document.documentElement.scrollWidth }));
    expect(widths.scroll, route).toBeLessThanOrEqual(widths.viewport + 1);
  }
});

test('Unknown HTML-like source text remains literal and never executes in either reader mode', async ({ page }) => {
  await page.goto('/reader/2');
  const formatted = page.locator('.reader-paper').getByTestId('formatted-text');
  await expect(formatted).toContainText('<script>window.__siyarUnsafeExecuted = true</script>');
  await expect(formatted).toContainText('<img src="invalid-test" onerror="window.__siyarUnsafeExecuted = true">');
  await expect(formatted.locator('script,img')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __siyarUnsafeExecuted?: boolean }).__siyarUnsafeExecuted)).toBeUndefined();
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  await expect(page.getByTestId('original-text')).toContainText('<script>');
  await expect(page.getByTestId('original-text').locator('script,img')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __siyarUnsafeExecuted?: boolean }).__siyarUnsafeExecuted)).toBeUndefined();
});

test('Editing a meaning cancels an older search response and allows an immediate new search', async ({ page }) => {
  let intercepted = false;
  let arrived!: () => void;
  let release!: () => void;
  let staleDelivered!: () => void;
  const requestArrived = new Promise<void>(resolve => { arrived = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  const staleComplete = new Promise<void>(resolve => { staleDelivered = resolve; });
  await page.route('**/api/search/smart', async route => {
    const isStale = !intercepted;
    if (!intercepted) {
      intercepted = true;
      arrived();
      await gate;
    }
    try {
      const response = await route.fetch({ url: `${isolatedUrl}/api/search/smart` });
      await route.fulfill({ response });
    } catch (error) {
      // The first request is intentionally aborted by the user edit.
      if (!route.request().failure()) throw error;
    } finally {
      if (isStale) staleDelivered();
    }
  });
  await page.goto('/search?kind=description');
  await page.getByRole('textbox', { name: 'صف ما تبحث عنه', exact: true }).fill('الإخلاص');
  await page.getByRole('button', { name: 'اقترح المعاني', exact: true }).click();
  await page.locator('.interpretation-card').last().getByRole('button', { name: 'اختيار هذا المعنى', exact: true }).click();
  await page.getByRole('button', { name: 'ابحث بهذا المعنى', exact: true }).click();
  await requestArrived;
  await page.getByRole('textbox', { name: 'الوصف المعتمد', exact: true }).fill('معنىمختلفغيرموجود');
  await expect(page.getByRole('button', { name: 'ابحث بهذا المعنى', exact: true })).toBeEnabled();
  release();
  await staleComplete;
  await expect(page.locator('.smart-results')).toHaveCount(0);
  const response = page.waitForResponse(response => response.url().endsWith('/api/search/smart') && response.request().postDataJSON()?.plan?.meaning === 'معنىمختلفغيرموجود');
  await page.getByRole('button', { name: 'ابحث بهذا المعنى', exact: true }).click();
  const result = await (await response).json();
  expect(result.interpretation.meaning).toBe('معنىمختلفغيرموجود');
  expect(result.total).toBe(0);
  await expect(page.locator('.smart-results .empty')).toContainText('لم تظهر مقاطع مناسبة');
  await expect(page.locator('.evidence')).toHaveCount(0);
});

test('Ready context search explains and refreshes a building paragraph supplement without executing a search', async ({ page }) => {
  let statusRequests = 0;
  let supplementReady = false;
  let searchRequests = 0;
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/search/smart') searchRequests += 1;
  });
  await page.clock.install();
  await page.route('**/api/semantic/status', route => {
    statusRequests += 1;
    return route.fulfill({ json: {
      status: 'ready', ready: true,
      paragraph_status: { status: supplementReady ? 'ready' : 'building', ready: supplementReady },
    } });
  });
  await page.goto('/search?kind=description');
  await expect(page.locator('.semantic-health')).toContainText('الفهرس الدلالي المحلي جاهز');
  const progress = page.getByText('البحث في السياق متاح الآن، ويجري إعداد تغطية إضافية للشواهد القصيرة.', { exact: true });
  await expect(progress).toBeVisible();
  await expect(page.getByText('البحث الدلالي غير جاهز', { exact: true })).toHaveCount(0);
  const initialRequests = statusRequests;
  supplementReady = true;
  await page.clock.runFor(15050);
  await expect(progress).toHaveCount(0);
  expect(statusRequests).toBeGreaterThan(initialRequests);
  const completedRequests = statusRequests;
  await page.clock.runFor(30000);
  expect(statusRequests).toBe(completedRequests);
  expect(searchRequests).toBe(0);
});
