/* Run against an isolated QA server; review/upload checks write test records. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const out=process.env.ROADINSIGHT_QA_OUTPUT||'data/runtime/ui_qa';
const url=process.env.ROADINSIGHT_QA_URL||'http://127.0.0.1:8502';
async function main(){
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.ROADINSIGHT_BROWSER?{executablePath:process.env.ROADINSIGHT_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1680,height:945},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url,{waitUntil:'networkidle'});
 const frame=page.frameLocator('iframe[title="roadinsight_app.workspace"]');
 await frame.locator('.nav-item').first().waitFor({timeout:30000});
 const evaluate=fn=>frame.locator('body').evaluate(fn);
 const go=async name=>{await frame.locator(`.nav-item[data-page="${name}"]`).click();await page.waitForTimeout(400);};
 const action=async name=>frame.locator(`[data-action="${name}"]`).first().click();
 await go('map');
 await action('expand-map');assert.equal(await frame.locator('.map-expanded').count(),1);await page.keyboard.press('Escape');assert.equal(await frame.locator('.map-expanded').count(),0);
 await frame.locator('[data-type="CONNECTIVITY_BREAK"]').uncheck();
 assert.equal(await evaluate(()=>visible().length),13);
 await frame.locator('#severity').selectOption('LOW');
 assert.equal(await evaluate(()=>visible().length),0);
 assert.match(await frame.locator('.issue-panel,.diagnosis-grid>.stack:last-child').first().innerText(),/未选中问题/);
 await action('reset-filters');
 assert.equal(await evaluate(()=>visible().length),18);
 await frame.locator('#min-confidence').evaluate(el=>{el.value='99';el.dispatchEvent(new Event('change',{bubbles:true}));});
 assert.equal(await evaluate(()=>visible().length),await evaluate(()=>data.issues.filter(x=>x.confidence>=.99).length));
 await action('reset-filters');
 await frame.locator('#date-start').fill('2027-01-01');
 await frame.locator('#date-start').dispatchEvent('change');
 assert.equal(await evaluate(()=>visible().length),0);
 await action('reset-filters');
 await frame.locator('#base-map').selectOption('roads');
 const layerCount=await evaluate(()=>Object.keys(RIMaps.get('diagnosis-map')._layers).length);
 await frame.locator('[data-layer="roads"]').uncheck();
 assert.ok(await evaluate(()=>Object.keys(RIMaps.get('diagnosis-map')._layers).length)<layerCount-1000);
 await frame.locator('[data-layer="roads"]').check();
 const target=await evaluate(()=>{const issue=data.issues.find(x=>x.issue_type==='CONNECTIVITY_BREAK');const m=RIMaps.get('diagnosis-map');m.setView([issue.latitude,issue.longitude],17,{animate:false});const p=m.latLngToContainerPoint([issue.latitude,issue.longitude]);return {id:issue.issue_id,x:p.x,y:p.y};});
 await page.waitForTimeout(400);
 await frame.locator('#diagnosis-map').click({position:{x:target.x,y:target.y}});
 assert.equal(await evaluate(()=>state.issueId),target.id);
 const turnId=await evaluate(()=>data.issues.find(x=>x.issue_type==='TURN_RESTRICTION_CONFLICT').issue_id);
 await frame.locator(`[data-action="open-issue"][data-id="${turnId}"]`).click();
 assert.match(await frame.locator('.delta-card').innerText(),/\+703\.3 m/);
 const options=await frame.locator('#replay-select option').count();
 assert.ok(options>=2);
 await frame.locator('#replay-select').selectOption({index:1});
 await action('review');
 await frame.locator('#reviewer').fill('STEP8 QA');
 await frame.locator('#review-note').fill('自动化交互检查，仍需人工现场核验。');
 await frame.locator('#conclusion').selectOption('needs_review');
 await action('submit-review');
 await frame.locator('.review-log').waitFor({timeout:15000});
 assert.equal(await evaluate(()=>current().status),'needs_review');
 await go('data');
 const csv='record_id,report_time,longitude,latitude,issue_type,source,description,region\nQA1,2026-09-16 10:00,121.43,31.18,missing_road,qa,测试观察,徐汇\nQA2,2026-09-16 10:00,999,31.18,missing_road,qa,异常坐标,徐汇\n';
 await frame.locator('#upload-file').setInputFiles({name:'step8-qa.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
 await frame.locator('.upload-result').waitFor({timeout:15000});
 assert.match(await frame.locator('.upload-result').innerText(),/有效 1 · 拒绝 1/);
 await action('save-upload');
 await frame.locator('[data-action="download-report"]').waitFor({timeout:20000});
 const downloaded=page.waitForEvent('download');await action('download-report');
 const download=await downloaded;await download.saveAs(path.join(out,'qa-upload-report.xlsx'));
 assert.equal(fs.readFileSync(path.join(out,'qa-upload-report.xlsx')).subarray(0,2).toString(),'PK');
 await frame.locator('#upload-file').setInputFiles({name:'missing.csv',mimeType:'text/csv',buffer:Buffer.from('record_id\nA\n')});
 await frame.locator('.error-note').waitFor({timeout:15000});
 assert.equal(await frame.locator('[data-action="save-upload"]').isDisabled(),true);
 // Deep links and unavailable replay states use the same immutable source IDs.
 const noPath=await evaluate(()=>data.routes.find(x=>x.reachability_change==='lost').issue_id);
 await page.goto(url+'?page=detail&issue='+noPath,{waitUntil:'networkidle'});
 await frame.locator('.detail-columns').waitFor({timeout:30000});
 assert.equal(await evaluate(()=>state.issueId),noPath);
 const lostReplay=await evaluate(()=>data.routes.find(x=>x.reachability_change==='lost').replay_id);
 await frame.locator('#replay-select').selectOption(lostReplay);
 assert.match(await frame.locator('.replay-grid').innerText(),/无可用路线/);
 await page.reload({waitUntil:'networkidle'});
 await frame.locator('.detail-columns').waitFor({timeout:30000});
 assert.equal(await evaluate(()=>state.issueId),noPath);
 // Screenshot a fresh browser session; QA review records remain in the isolated server.
 const shots=await browser.newPage({viewport:{width:1680,height:945},deviceScaleFactor:1});
 shots.on('pageerror',e=>errors.push(e.message));
 await shots.goto(url,{waitUntil:'networkidle'});
 const sf=shots.frameLocator('iframe[title="roadinsight_app.workspace"]');
 await sf.locator('.nav-item').first().waitFor({timeout:30000});
 const sizes=[];
 for(const [width,height] of [[1680,945],[1440,900]]){
  await shots.setViewportSize({width,height});
  for(const name of ['dashboard','map','detail','validation','data']){
   await sf.locator(`.nav-item[data-page="${name}"]`).click();await shots.waitForTimeout(700);
   const size=await sf.locator('.content').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth,height:el.clientHeight,scrollHeight:el.scrollHeight}));
   assert.ok(size.scrollWidth<=size.width+1,`${name} horizontal overflow at ${width}`);
   assert.equal(await sf.locator('img').evaluateAll(xs=>xs.filter(x=>!x.complete||x.naturalWidth===0).length),0,'broken map images');
   sizes.push({page:name,viewport:width,...size});
   await shots.screenshot({path:path.join(out,`${name}-${width}.png`)});
   if(name==='detail'){await sf.locator('.content').evaluate(el=>el.scrollTop=el.scrollHeight);await shots.screenshot({path:path.join(out,`detail-replay-${width}.png`)});}
  }
 }
 // Block the optional tile host; local road geometry must remain operational.
 await shots.route('https://tile.openstreetmap.org/**',route=>route.abort());
 await sf.locator('.nav-item[data-page="map"]').click();
 await sf.locator('#base-map').selectOption('roads');await sf.locator('#base-map').selectOption('osm');
 await sf.locator('.offline-badge').waitFor({timeout:15000});
 assert.ok(await sf.locator('body').evaluate(()=>Object.keys(RIMaps.get('diagnosis-map')._layers).length)>1000);
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify({status:'passed',errors,sizes,checks:['filters','empty state','layers','map click','detail identity','replay selection','manual review','CSV validation','DuckDB save','Excel download','missing columns','deep link','no path','reload','two viewport screenshots','offline geometry']},null,2));
 console.log(JSON.stringify({status:'passed',sizes},null,2));
 await browser.close();
}
main().catch(error=>{console.error(error);process.exit(1);});
