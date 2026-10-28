import { expect, test } from '@playwright/test';

// All requests, including review writes, are intercepted. This fixture is
// explicitly synthetic and never writes to the reference library.
const raw = '### $ شخصية مصطنعة 🧪\r\n# قال زيد: العلم نافع (1).\r\n# أولًا: الصدق، وثانيًا: الأمانة.\r\n# بيت مصطنع ... للاختبار فقط.\r\n';
const offset = 100;
const at = (text: string) => offset + Array.from(raw.slice(0, raw.indexOf(text))).length;
const span = (text: string) => ({ start: at(text), end: at(text) + Array.from(text).length });
const blocks = raw.split('\r\n').filter(Boolean).map((line, i) => {
  const start = at(line), prefix = i ? '# ' : '### $ ';
  return { id: `block-${i}`, type: i ? 'paragraph' : 'heading', source_char_start: start, source_char_end: start + Array.from(line).length + 2, runs: [{ type: 'text', text: line.slice(prefix.length), source_char_start: start + prefix.length, source_char_end: start + Array.from(line).length }] };
});
const view = { renderer_version: 'synthetic-reading', source_char_start: offset, source_char_end: offset + Array.from(raw).length, blocks, plain_text: blocks.map(block => block.runs[0].text).join('\n\n') };
const footnote = { ...span('(1)'), raw: '(1)', context: 'العلم نافع (1).' };
const presentation = { available: true, status: 'ready', biography_id: 1, revision: 'synthetic-v1', source_sha256: 'synthetic', reading_view: view, plain_text: '[عنوان تنظيمي: قول تجريبي]\nقال زيد: العلم نافع.\nإشارات الحواشي المحفوظة\n(1)', annotations: { headings: [{ at: blocks[1].source_char_start, label: 'قول تجريبي' }], quotes: [{ start: blocks[1].source_char_start, end: blocks[1].source_char_end }], poetry: [{ start: blocks[3].source_char_start, end: blocks[3].source_char_end }], lists: [{ start: at('أولًا:'), end: at('ثانيًا: الأمانة.') + Array.from('ثانيًا: الأمانة.').length, ordered: true, items: [span('أولًا: الصدق'), span('ثانيًا: الأمانة.')] }], bold: [span('نافع')], keywords: [span('العلم')], names: [span('زيد')], footnotes: [footnote] } };
const audit = { id: 7, topic_id: 5, topic_name: 'طلب العلم', biography_id: 1, segment_id: 10, evidence_text: 'قال زيد: العلم نافع (1).', source_char_start: at('قال زيد:'), source_char_end: footnote.end + 1, context_text: 'نص مصطنع: قال زيد: العلم نافع (1).', context_char_start: at('قال زيد:'), context_char_end: footnote.end + 1, relation_type: 'saying', subject_scope: 'mentioned_person', stance: 'reported', rationale: 'المقطع قول في منفعة العلم لشخص مذكور، لا وصف لصاحب الترجمة.', agent_decision: 'supported', human_status: 'pending' };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', async route => {
    const u = new URL(route.request().url());
    let json: unknown = { items: [], total: 0, page: 1, page_size: 10 };
    if (u.pathname === '/api/stats') json = { sources: 1, biographies: 6, people: 6, topics: 1, categories: 1, reviewed: 0, source: { is_synthetic: true } };
    else if (/\/biographies\/[16]$/.test(u.pathname)) json = { id: Number(u.pathname.split('/').at(-1)), heading: 'شخصية مصطنعة', full_text: raw, source_id: 1, source_char_start: offset, source_char_end: offset + Array.from(raw).length, extraction_status: 'candidate', source: { sha256: 'synthetic' } };
    else if (u.pathname.endsWith('/reading-view')) json = view;
    else if (u.pathname.endsWith('/presentation')) json = u.pathname.includes('/6/') ? { available: false, status: 'not_available', biography_id: 6 } : presentation;
    else if (/\/biographies\/1\/topics$/.test(u.pathname)) json = { pilot: true, coverage: { total: 3, agent_checked: 3, human_checked: 0, uncertain: 0, no_clear_topic: 2 }, items: [{ topic_id: 5, name: 'طلب العلم', dictionary_status: 'proposed_from_text', evidence_count: 1, human_reviewed_count: 0, evidence: [audit] }] };
    else if (u.pathname === '/api/topics') json = { items: [{ id: 5, name: 'طلب العلم' }], total: 1, page: 1, page_size: 100 };
    else if (u.pathname === '/api/pilot/reviews') json = { items: [audit], total: 1, page: 1, page_size: 10 };
    else if (u.pathname === '/api/semantic/status') json = { ready: false, status: 'missing_models', reason: 'بيئة اختبار مصطنعة' };
    else if (u.pathname === '/api/search/interpret') json = { method: 'rules', interpretations: [{ id: 'synthetic', label: 'العلم', meaning: 'منفعة العلم', include_terms: [], exclude_terms: [], topic_ids: [5] }] };
    else if (u.pathname === '/api/search/smart') json = { items: [], total: 0, page: 1, page_size: 10, mode: 'lexical_only' };
    await route.fulfill({ json });
  });
});

