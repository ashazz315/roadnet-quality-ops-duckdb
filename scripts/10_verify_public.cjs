/* Read-only browser acceptance, usable against localhost and GitHub Pages. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const output=process.env.ROADINSIGHT_QA_OUTPUT||'data/runtime/public_ui';fs.mkdirSync(output,{recursive:true});
 const base=process.env.ROADINSIGHT_QA_URL||'http://127.0.0.1:8510/';
 const browser=await chromium.launch({headless:true,...(process.env.ROADINSIGHT_BROWSER?{executablePath:process.env.ROADINSIGHT_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1680,height:945}}),errors=[],failed=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',r=>{if(r.status()>=400&&r.url().startsWith(base))failed.push([r.status(),r.url()]);});
 // Local road geometry must remain usable if optional third-party tiles fail.
 await page.route('**/*tile.openstreetmap.org/**',route=>route.abort());
 await page.goto(base,{waitUntil:'domcontentloaded'});
 await page.locator('.nav-item').first().waitFor({timeout:30000});
 assert.match(await page.locator('.top-meta').innerText(),/公开只读/);
 assert.match(await page.locator('.metrics').innerText(),/18/);
 for(const [width,height] of [[1680,945],[1440,900]]){
  await page.setViewportSize({width,height});
  for(const name of ['dashboard','map','detail','validation','data']){
   await page.locator(`.nav-item[data-page="${name}"]`).click();await page.waitForTimeout(180);
   const size=await page.locator('.content').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth}));
   assert.ok(size.scrollWidth<=size.width,`${name} overflows at ${width}`);
   await page.screenshot({path:path.join(output,`${name}-${width}.png`)});
  }
 }
 assert.equal(await page.locator('input[type="file"]').count(),0);
 assert.match(await page.locator('.content').innerText(),/Streamlit 工作台/);
 assert.equal(await page.locator('.top-meta a').getAttribute('href'),'https://roadinsight-v2-ashazz315.streamlit.app/');
 await page.locator('[data-action="download-snapshot"]').click();
 await page.locator('.nav-item[data-page="map"]').click();
 for(const input of await page.locator('input[data-type]').all())await input.uncheck();
 assert.equal(await page.locator('tr[data-issue]').count(),0);
 assert.match(await page.locator('.issue-panel').count()?await page.locator('.issue-panel').innerText():await page.locator('.content').innerText(),/未选中问题|当前筛选无结果/);
 await page.locator('[data-action="reset-filters"]').click();
 assert.equal(await page.locator('tr[data-issue]').count(),18);
 assert.ok(await page.locator('.leaflet-overlay-pane canvas').count()>0);
 const issue='issue-05df7e154203f10da880';
 await page.goto(base+'?page=detail&issue='+issue,{waitUntil:'domcontentloaded'});
 await page.locator('#route-before').waitFor();
 assert.equal(await page.evaluate(()=>current().issue_id),issue);
 const replay=await page.evaluate(()=>replaySelection(current()));
 assert.ok(replay.after.distance_m>replay.before.distance_m);
 await page.locator('[data-action="review"]').click();
 assert.match(await page.locator('.dialog').innerText(),/公开只读/);
 assert.equal(await page.locator('#reviewer').count(),0);await page.keyboard.press('Escape');
 await page.locator('.nav-item[data-page="validation"]').click();
 assert.match(await page.locator('.metrics').innerText(),/0\.750/);
 await page.goBack();await page.locator('#route-before').waitFor();
 await page.goForward();await page.locator('.benchmark-counts').waitFor();
 await page.reload();await page.locator('.benchmark-counts').waitFor();
 await page.locator('[data-action="evaluation-details"]').first().click();
 const report=JSON.parse(await page.locator('.dialog pre').innerText());
 assert.equal(report.metrics.overall.tp,18);assert.equal(report.metrics.overall.fn,6);
 assert.equal(report.metrics.overall.tn,null);assert.equal(report.reproducibility.status,'passed');
 await page.keyboard.press('Escape');
 // Tampered downloaded content must show an explicit error, never stale metrics.
 await page.route('**/snapshot.json',route=>route.fulfill({status:200,contentType:'application/json',body:'{}'}));
 await page.reload();await page.getByText(/数据版本校验失败/).waitFor();
 assert.equal(await page.locator('.metric').count(),0);
 assert.deepEqual(errors,[]);assert.deepEqual(failed,[]);
 fs.writeFileSync(path.join(output,'public-browser-report.json'),JSON.stringify({status:'passed',url:base,errors,failed,checks:['five pages at two desktop sizes','tile-independent geometry','empty filters','deep link and reload','history navigation','hypothetical replay','read-only actions','auditable benchmark','checksum rejection']},null,2));
 await browser.close();console.log('Public demo acceptance passed.');
})().catch(error=>{console.error(error);process.exit(1);});
