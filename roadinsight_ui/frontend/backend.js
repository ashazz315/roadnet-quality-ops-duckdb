/* Full-service controls; authorization is enforced again by the Python server. */
function backendNotice(){
 if(window.RIPublic||!backendAccess.temporary_storage)return '';
 return `<div class="alert-note" role="status">免费在线体验：上传文件、分析任务和核验记录仅临时保存，服务重启后可能丢失。请及时导出结果并保留原始数据。${backendAccess.can_edit?'<button data-action="download-snapshot">导出当前结果</button> <button data-action="download-reviews">导出核验记录</button>':''}</div>`;
}
function backendHeader(){
 if(window.RIPublic)return '';
 if(backendAccess.mode!=='server')return '<span>本地完整工作区</span>';
 if(backendAccess.login_enabled===false)return '<span>免费体验 · 公开浏览</span>';
 return `<button class="tiny-button" data-action="${backendAccess.can_edit?'editor-logout':'editor-login'}">${icon('shield')}${backendAccess.can_edit?'退出编辑':'编辑登录'}</button>`;
}
function backendPanel(){
 if(window.RIPublic)return '';
 if(backendAccess.login_enabled===false&&backendAccess.mode==='server')return card('公开体验','shield','<p class="alert-note">当前开放演示浏览。管理员配置编辑访问码后，可启用上传分析与人工核验。</p>');
 if(!backendAccess.can_edit)return card('公开浏览与编辑工作区','shield',`<p class="alert-note">任何人都可浏览演示。上传数据、运行分析和保存核验需要编辑访问码；上传数据不会自动公开。</p><button class="primary" data-action="editor-login">解锁编辑工作区</button>`);
 const status={running:'分析中',completed:'完成',failed:'失败',interrupted:'已中断'};
 return `<div class="backend-grid">${card('V2 数据包 → 检测 → 假设回放','route',`<p class="note">上海小样本路网 · ZIP ≤ 15 MB · 五类输入和数据集说明。旧版问题记录 CSV 请使用下方入口。</p><div class="upload-zone"><input type="file" id="bundle-file" accept=".zip" aria-label="上传 V2 数据包" ${backendBusy?'disabled':''}><p class="note">road_segment、road_node、turn_restriction、trajectory_point、user_feedback</p></div><button data-action="download-example" ${backendBusy?'disabled':''}>${icon('download')} 下载完整示例数据包</button>${bundlePreview?`<div class="alert-note success-note"><strong>${esc(bundlePreview.label)}</strong><br>${Object.entries(bundlePreview.rows).map(([name,n])=>`${esc(name)}: ${count(n)}`).join(' · ')}</div><button class="primary wide" data-action="run-v2" ${backendBusy?'disabled':''}>${icon('play')} 运行检测与假设回放</button>`:''}${backendBusy?'<p class="alert-note">正在处理，请稍候。较大样本可能需要几分钟；失败不会替换当前结果。</p>':''}<p class="note">每次结果独立保存。上传数据没有标注答案时，准确率保持未计算。</p>`)}${card('分析任务与数据切换','database',`<div class="between"><button data-action="select-demo" ${backendBusy?'disabled':''}>返回公开演示数据</button><button data-action="refresh-jobs" ${backendBusy?'disabled':''}>刷新任务</button></div><p class="note">当前：${selectedJob?'上传分析结果':'公开演示数据'} · 最近 50 个任务</p>${backendJobs.length?`<table class="data-table"><thead><tr><th>数据集</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${backendJobs.map(job=>`<tr><td title="${esc(job.message||'')}">${esc(job.label)}</td><td>${status[job.status]||esc(job.status)}</td><td>${fmtDate(job.created_at)}</td><td><button data-action="select-job" data-id="${esc(job.id)}" ${job.status!=='completed'||backendBusy?'disabled':''}>${job.id===selectedJob?'当前':'查看'}</button></td></tr>`).join('')}</tbody></table>`:empty('暂无上传分析','下载示例数据包，上传校验后运行一次完整分析。','route')}`)}</div>`;
}
function guestDataManagement(){return hero('Data Management / 数据管理','浏览演示数据；编辑工作区的数据仅向持有访问码的编辑者开放。')+backendPanel()+card('当前公开演示','database',`${kv('数据集',esc(data.dataset_label))}${kv('快照',esc(data.snapshot_id))}<button data-action="download-snapshot">导出演示数据与来源</button> ${iconButton('info','完整来源','provenance')}`);}
function handleBackendAction(action,target){
 if(window.RIPublic)return false;
 if(['review','submit-review','save-upload','download-report'].includes(action)&&!backendAccess.can_edit){handleBackendAction('editor-login');return true;}
 switch(action){
 case 'download-reviews':download(JSON.stringify(reviews,null,2),'roadinsight-reviews.json');return true;
 case 'editor-login':if(backendAccess.login_enabled===false){toast('当前仅开放演示浏览');return true;}modal('解锁编辑工作区',`<p class="note">仅供项目编辑者使用；普通访客无需登录即可浏览。访问码由部署管理员配置。</p><label class="field"><span>编辑访问码</span><input id="editor-token" type="password" maxlength="256" autocomplete="current-password"></label>`,`<div class="dialog-actions"><button class="primary" data-action="submit-login">解锁</button></div>`);return true;
 case 'submit-login':{const token=document.getElementById('editor-token').value;document.getElementById('editor-token').value='';event('login',{token});document.getElementById('modal-root').innerHTML='';return true;}
 case 'editor-logout':bundlePreview=null;uploadPreview=null;savedUpload=null;event('logout');return true;
 case 'run-v2':backendBusy=true;render();event('run_analysis');return true;
 case 'select-job':bundlePreview=null;event('select_job',{job_id:target.dataset.id});return true;
 case 'select-demo':bundlePreview=null;event('select_job',{job_id:null});return true;
 case 'refresh-jobs':event('refresh_jobs');return true;
 case 'download-example':event('download_example');return true;
 default:return false;
 }
}
document.addEventListener('change',e=>{
 if(e.target.id!=='bundle-file')return;
 const file=e.target.files[0];if(!file)return;
 if(file.size>15*1024*1024){toast('V2 数据包不得超过 15 MB');e.target.value='';return;}
 backendBusy=true;bundlePreview=null;
 const reader=new FileReader();reader.onload=()=>event('bundle_upload',{filename:file.name,base64:String(reader.result).split(',')[1]});
 reader.onerror=()=>{backendBusy=false;toast('读取失败，请重试');render();};reader.readAsDataURL(file);render();
});