test('Pilot rendering preserves literal runs with editorial headings, lists, colors and collapsed notes', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message)); page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto('/reader/1');
  const reader = page.getByTestId('pilot-reader');
  await expect(reader).toBeVisible();
  await expect(reader.locator('.pilot-helper-heading')).toContainText('عنوان تنظيمي');
  await expect(reader.locator('.pilot-source-quote')).toContainText('قال زيد: العلم نافع');
  await expect(reader.locator('.pilot-body')).not.toContainText('(1)');
  await expect(reader.locator('.pilot-body .pilot-person-name')).toHaveText('زيد');
  await expect(reader.locator('.pilot-body .pilot-person-name')).toHaveCSS('color', 'rgb(123, 45, 66)');
  await expect(reader.locator('.pilot-list')).toHaveCount(1);
  await expect(reader.locator('.pilot-list li')).toHaveCount(2);
  await expect(reader.locator('.pilot-list li').first()).toContainText('الصدق، و');
  const mappedText = await reader.locator('.pilot-body span[data-source-start]').allTextContents();
  expect(mappedText.join('')).toBe(blocks.map(block => block.runs[0].text).join('').replace('(1)', ''));
  await expect(reader.locator('.pilot-poetry')).toContainText('بيت مصطنع');
  await expect(reader.locator('.pilot-footnotes')).not.toHaveAttribute('open');
  await reader.getByText('إشارات الحواشي المحفوظة', { exact: false }).click();
  await expect(reader.locator('.pilot-footnote-raw')).toBeVisible();
  await expect(reader.locator('.pilot-footnotes')).toContainText('لم يُعثر على متون الحواشي');
  await reader.getByRole('checkbox', { name: 'تفعيل الإبراز' }).uncheck();
  await expect(reader).toHaveClass(/pilot-without-emphasis/);
  await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('[عنوان تنظيمي: قول تجريبي]');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('(1)');
  await page.getByRole('button', { name: 'نسخ الأصل الحرفي', exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(raw);
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  expect(await page.getByTestId('original-text').textContent()).toBe(raw);
  expect(errors).toEqual([]);
});

test('A witness on a hidden footnote opens and highlights the exact appendix marker', async ({ page }) => {
  await page.goto(`/reader/1?start=${footnote.start}&end=${footnote.end}`);
  await expect(page.locator('.pilot-footnotes')).toHaveAttribute('open');
  await expect(page.locator('.pilot-footnotes mark')).toHaveText('(1)');
  await page.getByRole('button', { name: 'موضع الإشارة في المتن' }).click();
  await expect(page.locator('.pilot-note-anchor')).toBeFocused();
  await page.goto(`/reader/1?start=${footnote.start + 1}&end=${footnote.end - 1}`);
  await expect(page.locator('.pilot-footnotes mark')).toHaveText('1');
  await page.goto(`/reader/1?start=${span('العلم').start}&end=${footnote.end}`);
  await expect(page.locator('.pilot-footnotes')).toHaveAttribute('open');
  await expect(page.locator('.pilot-body mark').first()).toBeInViewport();
});

test('A stale presentation explicitly falls back to the source-mapped standard reader', async ({ page }) => {
  await page.route('**/api/biographies/1/presentation', route => route.fulfill({ json: { available: false, status: 'stale', biography_id: 1, warning: 'تغيرت بصمة المصدر؛ أُوقف التنسيق التجريبي.' } }));
  await page.goto('/reader/1');
  await expect(page.getByTestId('pilot-reader')).toHaveCount(0);
  await expect(page.getByTestId('formatted-text')).toContainText('(1)');
  await expect(page.getByRole('note').filter({ hasText: 'تغيرت بصمة المصدر' })).toBeVisible();
});

