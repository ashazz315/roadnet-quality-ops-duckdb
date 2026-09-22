/* Full-server acceptance, isolated QA volume only: this intentionally writes test data. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const base=process.env.ROADINSIGHT_QA_URL||'http://127.0.0.1:8511/';
 if(!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(base))throw new Error('Write acceptance requires an isolated localhost service.');
 const token=process.env.ROADINSIGHT_QA_EDITOR_TOKEN;if(!token)throw new Error('Set a QA-only editor token.');
 const output=process.env.ROADINSIGHT_QA_OUTPUT||'data/runtime/server_ui';fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.ROADINSIGHT_BROWSER?{executablePath:process.env.ROADINSIGHT_BROWSER}:{})});
 const context=await browser.newContext({viewport:{width:1680,height:945}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*tile.openstreetmap.org/**',r=>r.abort());
 await page.goto(base,{waitUntil:'domcontentloaded'});
 const frame=page.frameLocator('iframe[title="roadinsight_app.workspace"]');
 await frame.locator('.nav-item').first().waitFor({timeout:45000});
 async function until(check,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){if(await check())return;await page.waitForTimeout(200);}console.error(await frame.locator('body').evaluate(()=>({page:state.page,selectedJob,access:backendAccess,jobs:backendJobs.map(j=>({id:j.id,status:j.status})),response:{action:response?.action,ok:response?.ok,message:response?.message}})));throw new Error('Timed out waiting for server result');}
 async function action(name,extra={}){
  const previous=await frame.locator('body').evaluate(()=>response?.event_id);
  await frame.locator('body').evaluate((_,{name,extra})=>event(name,extra),{name,extra});
  await until(()=>frame.locator('body').evaluate((_,prev)=>response?.event_id&&response.event_id!==prev,previous));
  return frame.locator('body').evaluate(()=>response);
 }
 assert.equal(await frame.locator('body').evaluate(()=>backendAccess.can_edit),false);
 await frame.locator('.nav-item[data-page="data"]').click();
 assert.equal(await frame.locator('input[type="file"]').count(),0);
 const rejected=await action('bundle_upload',{base64:'e30='});
 assert.equal(rejected.ok,false);assert.match(rejected.message,/解锁/);
 const badLogin=await action('login',{token:'wrong'});assert.equal(badLogin.ok,false);
 await page.waitForTimeout(3100);
 await frame.locator('[data-action="editor-login"]').first().click();
 await frame.locator('#editor-token').fill(token);await frame.locator('[data-action="submit-login"]').click();
 await until(()=>frame.locator('body').evaluate(()=>backendAccess.can_edit));
 await frame.locator('.nav-item[data-page="data"]').click();
 await frame.locator('#bundle-file').setInputFiles(path.resolve('data/demo/upload-example.zip'));
 await until(()=>frame.locator('body').evaluate(()=>!!bundlePreview&&!backendBusy));
 assert.equal(await frame.locator('body').evaluate(()=>bundlePreview.rows.trajectory_point),60004);
 await frame.locator('[data-action="run-v2"]').click();
 await until(()=>frame.locator('body').evaluate(()=>response?.action==='run_analysis'&&!backendBusy),300000);
 assert.equal(await frame.locator('body').evaluate(()=>response.ok),true,'Uploaded analysis failed; inspect the private worker log.');
 const job=await frame.locator('body').evaluate(()=>selectedJob);
 const summary=await frame.locator('body').evaluate(()=>({label:data.dataset_label,issues:data.issues.length,routes:data.routes.length,validation:validationReport}));
 assert.equal(summary.label,'Shanghai-Xuhui-upload-example');assert.equal(summary.issues,18);assert.equal(summary.routes,28);assert.equal(summary.validation,null);
 await frame.locator('.nav-item[data-page="validation"]').click();
 assert.match(await frame.locator('.metrics').innerText(),/未计算/);
 await frame.locator('.nav-item[data-page="detail"]').click();
 await frame.locator('[data-action="review"]').click();
 await frame.locator('#reviewer').fill('QA editor');await frame.locator('#review-note').fill('QA only: requires field verification');
 await frame.locator('[data-action="submit-review"]').click();
 await until(()=>frame.locator('body').evaluate(()=>reviews.some(row=>row.note==='QA only: requires field verification')));
 await frame.locator('.nav-item[data-page="data"]').click();
 await frame.locator('#upload-file').setInputFiles({name:'qa.csv',mimeType:'text/csv',buffer:Buffer.from('record_id,report_time,longitude,latitude,issue_type,source,description,region\nQA1,2026-09-22 10:00,121.43,31.18,missing_road,qa,QA only,Xuhui\n')});
 await until(()=>frame.locator('body').evaluate(()=>!!uploadPreview&&!pending));
 await frame.locator('[data-action="save-upload"]').click();
 await until(()=>frame.locator('body').evaluate(()=>!!savedUpload));
 assert.ok(await frame.locator('body').evaluate(()=>savedUpload.report_base64.length>100));
 await frame.locator('[data-action="select-demo"]').click();
 await until(()=>frame.locator('body').evaluate(()=>!selectedJob));
 await frame.locator(`button[data-action="select-job"][data-id="${job}"]`).click();
 await until(()=>frame.locator('body').evaluate((_,id)=>selectedJob===id,job));
 for(const [width,height] of [[1680,945],[1440,900]]){
  await page.setViewportSize({width,height});await frame.locator('.nav-item[data-page="data"]').click();
  const size=await frame.locator('.content').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth}));
  assert.ok(size.scrollWidth<=size.width);
  await page.screenshot({path:path.join(output,`server-data-${width}.png`)});
 }
 await frame.locator('[data-action="editor-logout"]').click();
 await until(()=>frame.locator('body').evaluate(()=>!backendAccess.can_edit));
 assert.equal(await frame.locator('body').evaluate(()=>backendJobs.length),0);
 assert.equal(await frame.locator('body').evaluate(()=>reviews.length),0);
 assert.equal(await frame.locator('body').evaluate(()=>data.dataset_label),'Shanghai-Xuhui-v1');
 assert.equal((await action('select_job',{job_id:job})).ok,false);
 const loginAgain=await action('login',{token});assert.equal(loginAgain.ok,true);
 await until(()=>frame.locator('body').evaluate((_,id)=>backendJobs.some(j=>j.id===id&&j.status==='completed'),job));
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(output,'server-browser-report.json'),JSON.stringify({status:'passed',job,errors,checks:['visitor writes rejected by backend','editor unlock','real uploaded analysis','no inherited benchmark accuracy','durable review','legacy upload and Excel report','data switching','logout isolation','history after login','two desktop layouts']},null,2));
 await browser.close();console.log('Full server acceptance passed.');
})().catch(error=>{console.error(error);process.exit(1);});
