import { expect, test } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

let server: ChildProcess;
let url: string;
const evidence = process.env.SIYAR_QA_SCREENSHOTS ?? path.join(os.homedir(), '.codex', 'visualizations', 'siyar-deployment');
test.use({ ignoreHTTPSErrors: true });
test.beforeAll(async () => {
  fs.mkdirSync(evidence, { recursive: true });
  const socket = net.createServer();
  await new Promise<void>(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = (socket.address() as net.AddressInfo).port;
  await new Promise<void>(resolve => socket.close(() => resolve()));
  url = `https://localhost:${port}`;
  server = spawn(process.env.SIYAR_E2E_PYTHON ?? 'python', ['scripts/serve_production_test.py', '--port', String(port)], { cwd: path.resolve(import.meta.dirname, '../..'), windowsHide: true });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('HTTPS production fixture did not start')), 60000);
    server.stdout?.on('data', data => { if (String(data).includes('READY')) { clearTimeout(timer); resolve(); } });
    server.stderr?.on('data', data => { if (String(data).includes('Traceback')) { clearTimeout(timer); reject(new Error(String(data))); } });
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exited: ${code}`)); });
  });
});
test.afterAll(async ({ request }) => {
  if (server && server.exitCode === null) {
    await request.get(url + '/__test_shutdown');
    await Promise.race([new Promise(resolve => server.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 10000))]);
    if (server.exitCode === null) server.kill();
  }
});

test('HTTPS visitor -> admin login -> protected review -> logout, desktop and mobile', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(url + '/reviews');
  await expect(page.getByRole('heading', { name: 'دخول المدير' })).toBeVisible();
  await expect(page.getByLabel('اسم المستخدم')).toBeVisible();
  await page.screenshot({ path: path.join(evidence, 'login-desktop.png'), fullPage: true });
  await page.getByLabel('اسم المستخدم').fill('browser-admin');
  await page.getByLabel('كلمة المرور').fill('synthetic-browser-password-only');
  await page.getByRole('button', { name: 'تسجيل الدخول', exact: true }).click();
  await expect(page).toHaveURL(url + '/reviews');
  await expect(page.getByRole('button', { name: 'تسجيل الخروج' })).toBeVisible();
  await expect(page.locator('main')).not.toContainText('يلزم تسجيل دخول المدير');
  await page.getByRole('button', { name: 'تسجيل الخروج' }).click();
  await expect(page.getByRole('heading', { name: 'دخول المدير' })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('اسم المستخدم')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(evidence, 'login-mobile.png'), fullPage: true });
  await page.goto(url + '/reader/1');
  await expect(page.locator('main')).toContainText('أبو عبيدة');
  const publicResponse = await request.get(url + '/api/pilot/evidence?page_size=1');
  expect(publicResponse.status()).toBe(200);
  const publicEvidence = await publicResponse.json();
  expect(publicEvidence.items.length).toBe(1);
  await page.goto(url + '/topics/' + publicEvidence.items[0].topic_id);
  await expect(page.getByLabel('شواهد الموضوع من العينة')).toBeVisible();
  expect(errors).toEqual([]);
  expect((await request.get(url + '/api/reviews')).status()).toBe(401);
});
