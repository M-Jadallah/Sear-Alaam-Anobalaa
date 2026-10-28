from pathlib import Path
root=Path(__file__).resolve().parents[3]
p=Path('C:/Users/asala/AppData/Local/Temp/siyar-diacritics-trial-h54sy2n1/qa_diacritics.cjs')
s=p.read_text(encoding='utf-8')
s=s.replace("const {chromium,expect}=require('C:/My-Project/Sear-Alaam-Anobalaa/frontend/node_modules/@playwright/test');", "// Read-only browser checks; never submits review decisions.\nconst {chromium,expect}=require('../frontend/node_modules/@playwright/test');")
s=s.replace("const root=__dirname,batch=process.argv[2],ids=process.argv.slice(3).map(Number);", "const root=process.env.SIYAR_QA_OUTPUT||'C:/Users/asala/.codex/visualizations/siyar-first-ten-20261028',batch=process.argv[2],ids=process.argv.slice(3).map(Number);")
s=s.replace("const base='http://127.0.0.1:5174',api='http://127.0.0.1:8001';", "const base=process.env.SIYAR_UI||'http://127.0.0.1:5174',api=process.env.SIYAR_API||'http://127.0.0.1:8001';")
s=s.replace("const shots='C:/Users/asala/.codex/visualizations/siyar-diacritics-20261028';", "const shots=root;")
s=s.replace("expect(p.batch_id).toBe(batch);", "expect(p.presentation_revision).toBeTruthy();")
s=s.replace("const prior=await get('/api/biographies/1/presentation');expect(prior.diacritics).toBeUndefined();expect(prior.presentation_revision).toBe('947c2450a064dc3f3b7ce0d2999af9b131f354ef123c614350a1ed96116f0a75');", "")
s=s.replace("expect((await get('/api/biographies/12/presentation')).available).toBe(false);", "expect((await get('/api/biographies/11/presentation')).available).toBe(false);expect((await get('/api/biographies/12/presentation')).available).toBe(false);")
(root/'scripts/qa_first_ten_reading.cjs').write_text(s,encoding='utf-8')
