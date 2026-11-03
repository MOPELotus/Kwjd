'use strict';
const $ = id => document.getElementById(id);
const games = {Genshin:'原神',StarRail:'星穹铁道',ZZZ:'绝区零'};
const labels = {calm:'舒缓',cheerful:'轻快',intense:'激烈/紧张',vocal:'人声',other:'其他'};
const pageSize = 50;
let tracks=[],byKey=new Map(),game='Genshin',page=1,currentKey=null,loading=false;
const saving=new Set();
let toastTimer;
try {game=localStorage.getItem('music-review-game')||game;}catch{}
if(!games[game])game='Genshin';
const audio=$('audio');
const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const duration = seconds => `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;

function toast(message,error=false){
  clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;
  toastTimer=setTimeout(()=>$('toast').hidden=true,error?8000:2600);
}
function filtered(){
  const query=$('search').value.trim().toLocaleLowerCase();
  return tracks.filter(t=>{
    if(t.game!==game || (query&&!`${t.title} ${t.album}`.toLocaleLowerCase().includes(query)))return false;
    const status=$('review-filter').value,category=$('label-filter').value;
    if(status==='classified'&&t.label===null || status==='unclassified'&&t.label!==null)return false;
    if(category==='usable')return ['calm','cheerful'].includes(t.label);
    if(category==='excluded')return ['intense','vocal','other'].includes(t.label);
    return category==='all'||category===t.label;
  });
}
function render(){
  const classified=tracks.filter(t=>t.label!==null).length;
  $('total').textContent=tracks.length.toLocaleString();$('classified').textContent=classified.toLocaleString();
  $('unclassified').textContent=tracks.filter(t=>t.label===null).length.toLocaleString();
  $('progress-text').textContent=`已有分类 ${classified.toLocaleString()} / ${tracks.length.toLocaleString()} 首 · 无需逐首确认`;
  $('progress-fill').style.width=`${tracks.length?classified/tracks.length*100:0}%`;
  $('games').innerHTML=Object.entries(games).map(([key,name])=>{
    const rows=tracks.filter(t=>t.game===key),done=rows.filter(t=>t.label!==null).length;
    return `<button class="game-tab ${key===game?'active':''}" data-game="${key}" aria-current="${key===game?'page':'false'}">${name}<span class="tab-count">${done} / ${rows.length}</span></button>`;
  }).join('');
  const rows=filtered(),pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.min(page,pages);
  $('list-count').textContent=`${games[game]} · ${rows.length.toLocaleString()} 首`;
  const visible=rows.slice((page-1)*pageSize,page*pageSize);
  $('tracks').innerHTML=visible.length?visible.map(t=>{
    const active=currentKey===t.key&&!audio.paused,pending=saving.has(t.key);
    return `<article class="track ${currentKey===t.key?'is-playing':''}" data-key="${esc(t.key)}">
      <button class="play" data-action="play" aria-label="${active?'暂停':'播放'} ${esc(t.title)}">${active?'Ⅱ':'▶'}</button>
      <div class="song"><div class="song-title">${esc(t.title)}</div><div class="song-meta"><span class="duration">${duration(t.duration)}</span><span>·</span><span class="album" title="${esc(t.album)}">${esc(t.album)}</span></div></div>
      <div class="segments" role="group" aria-label="${esc(t.title)}的分类">${Object.entries(labels).map(([key,name])=>`<button class="segment" data-action="label" data-label="${key}" aria-pressed="${t.label===key}" ${pending?'disabled':''}>${name}</button>`).join('')}</div>
      <div class="review-actions"><span class="category-status">${pending?'保存中…':t.label===null?'未分类':['calm','cheerful'].includes(t.label)?'可入视频':'不入视频'}</span><button class="clear" data-action="clear" ${pending||t.label===null?'disabled':''} title="清空分类，留待判断">留空</button></div>
    </article>`;
  }).join(''):`<div class="empty">${tracks.length?'没有符合条件的曲目。<br>试试其他筛选或搜索词。':'音乐库加载中…'}</div>`;
  $('page-info').textContent=`第 ${page} / ${pages} 页 · 每页 ${pageSize} 首`;
  $('previous-page').disabled=page<=1;$('next-page').disabled=page>=pages;
  $('previous-track').disabled=!currentKey;$('next-track').disabled=!tracks.length;
}
async function load(){
  if(loading||saving.size)return;loading=true;$('save-status').textContent='正在读取…';
  try{
    const response=await fetch('/api/tracks',{cache:'no-store'});if(!response.ok)throw new Error('读取音乐库失败');
    const data=await response.json();tracks=data.tracks;byKey=new Map(tracks.map(t=>[t.key,t]));
    $('save-status').textContent='✓ 已连接 · 自动保存';$('save-status').classList.remove('error');render();
  }catch(error){$('save-status').textContent='连接失败';$('save-status').classList.add('error');toast(error.message,true);}
  finally{loading=false;}
}
async function save(key,label){
  if(saving.has(key))return false;
  const row=byKey.get(key);if(!row)return false;
  saving.add(key);render();$('save-status').textContent='正在保存…';
  try{
    const response=await fetch(`/api/review/${encodeURIComponent(key)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label,source_sha256:row.source_sha256,expected_revision:row.revision})});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'保存失败，请重试');
    Object.assign(row,data.track);$('save-status').textContent='✓ 已保存';$('save-status').classList.remove('error');
    return true;
  }catch(error){$('save-status').textContent='未保存，请重试';$('save-status').classList.add('error');toast(error.message,true);return false;}
  finally{saving.delete(key);render();}
}
function showCurrentPage(){
  const i=filtered().findIndex(t=>t.key===currentKey);if(i>=0)page=Math.floor(i/pageSize)+1;
}
async function play(key){
  const row=byKey.get(key);if(!row)return;
  if(currentKey===key&&!audio.paused){audio.pause();return;}
  if(currentKey!==key){
    currentKey=key;audio.src=`/audio/${encodeURIComponent(key)}`;
    $('now-title').textContent=row.title;$('now-album').textContent=`${games[row.game]} · ${row.album}`;
    try{localStorage.setItem('music-review-last',key);}catch{}
  }
  showCurrentPage();render();
  try{await audio.play();}catch(error){toast('播放未能开始，请点击播放器重试。',true);}
}
function neighbor(delta){
  let list=filtered(),index=list.findIndex(t=>t.key===currentKey);
  if(index<0){list=tracks.filter(t=>t.game===game);index=list.findIndex(t=>t.key===currentKey);}
  const next=index<0?list[0]:list[index+delta];if(next)play(next.key);else toast(delta>0?'已经是最后一首':'已经是第一首');
}
$('games').addEventListener('click',e=>{
  const tab=e.target.closest('[data-game]');if(!tab)return;game=tab.dataset.game;page=1;
  try{localStorage.setItem('music-review-game',game);}catch{}render();
});
$('tracks').addEventListener('click',async e=>{
  const button=e.target.closest('button'),article=e.target.closest('[data-key]');if(!button||!article)return;
  const key=article.dataset.key,row=byKey.get(key);
  if(button.dataset.action==='play')return play(key);
  if(button.dataset.action==='label')return save(key,button.dataset.label);
  if(button.dataset.action==='clear')return save(key,null);
});
for(const id of ['search','review-filter','label-filter'])$(id).addEventListener(id==='search'?'input':'change',()=>{page=1;render();});
$('previous-page').onclick=()=>{page--;render();};$('next-page').onclick=()=>{page++;render();};
$('refresh').onclick=load;$('previous-track').onclick=()=>neighbor(-1);$('next-track').onclick=()=>neighbor(1);
audio.addEventListener('play',render);audio.addEventListener('pause',render);
audio.addEventListener('error',()=>{if(audio.src)toast('音频读取或解码失败，请刷新后重试；也可换用 Chrome 或 Edge。',true);});
document.addEventListener('keydown',e=>{
  if(e.ctrlKey||e.metaKey||e.altKey||e.target.matches('input,select,textarea,button')||e.target.isContentEditable)return;
  if(e.code==='Space'&&currentKey){e.preventDefault();audio.paused?play(currentKey):audio.pause();}
  if(/^[1-5]$/.test(e.key)&&currentKey){e.preventDefault();save(currentKey,Object.keys(labels)[Number(e.key)-1]);}
  if(e.key.toLowerCase()==='n'){e.preventDefault();neighbor(1);}
});
$('export').onclick=()=>{
  const reviews=tracks.map(t=>({key:t.key,game:t.game,title:t.title,album:t.album,label:t.label,label_name:labels[t.label]||null,label_source:t.human_override?'human':'model',source_sha256:t.source_sha256,updated_at:t.updated_at}));
  const blob=new Blob([JSON.stringify({exported_at:new Date().toISOString(),reviews},null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`音乐人工审核-${new Date().toISOString().slice(0,10)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
load();
