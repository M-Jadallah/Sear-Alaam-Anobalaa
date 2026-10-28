// Read-only visual evidence and contrast measurement on the published reader.
const {chromium,expect}=require('../frontend/node_modules/@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const output='C:/Users/asala/.codex/visualizations/siyar-first-ten-20261028';
function luminance(rgb){return rgb.slice(0,3).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}).reduce((s,x,i)=>s+x*[.2126,.7152,.0722][i],0)}
function ratio(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)}
(async()=>{const browser=await chromium.launch();try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),records=[];
 for(const id of [5,6]){
  await page.goto(`http://127.0.0.1:5173/reader/${id}`);
  await expect(page.getByTestId('pilot-reader')).toBeVisible();
  await page.getByRole('checkbox',{name:'إظهار الحركات المضافة',exact:true}).check();
  const target=page.locator('.pilot-body .pilot-criticism').last();await target.scrollIntoViewIfNeeded();
  const style=await target.evaluate(e=>{const s=getComputedStyle(e);return {color:s.color,background:s.backgroundColor,fontWeight:s.fontWeight,text:e.textContent}});
  const values=s=>s.match(/[\d.]+/g).map(Number);
  const contrast=ratio(values(style.color),values(style.background));expect(contrast).toBeGreaterThanOrEqual(4.5);
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
   await page.setViewportSize(viewport);await target.scrollIntoViewIfNeeded();
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
   const file=path.join(output,`reader-${id}-criticism-${name}.png`);await page.screenshot({path:file,animations:'disabled'});
   records.push({biography_id:id,viewport:name,screenshot:file,style,contrast});
  }
 }
 fs.writeFileSync(path.join(output,'criticism-contrast.json'),JSON.stringify(records,null,2)+'\n');console.log(JSON.stringify(records));
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
