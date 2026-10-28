import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

// Entirely synthetic, intercepted source; never publishes or reviews the
// installed book. Existing source marks and new editorial marks are distinct.
const raw = '### $ شَخصية مصطنعة 🧪\r\n# قال: حَسنٌ ونَص (1).\r\n# يروى عن عَلَم.\r\n';
const offset = 500;
const at = (value: string) => offset + Array.from(raw.slice(0, raw.indexOf(value))).length;
const span = (value: string) => ({ start: at(value), end: at(value) + Array.from(value).length });
const textRun = (value: string) => ({ type: 'text', text: value, source_char_start: at(value), source_char_end: at(value) + Array.from(value).length });
const end = offset + Array.from(raw).length;
const bounds = [offset, at('# قال'), at('# يروى'), end];
const note = { ...span('(1)'), raw: '(1)', context: 'قال: حَسنٌ ونَص (1).' };
const view = {
  renderer_version: 'synthetic-v2', source_char_start: offset, source_char_end: end,
  blocks: [
    { id: 'heading', type: 'heading', source_char_start: bounds[0], source_char_end: bounds[1], runs: [textRun('شَخصية مصطنعة 🧪')] },
    { id: 'report', type: 'paragraph', source_char_start: bounds[1], source_char_end: bounds[2], runs: [textRun('قال: حَسنٌ ونَص (1).')] },
    { id: 'name', type: 'paragraph', source_char_start: bounds[2], source_char_end: bounds[3], runs: [textRun('يروى عن عَلَم.')] },
  ],
  plain_text: 'شَخصية مصطنعة 🧪\n\nقال: حَسنٌ ونَص (1).\n\nيروى عن عَلَم.',
};
const insertions = [
  { base_start: at('قال'), at: at('قال') + 1, marks: 'َ' },
  { base_start: at('قال') + 2, at: at('قال') + 3, marks: 'َ' },
  { base_start: at('حَسنٌ') + 2, at: at('حَسنٌ') + 3, marks: 'َ' },
  { base_start: at('ونَص'), at: at('ونَص') + 1, marks: 'َ' },
  { base_start: at('ونَص') + 3, at: at('ونَص') + 4, marks: 'ٌّ' },
];
const notice = 'تشكيل تحريري أضافه الوكيل؛ موضع واحد بقي على ضبط المصدر لالتباسه.';
const plain = '[عنوان تنظيمي: خَبَرٌ مُصْطَنَعٌ]\n\nقال: حَسنٌ ونَص .\n\nيروى عن عَلَم.\n\n[ملحق إشارات الحواشي]\n(1) — قال: حَسنٌ ونَص (1).';
const vocalized = plain.replace('قال: حَسنٌ ونَص .', 'قَالَ: حَسَنٌ وَنَصٌّ .') + '\n\n' + notice;
const base = {
  available: true, status: 'ready', biography_id: 88, revision: 'synthetic-batch-v2', presentation_revision: 'synthetic-reading', batch_id: 'synthetic-vocalized', batch_label: 'دفعة تشكيل اصطناعية',
  source_sha256: 'synthetic', reading_view: view, plain_text: plain, vocalized_plain_text: vocalized,
  paragraph_groups: bounds.slice(0, -1).map((start, i) => ({ start, end: bounds[i + 1] })),
  criticism: [{ ...span('ونَص'), context_start: bounds[1], context_end: bounds[2], kind: 'report', reason: 'تعليق اختباري مصطنع.' }],
  annotations: { headings: [{ at: bounds[1], label: 'خَبَرٌ مُصْطَنَعٌ' }], quotes: [], poetry: [], lists: [], bold: [{ start: at('حَسنٌ') + 2, end: at('حَسنٌ') + 3 }], keywords: [], names: [{ start: at('ونَص') + 3, end: at('ونَص') + 4 }], footnotes: [note] },
  diacritics: { revision: 'synthetic-diacritics-v1', status: 'partial', insertions, coverage: { total_words: 10, checked_words: 10, complete_words: 9, uncertain_words: 1, not_applicable: 0 }, uncertainties: [{ ...span('عَلَم'), reason: 'اسم محتمل لم يحسم ضبطه من سياق الاختبار.' }], notice },
};

async function mock(page: Page, status: 'ready' | 'partial' | 'stale' | 'invalid' | 'absent' = 'partial') {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let json: unknown = { items: [], total: 0, page: 1, page_size: 10 };
    if (url.pathname === '/api/stats') json = { sources: 1, biographies: 1, people: 1, topics: 0, categories: 0, source: { is_synthetic: true } };
    else if (url.pathname === '/api/biographies/88') json = { id: 88, heading: 'شَخصية مصطنعة 🧪', full_text: raw, source_id: 1, source_char_start: offset, source_char_end: end, extraction_status: 'candidate' };
    else if (url.pathname.endsWith('/presentation')) json = { ...base, diacritics: status === 'absent' ? undefined : { ...base.diacritics, status } };
    else if (url.pathname.endsWith('/reading-view')) json = view;
    else if (url.pathname.endsWith('/topics')) json = { pilot: false, items: [] };
    await route.fulfill({ json });
  });
}

