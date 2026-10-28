// Read-only browser checks; never submits review decisions.
const {chromium,expect}=require('../frontend/node_modules/@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const root=process.env.SIYAR_QA_OUTPUT||'C:/Users/asala/.codex/visualizations/siyar-first-ten-20261028',batch=process.argv[2],ids=process.argv.slice(3).map(Number);
const base=process.env.SIYAR_UI||'http://127.0.0.1:5174',api=process.env.SIYAR_API||'http://127.0.0.1:8001';
const shots=root;fs.mkdirSync(shots,{recursive:true});
async function get(p){const r=await fetch(api+p);if(!r.ok)throw Error(p+' '+r.status);return r.json();}
function expectedBody(p,enabled=true,target){
 const marks=new Map((enabled?p.diacritics.insertions:[]).map(r=>[r.at,r.marks]));
 return p.reading_view.blocks.flatMap(b=>b.runs).map(r=>{
  if(r.type==='virtual_space')return !target||target.start<=r.source_char_start&&r.source_char_start<target.end?r.text:'';
  if(r.type!=='text')return '';
  return Array.from(r.text).map((c,i)=>{
   const at=r.source_char_start+i;
   if(target&&!(target.start<=at&&at<target.end))return '';
   if(p.annotations.footnotes.some(n=>n.start<=at&&at<n.end))return '';
   return c+(marks.get(at+1)||'');
  }).join('');
 }).join('');
}
async function body(page){return page.locator('.pilot-body').evaluate(e=>{const c=e.cloneNode(true);c.querySelectorAll('.pilot-helper-heading,.page-anchor').forEach(n=>n.remove());return c.textContent;});}
(async()=>{const browser=await chromium.launch();try{
 const ctx=await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write'],reducedMotion:'reduce'});
 const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 const report={batch_id:batch,reviewer:'/root',browser:'Chromium desktop1440x1000/mobile390x844',biographies:[],checks:{},screenshots:[]};
 for(const id of ids){
  const p=await get(`/api/biographies/${id}/presentation`),raw=await get(`/api/biographies/${id}`);
  expect(p.diacritics.status).toBe(p.diacritics.coverage.uncertain_words?'partial':'ready');expect(p.presentation_revision).toBeTruthy();
  await page.goto(base+`/reader/${id}`);const reader=page.getByTestId('pilot-reader');await expect(reader).toBeVisible();
  const toggle=page.getByRole('checkbox',{name:'إظهار الحركات المضافة',exact:true});await toggle.check();
  expect(await body(page)).toBe(expectedBody(p));
  await expect(page.getByRole('note',{name:'التشكيل التحريري'})).toContainText('أضافه الوكيل');
  expect(await reader.locator('.pilot-body p br').count()).toBe(0);
  for(const crit of p.criticism){
   const nodes=reader.locator(`.pilot-body [data-criticism-start="${crit.start}"]`);
   expect((await nodes.allTextContents()).join('').replace(/\s/g,'')).toBe(expectedBody(p,true,crit).replace(/\s/g,''));
  }
  await page.getByRole('button',{name:'نسخ النص مع المرجع',exact:true}).click();
  expect((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n')).toContain(p.vocalized_plain_text.replace(/\r\n/g,'\n'));
  await toggle.focus();await page.keyboard.press('Space');expect(await body(page)).toBe(expectedBody(p,false));
  await page.getByRole('button',{name:'نسخ النص مع المرجع',exact:true}).click();
  const unshapedCopy=(await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n');expect(unshapedCopy).toContain(p.plain_text.replace(/\r\n/g,'\n'));expect(unshapedCopy).not.toContain(p.diacritics.notice);
  await page.reload();await expect(toggle).not.toBeChecked();expect(await body(page)).toBe(expectedBody(p,false));await toggle.check();
  await page.getByRole('button',{name:'نسخ الأصل الحرفي',exact:true}).click();expect((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n')).toBe(raw.full_text.replace(/\r\n/g,'\n'));
  await page.getByRole('button',{name:'النص الأصلي',exact:true}).click();expect(await page.getByTestId('original-text').textContent()).toBe(raw.full_text);await page.getByRole('button',{name:'قراءة منسّقة',exact:true}).click();
  const site=p.diacritics.insertions.find(r=>r.base_start>raw.source_char_start+100);
  await page.goto(base+`/reader/${id}?start=${site.base_start}&end=${site.base_start+1}`);await expect(page.locator('.pilot-body mark').first()).toBeVisible();
  const selected=(await page.locator('.pilot-body mark').allTextContents()).join('');expect(selected).toContain(site.marks);
  const originalCluster=Array.from(raw.full_text).slice(site.base_start-raw.source_char_start,site.at-raw.source_char_start).join('');expect(selected).toBe(originalCluster+site.marks);
  expect(await page.locator('.pilot-body mark').first().evaluate(e=>e.childNodes.length===1&&e.firstChild.nodeType===Node.TEXT_NODE||e.textContent.length>0)).toBe(true);
  if(p.annotations.footnotes.length){const note=p.annotations.footnotes[0];await page.goto(base+`/reader/${id}?start=${note.start}&end=${note.end}`);await expect(page.locator('.pilot-footnotes')).toHaveAttribute('open');await expect(page.locator('.pilot-footnotes mark')).toHaveText(note.raw);await page.getByRole('button',{name:'موضع الإشارة في المتن',exact:true}).first().click();await expect(page.locator(`#pilot-note-position-${note.start}`)).toBeFocused();}
  await page.goto(base+`/reader/${id}`);await expect(reader).toBeVisible();await page.locator('.pilot-body p').first().scrollIntoViewIfNeeded();
  const desktop=path.join(shots,`reader-${id}-vocalized-desktop.png`);await page.screenshot({path:desktop,animations:'disabled'});report.screenshots.push(desktop);
  await page.setViewportSize({width:390,height:844});for(let i=0;i<6;i++)await page.getByRole('button',{name:'تكبير الخط',exact:true}).click();
  await page.locator('.pilot-body p').first().scrollIntoViewIfNeeded();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  const mobile=path.join(shots,`reader-${id}-vocalized-mobile35.png`);await page.screenshot({path:mobile,animations:'disabled'});report.screenshots.push(mobile);
  const general=page.getByRole('checkbox',{name:'تفعيل الإبراز',exact:true}),criticism=page.getByRole('checkbox',{name:'إبراز عبارات التحقيق',exact:true});await general.uncheck();await expect(reader).toHaveClass(/pilot-without-emphasis/);await criticism.uncheck();await expect(reader).toHaveClass(/pilot-without-criticism/);await toggle.uncheck();expect(await body(page)).toBe(expectedBody(p,false));
  report.biographies.push({id,coverage:p.diacritics.coverage,insertion_sites:p.diacritics.insertions.length,paragraph_groups:p.paragraph_groups.length,unicode_source_length:Array.from(raw.full_text).length,copy:true,source_roundtrip:true,highlight:true});await page.setViewportSize({width:1440,height:1000});
 }
 
 expect((await get('/api/biographies/11/presentation')).available).toBe(false);expect((await get('/api/biographies/12/presentation')).available).toBe(false);
 expect(errors).toEqual([]);report.console_errors=errors;
 for(const key of ['desktop','mobile','keyboard','copy_raw','copy_formatted','highlight','footnotes','no_horizontal_overflow','no_console_errors','unchanged_outside_batch','diacritics_toggle'])report.checks[key]=true;
 fs.writeFileSync(path.join(root,`qa-${batch}.json`),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
