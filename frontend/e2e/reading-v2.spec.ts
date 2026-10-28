import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { firstUncuratedBiography, useSourceVocalization } from './real-curation';

// Explicitly synthetic source. Every API request is intercepted in this group;
// these tests never mutate the installed reference library.
const raw = '### $ شخصية مصطنعة 🧪\r\n# قال راوٍ: هذا خبر، وأشار به يوم\r\n~~PageV01P005\r\n# السقيفة. ثم قال: إسناده حسن، فهو وغيره منام (1).\r\n# خبر مستقل: نَصٌّ 🧪 محفوظ.\r\n# بيت مصطنع ... للاختبار فقط.\r\n';
const offset = 100;
const at = (text: string) => offset + Array.from(raw.slice(0, raw.indexOf(text))).length;
const span = (text: string) => ({ start: at(text), end: at(text) + Array.from(text).length });
const textRun = (text: string) => ({ type: 'text', text, source_char_start: at(text), source_char_end: at(text) + Array.from(text).length });
const spaceRun = (text: string) => ({ type: 'virtual_space', text: ' ', source_char_start: at(text), source_char_end: at(text) });
const note = { ...span('(1)'), raw: '(1)', context: 'إسناده حسن، فهو وغيره منام (1).' };
const end = offset + Array.from(raw).length;
const baseBlocks = raw.split('\r\n').filter(Boolean).map((line, i) => ({ id: `base-${i}`, type: i ? 'paragraph' : 'heading', source_char_start: at(line), source_char_end: at(line) + Array.from(line).length + 2, runs: [textRun(line.slice(i ? 2 : 6))] }));
const groupBounds = [offset, at('# قال'), at('# خبر مستقل'), at('# بيت'), end];
const groupedBlocks = [
  baseBlocks[0],
  { id: 'group-report', type: 'paragraph', source_char_start: groupBounds[1], source_char_end: groupBounds[2], runs: [textRun('قال راوٍ: هذا خبر، وأشار به يوم'), spaceRun('~~Page'), { type: 'page', text: 'PageV01P005', raw_marker: 'PageV01P005', source_char_start: at('PageV01P005'), source_char_end: at('PageV01P005') + 11 }, spaceRun('السقيفة'), textRun('السقيفة. ثم قال: إسناده حسن، فهو وغيره منام (1).')] },
  { ...baseBlocks[4], id: 'group-independent' },
  { ...baseBlocks[5], id: 'group-poetry' },
];
const view = { renderer_version: 'synthetic-v2', source_char_start: offset, source_char_end: end, blocks: groupedBlocks, plain_text: 'شخصية مصطنعة 🧪\n\nقال راوٍ: هذا خبر، وأشار به يوم السقيفة. ثم قال: إسناده حسن، فهو وغيره منام (1).\n\nخبر مستقل: نَصٌّ 🧪 محفوظ.\n\nبيت مصطنع ... للاختبار فقط.' };
const plain = '[عنوان تنظيمي: خبر تجريبي]\n\nقال راوٍ: هذا خبر، وأشار به يوم السقيفة. ثم قال: إسناده حسن، فهو وغيره منام .\n\nخبر مستقل: نَصٌّ 🧪 محفوظ.\n\nبيت مصطنع ... للاختبار فقط.\n\n[ملحق إشارات الحواشي]\n(1)';
const presentation = {
  available: true, status: 'ready', biography_id: 1, revision: 'synthetic-pilot-v1', presentation_revision: 'synthetic-reading-v2', source_sha256: 'synthetic', reading_view: view, plain_text: plain,
  paragraph_groups: groupBounds.slice(0, -1).map((start, i) => ({ start, end: groupBounds[i + 1] })),
  criticism: [{ ...span('إسناده حسن، فهو وغيره منام'), context_start: groupBounds[1], context_end: groupBounds[2], kind: 'report', reason: 'إبراز قيد المنام مع تقويم الإسناد ضمن نص مصطنع.' }],
  annotations: { headings: [{ at: groupBounds[1], label: 'خبر تجريبي' }], quotes: [{ start: at('هذا خبر'), end: at('السقيفة.') + Array.from('السقيفة.').length }], poetry: [{ start: groupBounds[3], end }], lists: [], bold: [span('إسناده حسن')], keywords: [span('منام')], names: [span('راوٍ')], footnotes: [note] },
};

