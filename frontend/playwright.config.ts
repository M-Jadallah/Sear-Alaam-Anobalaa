import { defineConfig, devices } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';

// The suite uses the documented local development servers. Production-source
// tests are read-only; all review decisions use a separate disposable database.
export default defineConfig({
  testDir: './e2e',
  outputDir: path.join(os.tmpdir(), 'siyar-playwright-results'),
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 12_000 },
  reporter: 'list',
  use: {
    baseURL: process.env.SIYAR_E2E_URL ?? 'http://127.0.0.1:5173',
    viewport: { width: 1440, height: 1000 },
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } }],
});
