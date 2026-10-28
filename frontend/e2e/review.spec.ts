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
let serverOutput = '';
const synthetic = '######OpenITI#\n#META#Header#End#\n### $ شخصية تجريبية أولى\n# نص مصطنع 🧪 فيه الإخلاص، وذُكرت شخصية تجريبية ثانية؛ للاختبار فقط.\nPageV01P001\n### $ شخصية تجريبية ثانية\n# نص مصطنع فيه طلب العلم؛ لا ينسب إلى الكتاب.\nPageV01P002\n';

async function freePort(): Promise<number> {
  const socket = net.createServer();
  await new Promise<void>(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = (socket.address() as net.AddressInfo).port;
  await new Promise<void>(resolve => socket.close(() => resolve()));
  return port;
}

test.beforeAll(async () => {
  directory = fs.mkdtempSync(path.join(os.tmpdir(), 'siyar-ui-review-'));
  const source = path.join(directory, 'synthetic-review.mARkdown');
  const database = path.join(directory, 'synthetic.sqlite3');
  fs.writeFileSync(source, synthetic, 'utf8');
  execFileSync(python, ['-m', 'backend.importer', '--source', source, '--db', database], { cwd: root });
  const port = await freePort();
  isolatedUrl = `http://127.0.0.1:${port}`;
  server = spawn(python, ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: root, windowsHide: true,
    env: { ...process.env, SIYAR_DB: database, SIYAR_SOURCE: source },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout?.on('data', data => { serverOutput += String(data); });
  server.stderr?.on('data', data => { serverOutput += String(data); });
  const until = Date.now() + 15_000;
  while (Date.now() < until) {
    try { if ((await fetch(`${isolatedUrl}/api/health`)).ok) return; } catch {}
    if (server.exitCode !== null) throw new Error(`Synthetic server exited: ${serverOutput}`);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`Synthetic review server not ready: ${serverOutput}`);
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill();
    await Promise.race([new Promise(resolve => server.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 5000))]);
  }
  // Only remove the exact OS-created temporary directory; never touch the real database.
  if (directory && path.dirname(directory) === os.tmpdir() && path.basename(directory).startsWith('siyar-ui-review-')) fs.rmSync(directory, { recursive: true, force: true });
});

test.beforeEach(async ({ page }) => {
  // Vite still serves the real app; every API call in these tests is redirected
  // to the explicitly synthetic throwaway backend. No production row is reviewed.
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: isolatedUrl + url.pathname + url.search });
    await route.fulfill({ response });
  });
});

test('Review UI validates reviewer, approves literal topic evidence, records audit, preserves source', async ({ page }) => {
  const original = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  await page.goto('/reviews');
  await expect(page.getByRole('note').filter({ hasText: 'عينة مصطنعة للاختبار — ليست من الكتاب' })).toBeVisible();
  const first = page.locator('.evidence').first();
  await first.getByRole('button', { name: 'مراجعة هذا الموضع' }).click();
  await page.getByRole('button', { name: 'اعتماد بعد المراجعة' }).click();
  await expect(page.getByRole('alert')).toContainText('أدخل اسم المراجع');
  await page.getByRole('textbox', { name: 'اسم المراجع' }).fill('مراجع اختبار مصطنع');
  await page.getByRole('textbox', { name: 'ملاحظة المراجعة' }).fill('قرار آلي لاختبار الواجهة على عينة مصطنعة فقط.');
  await page.getByRole('button', { name: 'اعتماد بعد المراجعة' }).click();
  await expect(page.locator('.success[role="status"]')).toContainText('حُفظ القرار في سجل التدقيق');
  await page.getByRole('combobox', { name: 'حالة التدقيق' }).selectOption('reviewed');
  await expect(page.locator('.evidence')).toHaveCount(1);
  await expect(page.locator('.evidence .status')).toHaveText('مُراجَع');
  await page.getByRole('button', { name: 'عرض سجل التدقيق' }).click();
  await expect(page.locator('.log-entry')).toContainText('مراجع اختبار مصطنع');
  const unchanged = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  expect(unchanged.full_text).toBe(original.full_text);
  const screenshotDirectory = process.env.SIYAR_QA_SCREENSHOTS ?? path.join(os.homedir(), '.codex', 'visualizations', 'siyar-qa-20261027');
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  await page.screenshot({ path: path.join(screenshotDirectory, 'review-synthetic.png'), fullPage: false });
});

test('Unicode codepoint reader highlighting stays exact after an astral character', async ({ page }) => {
  const results = await (await fetch(`${isolatedUrl}/api/search?q=${encodeURIComponent('الإخلاص')}`)).json();
  const result = results.items[0];
  const original = await (await fetch(`${isolatedUrl}/api/biographies/${result.biography_id}`)).json();
  const points = Array.from(original.full_text as string);
  const relativeStart = points.join('').indexOf('الإخلاص');
  const start = original.source_char_start + Array.from(points.join('').slice(0, relativeStart)).length;
  const end = start + Array.from('الإخلاص').length;
  await page.goto(`/reader/${result.biography_id}?start=${start}&end=${end}`);
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  await expect(page.getByTestId('original-text')).toBeVisible();
  expect(await page.getByTestId('original-text').textContent()).toBe(original.full_text);
  expect(await page.locator('mark').textContent()).toBe('الإخلاص');
});

