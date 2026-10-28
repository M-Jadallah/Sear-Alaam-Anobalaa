import { expect, test } from '@playwright/test';

// Every request (including decisions) is intercepted. These biographies and
// batches are explicitly synthetic and never touch the reference database.
const legacyId = 'legacy-first-five:synthetic';
const batchId = 'synthetic-six-seven';
const batches = [
  { id: legacyId, label: 'العينة الأولى', source_id: 1, start_ordinal: 1, end_ordinal: 5, legacy: true, published: true, biographies: [{ id: 101, source_ordinal: 1, heading: 'شخصية تجريبية أولى' }] },
  { id: batchId, label: 'دفعة اختبار ثانية', source_id: 1, start_ordinal: 6, end_ordinal: 7, legacy: false, published: true, biographies: [{ id: 206, source_ordinal: 6, heading: 'شخصية مصطنعة سادسة' }, { id: 207, source_ordinal: 7, heading: 'شخصية مصطنعة سابعة' }] },
];
const audit = { id: 72, topic_id: 5, topic_name: 'موضوع اصطناعي', biography_id: 206, segment_id: 810, evidence_text: 'خبر مصطنع للاختبار.', source_char_start: 100, source_char_end: 120, context_text: 'خبر مصطنع للاختبار.', context_char_start: 100, context_char_end: 120, relation_type: 'discussion', subject_scope: 'general_context', stance: 'reported', rationale: 'اختبار فصل قرارات الدفعات.', agent_decision: 'supported', human_status: 'pending', batch_id: batchId };
const raw = '### $ شخصية مصطنعة سادسة\n# خبر مصطنع للاختبار.\n# متن حاشية مصطنع محفوظ ظاهرًا دون ربط محقق.\n';
const formatted = 'شخصية مصطنعة سادسة\n\nخبر مصطنع للاختبار.\n\nمتن حاشية مصطنع محفوظ ظاهرًا دون ربط محقق.\n\nتوجد متون حواشٍ ظاهرة في هذا المثال؛ لم تربط بإشاراتها بعد.';
const view = { renderer_version: 'synthetic', source_char_start: 0, source_char_end: Array.from(raw).length, blocks: [{ id: 'synthetic-1', type: 'paragraph', source_char_start: 0, source_char_end: Array.from(raw).length, runs: [{ type: 'text', text: raw, source_char_start: 0, source_char_end: Array.from(raw).length }] }], plain_text: raw };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', async route => {
    const u = new URL(route.request().url());
    let json: unknown = { items: [], total: 0, page: 1, page_size: 10 };
    if (u.pathname === '/api/stats') json = { sources: 1, biographies: 7, people: 7, topics: 1, categories: 1, source: { is_synthetic: true } };
    else if (u.pathname === '/api/curation/batches') json = { items: batches };
    else if (u.pathname === '/api/biographies') json = { items: batches[0].biographies, total: 1, page: 1, page_size: 5 };
    else if (u.pathname === '/api/topics') json = { items: [{ id: 5, name: 'موضوع اصطناعي' }], total: 1, page: 1, page_size: 100 };
    else if (u.pathname === '/api/pilot/reviews') json = { items: [audit], total: 1, page: 1, page_size: 10 };
    else if (u.pathname === '/api/pilot/coverage') json = { items: [{ ...audit, assessment: 'no_clear_topic', note: 'قراءة مصطنعة.', reviewer: 'وكيل اختبار', human_checked: 0, text_original: audit.evidence_text }], total: 1, page: 1, page_size: 10 };
    else if (u.pathname === '/api/semantic/status') json = { ready: false, status: 'missing_models', reason: 'بيئة اختبار' };
    else if (u.pathname === '/api/search/interpret') json = { method: 'rules', interpretations: [{ id: 'synthetic', label: 'المعنى التجريبي', meaning: 'خبر مصطنع', include_terms: [], exclude_terms: [], topic_ids: [] }] };
    else if (u.pathname === '/api/search/smart') json = { items: [], total: 0, page: 1, page_size: 10, mode: 'lexical_only' };
    else if (u.pathname === '/api/biographies/206') json = { id: 206, heading: 'شخصية مصطنعة سادسة', full_text: raw, source_id: 1, source_char_start: 0, source_char_end: Array.from(raw).length, extraction_status: 'candidate' };
    else if (u.pathname.endsWith('/reading-view')) json = view;
    else if (u.pathname.endsWith('/presentation')) json = { available: true, status: 'ready', biography_id: 206, revision: 'synthetic-v1', batch_id: batchId, batch_label: 'دفعة اختبار ثانية', footnote_notice: 'توجد متون حواشٍ ظاهرة في هذا المثال؛ لم تربط بإشاراتها بعد.', reading_view: view, plain_text: formatted, annotations: { headings: [], quotes: [], poetry: [], lists: [], bold: [], keywords: [], names: [], footnotes: [] } };
    else if (u.pathname.endsWith('/topics')) json = { pilot: false, items: [] };
    await route.fulfill({ json });
  });
});