test('Exclusion recommendation approval is distinct from confirming a topic', async ({ page }) => {
  await page.route('**/api/pilot/reviews?*', route => route.fulfill({ json: { items: [{ ...audit, agent_decision: 'exclude', rationale: 'اللفظ جزء من اسم راوٍ.' }], total: 1, page: 1, page_size: 10 } }));
  await page.goto('/pilot/reviews');
  await expect(page.getByRole('button', { name: 'اعتماد الاستبعاد', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'اعتماد بشري وحفظ التعديل' })).toHaveCount(0);
  await expect(page.getByText('اعتماد الاستبعاد يرفض الربط اللفظي الأصلي', { exact: false })).toBeVisible();
});

test('Human coverage checks do not approve topic recommendations', async ({ page }) => {
  let body: Record<string, unknown> | undefined;
  let topicWrites = 0;
  page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/pilot/reviews/')) topicWrites++; });
  await page.route('**/api/pilot/coverage?*', route => route.fulfill({ json: { items: [{ segment_id: 10, biography_id: 1, text_original: audit.evidence_text, source_char_start: audit.source_char_start, source_char_end: audit.source_char_end, assessment: 'no_clear_topic', note: 'فُحص المقطع المصطنع.', reviewer: 'وكيل', human_checked: 0 }], total: 1, page: 1, page_size: 10 } }));
  await page.route('**/api/pilot/coverage/10', async route => { body = route.request().postDataJSON(); await route.fulfill({ json: { ok: true } }); });
  await page.goto('/pilot/reviews?kind=coverage');
  await expect(page.getByText('لا موضوع واضح', { exact: false })).toBeVisible();
  const save = page.getByRole('button', { name: 'تسجيل الفحص البشري للمقطع' });
  await expect(save).toBeDisabled();
  await page.getByRole('textbox', { name: 'مراجع المقطع 10', exact: true }).fill('مراجع اختبار');
  await save.click();
  await expect.poll(() => body).toEqual({ reviewer: 'مراجع اختبار', note: '' });
  expect(topicWrites).toBe(0);
});

test('Topic catalog and concept selectors include proposed entries after the first hundred', async ({ page }) => {
  const rows = Array.from({ length: 101 }, (_, i) => ({ id: i + 1, name: i === 100 ? 'موضوع مقترح بعد المائة' : `موضوع تجريبي ${i + 1}`, hit_count: 0, reviewed_count: 0 }));
  await page.route('**/api/topics?*', route => { const pageNumber = Number(new URL(route.request().url()).searchParams.get('page') ?? 1); return route.fulfill({ json: { items: rows.slice((pageNumber - 1) * 100, pageNumber * 100), total: 101, page: pageNumber, page_size: 100 } }); });
  await page.goto('/topics');
  await expect(page.locator('.topic-row')).toHaveCount(101);
  await expect(page.locator('.topic-row').last()).toContainText('موضوع مقترح بعد المائة');
  await page.goto('/search?kind=description');
  await page.getByRole('textbox', { name: 'صف ما تبحث عنه', exact: true }).fill('منفعة العلم');
  await page.getByRole('button', { name: 'اقترح المعاني' }).click();
  await page.getByRole('button', { name: 'اختيار هذا المعنى' }).click();
  await expect(page.getByRole('option', { name: 'موضوع مقترح بعد المائة', exact: true })).toHaveCount(1);
});

test('Topic panel distinguishes mentioned people and agent checks from human approval, with mobile layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/reader/1');
  const panel = page.getByRole('region', { name: 'موضوعات الترجمة وشواهدها' });
  await expect(panel).toContainText(/فحص الوكلاء: [3٣] من [3٣]/);
  await panel.locator('.pilot-topic-row > summary').click();
  await expect(panel).toContainText('يتصل بشخص مذكور');
  await expect(panel).toContainText('بانتظار اعتمادك');
  await expect(panel.getByRole('link', { name: 'اقرأ الشاهد في سياقه' })).toHaveAttribute('href', `/reader/1?start=${audit.source_char_start}&end=${audit.source_char_end}`);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.goto('/reader/6');
  await expect(page.getByTestId('formatted-text')).toBeVisible();
  await expect(page.getByTestId('pilot-reader')).toHaveCount(0);
  await expect(page.getByTestId('formatted-text')).toContainText('(1)');
});