test('Mention identity needs an explicit choice before approval', async ({ page }) => {
  await page.goto('/reviews?kind=mentions');
  await expect(page.locator('.evidence')).toHaveCount(1);
  await page.getByRole('button', { name: 'مراجعة هذا الموضع' }).click();
  await page.getByRole('textbox', { name: 'اسم المراجع' }).fill('مراجع هوية مصطنعة');
  await page.getByRole('button', { name: 'اعتماد بعد المراجعة' }).click();
  await expect(page.getByRole('alert')).toContainText('اختر هوية العلم');
  await page.getByRole('textbox', { name: 'البحث عن الهوية' }).fill('ثانية');
  await expect(page.getByRole('combobox', { name: 'الهوية المطابقة' }).locator('option')).toHaveCount(2);
  await page.getByRole('combobox', { name: 'الهوية المطابقة' }).selectOption('2');
  await page.getByRole('button', { name: 'اعتماد بعد المراجعة' }).click();
  await expect(page.locator('.success[role="status"]')).toContainText('حُفظ القرار في سجل التدقيق');
  await page.goto('/people/2');
  await expect(page.getByRole('heading', { name: 'الترجمة الأصلية', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'ذكره في تراجم أخرى', exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'حالة التدقيق' }).selectOption('reviewed');
  await expect(page.locator('.evidence')).toHaveCount(1);
  await expect(page.locator('.evidence .status')).toHaveText('مُراجَع');
});

test('Reader creates a manual topic candidate from the exact selected source range', async ({ page }) => {
  const original = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  const segments = await (await fetch(`${isolatedUrl}/api/biographies/1/segments`)).json();
  const segment = segments.items[0];
  const topics = await (await fetch(`${isolatedUrl}/api/topics?page_size=100`)).json();
  const chosen = topics.items.find((topic: { name: string }) => topic.name === 'التوحيد');
  expect(chosen).toBeTruthy();
  await page.goto('/reader/1');
  await page.getByRole('button', { name: 'استعراض المقاطع وحدودها' }).click();
  await page.getByRole('button', { name: 'ربط بموضوع', exact: true }).first().click();
  const form = page.locator('.manual-tag');
  await expect(form).toBeVisible();
  await expect(form.getByRole('combobox', { name: 'حالة الوسم' })).toHaveValue('candidate');
  await form.getByRole('combobox', { name: 'موضوع الوسم اليدوي' }).selectOption(String(chosen.id));
  await form.getByRole('textbox', { name: 'مراجع الوسم اليدوي' }).fill('مراجع ربط يدوي مصطنع');
  await expect(form.getByRole('spinbutton', { name: 'بداية الوسم اليدوي' })).toHaveValue(String(segment.source_char_start));
  await expect(form.getByRole('spinbutton', { name: 'نهاية الوسم اليدوي' })).toHaveValue(String(segment.source_char_end));
  const response = page.waitForResponse(response => response.url().includes(`/api/segments/${segment.id}/topic-hits`) && response.request().method() === 'POST');
  await form.getByRole('button', { name: 'حفظ ربط الموضوع' }).click();
  const savedResponse = await response;
  expect(savedResponse.ok()).toBeTruthy();
  const saved = await savedResponse.json();
  expect(saved.method).toBe('manual');
  expect(saved.review_status).toBe('candidate');
  expect(saved.evidence_text).toBe(segment.text_original);
  const originalSlice = Array.from(synthetic).slice(saved.source_char_start, saved.source_char_end).join('');
  expect(saved.evidence_text).toBe(originalSlice);
  await expect(form).toHaveCount(0);
  const audit = await (await fetch(`${isolatedUrl}/api/review-log`)).json();
  expect(audit.items.some((entry: { reviewer: string; entity_id: number }) => entry.reviewer === 'مراجع ربط يدوي مصطنع' && entry.entity_id === saved.id)).toBeTruthy();
  const unchanged = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  expect(unchanged.full_text).toBe(original.full_text);
});

test('Segment review saves structural type and boundaries without editing original biography', async ({ page }) => {
  const original = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  const queue = await (await fetch(`${isolatedUrl}/api/reviews?kind=segments&status=candidate`)).json();
  const segment = queue.items[0];
  await page.goto('/reviews?kind=segments');
  await page.locator('.evidence').first().getByRole('button', { name: 'مراجعة هذا الموضع' }).click();
  await page.getByRole('textbox', { name: 'اسم المراجع' }).fill('مراجع مقطع مصطنع');
  await page.getByRole('combobox', { name: 'نوع المقطع' }).selectOption('saying');
  await expect(page.getByRole('spinbutton', { name: 'بداية الشاهد' })).toHaveValue(String(segment.source_char_start));
  await expect(page.getByRole('spinbutton', { name: 'نهاية الشاهد' })).toHaveValue(String(segment.source_char_end));
  const response = page.waitForResponse(response => response.url().includes(`/api/reviews/segments/${segment.id}`) && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'حفظ حدود المقطع' }).click();
  const savedResponse = await response;
  expect(savedResponse.ok()).toBeTruthy();
  const saved = await savedResponse.json();
  expect(saved.segment_type).toBe('saying');
  expect(saved.segmentation_status).toBe('reviewed');
  expect(saved.source_char_start).toBe(segment.source_char_start);
  expect(saved.source_char_end).toBe(segment.source_char_end);
  expect(saved.text_original).toBe(segment.text_original);
  await expect(page.locator('.success[role="status"]')).toContainText('حُفظ القرار في سجل التدقيق');
  await page.getByRole('combobox', { name: 'حالة التدقيق' }).selectOption('reviewed');
  await expect(page.locator('.evidence')).toHaveCount(1);
  await expect(page.locator('.evidence .status')).toHaveText('مُراجَع');
  const unchanged = await (await fetch(`${isolatedUrl}/api/biographies/1`)).json();
  expect(unchanged.full_text).toBe(original.full_text);
});
