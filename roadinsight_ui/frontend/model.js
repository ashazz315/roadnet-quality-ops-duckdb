/* Pure presentation calculations, also exercised by Node's built-in test runner. */
(function (root) {
  'use strict';
  const TYPES = {
    CONNECTIVITY_BREAK: {en:'Connectivity Break', zh:'连接性断裂', color:'#ff575f', icon:'route'},
    TURN_RESTRICTION_CONFLICT: {en:'Turn Restriction Conflict', zh:'转向限制冲突', color:'#ffae2d', icon:'turn'},
    ONEWAY_DIRECTION_CONFLICT: {en:'Oneway Conflict', zh:'单行通行冲突', color:'#438fff', icon:'direction'},
    MISSING_OR_CHANGED_ROAD_CANDIDATE: {en:'Missing Road Candidate', zh:'疑似缺失道路', color:'#a56aff', icon:'map'},
  };
  const SCENARIOS = {navigation:['Navigation','导航'],routing:['Route Planning','路线规划'],eta:['ETA','预计到达时间'],dispatch:['Dispatch','调度派单'],poi_local_service:['POI / Local Service','周边服务']};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = value => value ? new Date(value).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).slice(0,16) : '—';
  const day = value => date(value).slice(0,10);
  function defaults(data) {
    const days=data.issues.flatMap(x=>[day(x.first_observed_at),day(x.last_observed_at)]).filter(x=>x!=='—').sort();
    return {types:Object.keys(TYPES),severity:'all',min:0,max:100,start:days[0]||'',end:days.at(-1)||''};
  }
  function filtered(data, f) {
    return data.issues.filter(x=>f.types.includes(x.issue_type)&&(f.severity==='all'||x.severity===f.severity)&&x.confidence*100>=f.min&&x.confidence*100<=f.max&&(!f.start||day(x.last_observed_at)>=f.start)&&(!f.end||day(x.first_observed_at)<=f.end));
  }
  const select = (issues,id) => issues.find(x=>x.issue_id===id) || issues[0] || null;
  function distribution(issues){return Object.keys(TYPES).map(type=>({type,...TYPES[type],count:issues.filter(x=>x.issue_type===type).length}));}
  function business(issues){return Object.entries(SCENARIOS).map(([key,names])=>({key,names,counts:['HIGH','MEDIUM','LOW'].map(level=>issues.filter(issue=>issue.business_impact.some(row=>row.business_scenario===key&&row.impact_level===level)).length)}));}
  function replayStats(routes){return {count:routes.length,shorter:routes.filter(x=>x.distance_change==='shorter').length,longer:routes.filter(x=>x.distance_change==='longer').length,restored:routes.filter(x=>x.reachability_change==='restored').length,lost:routes.filter(x=>x.reachability_change==='lost').length};}
  function routeMetric(value,unit){return value===null||value===undefined?'不可计算':`${Number(value).toFixed(1)} ${unit}`;}
  function difference(row){return row.distance_delta_m===null?'可达性变化，差值不适用':`${row.distance_delta_m>=0?'+':''}${row.distance_delta_m.toFixed(1)} m / ${row.eta_delta_s>=0?'+':''}${row.eta_delta_s.toFixed(1)} s`;}
  const api={TYPES,SCENARIOS,escape,date,day,defaults,filtered,select,distribution,business,replayStats,routeMetric,difference};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.RI=api;
})(typeof window!=='undefined'?window:globalThis);