test('Added marks preserve ordered-list items and poetry boundaries without changing source ranges', async ({ page }) => {
  await mock(page);
  const listRange = span('قال: حَسنٌ ونَص (1).');
  const verseRange = span('يروى عن عَلَم.');
  await page.route('**/api/biographies/88/presentation', route => route.fulfill({ json: {
    ...base,
    annotations: { ...base.annotations, lists: [{ ...listRange, ordered: true, items: [listRange] }], poetry: [verseRange] },
  } }));
  await page.goto('/reader/88');
  const reader = page.getByTestId('pilot-reader');
  await expect(reader.locator('ol.pilot-list')).toHaveCount(1);
  await expect(reader.locator('ol.pilot-list li')).toHaveCount(1);
  await expect(reader.locator('ol.pilot-list')).toContainText('قَالَ: حَسَنٌ وَنَصٌّ .');
  await expect(reader.locator('.pilot-poetry')).toHaveCount(1);
  await expect(reader.locator('.pilot-poetry')).toContainText('يروى عن عَلَم.');
  await expect(reader.locator('.pilot-poetry p')).toHaveCSS('white-space', 'pre-wrap');
  await reader.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true }).uncheck();
  await expect(reader.locator('ol.pilot-list')).toContainText('قال: حَسنٌ ونَص .');
  await expect(reader.locator('.pilot-poetry')).toContainText('يروى عن عَلَم.');
});

test('Added vocalization defaults on; disabling it retains original vowels and persists across reload', async ({ page }) => {
  await mock(page); await page.goto('/reader/88');
  const toggle = page.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true });
  await expect(toggle).toBeChecked();
  await expect(page.locator('.pilot-body')).toContainText('قَالَ: حَسَنٌ وَنَصٌّ .');
  await toggle.uncheck();
  await expect(page.locator('.pilot-body')).toContainText('قال: حَسنٌ ونَص .');
  await expect(page.locator('.pilot-body')).not.toContainText('حَسَنٌ');
  await page.reload(); await expect(toggle).not.toBeChecked();
  await expect(page.locator('.pilot-body')).toContainText('حَسنٌ');
});

test('New marks stay in their base text node and inherit source highlight, color and criticism', async ({ page }) => {
  await mock(page); const selected = at('ونَص') + 3;
  await page.goto(`/reader/88?start=${selected}&end=${selected + 1}`);
  const mark = page.locator('.pilot-body mark');
  await expect(mark).toHaveText('صٌّ');
  await expect(mark).toHaveAttribute('data-source-start', String(selected));
  await expect(mark).toHaveAttribute('data-source-end', String(selected + 1));
  expect(await mark.evaluate(element => element.firstElementChild?.firstChild?.textContent)).toBe('صٌّ');
  await expect(page.locator('.pilot-body .pilot-person-name')).toHaveCSS('color', 'rgb(123, 45, 66)');
  await expect(mark.locator('.pilot-criticism')).toHaveCSS('font-weight', '700');
  await page.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true }).uncheck();
  await expect(mark).toHaveText('ص');
  const existingMark = at('حَسنٌ') + 1;
  await page.goto(`/reader/88?start=${existingMark}&end=${existingMark + 1}`);
  await expect(page.locator('.pilot-body mark')).toHaveText('حَ');
});

test('Formatted copy follows the toggle and includes the source appendix; raw copy and raw view remain exact', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await mock(page); await page.goto('/reader/88');
  await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toContain(vocalized);
  await page.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true }).uncheck();
  await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
  const unvocalized = (await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n');
  expect(unvocalized).toContain(plain); expect(unvocalized).not.toContain(notice);
  await page.getByRole('button', { name: 'نسخ الأصل الحرفي', exact: true }).click();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toBe(raw.replace(/\r\n/g, '\n'));
  await page.getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  expect(await page.getByTestId('original-text').textContent()).toBe(raw);
});

test('Ambiguous words retain source spelling and expose a range link without suggesting human approval', async ({ page }) => {
  await mock(page); await page.goto('/reader/88');
  const noticeRegion = page.getByRole('note', { name: 'التشكيل التحريري' });
  await expect(noticeRegion).toContainText(notice); await expect(noticeRegion).toContainText(/مواضع تحتاج مراجعة: [1١]/);
  await expect(page.locator('.pilot-body')).toContainText('عَلَم');
  await noticeRegion.getByText('مواضع التشكيل الملتبسة', { exact: true }).click();
  const link = noticeRegion.getByRole('link', { name: /موضع في الأصل/ });
  await expect(link).toHaveAttribute('href', `/reader/88?start=${at('عَلَم')}&end=${at('عَلَم') + 5}`);
  await link.click(); await expect(page.locator('.pilot-body mark')).toHaveText('عَلَم');
});

test('Stale or invalid additions are not applied or copied; a legacy presentation has no new control', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  for (const status of ['stale', 'invalid', 'absent'] as const) {
    await page.unroute('**/api/**'); await mock(page, status); await page.goto('/reader/88');
    await expect(page.getByRole('checkbox', { name: 'إظهار الحركات المضافة' })).toHaveCount(0);
    await expect(page.locator('.pilot-body')).toContainText('قال: حَسنٌ ونَص .');
    await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).not.toContain('قَالَ');
    if (status !== 'absent') await expect(page.getByRole('status')).toContainText('تعذر التحقق من طبقة التشكيل المضاف');
  }
});

test('Vocalization fits mobile with large text, keyboard toggles and exact moved-note highlighting', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 }); await mock(page); await page.goto('/reader/88');
  for (let i = 0; i < 6; i++) await page.getByRole('button', { name: 'تكبير الخط', exact: true }).click();
  await expect(page.locator('.pilot-body')).toHaveCSS('overflow', 'visible');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const toggle = page.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true });
  await toggle.focus(); await page.keyboard.press('Space'); await expect(toggle).not.toBeChecked();
  await page.keyboard.press('Space'); await expect(toggle).toBeChecked();
  await page.goto(`/reader/88?start=${note.start}&end=${note.end}`);
  await expect(page.locator('.pilot-footnotes')).toHaveAttribute('open');
  await expect(page.locator('.pilot-footnotes mark')).toHaveText('(1)');
  await expect(page.locator('.pilot-footnotes mark')).toBeInViewport();
  expect(errors).toEqual([]);
});
