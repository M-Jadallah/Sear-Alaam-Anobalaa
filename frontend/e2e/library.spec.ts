import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const evidenceDirectory = process.env.SIYAR_QA_SCREENSHOTS ?? path.join(os.homedir(), '.codex', 'visualizations', 'siyar-qa-20261027');
fs.mkdirSync(evidenceDirectory, { recursive: true });
const arabicNumber = (n: number) => new Intl.NumberFormat('ar').format(n);

test('Arabic application identity, real statistics, and console health', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const stats = await (await request.get('/api/stats')).json();
  await page.goto('/');
  await expect(page).toHaveTitle('موسوعة سير أعلام النبلاء');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ar');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'ابحث في سير أعلام النبلاء' })).toBeVisible();
  await expect(page.locator('.stats-strip')).toContainText(arabicNumber(stats.biographies));
  await expect(page.locator('.stats-strip')).toContainText(arabicNumber(stats.topics));
  if (stats.source?.sha256 === '7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0') {
    expect(stats.biographies).toBe(5943);
  }
  expect(stats.topics).toBeGreaterThanOrEqual(81);
  expect(stats.categories).toBe(9);
  await expect(page.locator('vite-error-overlay')).toHaveCount(0);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.screenshot({ path: path.join(evidenceDirectory, 'home-desktop.png'), fullPage: false });
  expect(errors).toEqual([]);
});

test('Arabic search normalization preserves literal evidence and links to exact reader range', async ({ page, request }) => {
  const withHamza = await (await request.get('/api/search', { params: { q: 'الإخلاص', scope: 'text' } })).json();
  const withoutHamza = await (await request.get('/api/search', { params: { q: 'الاخلاص', scope: 'text' } })).json();
  expect(withHamza.total).toBe(withoutHamza.total);
  expect(withHamza.items.map((item: { id: number }) => item.id)).toEqual(withoutHamza.items.map((item: { id: number }) => item.id));
  test.skip(!withHamza.total, 'No real-source الإخلاص result available; no substitute data is shown.');
  await page.goto('/');
  await page.getByRole('textbox', { name: 'عبارة البحث' }).fill('الاخلاص');
  await page.getByRole('button', { name: 'بحث', exact: true }).click();
  await expect(page).toHaveURL(/\/search\?/);
  await expect(page.locator('.evidence')).toHaveCount(withoutHamza.items.length);
  const first = withoutHamza.items[0];
  await page.locator('.evidence').first().getByRole('button', { name: 'عرض الشاهد الخام', exact: true }).click();
  expect(await page.locator('.evidence .quote').first().textContent()).toBe(first.text_original);
  await expect(page.locator('.evidence').first()).toContainText('مرشّح');
  await page.locator('.evidence').first().getByRole('link', { name: 'اقرأ السياق' }).click();
  const reader = await (await request.get(`/api/${first.biography_id ? 'biographies' : 'sections'}/${first.biography_id ?? first.section_id}`)).json();
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  await expect(page.getByTestId('original-text')).toBeVisible();
  expect(await page.getByTestId('original-text').textContent()).toBe(reader.full_text);
  const literal = Array.from(reader.full_text as string).slice(first.source_char_start - reader.source_char_start, first.source_char_end - reader.source_char_start).join('');
  expect(literal).toBe(first.text_original);
  expect(await page.getByTestId('original-text').locator('mark').textContent()).toBe(literal);
  // Capture the settled highlighted range after fonts and scrolling stop.
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>(resolve => {
      let y = window.scrollY;
      let stable = 0;
      const frame = () => {
        stable = window.scrollY === y ? stable + 1 : 0;
        y = window.scrollY;
        if (stable >= 12) resolve();
        else requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    });
  });
  await page.screenshot({ path: path.join(evidenceDirectory, 'reader-desktop.png'), fullPage: false });
});

test('All dictionary topics and proposed additions are reachable with status filtering', async ({ page, request }) => {
  const topics = await (await request.get('/api/topics?page_size=100')).json();
  const all = [...topics.items];
  for (let pageNumber = 2; all.length < topics.total; pageNumber++) {
    const next = await (await request.get(`/api/topics?page_size=100&page=${pageNumber}`)).json();
    expect(next.items.length).toBeGreaterThan(0);
    all.push(...next.items);
  }
  expect(all.filter(topic => topic.dictionary_status !== 'proposed_from_text')).toHaveLength(81);
  await page.goto('/topics');
  await expect(page.locator('.topic-row')).toHaveCount(topics.total);
  await page.getByRole('textbox', { name: 'تصفية الموضوعات' }).fill('الغيبة');
  await expect(page.locator('.topic-row')).toHaveCount(1);
  await page.locator('.topic-row').first().click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('الغيبة');
  await page.getByRole('combobox', { name: 'حالة التدقيق' }).selectOption('reviewed');
  await expect(page.locator('.loading')).toHaveCount(0);
  const reviewedStatuses = await page.locator('.evidence .status').allTextContents();
  expect(reviewedStatuses.every(status => status === 'مُراجَع')).toBeTruthy();
  if (!reviewedStatuses.length) await expect(page.locator('.empty')).toContainText('هذا لا يثبت غيابها عن الكتاب');
});

