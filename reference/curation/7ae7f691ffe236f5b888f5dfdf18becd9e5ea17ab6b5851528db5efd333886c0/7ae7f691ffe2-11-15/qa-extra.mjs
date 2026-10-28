import {createRequire} from 'node:module'; import fs from 'node:fs'; import path from 'node:path'; import assert from 'node:assert/strict';
const {chromium,expect}=createRequire(path.resolve('frontend/package.json'))('@playwright/test');
const dir=path.resolve('reference/curation/7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0/7ae7f691ffe2-11-15');
const proof=JSON.parse(fs.readFileSync(path.join(dir,'preview.json'))), base='http://127.0.0.1:5174', out=path.join(dir,'qa-preview-images',proof.revision);
const browser=await chromium.launch(); const context=await browser.newContext({baseURL:base,viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']}); const page=await context.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message)); page.on('console',e=>{if(e.type()==='error') errors.push(e.text())});
const api=async u=>page.evaluate(async u=>{let r=await fetch(u);if(!r.ok)throw Error(`${u}: ${r.status}`);return r.json()},u);
try{
 await page.goto('/');
 const reports=[];
 for(const id of [11,12,13,14,15]){
  const p=await api(`/api/biographies/${id}/presentation`); assert.equal(p.presentation_revision,proof.revision); await page.goto(`/reader/${id}`); const reader=page.getByTestId('pilot-reader'); await expect(reader).toBeVisible(); await page.getByRole('button',{name:'قراءة منسّقة',exact:true}).click();
  const name=reader.locator('.pilot-body .pilot-person-name').first(); await expect(name).toBeVisible(); const originalColor=await name.evaluate(el=>getComputedStyle(el).color); const general=reader.getByRole('checkbox',{name:'تفعيل الإبراز',exact:true}); const critical=reader.getByRole('checkbox',{name:'إبراز عبارات التحقيق',exact:true}); await general.check(); await critical.check();
  const critique=reader.locator('.pilot-body .pilot-criticism').first(); if(p.criticism.length){await expect(critique).toBeVisible(); await expect(critique).toHaveCSS('background-color','rgb(245, 234, 208)');}
  await general.uncheck(); assert.notEqual(await name.evaluate(el=>getComputedStyle(el).color),originalColor); if(p.criticism.length)await expect(critique).toHaveCSS('background-color','rgb(245, 234, 208)');
  await critical.focus(); await page.keyboard.press('Space'); await expect(critical).not.toBeChecked(); if(p.criticism.length)await expect(critique).toHaveCSS('background-color','rgba(0, 0, 0, 0)'); await general.check(); await critical.check();
  const foot=reader.locator('details.pilot-footnotes'); await foot.locator('summary').click(); await expect(foot).toHaveAttribute('open'); await expect(foot).toContainText(p.footnote_notice); assert.equal(await foot.locator('.pilot-footnote-raw').count(),p.annotations.footnotes.length);
  const anchor=reader.locator('.page-anchor button,button.page-anchor').first(); if(await anchor.count()){await anchor.click(); await expect(page.getByRole('note').filter({hasText:'علامة OpenITI خام'}).first()).toBeVisible();}
  await page.evaluate(()=>window.scrollTo(0,0)); await page.screenshot({path:path.join(out,`biography-${id}-full-desktop.png`),fullPage:true});
  reports.push({id,names:p.annotations.names.length,criticism:p.criticism.length,moved_footnotes:p.annotations.footnotes.length,paragraphs:p.paragraph_groups.length});
 }
 await page.goto('/reader/14'); await page.getByRole('checkbox',{name:'إظهار الحركات المضافة',exact:true}).uncheck(); const b=page.getByTestId('pilot-reader').locator('p[data-source-start="1620785"]'); await expect(b).toContainText('لأنه كان'); await expect(b).toContainText('أكثرهم'); assert.equal(await b.locator('p,br').count(),0);
 await page.goto('/pilot/reviews?batch_id=7ae7f691ffe2-11-15&biography_id=14'); await expect(page.getByRole('combobox',{name:'دفعة المراجعة'})).toHaveValue('7ae7f691ffe2-11-15'); const card=page.locator('.pilot-review-card').first(); await expect(card).toContainText('بانتظار اعتمادك'); await card.locator('details.context-details').last().locator('summary').click(); await expect(card).toContainText('إنما هي رخصة لسالم خاصة'); await page.screenshot({path:path.join(out,'review-14.png')});
 const item=(await api('/api/pilot/reviews?batch_id=7ae7f691ffe2-11-15&biography_id=14&status=pending&page_size=10')).items[0]; await page.goto(`/topics/${item.topic_id}`); await expect(page.locator('.pilot-topic-evidence').first()).toBeVisible(); await expect(page.locator('.pilot-topic-evidence').first()).toContainText('بانتظار اعتمادك'); await page.screenshot({path:path.join(out,'topic-14.png')});
 await page.goto('/reader/10'); await expect(page.getByTestId('pilot-reader')).toBeVisible(); await page.goto('/reader/16'); await expect(page.getByTestId('pilot-reader')).toHaveCount(0); await expect(page.getByTestId('formatted-text')).toBeVisible();
 assert.deepEqual(errors,[]); console.log(JSON.stringify({revision:proof.revision,reports,paragraphContinuation:'14:1620785',review:'pending and context observed; no decisions written',outside:[10,16],errors},null,2));
} finally{await browser.close()}
