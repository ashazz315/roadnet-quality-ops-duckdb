/* Standalone transport for the same UI; no upload/review backend is exposed. */
'use strict';
window.RIPublic={
  release:null,
  message(type,extra){
    if(type!=='streamlit:setComponentValue'||extra.value.action!=='navigate')return;
    const {page,issue_id}=extra.value,url=new URL(location.href);
    url.searchParams.set('page',page);
    if(issue_id&&(page==='map'||page==='detail'))url.searchParams.set('issue',issue_id);
    else url.searchParams.delete('issue');
    if(url.href!==location.href)history.pushState(null,'',url);
  }
};
window.addEventListener('DOMContentLoaded',async()=>{
  try{
    const manifestResponse=await fetch('release.json',{cache:'no-cache'});
    if(!manifestResponse.ok)throw new Error('发布清单读取失败');
    const manifest=await manifestResponse.json();
    if(manifest.schema_version!==1)throw new Error('发布清单版本不受支持');
    window.RIPublic.release=manifest;
    async function checked(name){
      const response=await fetch(name,{cache:'no-cache'});
      if(!response.ok)throw new Error('数据文件读取失败：'+name);
      const bytes=await response.arrayBuffer();
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
      if(hash!==manifest.files[name])throw new Error('数据版本校验失败，请刷新重试');
      return JSON.parse(new TextDecoder().decode(bytes));
    }
    const [snapshot,validation]=await Promise.all([checked('snapshot.json'),checked('validation.json')]);
    const params=new URLSearchParams(location.search);
    initialize({snapshot,validation,reviews:[],initial_page:params.get('page')||'dashboard',initial_issue:params.get('issue')});
    window.addEventListener('popstate',()=>{
      const p=new URLSearchParams(location.search),page=p.get('page')||'dashboard',id=p.get('issue');
      state.page=pages[page]?page:'dashboard';
      if(data.issues.some(row=>row.issue_id===id)){state.issueId=id;if(!visible().some(row=>row.issue_id===id))state.filters=RI.defaults(data);}
      normalize();render();
    });
  }catch(error){
    const root=document.getElementById('root');root.replaceChildren();
    const box=document.createElement('div');box.className='loading';
    box.textContent='演示暂时无法载入。'+error.message+'。请刷新页面，或从项目仓库运行本地完整版。';
    root.append(box);
  }
});
