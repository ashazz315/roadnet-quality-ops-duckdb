/* Leaflet is locally bundled. OSM tiles are optional; current roads work offline. */
window.RIMaps=(function(){
  const active=[],views={};
  const latlon=p=>[p[1],p[0]];
  function clear(){for(const {map,id} of active){views[id]={center:map.getCenter(),zoom:map.getZoom()};map.remove();}active.length=0;}
  function draw(id,data,{issues=[],selected=null,layers={},onSelect=null,route=null,bounds=null,focus=false,tiles=true}={}){
    const el=document.getElementById(id);if(!el)return;
    const map=L.map(el,{zoomControl:false,attributionControl:true,preferCanvas:true,scrollWheelZoom:false});
    active.push({map,id});L.control.zoom({position:'topright'}).addTo(map);L.control.scale({imperial:false,position:'bottomleft'}).addTo(map);
    map.attributionControl.setPrefix(false);map.attributionControl.addAttribution('<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors</a>');
    if(tiles){
      const tile=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,updateWhenIdle:true});
      let failed=0;tile.on('tileerror',()=>{if(++failed===2){const tag=document.createElement('span');tag.className='offline-badge';tag.textContent='底图暂不可用 · 路网可正常查看';el.appendChild(tag);}});tile.addTo(map);
    }
    const all=data.roads.flatMap(road=>road.coordinates.map(latlon));
    const total=L.latLngBounds(all);
    if(layers.roads!==false){for(const road of data.roads){L.polyline(road.coordinates.map(latlon),{color:tiles?'#859bb7':'#aec1d6',weight:['primary','secondary','trunk'].includes(road.road_class)?2.1:1.2,opacity:tiles?.65:.9,interactive:false}).addTo(map);}}
    if(!tiles){
      const names=new Set();for(const road of data.roads){if(!road.name||names.has(road.name)||!['primary','secondary','trunk'].includes(road.road_class)||names.size>20)continue;names.add(road.name);const p=road.coordinates[Math.floor(road.coordinates.length/2)];L.marker(latlon(p),{interactive:false,icon:L.divIcon({className:'road-label',html:RI.escape(road.name),iconSize:[110,14]})}).addTo(map);}
    }
    if(selected){
      if(layers.trajectory!==false){for(const track of selected.trajectories){if(track.context.length>1)L.polyline(track.context.map(latlon),{color:'#25a8ee',weight:2,opacity:.45,interactive:false}).addTo(map);}}
      if(layers.support!==false){for(const track of selected.trajectories){if(track.support.length>1)L.polyline(track.support.map(latlon),{color:'#ff4f62',weight:3,opacity:.72,interactive:false,dashArray:'5 4'}).addTo(map);}}
      if(layers.feedback!==false){for(const row of selected.feedback){L.circleMarker([row.latitude,row.longitude],{radius:6,color:'#9b55e8',fillColor:'#bb86f7',fillOpacity:.85,weight:2}).bindPopup(`<b>用户反馈 · ${RI.escape(row.feedback_id)}</b><br>${RI.escape(row.description)}`).addTo(map);}}
      if(layers.issues!==false)L.geoJSON(selected.geometry,{pointToLayer:(_,p)=>L.circleMarker(p,{radius:8,color:'#ff4d5d',fillOpacity:.2}),style:{color:'#ff4d5d',weight:4,opacity:.85}}).addTo(map);
    }
    if(layers.issues!==false){for(const issue of issues){
      const chosen=selected?.issue_id===issue.issue_id;
      if(chosen)L.circleMarker([issue.latitude,issue.longitude],{radius:12,color:'#ff4f60',weight:2,fillColor:'#ff4f60',fillOpacity:.15,interactive:false}).addTo(map);
      const color=issue.severity==='HIGH'?'#ff5263':issue.severity==='MEDIUM'?'#ffaf26':'#478eff';
      const marker=L.circleMarker([issue.latitude,issue.longitude],{radius:chosen?7:5.5,color:'#fff',weight:2,fillColor:color,fillOpacity:.96}).addTo(map);
      marker.bindTooltip(`${RI.escape(RI.TYPES[issue.issue_type].en)} · ${(issue.confidence*100).toFixed(1)}% 证据评分`);
      marker.on('click',()=>{const same=issues.filter(x=>x.latitude===issue.latitude&&x.longitude===issue.longitude);const next=same.length>1?same[(same.findIndex(x=>x.issue_id===selected?.issue_id)+1)%same.length]:[issue][0];if(onSelect)onSelect(next.issue_id);});
    }}
    if(route?.geometry){L.geoJSON(route.geometry,{style:{color:route.phase==='before'?'#ff5864':'#1478ff',weight:4,opacity:1}}).addTo(map);const coords=route.geometry.coordinates;if(route.geometry.type==='LineString'){for(const [p,label] of [[coords[0],'起'],[coords.at(-1),'终']])L.marker(latlon(p),{icon:L.divIcon({className:'route-endpoint',html:label,iconSize:[24,24],iconAnchor:[12,12]})}).addTo(map);}}
    let target=bounds?L.latLngBounds(bounds):total;
    if(focus&&selected){let points=[[selected.latitude,selected.longitude],...selected.trajectories.flatMap(t=>t.context.map(latlon))];target=L.latLngBounds(points).pad(.22);}
    if(views[id]&&!focus&&!bounds)map.setView(views[id].center,views[id].zoom);else map.fitBounds(target,{padding:[18,18],maxZoom:focus?17:17});
    const Reset=L.Control.extend({options:{position:'topright'},onAdd(){const box=L.DomUtil.create('div','leaflet-bar map-reset');const button=L.DomUtil.create('button','',box);button.type='button';button.innerHTML=icon('refresh');button.title='重置地图范围';button.setAttribute('aria-label','重置地图范围');L.DomEvent.disableClickPropagation(box);L.DomEvent.on(button,'click',()=>map.fitBounds(target,{padding:[18,18],maxZoom:17}));return box;}});new Reset().addTo(map);
    setTimeout(()=>{if(active.some(item=>item.map===map))map.invalidateSize();},30);
    return map;
  }
  return {draw,clear,views,get:id=>active.find(item=>item.id===id)?.map};
})();