test.describe('Source-mapped reading v2 with synthetic isolated responses', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let json: unknown = { items: [], total: 0, page: 1, page_size: 10 };
      if (path === '/api/stats') json = { sources: 1, biographies: 6, people: 6, topics: 0, categories: 0, reviewed: 0, source: { is_synthetic: true } };
      else if (/\/biographies\/[16]$/.test(path)) json = { id: Number(path.split('/').at(-1)), heading: 'شخصية مصطنعة', full_text: raw, source_id: 1, source_char_start: offset, source_char_end: end, extraction_status: 'candidate', source: { sha256: 'synthetic' } };
      else if (path.endsWith('/presentation')) json = path.includes('/6/') ? { available: false, status: 'not_available' } : presentation;
      else if (path.endsWith('/reading-view')) json = { ...view, blocks: baseBlocks };
      else if (path.endsWith('/topics')) json = { pilot: false, items: [] };
      await route.fulfill({ json });
    });
  });

  test('A connected report spans page markers and embedded quotes in one naturally wrapping paragraph', async ({ page }) => {
    await page.goto('/reader/1');
    const reader = page.getByTestId('pilot-reader');
    await expect(reader).toHaveClass(/pilot-reading-v2/);
    const report = reader.locator(`.pilot-body p[data-source-start="${groupBounds[1]}"]`);
    await expect(report).toHaveCount(1);
    await expect(report).toContainText('وأشار به يوم');
    await expect(report).toContainText('السقيفة. ثم قال:');
    await expect(report.locator('.pilot-inline-quote')).not.toHaveCount(0);
    await expect(report.locator('p,br')).toHaveCount(0);
    await expect(report).toHaveCSS('white-space', 'normal');
    await expect(reader.locator('.pilot-body p')).toHaveCount(3);
    await expect(reader.locator('.pilot-poetry p')).toHaveCSS('white-space', 'pre-wrap');
    await report.getByRole('button', { name: 'إحالة PageV01P005' }).click();
    await expect(report.getByRole('note')).toContainText('علامة OpenITI خام');
  });

  test('The full qualification is emphasized, with independent general and criticism toggles', async ({ page }) => {
    await page.goto('/reader/1');
    const reader = page.getByTestId('pilot-reader');
    const criticism = reader.locator('.pilot-body .pilot-criticism');
    await expect(criticism.first()).toBeVisible();
    expect((await criticism.allTextContents()).join('')).toBe('إسناده حسن، فهو وغيره منام');
    await expect(criticism.first()).toHaveCSS('font-weight', '700');
    await expect(criticism.first()).toHaveCSS('background-color', 'rgb(245, 234, 208)');
    await expect(reader).toContainText('الإبراز المظلل: ألفاظ تقويم ونقد واردة في المصدر');
    await reader.getByRole('checkbox', { name: 'تفعيل الإبراز', exact: true }).uncheck();
    await expect(criticism.first()).toHaveCSS('font-weight', '700');
    await expect(reader.locator('.pilot-body .pilot-person-name')).toHaveCSS('color', await reader.locator('.pilot-body').evaluate(element => getComputedStyle(element).color));
    await reader.getByRole('checkbox', { name: 'إبراز عبارات التحقيق', exact: true }).uncheck();
    await expect(criticism.first()).toHaveCSS('font-weight', '400');
    await expect(criticism.first()).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await reader.getByRole('checkbox', { name: 'تفعيل الإبراز', exact: true }).check();
    await expect(reader.locator('.pilot-body .pilot-bold')).toHaveCSS('font-weight', '700');
    expect((await criticism.allTextContents()).join('')).toBe('إسناده حسن، فهو وغيره منام');
  });

  test('Unicode witnesses and criticism highlights preserve their literal characters and readable selection', async ({ page }) => {
    const selected = span('نَصٌّ 🧪');
    await page.goto(`/reader/1?start=${selected.start}&end=${selected.end}`);
    await expect(page.locator('.pilot-body mark').first()).toBeVisible();
    expect((await page.locator('.pilot-body mark').allTextContents()).join('')).toBe('نَصٌّ 🧪');
    const critique = span('إسناده حسن');
    await page.goto(`/reader/1?start=${critique.start}&end=${critique.end}`);
    await expect(page.locator('.pilot-body mark').first()).toBeVisible();
    expect((await page.locator('.pilot-body mark').allTextContents()).join('')).toBe('إسناده حسن');
    await expect(page.locator('.pilot-body mark .pilot-criticism')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await expect(page.locator('.pilot-body mark')).toHaveCSS('background-color', 'rgb(255, 240, 165)');
  });

  test('Formatted copy uses grouped paragraphs and appendix while raw display and copy are literal', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/reader/1');
    await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
    expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toContain(plain);
    await page.getByRole('button', { name: 'نسخ الأصل الحرفي', exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(raw);
    await page.getByRole('button', { name: 'النص الأصلي', exact: true }).click();
    expect(await page.getByTestId('original-text').textContent()).toBe(raw);
    await page.getByRole('button', { name: 'قراءة منسّقة', exact: true }).click();
    await expect(page.locator('.pilot-reading-v2')).toBeVisible();
  });

  test('A moved footnote remains reachable inside the joined report and opens its exact appendix marker', async ({ page }) => {
    await page.goto(`/reader/1?start=${note.start + 1}&end=${note.end - 1}`);
    await expect(page.locator('.pilot-footnotes')).toHaveAttribute('open');
    await expect(page.locator('.pilot-footnotes mark')).toHaveText('1');
    await expect(page.locator('.pilot-footnotes mark')).toBeInViewport();
    await page.getByRole('button', { name: 'موضع الإشارة في المتن' }).click();
    await expect(page.locator('.pilot-note-anchor')).toBeFocused();
    await expect(page.locator(`p[data-source-start="${groupBounds[1]}"] .pilot-note-anchor`)).toHaveCount(1);
  });

  test('Mobile wrapping, font controls and keyboard toggle work without truncation or horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.goto('/reader/1');
    const paragraph = page.locator(`.pilot-body p[data-source-start="${groupBounds[1]}"]`);
    await expect(paragraph).toHaveCSS('white-space', 'normal');
    await expect(paragraph).toHaveCSS('text-overflow', 'clip');
    await page.getByRole('button', { name: 'تكبير الخط', exact: true }).click();
    await expect(page.locator('.reader-content')).toHaveCSS('font-size', '25px');
    const toggle = page.getByRole('checkbox', { name: 'إبراز عبارات التحقيق', exact: true });
    await toggle.focus(); await page.keyboard.press('Space');
    await expect(toggle).not.toBeChecked();
    await expect(page.locator('.pilot-reader')).toHaveClass(/pilot-without-criticism/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(errors).toEqual([]);
  });

  test('An absent reading overlay keeps the existing pilot renderer and the sixth biography stays on its standard reader', async ({ page }) => {
    await page.route('**/api/biographies/1/presentation', route => route.fulfill({ json: { ...presentation, presentation_revision: undefined, paragraph_groups: undefined, criticism: undefined } }));
    await page.goto('/reader/1');
    await expect(page.getByTestId('pilot-reader')).not.toHaveClass(/pilot-reading-v2/);
    await expect(page.getByRole('checkbox', { name: 'إبراز عبارات التحقيق', exact: true })).toHaveCount(0);
    await page.goto('/reader/6');
    await expect(page.getByTestId('pilot-reader')).toHaveCount(0);
    await expect(page.getByTestId('formatted-text')).toBeVisible();
  });

  test('A technical-only group preserves its API coverage without adding an empty visual paragraph', async ({ page }) => {
    const technicalView = {
      ...view,
      coverage: { source_chars: end - offset, accounted_chars: end - offset, complete: true },
      blocks: [...groupedBlocks.slice(0, -1), { ...groupedBlocks.at(-1), source_char_end: end - 1 }, { id: 'technical-final-newline', type: 'paragraph', source_char_start: end - 1, source_char_end: end, runs: [{ type: 'virtual_space', text: ' ', source_char_start: end - 1, source_char_end: end - 1 }] }],
    };
    await page.route('**/api/biographies/1/presentation', route => route.fulfill({ json: { ...presentation, reading_view: technicalView } }));
    await page.goto('/reader/1');
    const reader = page.locator('.pilot-reading-v2');
    await expect(reader).toBeVisible();
    await expect(reader.locator('.pilot-body p')).toHaveCount(3);
    await expect(reader.locator(`.pilot-body p[data-source-start="${end - 1}"]`)).toHaveCount(0);
    const retained = await page.evaluate(async () => (await fetch('/api/biographies/1/presentation')).json());
    expect(retained.reading_view.blocks.at(-1).id).toBe('technical-final-newline');
    expect(retained.reading_view.coverage.complete).toBe(true);
    expect(retained.reading_view.coverage.accounted_chars).toBe(retained.reading_view.coverage.source_chars);
  });
});

