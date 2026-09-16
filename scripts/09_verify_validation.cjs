/* Read-only UI checks for evaluated, low-quality and unavailable report states. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const output=process.env.ROADINSIGHT_QA_OUTPUT||'data/runtime/validation_ui';fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.ROADINSIGHT_BROWSER?{executablePath:process.env.ROADINSIGHT_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1680,height:945}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.ROADINSIGHT_QA_URL||'http://127.0.0.1:8503',{waitUntil:'networkidle'});
 const frame=page.frameLocator('iframe[title="roadinsight_app.workspace"]');await frame.locator('.nav-item').first().waitFor({timeout:30000});
 await frame.locator('.nav-item[data-page="validation"]').click();await page.waitForTimeout(500);
 const metrics=await frame.locator('body').evaluate(()=>validationReport.metrics.overall);
 assert.equal(metrics.tp+metrics.fn,metrics.injected);assert.equal(metrics.tp+metrics.fp,metrics.detected);
 assert.equal(metrics.precision,metrics.tp/metrics.detected);
 assert.match(await frame.locator('.metrics').innerText(),new RegExp(metrics.recall.toFixed(3)));
 assert.match(await frame.locator('.outcome-grid').innerText(),/未定义/);
 await frame.locator('[data-action="evaluation-details"]').first().click();
 const report=JSON.parse(await frame.locator('.dialog pre').innerText());assert.deepEqual(report.metrics.overall,metrics);
 assert.equal(report.reproducibility.status,'passed');await page.keyboard.press('Escape');
 for(const [width,height] of [[1680,945],[1440,900]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(300);
  const size=await frame.locator('.content').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth}));
  assert.ok(size.scrollWidth<=size.width);
  await page.screenshot({path:path.join(output,`validation-${width}.png`)});
 }
 await frame.locator('body').evaluate(()=>{window.__qaReport=structuredClone(validationReport);validationReport.data_quality.matching_success_rate=.4;validationReport.data_quality.matching_status='warning';render();});
 assert.match(await frame.locator('.error-note').innerText(),/匹配成功率低于/);
 await frame.locator('body').evaluate(()=>{validationReport=null;validationNotice='评估报告不可用于当前快照：版本不一致';render();});
 assert.match(await frame.locator('.error-note').innerText(),/版本不一致/);
 assert.match(await frame.locator('.metrics').innerText(),/未计算/);
 await frame.locator('body').evaluate(()=>{validationReport=window.__qaReport;validationNotice=null;render();});
 for(const name of ['dashboard','map','detail','validation','data']){await frame.locator(`.nav-item[data-page="${name}"]`).click();await page.waitForTimeout(150);}
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(output,'validation-browser-report.json'),JSON.stringify({status:'passed',metrics,errors,checks:['computed metrics','undefined negative universe','auditable details','repeat run provenance','quality warning','stale report fallback','two viewport sizes','all navigation']},null,2));
 await browser.close();console.log('Validation UI checks passed.');
})().catch(error=>{console.error(error);process.exit(1);});
