const $ = s => document.querySelector(s);
const LECT = {Lecture1:'--l1',Lecture2:'--l2',Lecture3:'--l3',Lecture4:'--l4',
  Lecture5:'--l5','Lectura6':'--l6',Lecture7:'--l7',Lecture8:'--l8'};
const lectColor = l => getComputedStyle(document.body)
  .getPropertyValue(LECT[(l||'').replace(/-DS4400.*|-new/g,'').replace('Bias-variance-error-correction','Lecture8')] || '--dim').trim() || '#8f8b83';

let graphLoaded = false;
function switchView(v){
  document.querySelectorAll('.seg-btn').forEach(b=>{
    const on = b.dataset.view===v; b.classList.toggle('on',on); b.setAttribute('aria-selected',on);});
  $('#view-chat').classList.toggle('hidden', v!=='chat');
  $('#view-graph').classList.toggle('hidden', v!=='graph');
  if(v==='graph' && !graphLoaded){ loadGraph(); graphLoaded = true; }
}
document.querySelectorAll('.seg-btn').forEach(b=>b.onclick=()=>switchView(b.dataset.view));

async function loadDue(){
  try{
    const r = await fetch('/api/due?days=14'); const rows = await r.json();
    const box = $('#upcoming');
    if(!Array.isArray(rows) || rows.length===0){ box.innerHTML='<div class="recent">Nothing due</div>'; return; }
    const today = new Date().toDateString();
    box.innerHTML = rows.map(x=>{
      const d = new Date(x.due); const isToday = d.toDateString()===today;
      const when = d.toLocaleString('en-US',{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
      return `<div class="due-card"><div class="n">${x.name}</div>
        <div class="d${isToday?' today':''}">${when}${isToday?' · today':''} · ${x.course}</div></div>`;
    }).join('');
  }catch(e){ $('#upcoming').innerHTML='<div class="recent">Deadlines unavailable</div>'; }
}

// ---- Chat: send, markdown+KaTeX render, citations, recents (Task 7) ----
function addMsg(html, who){
  const el = document.createElement('div');
  el.className = who==='user' ? 'msg-user' : 'msg-ai';
  el.innerHTML = html; $('#messages').appendChild(el);
  $('#messages').scrollTop = $('#messages').scrollHeight;
  return el;
}
function renderAnswer(res){
  const body = marked.parse(res.answer);
  const cites = (res.sources||[]).map(s=>`<span class="cite">${s.source} · ${s.section}</span>`).join('');
  const el = addMsg(body + (cites?`<div class="cites">${cites}</div>`:''), 'ai');
  renderMathInElement(el, {delimiters:[
    {left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}]});
}
async function ask(q){
  addMsg(q.replace(/</g,'&lt;'),'user');
  const thinking = addMsg('<span class="cite">thinking…</span>','ai');
  try{
    const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({q})});
    const res = await r.json(); thinking.remove(); renderAnswer(res);
  }catch(e){ thinking.innerHTML = 'Request failed — is the server running?'; }
}
$('#composer').addEventListener('submit', e=>{
  e.preventDefault(); const q = $('#q').value.trim(); if(!q) return;
  $('#q').value=''; ask(q);
});
$('#new-chat').onclick = ()=>{ $('#messages').innerHTML=''; };

// recent concepts (last opened/asked), localStorage
function pushRecent(name, lect){
  const k='recents'; let list = JSON.parse(localStorage.getItem(k)||'[]');
  list = [{name,lect}, ...list.filter(x=>x.name!==name)].slice(0,6);
  localStorage.setItem(k, JSON.stringify(list)); renderRecents();
}
function renderRecents(){
  const list = JSON.parse(localStorage.getItem('recents')||'[]');
  $('#recents').innerHTML = list.map(x=>
    `<div class="recent" data-c="${x.name}"><span class="dot" style="background:${lectColor(x.lect)}"></span>${x.name}</div>`
  ).join('');
  $('#recents').querySelectorAll('.recent').forEach(el=>
    el.onclick=()=>{ switchView('chat'); ask(`Explain ${el.dataset.c} from my course material`); });
}

// ---- Graph: force-graph + concept panel (Task 8) ----
let fg;
async function loadGraph(){
  const r = await fetch('/api/graph'); const g = await r.json();
  const data = { nodes: g.nodes.map(n=>({...n})),
                 links: g.edges.map(e=>({source:e.s, target:e.t})) };
  const el = $('#graph');
  fg = ForceGraph()(el)
    .width(el.clientWidth).height(el.clientHeight)   // explicit: auto-size measures 0 before layout flushes
    .backgroundColor('#141417')
    .graphData(data)
    .nodeLabel('id')
    .nodeRelSize(4)
    .nodeVal(n=>1+n.degree)
    .nodeColor(n=>lectColor(n.lect))
    .linkColor(()=> 'rgba(180,180,190,0.25)')
    .nodeCanvasObjectMode(()=>'after')
    .nodeCanvasObject((n,ctx,scale)=>{           // glow + label for hubs
      ctx.shadowColor = lectColor(n.lect); ctx.shadowBlur = 12;
      ctx.beginPath(); ctx.arc(n.x,n.y,(1+n.degree)*0.9+2,0,2*Math.PI);
      ctx.fillStyle = lectColor(n.lect); ctx.fill(); ctx.shadowBlur = 0;
      if(n.degree>=4 || scale>2){
        ctx.font = `${11/scale}px Inter`; ctx.fillStyle='#cfc9bf';
        ctx.fillText(n.id, n.x+ (1+n.degree)*0.9+3, n.y+3);
      }
    })
    .onNodeClick(n=>openConcept(n.id, n.lect));
  addEventListener('resize', ()=>{ if(fg) fg.width(el.clientWidth).height(el.clientHeight); });
}
async function openConcept(name, lect){
  const r = await fetch('/api/concept/'+encodeURIComponent(name));
  if(!r.ok){ return; }
  const c = await r.json(); pushRecent(c.name, lect);
  const links = (c.links||[]).map(l=>`<div class="lk" data-l="${l}">↳ ${l}</div>`).join('');
  const lects = (c.lectures||[]).map(x=>x.replace(/-DS4400.*|-new/g,'')).join(', ');
  const p = $('#concept-panel'); p.classList.remove('hidden');
  p.innerHTML = `<h3>${c.name}</h3><p>${c.definition||''}</p>
    ${links?`<div class="pl">Links</div>${links}`:''}
    ${lects?`<div class="pl">In</div><div style="font-size:12px;color:var(--dim)">${lects}</div>`:''}
    <button class="ask">Ask about this →</button>`;
  p.querySelector('.ask').onclick = ()=>{
    switchView('chat'); ask(`Explain ${c.name} from my course material`); };
  p.querySelectorAll('.lk').forEach(el=>el.onclick=()=>openConcept(el.dataset.l, lect));
}

loadDue();
renderRecents();