test('Review batches populate real biography IDs dynamically and preserve scope across tabs and decisions', async ({ page }) => {
  const requested: string[] = [];
  let body: Record<string, unknown> | undefined;
  page.on('request', request => { if (new URL(request.url()).pathname === '/api/pilot/reviews') requested.push(request.url()); });
  await page.route('**/api/pilot/reviews/72', async route => { body = route.request().postDataJSON(); await route.fulfill({ json: { ok: true } }); });
  await page.goto('/pilot/reviews');
  await page.getByRole('combobox', { name: 'دفعة المراجعة' }).selectOption(batchId);
  await expect(page.getByRole('combobox', { name: 'ترجمة العينة' }).locator('option')).toHaveCount(3);
  await expect(page.getByRole('option', { name: /شخصية تجريبية أولى/ })).toHaveCount(0);
  await page.getByRole('combobox', { name: 'ترجمة العينة' }).selectOption('206');
  await expect.poll(() => requested.some(url => new URL(url).searchParams.get('batch_id') === batchId && new URL(url).searchParams.get('biography_id') === '206')).toBe(true);
  await expect(page.getByRole('option', { name: /٦ — شخصية مصطنعة سادسة|6 — شخصية مصطنعة سادسة/ })).toHaveAttribute('value', '206');
  await page.getByRole('textbox', { name: 'اسم المراجع 72', exact: true }).fill('مراجع اختبار فقط');
  await page.getByRole('button', { name: 'اعتماد بشري وحفظ التعديل' }).click();
  await expect.poll(() => body).toMatchObject({ batch_id: batchId, decision: 'reviewed', reviewer: 'مراجع اختبار فقط' });
  await page.getByRole('link', { name: 'تغطية فحص المقاطع', exact: true }).click();
  await expect(page).toHaveURL(/kind=coverage/);
  expect(new URL(page.url()).searchParams.get('batch_id')).toBe(batchId);
  expect(new URL(page.url()).searchParams.get('biography_id')).toBe('206');
});

test('Changing review batch clears an incompatible biography and coverage writes include their batch', async ({ page }) => {
  let body: Record<string, unknown> | undefined;
  await page.route('**/api/pilot/coverage/810', async route => { body = route.request().postDataJSON(); await route.fulfill({ json: { ok: true } }); });
  await page.goto(`/pilot/reviews?kind=coverage&batch_id=${legacyId}&biography_id=101`);
  await page.getByRole('combobox', { name: 'دفعة المراجعة' }).selectOption(batchId);
  expect(new URL(page.url()).searchParams.has('biography_id')).toBe(false);
  await expect(page.getByRole('combobox', { name: 'ترجمة العينة' })).toHaveValue('');
  await page.getByRole('textbox', { name: 'مراجع المقطع 810', exact: true }).fill('مراجع تغطية مصطنعة');
  await page.getByRole('button', { name: 'تسجيل الفحص البشري للمقطع' }).click();
  await expect.poll(() => body).toEqual({ reviewer: 'مراجع تغطية مصطنعة', note: '', batch_id: batchId });
});