const realSourceDigest = '7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0';
const screenshots = process.env.SIYAR_READING_SCREENSHOTS ?? 'C:/Users/asala/.codex/visualizations/siyar-reading-v2-20261028';
type MappedRun = { type: string; text: string; source_char_start: number; source_char_end: number };
type MappedBlock = { type: string; source_char_start: number; source_char_end: number; runs: MappedRun[] };
type Range = { start: number; end: number };
function visibleRuns(blocks: MappedBlock[], notes: Range[], target?: Range) {
  return blocks.filter(block => block.runs.some(run => run.type === 'page' || run.type === 'text' && run.text.trim())).flatMap(block => block.runs).map(run => {
    if (run.type === 'virtual_space') return !target || target.start <= run.source_char_start && run.source_char_start < target.end ? run.text : '';
    if (run.type !== 'text') return '';
    return Array.from(run.text).filter((_, index) => {
      const position = run.source_char_start + index;
      return (!target || position >= target.start && position < target.end) && !notes.some(note => note.start <= position && position < note.end);
    }).join('');
  }).join('');
}

test.describe('Read-only installed five-biography reading overlay', () => {
  test('All five biographies render every grouped literal run and every authored criticism without quote-induced paragraphs', async ({ page, request }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    let criticalCount = 0;
    for (const id of [1, 2, 3, 4, 5]) {
      const response = await (await request.get(`/api/biographies/${id}/presentation`)).json();
      test.skip(!response.presentation_revision || response.source_sha256 !== realSourceDigest, 'The real source-bound reading overlay has not been installed; no synthetic substitute is counted.');
      await page.goto(`/reader/${id}`);
      await useSourceVocalization(page);
      const reader = page.locator('.pilot-reading-v2');
      await expect(reader).toBeVisible();
      expect(response.reading_view.coverage.complete).toBe(true);
      expect(response.reading_view.coverage.accounted_chars).toBe(response.reading_view.coverage.source_chars);
      expect(await reader.locator('.pilot-body').evaluate(element => {
        const clone = element.cloneNode(true) as HTMLElement;
        clone.querySelectorAll('.pilot-helper-heading,.page-anchor').forEach(node => node.remove());
        return clone.textContent;
      })).toBe(visibleRuns(response.reading_view.blocks, response.annotations.footnotes));
      await expect(reader.locator('.pilot-body p')).toHaveCount(response.reading_view.blocks.filter((block: MappedBlock) => block.type === 'paragraph' && block.runs.some(run => run.type === 'page' || run.type === 'text' && run.text.trim())).length);
      await expect(reader.locator('.pilot-body p p,.pilot-body p br')).toHaveCount(0);
      for (const critique of response.criticism) {
        const literal = await reader.locator(`.pilot-body [data-criticism-start="${critique.start}"][data-criticism-end="${critique.end}"]`).allTextContents();
        const expected = visibleRuns(response.reading_view.blocks, response.annotations.footnotes, critique).replace(/\s+/g, ' ').trim();
        // Structural join spaces are zero-width source insertions; text fragments
        // are compared apart from whitespace, while the full AST check above
        // separately verifies all inserted whitespace in its original order.
        expect(literal.join('').replace(/\s+/g, '')).toBe(expected.replace(/\s+/g, ''));
        criticalCount++;
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await expect(page.locator('vite-error-overlay')).toHaveCount(0);
    }
    expect(criticalCount).toBeGreaterThan(20);
    expect(errors).toEqual([]);
  });

  test('Real page-break continuations remain in a single paragraph on desktop and mobile', async ({ page, request }) => {
    fs.mkdirSync(screenshots, { recursive: true });
    for (const [id, first, second] of [[1, 'وأشار به يوم', 'السقيفة؛'], [5, 'قال عمر لسعد: قد', 'شكوك في كل شيء']] as const) {
      const p = await (await request.get(`/api/biographies/${id}/presentation`)).json();
      test.skip(!p.presentation_revision || p.source_sha256 !== realSourceDigest, 'Actual reading overlay absent.');
      await page.goto(`/reader/${id}`);
      await useSourceVocalization(page);
      const paragraph = page.locator('.pilot-body p').filter({ hasText: first });
      await expect(paragraph).toHaveCount(1);
      await expect(paragraph).toContainText(second);
      await paragraph.scrollIntoViewIfNeeded();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: path.join(screenshots, `after-reader-${id}-desktop.png`), animations: 'disabled' });
      await page.setViewportSize({ width: 390, height: 844 });
      await paragraph.scrollIntoViewIfNeeded();
      await expect(paragraph).toHaveCSS('white-space', 'normal');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await page.screenshot({ path: path.join(screenshots, `after-reader-${id}-mobile.png`), animations: 'disabled' });
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
  });

  test('Real criticism retains qualifications, independent controls, exact copy, witness links and accessible contrast', async ({ page, request, context }) => {
    const p = await (await request.get('/api/biographies/4/presentation')).json();
    test.skip(!p.presentation_revision || p.source_sha256 !== realSourceDigest, 'Actual reading overlay absent.');
    const original = await (await request.get('/api/biographies/4')).json();
    const qualificationAt = original.source_char_start + Array.from(original.full_text.slice(0, original.full_text.indexOf('فهو وغيره منام'))).length;
    const criticism = p.criticism.find((row: Range) => row.start <= qualificationAt && qualificationAt < row.end);
    expect(criticism).toBeTruthy();
    const phrase = Array.from(original.full_text).slice(criticism.start - original.source_char_start, criticism.end - original.source_char_start).join('');
    expect(phrase).toContain('إسناده حسن'); expect(phrase).toContain('فهو وغيره منام');
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/reader/4');
    await useSourceVocalization(page);
    const fragments = page.locator(`[data-criticism-start="${criticism.start}"][data-criticism-end="${criticism.end}"]`);
    await expect(fragments.first()).toBeVisible();
    await fragments.first().scrollIntoViewIfNeeded();
    await page.evaluate(() => document.fonts.ready);
    fs.mkdirSync(screenshots, { recursive: true });
    await page.screenshot({ path: path.join(screenshots, 'after-reader-4-criticism-desktop.png'), animations: 'disabled' });
    const contrast = await fragments.first().evaluate(element => {
      const rgb = (value: string) => value.match(/[\d.]+/g)!.slice(0, 3).map(Number);
      const luminance = (value: number[]) => value.map(channel => channel / 255).map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
      const style = getComputedStyle(element), values = [luminance(rgb(style.color)), luminance(rgb(style.backgroundColor))].sort((a, b) => b - a);
      return (values[0] + .05) / (values[1] + .05);
    });
    expect(contrast).toBeGreaterThanOrEqual(4.5);
    await page.getByRole('checkbox', { name: 'تفعيل الإبراز', exact: true }).uncheck();
    await expect(fragments.first()).toHaveCSS('font-weight', '700');
    await page.setViewportSize({ width: 390, height: 844 });
    await fragments.first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(screenshots, 'after-reader-4-criticism-mobile.png'), animations: 'disabled' });
    const toggle = page.getByRole('checkbox', { name: 'إبراز عبارات التحقيق', exact: true });
    await toggle.focus(); await page.keyboard.press('Space'); await expect(toggle).not.toBeChecked();
    await expect(fragments.first()).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
    expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toContain(p.plain_text.replace(/\r\n/g, '\n'));
    await page.getByRole('button', { name: 'نسخ الأصل الحرفي', exact: true }).click();
    expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toBe(original.full_text.replace(/\r\n/g, '\n'));
    await page.goto(`/reader/4?start=${criticism.start}&end=${criticism.end}`);
    await useSourceVocalization(page);
    await expect(page.locator('.pilot-body mark').first()).toBeInViewport();
    expect((await page.locator('.pilot-body mark').allTextContents()).join('').replace(/\s+/g, '')).toBe(visibleRuns(p.reading_view.blocks, p.annotations.footnotes, criticism).replace(/\s+/g, ''));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    const outside = await firstUncuratedBiography(request, original.source_id);
    if (outside) {
      const originalOutside = await (await request.get(`/api/biographies/${outside.id}`)).json();
      const outsidePresentation = await (await request.get(`/api/biographies/${outside.id}/presentation`)).json();
      expect(outsidePresentation.available).toBe(false);
      await page.goto(`/reader/${outside.id}`);
      await expect(page.getByTestId('pilot-reader')).toHaveCount(0);
      await page.getByRole('button', { name: 'النص الأصلي', exact: true }).click();
      expect(await page.getByTestId('original-text').textContent()).toBe(originalOutside.full_text);
    } else test.info().annotations.push({ type: 'note', description: 'Every biography after the legacy sample is curated; only the outside-batch reader check is unavailable.' });
  });
});
