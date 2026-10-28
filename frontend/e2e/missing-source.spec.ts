import { expect, test } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

let directory: string;
let server: ChildProcess;
let isolatedUrl: string;

test.beforeAll(async () => {
  directory = fs.mkdtempSync(path.join(os.tmpdir(), 'siyar-ui-missing-'));
  const socket = net.createServer();
  await new Promise<void>(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = (socket.address() as net.AddressInfo).port;
  await new Promise<void>(resolve => socket.close(() => resolve()));
  isolatedUrl = `http://127.0.0.1:${port}`;
  server = spawn(process.env.SIYAR_E2E_PYTHON ?? 'python', ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: path.resolve(import.meta.dirname, '../..'), windowsHide: true, stdio: 'ignore',
    env: { ...process.env, SIYAR_DB: path.join(directory, 'empty.sqlite3'), SIYAR_SOURCE: path.join(directory, 'missing.mARkdown') },
  });
  const until = Date.now() + 15_000;
  while (Date.now() < until) {
    try { if ((await fetch(`${isolatedUrl}/api/health`)).ok) return; } catch {}
    if (server.exitCode !== null) throw new Error('The isolated empty-source server exited.');
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('The isolated empty-source server did not become ready.');
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill();
    await Promise.race([new Promise(resolve => server.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 5000))]);
  }
  if (directory && path.dirname(directory) === os.tmpdir() && path.basename(directory).startsWith('siyar-ui-missing-')) fs.rmSync(directory, { recursive: true, force: true });
});

test('Missing source shows explicit Arabic setup notice and no fabricated biographies', async ({ page }) => {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: isolatedUrl + url.pathname + url.search });
    await route.fulfill({ response });
  });
  const stats = await (await fetch(`${isolatedUrl}/api/stats`)).json();
  expect(stats.source_missing).toBeTruthy();
  expect(stats.source_file_exists).toBeFalsy();
  expect(stats.biographies).toBe(0);
  expect(stats.sources).toBe(0);
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('النص المرجعي غير موجود');
  await expect(page.getByRole('alert')).toContainText('data/source.mARkdown');
  await expect(page.locator('.bio-row')).toHaveCount(0);
  await page.goto('/people');
  await expect(page.locator('.directory-row')).toHaveCount(0);
  await expect(page.locator('.empty')).toBeVisible();
  await page.goto('/topics');
  await expect(page.locator('.topic-row')).toHaveCount(81);
  expect(await page.locator('.topic-row p').allTextContents()).toEqual(expect.arrayContaining([expect.stringContaining('0 شاهدًا')]));
});