test('Smart search uses an explicit batch independently of the old five-biography pilot scope', async ({ page }) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route('**/api/search/smart', async route => { bodies.push(route.request().postDataJSON()); await route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10, mode: 'lexical_only' } }); });
  await page.goto('/search?kind=description');
  await page.getByRole('textbox', { name: 'صف ما تبحث عنه', exact: true }).fill('خبر مصطنع');
  await page.getByRole('button', { name: 'اقترح المعاني' }).click();
  await page.getByRole('button', { name: 'اختيار هذا المعنى' }).click();
  await page.getByRole('checkbox', { name: 'البحث في التراجم الخمس التجريبية فقط' }).check();
  await page.getByRole('combobox', { name: 'دفعة البحث' }).selectOption(batchId);
  await expect(page.getByRole('checkbox', { name: 'البحث في التراجم الخمس التجريبية فقط' })).not.toBeChecked();
  await page.getByRole('button', { name: 'ابحث بهذا المعنى' }).click();
  await expect.poll(() => bodies).toHaveLength(1);
  expect(bodies[0]).toMatchObject({ plan: { batch_id: batchId, pilot_only: false } });
  await expect(page.locator('.smart-results')).toBeVisible();
  await page.getByRole('checkbox', { name: 'البحث في التراجم الخمس التجريبية فقط' }).check();
  await expect(page.getByRole('combobox', { name: 'دفعة البحث' })).toHaveValue('');
  await expect(page.locator('.smart-results')).toHaveCount(0);
  await page.getByRole('button', { name: 'ابحث بهذا المعنى' }).click();
  await expect.poll(() => bodies).toHaveLength(2);
  expect(bodies[1]).toMatchObject({ plan: { pilot_only: true } });
  expect((bodies[1].plan as Record<string, unknown>).batch_id).toBeUndefined();
});

test('New reader batches use a neutral label, retain unlinked footnote bodies and copy the source-backed form', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/reader/206');
  await expect(page.locator('.pilot-trial-label')).toContainText('دفعة تحريرية: دفعة اختبار ثانية');
  await expect(page.locator('.pilot-trial-label')).not.toContainText('الخمس');
  await expect(page.getByTestId('pilot-reader')).toBeVisible();
  await page.locator('.pilot-footnotes > summary').click();
  await expect(page.locator('.pilot-footnotes')).toContainText('توجد متون حواشٍ ظاهرة');
  await expect(page.locator('.pilot-footnotes')).not.toContainText('لم يُعثر');
  await expect(page.locator('.pilot-body')).toContainText('متن حاشية مصطنع محفوظ ظاهرًا دون ربط محقق.');
  await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toContain(formatted);
  await page.getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  expect(await page.getByTestId('original-text').textContent()).toBe(raw);
});

test('An older API without the optional batch catalog falls back to dynamic legacy biographies without a visible error', async ({ page }) => {
  const writes: Record<string, unknown>[] = [];
  await page.route('**/api/curation/batches', route => route.fulfill({ status: 404, json: { detail: 'Not Found' } }));
  await page.route('**/api/pilot/reviews?*', route => route.fulfill({ json: { items: [{ ...audit, batch_id: undefined }], total: 1, page: 1, page_size: 10 } }));
  await page.route('**/api/pilot/reviews/72', async route => { writes.push(route.request().postDataJSON()); await route.fulfill({ json: { ok: true } }); });
  await page.goto('/pilot/reviews');
  await expect(page.getByRole('combobox', { name: 'دفعة المراجعة' })).toHaveCount(0);
  await expect(page.getByRole('option', { name: /شخصية تجريبية أولى/ })).toHaveAttribute('value', '101');
  await expect(page.locator('.notice.danger')).toHaveCount(0);
  await page.getByRole('textbox', { name: 'اسم المراجع 72', exact: true }).fill('اختبار توافق قديم');
  await page.getByRole('button', { name: 'اعتماد بشري وحفظ التعديل' }).click();
  await expect.poll(() => writes).toHaveLength(1);
  expect(writes[0].batch_id).toBeUndefined();
  await page.goto('/search?kind=description');
  await page.getByRole('textbox', { name: 'صف ما تبحث عنه', exact: true }).fill('خبر مصطنع');
  await page.getByRole('button', { name: 'اقترح المعاني' }).click();
  await page.getByRole('button', { name: 'اختيار هذا المعنى' }).click();
  await expect(page.getByRole('combobox', { name: 'دفعة البحث' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'ابحث بهذا المعنى' })).toBeEnabled();
});

test('Batch controls remain keyboard accessible and fit a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/pilot/reviews');
  const selector = page.getByRole('combobox', { name: 'دفعة المراجعة' });
  await selector.focus(); await page.keyboard.press('End'); await page.keyboard.press('Enter');
  await expect(selector).toHaveValue(batchId);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