test('Search result pages load distinct bounded batches and previous navigation restores them', async ({ page, request }) => {
  const first = await (await request.get('/api/search', { params: { q: 'الله', scope: 'text', page: '1' } })).json();
  test.skip(first.total <= first.page_size, 'The imported source has no multi-page search result.');
  const second = await (await request.get('/api/search', { params: { q: 'الله', scope: 'text', page: '2' } })).json();
  await page.goto('/search?q=الله');
  await expect(page.locator('.evidence')).toHaveCount(first.page_size);
  await page.locator('.evidence').first().getByRole('button', { name: 'عرض الشاهد الخام', exact: true }).click();
  expect(await page.locator('.evidence .quote').first().textContent()).toBe(first.items[0].text_original);
  await page.getByRole('navigation', { name: 'صفحات النتائج' }).getByRole('button', { name: 'التالي' }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.locator('.evidence')).toHaveCount(second.items.length);
  await page.locator('.evidence').first().getByRole('button', { name: 'عرض الشاهد الخام', exact: true }).click();
  await expect.poll(() => page.locator('.evidence .quote').first().textContent()).toBe(second.items[0].text_original);
  expect(second.items[0].id).not.toBe(first.items[0].id);
  await page.getByRole('navigation', { name: 'صفحات النتائج' }).getByRole('button', { name: 'السابق' }).click();
  await expect(page.locator('.evidence').first().getByRole('button', { name: 'عرض الشاهد الخام', exact: true })).toBeVisible();
  await page.locator('.evidence').first().getByRole('button', { name: 'عرض الشاهد الخام', exact: true }).click();
  await expect.poll(() => page.locator('.evidence .quote').first().textContent()).toBe(first.items[0].text_original);
});

test('Person view separates original biography from mentions and exposes independent identities', async ({ page, request }) => {
  const people = await (await request.get('/api/people?page_size=1')).json();
  test.skip(!people.total, 'No imported people; no invented identities are used.');
  const person = people.items[0];
  const detail = await (await request.get(`/api/people/${person.id}`)).json();
  await page.goto(`/people/${person.id}`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(person.canonical_name);
  await expect(page.getByRole('heading', { name: 'الترجمة الأصلية', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'ذكره في تراجم أخرى', exact: true })).toBeVisible();
  await expect(page.locator('.bio-row')).toHaveCount(detail.biographies.length);
  await expect(page.getByRole('note')).toContainText('لا تثبت هوية المذكور');
  await page.getByRole('combobox', { name: 'حالة التدقيق' }).selectOption('reviewed');
  await expect(page.locator('.loading')).toHaveCount(0);
  const approved = await (await request.get(`/api/people/${person.id}/mentions?status=reviewed`)).json();
  await expect(page.locator('.evidence')).toHaveCount(approved.items.length);
});

test('Source view displays fingerprint and readable non-biographical source sections', async ({ page, request }) => {
  const stats = await (await request.get('/api/stats')).json();
  await page.goto('/source');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('المصدر والمنهج');
  if (stats.source) await expect(page.locator('main')).toContainText(stats.source.sha256);
  await expect(page.locator('main')).toContainText('صفحات مفقودة');
  const sectionsPanel = page.locator('.source-panel').filter({ has: page.getByRole('heading', { name: 'الأقسام خارج التراجم' }) });
  await expect(sectionsPanel.locator('a.category-row').first()).toBeVisible();
  const firstSection = await sectionsPanel.locator('a.category-row').first().getAttribute('href');
  const sections = await (await request.get('/api/sections?page_size=20')).json();
  if (sections.total > sections.page_size) {
    await sectionsPanel.getByRole('button', { name: 'التالي' }).click();
    await expect.poll(() => sectionsPanel.locator('a.category-row').first().getAttribute('href')).not.toBe(firstSection);
  }
  await sectionsPanel.locator('a.category-row').first().click();
  await expect(page).toHaveURL(/\/sections\/\d+/);
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  await expect(page.getByTestId('original-text')).not.toBeEmpty();
});

test('Mobile navigation and key surfaces fit a 390px viewport', async ({ page, request }) => {
  const stats = await (await request.get('/api/stats')).json();
  await page.setViewportSize({ width: 390, height: 844 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'ابحث في سير أعلام النبلاء' })).toBeVisible();
  await page.getByRole('button', { name: 'فتح القائمة' }).click();
  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('link', { name: 'الموضوعات', exact: true }).click();
  await expect(page).toHaveURL(/\/topics$/);
  await expect(page.locator('.sidebar')).not.toHaveClass(/open/);
  await expect(page.locator('.topic-row')).toHaveCount(stats.topics);
  await page.screenshot({ path: path.join(evidenceDirectory, 'topics-mobile.png'), fullPage: false });
  for (const route of ['/', '/topics', '/people', '/source', '/search?q=الاخلاص', '/reader/1']) {
    await page.goto(route);
    await expect(page.locator('.loading')).toHaveCount(0);
    const widths = await page.evaluate(() => ({ viewport: window.innerWidth, scroll: document.documentElement.scrollWidth }));
    expect(widths.scroll, `Horizontal overflow on ${route}`).toBeLessThanOrEqual(widths.viewport + 1);
  }
  expect(errors).toEqual([]);
});
