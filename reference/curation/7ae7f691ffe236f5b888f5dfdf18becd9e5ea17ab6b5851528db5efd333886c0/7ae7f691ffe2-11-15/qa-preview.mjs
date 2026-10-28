// Read-only QA aid for one real preview. It never writes qa.json or approves reviews.
// Run from project root after starting the isolated preview API and Vite servers.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(path.resolve('frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const dir = path.dirname(fileURLToPath(import.meta.url));
const json = name => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
const manifest = json('manifest.json'), proof = json('preview.json');
const url = process.env.SIYAR_PREVIEW_URL || 'http://127.0.0.1:5174';
const out = path.join(dir, 'qa-preview-images', proof.revision);
const normalize = value => value.replace(/\r\n/g, '\n');
const clean = value => value.replace(/\s+/g, '');
function visibleRuns(blocks, notes, insertions = [], target = null) {
  const marks = new Map(insertions.map(item => [item.at, item.marks]));
  return blocks.filter(block => block.runs.some(run => run.type === 'page' || run.type === 'text' && run.text.trim()))
    .flatMap(block => block.runs).map(run => {
      if (run.type === 'virtual_space') return !target || target.start <= run.source_char_start && run.source_char_start < target.end ? run.text : '';
      if (run.type !== 'text') return '';
      return Array.from(run.text).map((char, index) => {
        const at = run.source_char_start + index;
        if (target && (at < target.start || at >= target.end) || notes.some(note => note.start <= at && at < note.end)) return '';
        return char + (marks.get(at + 1) || '');
      }).join('');
    }).join('');
}
async function bodyText(page) {
  return page.locator('.pilot-body').evaluate(element => {
    const clone = element.cloneNode(true);
    clone.querySelectorAll('.pilot-helper-heading,.page-anchor').forEach(node => node.remove());
    return clone.textContent;
  });
}
async function readAPI(page, endpoint) {
  return page.evaluate(async endpoint => {
    const response = await fetch(endpoint);
    if (!response.ok) throw new Error(`${endpoint}: ${response.status}`);
    return response.json();
  }, endpoint);
}
assert.equal(proof.preview_only, true, 'يجب استخدام معاينة معزولة.');
assert.equal(proof.source_sha256, manifest.source_sha256);
assert.ok(fs.existsSync(proof.database), 'قاعدة المعاينة المشار إليها مفقودة.');
assert.deepEqual(manifest.biographies.map(b => b.source_ordinal), [11, 12, 13, 14, 15]);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ baseURL: url, viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'], reducedMotion: 'reduce' });
const page = await context.newPage(), errors = [], results = [];
page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
page.on('console', message => { if (message.type() === 'error') errors.push(`console: ${message.text()}`); });
try {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  const catalog = await readAPI(page, '/api/curation/batches');
  const batch = catalog.items.find(item => item.id === manifest.batch_id);
  assert.ok(batch, 'واجهة المعاينة لا تتصل بقاعدة تضم الدفعة.');
  assert.deepEqual(batch.biographies.map(b => b.id), manifest.biographies.map(b => b.id));
  for (const bio of manifest.biographies) {
    const id = bio.id, source = json(`${bio.directory}/source.json`), gold = json(`${bio.directory}/gold.json`);
    const items = Array.isArray(gold) ? gold : gold.items;
    assert.ok(items?.length, `لا يوجد شاهد مجمد للترجمة ${id}`);
    const details = await readAPI(page, `/api/biographies/${id}`);
    const p = await readAPI(page, `/api/biographies/${id}/presentation`);
    assert.equal(details.full_text, source.full_text, `الأصل في القاعدة لا يطابق التصدير ${id}`);
    assert.equal(p.source_sha256, proof.source_sha256);
    assert.equal(p.presentation_revision, proof.revision, `المعاينة لا تحمل الإصدار الحالي ${id}`);
    assert.ok(['ready', 'partial'].includes(p.diacritics?.status));
    assert.equal(p.reading_view.coverage.complete, true);
    await page.goto(`/reader/${id}`);
    await expect(page.getByTestId('pilot-reader')).toBeVisible();
    const reader = page.getByTestId('pilot-reader');
    const mode = page.getByRole('button', { name: 'قراءة منسّقة', exact: true });
    await mode.click();
    const toggle = reader.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true });
    await toggle.check();
    await expect(reader).toHaveClass(/pilot-vocalized/);
    assert.equal(await bodyText(page), visibleRuns(p.reading_view.blocks, p.annotations.footnotes, p.diacritics.insertions), `العرض المشكول لا يطابق AST للترجمة ${id}`);
    await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
    let copied = normalize(await page.evaluate(() => navigator.clipboard.readText()));
    assert.ok(copied.includes(normalize(p.vocalized_plain_text)), `النسخ المشكول لا يطابق API للترجمة ${id}`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(out, `biography-${id}-desktop.png`), animations: 'disabled' });
    await toggle.uncheck();
    assert.equal(await bodyText(page), visibleRuns(p.reading_view.blocks, p.annotations.footnotes), `العرض دون الإضافات لا يطابق AST للترجمة ${id}`);
    await page.getByRole('button', { name: 'نسخ النص مع المرجع', exact: true }).click();
    copied = normalize(await page.evaluate(() => navigator.clipboard.readText()));
    assert.ok(copied.includes(normalize(p.plain_text)), `النسخ المنسق غير المشكول لا يطابق API للترجمة ${id}`);
    await page.reload(); await expect(toggle).not.toBeChecked();
    await page.getByRole('button', { name: 'نسخ الأصل الحرفي', exact: true }).click();
    assert.equal(normalize(await page.evaluate(() => navigator.clipboard.readText())), normalize(source.full_text));
    await page.getByRole('button', { name: 'النص الأصلي', exact: true }).click();
    assert.equal(await page.getByTestId('original-text').textContent(), source.full_text);
    await mode.click();
    const witness = items[0];
    await page.goto(`/reader/${id}?start=${witness.start}&end=${witness.end}`);
    await expect(page.locator('.pilot-body mark').first()).toBeVisible();
    const actual = (await page.locator('.pilot-body mark').allTextContents()).join('');
    const expected = visibleRuns(p.reading_view.blocks, p.annotations.footnotes, [], witness);
    assert.ok(clean(expected), `الشاهد غير ظاهر في المتن ${id}`);
    assert.equal(clean(actual), clean(expected), `الشاهد المظلل لا يطابق AST ${id}`);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: 'تكبير الخط', exact: true }).click();
    const mobileToggle = page.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true });
    await mobileToggle.focus(); await page.keyboard.press('Space'); await expect(mobileToggle).toBeChecked();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `تجاوز أفقي في الترجمة ${id}`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(out, `biography-${id}-mobile.png`), animations: 'disabled' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    results.push({ id, witness, desktop: path.join(out, `biography-${id}-desktop.png`), mobile: path.join(out, `biography-${id}-mobile.png`) });
  }
  assert.deepEqual(errors, [], 'أخطاء JavaScript أو console في المعاينة');
  const result = { revision: proof.revision, gold_frozen_sha256: proof.gold_frozen_sha256, source_sha256: proof.source_sha256, url, results, errors, limitation: 'فحص آلي مساعد فقط؛ لا يغني عن مراجعة بصرية مستقلة أو فحص جميع بوابات QA.' };
  fs.writeFileSync(path.join(dir, 'qa-automated-result.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
}
