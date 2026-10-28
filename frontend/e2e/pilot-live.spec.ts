import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { firstUncuratedBiography, useSourceVocalization } from './real-curation';

// Read-only checks against the installed real-source pilot. All decision
// mutation tests are in pilot.spec.ts and use intercepted synthetic requests.
const directory = process.env.SIYAR_PILOT_SCREENSHOTS ?? 'C:/Users/asala/.codex/visualizations/siyar-pilot-20261027';
const digest = '7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0';
type Span = { start: number; end: number };
type Run = { type: string; text: string; source_char_start: number };

test('Real five-biography reader preserves every visible source character outside reviewed footnote markers', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  fs.mkdirSync(directory, { recursive: true });
  for (const id of [1, 2, 3, 4, 5]) {
    const presentation = await (await request.get(`/api/biographies/${id}/presentation`)).json();
    test.skip(!presentation.available || presentation.source_sha256 !== digest, 'The actual source-bound pilot is not installed; no substitute source is tested.');
    await page.goto(`/reader/${id}`);
    await useSourceVocalization(page);
    const reader = page.getByTestId('pilot-reader');
    await expect(reader).toBeVisible();
    await expect(reader.locator('.pilot-footnotes')).not.toHaveAttribute('open');
    const notes: Span[] = presentation.annotations.footnotes;
    const expected = presentation.reading_view.blocks.flatMap((block: { runs: Run[] }) => block.runs).map((run: Run) => {
      if (run.type === 'virtual_space') return run.text;
      if (run.type !== 'text') return '';
      return Array.from(run.text).filter((_, index) => !notes.some(note => note.start <= run.source_char_start + index && note.end > run.source_char_start + index)).join('');
    }).join('');
    const rendered = await reader.locator('.pilot-body').evaluate(element => {
      const clone = element.cloneNode(true) as HTMLElement;
      clone.querySelectorAll('.pilot-helper-heading,.page-anchor').forEach(node => node.remove());
      return clone.textContent;
    });
    expect(rendered, `Literal reading coverage biography ${id}`).toBe(expected);
    if (id === 1) {
      await expect(reader.locator('.pilot-list')).toHaveCount(presentation.annotations.lists.length);
      await expect(reader.locator('.pilot-list li')).toHaveCount(6);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    if ([1, 4, 5].includes(id)) await page.screenshot({ path: path.join(directory, `reader-${id}-desktop.png`) });
  }
  expect(errors).toEqual([]);
});

test('Real pilot note witness opens its exact marker and raw copy remains the original text', async ({ page, request, context }) => {
  const presentation = await (await request.get('/api/biographies/1/presentation')).json();
  test.skip(!presentation.available || presentation.source_sha256 !== digest, 'Actual pilot absent.');
  const original = await (await request.get('/api/biographies/1')).json();
  const note = presentation.annotations.footnotes[0];
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(`/reader/1?start=${note.start}&end=${note.end}`);
  await useSourceVocalization(page);
  await expect(page.locator('.pilot-footnotes')).toHaveAttribute('open');
  await expect(page.locator('.pilot-footnotes mark')).toHaveText(note.raw);
  await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toContain(presentation.plain_text.replace(/\r\n/g, '\n'));
  await page.getByRole('button', { name: 'نسخ الأصل الحرفي', exact: true }).click();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toBe(original.full_text.replace(/\r\n/g, '\n'));
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
  expect(await page.getByTestId('original-text').textContent()).toBe(original.full_text);
  await page.screenshot({ path: path.join(directory, 'reader-1-original-desktop.png') });
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'قراءة منسّقة', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.pilot-footnotes mark')).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: path.join(directory, 'reader-1-footnote-mobile.png') });
});

test('Real topic review and public catalog distinguish proposals, and an uncurated reader stays unchanged', async ({ page, request }) => {
  const p = await (await request.get('/api/biographies/1/presentation')).json();
  test.skip(!p.available || p.source_sha256 !== digest, 'Actual pilot absent.');
  const originalPilot = await (await request.get('/api/biographies/1')).json();
  const outside = await firstUncuratedBiography(request, originalPilot.source_id);
  if (outside) {
    const originalOutside = await (await request.get(`/api/biographies/${outside.id}`)).json();
    const outsidePresentation = await (await request.get(`/api/biographies/${outside.id}/presentation`)).json();
    expect(outsidePresentation.available).toBe(false);
    await page.goto(`/reader/${outside.id}`);
    await expect(page.getByTestId('pilot-reader')).toHaveCount(0);
    await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'النص الأصلي', exact: true }).click();
    expect(await page.getByTestId('original-text').textContent()).toBe(originalOutside.full_text);
  } else test.info().annotations.push({ type: 'note', description: 'Every biography after the legacy sample is curated; only the outside-batch reader check is unavailable.' });
  await page.goto('/pilot/reviews');
  await expect(page.locator('.pilot-review-card').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'اعتماد بشري وحفظ التعديل' }).first()).toBeDisabled();
  await page.screenshot({ path: path.join(directory, 'topic-reviews-desktop.png') });
  await page.goto('/topics');
  const catalog = await (await request.get('/api/topics?page_size=100')).json();
  await expect(page.locator('.topic-row')).toHaveCount(catalog.total);
  const proposed = catalog.items.find((topic: { dictionary_status: string }) => topic.dictionary_status === 'proposed_from_text');
  expect(proposed).toBeTruthy();
  await page.goto(`/topics/${proposed.id}`);
  await expect(page.getByRole('region', { name: 'شواهد الموضوع من العينة' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'شواهد الموضوع من العينة' })).toContainText('فحص نصي بواسطة وكيل');
  await page.screenshot({ path: path.join(directory, 'proposed-topic-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/reader/4');
  await page.getByRole('group', { name: 'وضع القراءة' }).getByRole('button', { name: 'قراءة منسّقة', exact: true }).click();
  await expect(page.getByTestId('pilot-reader')).toBeVisible();
  await expect(page.getByRole('region', { name: 'موضوعات الترجمة وشواهدها' })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: path.join(directory, 'reader-4-mobile.png') });
  await page.getByRole('checkbox', { name: 'تفعيل الإبراز' }).focus();
  await page.keyboard.press('Space');
  await expect(page.getByTestId('pilot-reader')).toHaveClass(/pilot-without-emphasis/);
});