test('Pilot review requires a human name and posts the explicit edited evidence decision only to the mock', async ({ page }) => {
  let body: Record<string, unknown> | undefined;
  await page.route('**/api/pilot/reviews/7', async route => { body = route.request().postDataJSON(); await route.fulfill({ json: { ok: true } }); });
  await page.goto('/pilot/reviews');
  const approve = page.getByRole('button', { name: 'اعتماد بشري وحفظ التعديل' });
  await expect(approve).toBeDisabled();
  await page.getByRole('textbox', { name: 'اسم المراجع 7', exact: true }).fill('مراجع بيئة الاختبار');
  await page.getByRole('textbox', { name: 'ملاحظة المراجع 7', exact: true }).fill('قرأت السياق المصطنع.');
  await approve.click();
  await expect.poll(() => body).toMatchObject({ decision: 'reviewed', reviewer: 'مراجع بيئة الاختبار', topic_id: 5, source_char_start: audit.source_char_start, source_char_end: audit.source_char_end });
});

test('Smart topic controls invalidate results and submit filter, pilot and human review preferences', async ({ page }) => {
  let body: Record<string, unknown> | undefined;
  await page.route('**/api/search/smart', async route => { body = route.request().postDataJSON(); await route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10, mode: 'lexical_only' } }); });
  await page.goto('/search?kind=description');
  await page.getByRole('textbox', { name: 'صف ما تبحث عنه', exact: true }).fill('منفعة العلم');
  await page.getByRole('button', { name: 'اقترح المعاني' }).click();
  await page.getByRole('button', { name: 'اختيار هذا المعنى' }).click();
  await page.getByLabel('استخدام الموضوعات المختارة').selectOption('filter');
  await page.getByRole('checkbox', { name: 'البحث في التراجم الخمس التجريبية فقط' }).check();
  await page.getByRole('checkbox', { name: 'شواهد الموضوعات المعتمدة بشريًا فقط' }).check();
  await page.getByRole('button', { name: 'ابحث بهذا المعنى' }).click();
  await expect.poll(() => body).toMatchObject({ plan: { topic_mode: 'filter', reviewed_only: true, pilot_only: true, topic_ids: [5] } });
});

test('A narrow deduplicated witness retains its qualified criticism context and safe reader link', async ({ page }) => {
  const contextText = 'نص مصطنع: ذُكر خبر في طلب العلم. ثم قال الناقد: حديث منكر.';
  const contextStart = 90, contextEnd = contextStart + Array.from(contextText).length;
  const narrowStart = 110, narrowEnd = 114;
  let readerUrl = `/reader/1?start=${contextStart}&end=${contextEnd}`;
  const contextView = { renderer_version: 'synthetic-reading', source_char_start: contextStart, source_char_end: contextEnd, blocks: [{ id: 'context-1', type: 'paragraph', source_char_start: contextStart, source_char_end: contextEnd, runs: [{ type: 'text', text: contextText, source_char_start: contextStart, source_char_end: contextEnd }] }], plain_text: contextText };
  await page.route('**/api/search?*', route => route.fulfill({ json: { items: [{ id: 70, source_id: 1, biography_id: 1, heading: 'عينة مصطنعة', source_char_start: narrowStart, source_char_end: narrowEnd, text_original: 'العلم', evidence_text: 'العلم', topic_audit_id: 7, topic_review_status: 'pending', match_type: 'topic_candidate', retrieval_methods: ['semantic', 'topic_candidate'], stance: 'criticism', subject_scope: 'mentioned_person', context_text: contextText, context_char_start: contextStart, context_char_end: contextEnd, context_reading_view: contextView, reader_url: readerUrl }], total: 1, page: 1, page_size: 10 } }));
  await page.goto('/search?q=العلم');
  const item = page.locator('.evidence');
  await expect(item.locator('.quote').first()).toHaveText('العلم');
  await expect(item.getByLabel('دلالة الشاهد الموضوعي')).toContainText('نقد رواية');
  await expect(item.getByLabel('دلالة الشاهد الموضوعي')).toContainText('يتصل بشخص مذكور');
  await expect(item.getByRole('link', { name: 'اقرأ السياق', exact: true })).toHaveAttribute('href', readerUrl);
  await item.getByText('السياق الموضوعي المؤهل', { exact: true }).click();
  await expect(item.locator('.qualified-topic-context')).toContainText('حديث منكر');
  await expect(item.locator('.qualified-topic-context .formatted-text')).toBeVisible();
  for (const invalid of ['javascript:alert(1)', `/reader/2?start=${contextStart}&end=${contextEnd}`, '/reader/1?start=100&end=90']) {
    readerUrl = invalid;
    await page.reload();
    await expect(item.getByRole('link', { name: 'اقرأ السياق', exact: true })).toHaveAttribute('href', `/reader/1?start=${narrowStart}&end=${narrowEnd}`);
  }
});
