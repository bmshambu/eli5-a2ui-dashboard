"""The interactive explorer: a Power BI-style report page inside GE's side panel.

The tab is pre-aggregated in Python (analysis.cube) and embedded as JSON; everything else runs in
the browser, so filters and clicks respond instantly with no round trip to the agent:
  - filter dropdowns along the top (one per category, plus the period range) and KPI tiles
  - four views on tabs: a bubble chart (any two numbers, sized by a third, quadrants named and
    coloured by verdict, labels on the distinctive bubbles, drag to zoom) with a ranked list
    beside it; a treemap (blocks grouped by one category, sized by an amount, coloured by a
    score); a heatmap (category by period or by a second category); and a sortable table
  - a "What this shows" panel beside every view that tells the story of what is on screen, and
    of whatever the pointer is on
  - cross-filtering: click a bubble, block, cell or row and everything else filters to it
  - "Explain this view", which sends the current filters to the agent so the insight cards are
    rewritten for exactly what is on screen
The frame's CSP allows inline scripts only (proven in GE), so no charting library is loaded.
"""
import html
import json

CSS = r"""
.ex{display:flex;flex-direction:column;gap:10px;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--q1:#2a78d6;--q2:#1baf7a;--q3:#eb6834;--q4:#9a9890}
:root[data-theme=dark] .ex{--q4:#6f6d68}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .ex{--q4:#6f6d68}}
.muted{color:var(--muted)}
.ex-filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.ex select{font:inherit;font-size:12.5px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);max-width:190px}
.dd-l{font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-left:4px}
details.dd{position:relative}
details.dd summary{list-style:none;cursor:pointer;border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:3px 12px;font-size:12.5px;color:var(--ink)}
details.dd summary::-webkit-details-marker{display:none}
details.dd summary::after{content:" ▾";color:var(--muted)}details.dd[open] summary{border-color:var(--accent)}
details.dd summary b{background:var(--accent);color:#fff;border-radius:999px;padding:0 6px;font-size:11px;margin-left:2px}
.menu{position:absolute;z-index:15;top:calc(100% + 4px);left:0;min-width:200px;max-width:280px;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px;box-shadow:0 8px 24px rgba(0,0,0,.18)}
.menu-top{display:flex;gap:8px;align-items:center;margin-bottom:6px}
.menu .list{max-height:220px;overflow:auto}
.menu label.opt{display:flex;gap:6px;align-items:center;font-size:13px;padding:2px 0;cursor:pointer}
.menu input.find{flex:1;font:inherit;font-size:12px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--page);color:var(--ink)}
.linkbtn{background:none;border:0;color:var(--accent);font:inherit;font-size:12px;cursor:pointer;padding:0 4px}.linkbtn[disabled]{color:var(--muted);cursor:default}
.ex-bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:13px}
.fchip{background:var(--chip);border-radius:999px;padding:3px 10px;display:inline-flex;gap:6px;align-items:center}
.fchip button{background:none;border:0;color:var(--ink2);cursor:pointer;font-size:13px;padding:0}
.ex-bar .spacer{flex:1}
.btn{border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:8px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}
.btn.ghost{background:var(--surface);color:var(--accent)}
.ex-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.ex-kpis .kpi{padding:9px 12px}.ex-kpis .kpi .v{font-size:20px}
.ex-top{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;border-bottom:1px solid var(--line);padding-bottom:8px}
.ex-tabs{display:flex;gap:2px}
.vt{background:none;border:0;font:600 11.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding:6px 10px;border-radius:6px;cursor:pointer}
.vt:hover{color:var(--ink)}.vt.on{background:var(--ink);color:var(--page)}
.ex-set{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center}.pair{display:inline-flex;gap:5px;align-items:center;white-space:nowrap}
.ex-body{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:12px;align-items:start}
@media (max-width:1000px){.ex-body{grid-template-columns:minmax(0,1fr) 250px}}
@media (max-width:680px){.ex-body{grid-template-columns:minmax(0,1fr)}}
.vis{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px;min-width:0}
.vhead{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
#ex-zoom{white-space:nowrap;border:1px solid var(--line);border-radius:6px;padding:3px 8px}
.vis h3{font-size:15px;margin:0}.vis .hint{font-size:12px;color:var(--muted);margin:2px 0 6px}
.bub{width:100%;height:auto;display:block;overflow:visible;user-select:none;cursor:crosshair}
.bub circle.b{stroke:var(--surface);stroke-width:1.2;fill-opacity:.72;cursor:pointer;transition:transform .45s ease,r .45s ease,opacity .25s}
.bub circle.b:hover,.bub circle.b.hi{stroke:var(--ink);stroke-width:2;fill-opacity:.95}
.bub circle.b.on{stroke:var(--ink);stroke-width:2.5;fill-opacity:1}
.bub .dim{opacity:.15}
.bub .quad{stroke:var(--axis);stroke-dasharray:3 4}.bub .qlab{font:600 10.5px var(--mono);letter-spacing:.06em;fill:var(--muted)}
.bub .blab{font-size:11px;fill:var(--ink);font-weight:600;pointer-events:none;paint-order:stroke;stroke:var(--surface);stroke-width:3px}
.bub .zr{fill:var(--s1soft);stroke:var(--s1);stroke-dasharray:4 3}
.empty{padding:48px 12px;text-align:center;color:var(--muted);font-size:14px;border:1px dashed var(--axis);border-radius:8px;margin:8px 0}
.tm{position:relative;width:100%;overflow:hidden;border-radius:6px}
.tc{position:absolute;box-sizing:border-box;overflow:hidden;padding:3px 5px;font-size:11.5px;line-height:1.25;border:1px solid var(--surface);cursor:pointer;transition:opacity .2s}
.tc b{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tc span{font:10.5px var(--mono);opacity:.85;white-space:nowrap}
.tc em{font:normal 600 10px var(--mono);opacity:.8}
.tc:hover{outline:2px solid var(--ink);outline-offset:-2px;z-index:2}.tc.on{outline:2.5px solid var(--ink);outline-offset:-2px;z-index:3}.tc.dim{opacity:.25}
.tg{position:absolute;box-sizing:border-box;display:flex;justify-content:space-between;gap:6px;align-items:center;font:600 10.5px var(--mono);letter-spacing:.06em;
text-transform:uppercase;background:var(--page);color:var(--ink2);padding:0 6px;border:1px solid var(--line);overflow:hidden;white-space:nowrap;cursor:pointer}
.tg:hover{color:var(--ink)}
.hm{overflow:auto;max-height:520px}
.hm table{border-collapse:separate;border-spacing:2px;font-size:11.5px;font-variant-numeric:tabular-nums}
.hm th{font-weight:600;color:var(--ink2);padding:2px 5px;white-space:nowrap;position:sticky;top:0;background:var(--surface)}
.hm th.rh{text-align:left;left:0;z-index:1}.hm td.rh{position:sticky;left:0;background:var(--surface);font-weight:600;color:var(--ink2);white-space:nowrap;padding-right:6px;cursor:pointer}
.hm td.c{min-width:34px;height:26px;text-align:center;border-radius:3px;cursor:pointer;transition:opacity .2s}
.hm td.c:hover{outline:2px solid var(--ink2)}.hm td.c.on{outline:2.5px solid var(--ink)}.hm td.c.e{background:var(--grid)}
.hm .dim{opacity:.22}.hm td.tot{color:var(--ink2);text-align:right;font-weight:600;padding-left:6px}
.extbl{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
.extbl th{text-align:right;color:var(--ink2);font-weight:600;border-bottom:1px solid var(--line);padding:5px 8px;cursor:pointer;white-space:nowrap}
.extbl th:first-child,.extbl td:first-child{text-align:left}.extbl td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right}
.extbl tr.r{cursor:pointer}.extbl tr.r:hover{background:var(--chip)}.extbl tr.on{background:var(--s1soft)}
.extbl .bar{display:inline-block;height:8px;background:var(--s1);border-radius:0 3px 3px 0;margin-right:6px;vertical-align:0}
.tblbox{max-height:520px;overflow:auto}
.ex .legend{margin-top:6px}.ex .ramp .rl{margin:0 6px;font-variant-numeric:tabular-nums}
.side{display:flex;flex-direction:column;gap:10px;position:sticky;top:8px}
#ex-side,#ex-rank,.brief{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px}
.side-h,.rk-h{display:flex;justify-content:space-between;font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
.side-sub{font:inherit;color:var(--muted)}
.side-t{margin:2px 0 4px;font-size:15px}
.side-l{list-style:none;margin:0;padding:0;font-size:13px;line-height:1.45}.side-l li{padding:5px 0;border-top:1px solid var(--line)}.side-l li:first-child{border-top:0}
.side .tag{font-size:11px;border-radius:999px;padding:0 7px;margin-right:4px;background:var(--chip);color:var(--ink2)}
.rk{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
.rk th{font:600 10px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);text-align:right;padding:3px 4px;border-bottom:1px solid var(--line)}
.rk th:nth-child(2),.rk td:nth-child(2){text-align:left}.rk td{text-align:right;padding:3px 4px;white-space:nowrap;max-width:130px;overflow:hidden;text-overflow:ellipsis}
.rk td:first-child{color:var(--muted)}.rk tr.r{cursor:pointer}.rk tr.r:hover,.rk tr.hi{background:var(--chip)}.rk tr.on{background:var(--s1soft)}
.rk .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.rk-p{display:flex;justify-content:center;gap:6px;align-items:center;font-size:12px;color:var(--muted);margin-top:6px}
.hcard{position:fixed;z-index:30;pointer-events:none;background:#111214;color:#f0efec;border-radius:8px;padding:10px 12px;max-width:270px;font-size:12.5px;line-height:1.4;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.hcard .cv{font-size:22px;font-weight:700}.hcard .cv span{font-size:12px;font-weight:400;opacity:.7}.hcard .cn{font-weight:700;margin-bottom:2px}
.hcard .cm{opacity:.85}.hcard .cq{margin:3px 0}.hcard .cf{margin-top:5px;font-weight:600;opacity:.9}.hcard .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.ex-empty{padding:30px;text-align:center;color:var(--muted)}
@media (prefers-reduced-motion:reduce){.bub circle.b{transition:none}}
"""

JS = r"""
(function(){
var D=JSON.parse(document.getElementById('ex-data').textContent);
var C=D.cube, ND=C.dims.length, T=C.time, MS=C.measures, NM=MS.length;
var SERIES=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#6250d6','#e34948'];
var RAMP=['#e8f1fd','#cde2fb','#b7d3f6','#9ec5f4','#86b6ef','#6da7ec','#5598e7','#3987e5','#2a78d6','#256abf','#1c5cab','#104281'];
var QCOL=['var(--q1)','var(--q2)','var(--q3)','var(--q4)'];
var DF=D.defaults;
var S={dimSel:C.dims.map(function(d){return null;}),from:0,to:T?T.labels.length-1:0,sel:null,view:DF.view||'bubble',
  detail:DF.detail,color:DF.color,x:DF.x,y:DF.y,size:DF.size,hmRow:DF.hmRow,hmCol:DF.hmCol,hmM:DF.hmM,sortKey:'y',sortDir:-1,
  tmGroup:DF.tmGroup,tmCell:DF.tmCell,tmSize:DF.tmSize,tmColor:DF.tmColor,zoom:null,page:0,hover:null};
// colour by quadrant verdict unless the bubbles are rows coloured by their segment
if(S.color===S.detail)S.color='quad';
Object.keys(D.init.dims||{}).forEach(function(k){S.dimSel[+k]=new Set(D.init.dims[k]);});
if(T&&D.init.from!=null)S.from=D.init.from; if(T&&D.init.to!=null)S.to=D.init.to;
var $=function(id){return document.getElementById(id);};
function esc(s){return String(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function num(v){if(v==null||!isFinite(v))return 'n/a';var a=Math.abs(v),f=function(x,d){return (+x.toFixed(d)).toString();};
  if(a>=1e9)return f(v/1e9,a<1e10?2:1)+'B'; if(a>=1e6)return f(v/1e6,a<1e7?2:1)+'M'; if(a>=1e4)return f(v/1e3,a<1e5?2:1)+'K';
  if(a>=100||v%1===0)return Math.round(v).toLocaleString('en-GB'); return f(v,2);}
function pct(v){return v==null||!isFinite(v)?'n/a':(v>0?'+':'')+v.toFixed(1)+'%';}
// ── metrics ──
function metrics(){var out=[];MS.forEach(function(m,i){
  out.push(['m:'+i+':total',m.name+(m.rate?' (average)':' (total)')]);
  out.push(['m:'+i+':avg',m.name+(m.rate?' (average)':' per row')]);
  if(T)out.push(['m:'+i+':growth',m.name+' growth %']);
  if(!m.rate)out.push(['m:'+i+':share',m.name+' share %']);});
  out.push(['rows','Rows']);return out;}
var MET=metrics(), MLAB={}; MET.forEach(function(m){MLAB[m[0]]=m[1];});
function isPct(id){return /growth|share/.test(id);}
function fmt(id,v){return isPct(id)?(id.indexOf('share')>0?(v==null?'n/a':v.toFixed(1)+'%'):pct(v)):num(v);}
function rowOk(r,ignoreSel){
  for(var d=0;d<ND;d++){if(S.dimSel[d]&&!S.dimSel[d].has(r[d]))return false;}
  if(T&&(r[ND]<S.from||r[ND]>S.to))return false;
  if(!ignoreSel&&S.sel){var s=S.sel; if(r[s.dim]!==s.val)return false;
    if(s.col!=null){if(s.colDim==='time'){if(r[ND]!==s.col)return false;}else if(r[+s.colDim]!==s.col)return false;}}
  return true;}
function rowsFor(visual){return C.rows.filter(function(r){return rowOk(r,S.sel&&S.sel.src===visual);});}
function acc(){return {n:0,s:new Array(NM).fill(0),c:new Array(NM).fill(0),ts:{},color:{}};}
function add(a,r){a.n+=r[ND+1];for(var i=0;i<NM;i++){a.s[i]+=r[ND+2+2*i];a.c[i]+=r[ND+3+2*i];}
  if(T){var t=r[ND];if(!a.ts[t])a.ts[t]={s:new Array(NM).fill(0),c:new Array(NM).fill(0)};
    for(var j=0;j<NM;j++){a.ts[t].s[j]+=r[ND+2+2*j];a.ts[t].c[j]+=r[ND+3+2*j];}}
  if(S.color!=null){var cv=r[S.color];a.color[cv]=(a.color[cv]||0)+r[ND+1];}}
function groupBy(rows,key){var g=new Map();rows.forEach(function(r){var k=key(r);if(!g.has(k))g.set(k,acc());add(g.get(k),r);});return g;}
function total(rows){var a=acc();rows.forEach(function(r){add(a,r);});return a;}
function val(a,id,tot){if(id==='rows')return a.n;var p=id.split(':'),i=+p[1],m=MS[i],k=p[2];
  if(k==='total')return m.rate?(a.c[i]?a.s[i]/a.c[i]:null):a.s[i];
  if(k==='avg')return m.rate?(a.c[i]?a.s[i]/a.c[i]:null):(a.n?a.s[i]/a.n:null);
  if(k==='share')return tot&&tot.s[i]?a.s[i]/tot.s[i]*100:null;
  if(k==='growth'){var ks=Object.keys(a.ts).map(Number).sort(function(x,y){return x-y;});if(ks.length<2)return null;
    var f=a.ts[ks[0]],l=a.ts[ks[ks.length-1]],fv=m.rate?(f.c[i]?f.s[i]/f.c[i]:null):f.s[i],lv=m.rate?(l.c[i]?l.s[i]/l.c[i]:null):l.s[i];
    if(ks[0]!==S.from||ks[ks.length-1]!==S.to)return null; // only compare like for like
    return fv?(lv-fv)/Math.abs(fv)*100:null;}
  return null;}
function median(a){if(!a.length)return 0;var s=a.slice().sort(function(x,y){return x-y;}),h=s.length>>1;return s.length%2?s[h]:(s[h-1]+s[h])/2;}
function ticks(lo,hi,n){if(!isFinite(lo)||!isFinite(hi))return [0,1];if(hi===lo){hi+=Math.abs(hi)||1;lo-=Math.abs(lo)||1;}
  var raw=(hi-lo)/(n-1),mag=Math.pow(10,Math.floor(Math.log10(raw))),step=[1,2,2.5,5,10].map(function(m){return m*mag;}).find(function(s){return s>=raw;});
  var out=[],v=Math.floor(lo/step)*step;while(v<hi+step*.999){out.push(+v.toFixed(10));v+=step;}return out;}
function dimName(i){return C.dims[i].name;} function dv(d,i){return C.dims[d].values[i];}
function colorOf(a){if(S.color==null)return SERIES[0];var best=-1,bv=-1;for(var k in a.color){if(a.color[k]>bv){bv=a.color[k];best=+k;}}
  var n=C.dims[S.color].values.length;return n<=8?SERIES[best]:SERIES[0];}

function insightBox(id,items){var el=$(id);if(!el)return;items=items.filter(Boolean);
  el.innerHTML=items.length?'<b class="h">Insights for this view</b><ul>'+items.map(function(t){return '<li>'+t+'</li>';}).join('')+'</ul>':'';
  el.style.display=items.length?'':'none';}
function bold(t){return '<b>'+esc(t)+'</b>';}
function corr(xs,ys){var n=xs.length;if(n<3)return null;var mx=xs.reduce(function(a,b){return a+b;},0)/n,my=ys.reduce(function(a,b){return a+b;},0)/n,sxy=0,sxx=0,syy=0;
  for(var i=0;i<n;i++){sxy+=(xs[i]-mx)*(ys[i]-my);sxx+=(xs[i]-mx)*(xs[i]-mx);syy+=(ys[i]-my)*(ys[i]-my);}return sxx&&syy?sxy/Math.sqrt(sxx*syy):null;}
function robustOut(list,f){var v=list.map(f),m=median(v),mad=median(v.map(function(x){return Math.abs(x-m);}));if(!mad)return null;
  var best=null,bz=0;list.forEach(function(p,i){var z=.6745*(v[i]-m)/mad;if(Math.abs(z)>Math.abs(bz)){bz=z;best=p;}});return Math.abs(bz)>3?{p:best,z:bz,m:m}:null;}
function names(list){return list.slice(0,3).map(function(p){return bold(dv(S.detail,p.k));}).join(', ')+(list.length>3?' and '+(list.length-3)+' more':'');}
function bubbleInsights(pts,mx,my,sx,sy){var out=[],n=pts.length,yl=MLAB[sy],xl=MLAB[sx];if(!n)return out;
  var byY=pts.slice().sort(function(a,b){return b.y-a.y;}),sumY=pts.reduce(function(a,p){return a+Math.max(p.y,0);},0),sumable=/:total$/.test(sy)&&!MS[+sy.split(':')[1]].rate;
  out.push(bold(dv(S.detail,byY[0].k))+' leads on '+esc(yl)+' at '+bold(fmt(sy,byY[0].y))+(sumable&&sumY?' ('+(byY[0].y/sumY*100).toFixed(1)+'% of the bubbles shown)':'')+'.');
  if(n>=4){var gr=/growth/.test(sx),q1=pts.filter(function(p){return p.x>mx&&p.y>my;}),q3=pts.filter(function(p){return p.x>mx&&p.y<=my;}),q2=pts.filter(function(p){return p.x<=mx&&p.y>my;});
    if(q1.length)out.push('<span class="tag">'+(gr?'Big and growing':'High on both')+'</span>'+names(q1)+(gr?': protect and invest.':'.'));
    if(gr&&q3.length)out.push('<span class="tag">Small but growing</span>'+names(q3)+': the ones to watch.');
    if(gr&&q2.length)out.push('<span class="tag">Big but slower</span>'+names(q2)+': large, but losing momentum against the rest.');}
  if(sumable&&n>=5){var top3=byY.slice(0,3).reduce(function(a,p){return a+Math.max(p.y,0);},0);if(sumY)out.push('The top 3 make '+bold((top3/sumY*100).toFixed(1)+'%')+' of '+esc(yl)+' across '+n+' '+esc(dimName(S.detail))+' values.');}
  var o=robustOut(pts,function(p){return p.x;});if(o)out.push(bold(dv(S.detail,o.p.k))+' stands apart on '+esc(xl)+': '+bold(fmt(sx,o.p.x))+' against a typical '+esc(fmt(sx,o.m))+'.');
  if(n>=5){var r=corr(pts.map(function(p){return p.x;}),pts.map(function(p){return p.y;}));
    if(r!=null)out.push(Math.abs(r)>=.5?esc(xl)+' and '+esc(yl)+' move '+(r>0?'together':'in opposite directions')+' (correlation '+bold(r.toFixed(2))+').':'Little link between '+esc(xl)+' and '+esc(yl)+' (correlation '+r.toFixed(2)+'), so size does not predict it here.');}
  return out.slice(0,5);}
function heatInsights(rk,cols,cellv,isT,cd,rd){var out=[],vals=[],rl=function(r){return dv(rd,r);},cl=function(c){return isT?T.labels[c]:dv(+cd,c);};
  rk.forEach(function(r,i){cols.forEach(function(c,j){var v=cellv[i][j];if(v!=null)vals.push({r:r,c:c,i:i,j:j,v:v});});});if(vals.length<4)return out;
  var hi=vals.reduce(function(a,b){return b.v>a.v?b:a;}),lo=vals.reduce(function(a,b){return b.v<a.v?b:a;});
  out.push('Hottest cell: '+bold(rl(hi.r))+' in '+bold(cl(hi.c))+' at '+bold(num(hi.v))+'; coolest: '+esc(rl(lo.r))+' in '+esc(cl(lo.c))+' ('+esc(num(lo.v))+').');
  var nr=rk.length,nc=cols.length,rm=[],cm=[],g=0,cnt=0;
  for(var i=0;i<nr;i++){var s=0,k=0;for(var j=0;j<nc;j++)if(cellv[i][j]!=null){s+=cellv[i][j];k++;}rm.push(k?s/k:0);}
  for(var j2=0;j2<nc;j2++){var s2=0,k2=0;for(var i2=0;i2<nr;i2++)if(cellv[i2][j2]!=null){s2+=cellv[i2][j2];k2++;g+=cellv[i2][j2];cnt++;}cm.push(k2?s2/k2:0);}
  g=cnt?g/cnt:0;
  var colTot=cols.map(function(c,j){var s=0;for(var i=0;i<nr;i++)if(cellv[i][j]!=null)s+=cellv[i][j];return s;}),pj=colTot.indexOf(Math.max.apply(null,colTot));
  out.push((isT?'Strongest period':'Strongest column')+': '+bold(cl(cols[pj]))+' ('+esc(num(colTot[pj]))+' across the rows shown).');
  if(isT&&nc>=2){var ch=[];rk.forEach(function(r,i){var f=null,l=null;for(var j=0;j<nc;j++)if(cellv[i][j]!=null){if(f==null)f=cellv[i][j];l=cellv[i][j];}if(f)ch.push({r:r,g:(l-f)/Math.abs(f)*100});});
    if(ch.length>=2){ch.sort(function(a,b){return b.g-a.g;});out.push('From '+esc(cl(cols[0]))+' to '+esc(cl(cols[nc-1]))+', '+bold(rl(ch[0].r))+' grew most ('+bold(pct(ch[0].g))+')'+(ch[ch.length-1].g<ch[0].g?' and '+esc(rl(ch[ch.length-1].r))+' least ('+esc(pct(ch[ch.length-1].g))+')':'')+'.');}}
  var best=null;vals.forEach(function(x){var e=rm[x.i]+cm[x.j]-g;if(e>0){var d=(x.v-e)/e;if(!best||Math.abs(d)>Math.abs(best.d))best={x:x,d:d,e:e};}});
  if(best&&Math.abs(best.d)>=.35)out.push('Unusual: '+bold(rl(best.x.r))+' in '+bold(cl(best.x.c))+' is '+bold(pct(best.d*100))+' against what its row and column would predict ('+esc(num(best.e))+'). Worth a look.');
  if(nc>=4){var cv=rk.map(function(r,i){var v=[];for(var j=0;j<nc;j++)if(cellv[i][j]!=null)v.push(cellv[i][j]);var m=v.reduce(function(a,b){return a+b;},0)/(v.length||1),sd=Math.sqrt(v.reduce(function(a,b){return a+(b-m)*(b-m);},0)/(v.length||1));return {r:r,cv:m?sd/Math.abs(m):0};}).sort(function(a,b){return a.cv-b.cv;});
    if(cv.length>=3)out.push('Most steady: '+bold(rl(cv[0].r))+'; most volatile: '+bold(rl(cv[cv.length-1].r))+' (swings of about '+(cv[cv.length-1].cv*100).toFixed(0)+'% around its average).');}
  return out.slice(0,5);}
function tableInsights(list){var out=[],k=S.sortKey==='name'?'y':S.sortKey,id=k==='x'?S.x:k==='size'?S.size:S.y,v=list.filter(function(r){return r[k]!=null;});
  if(v.length<2)return out;v=v.slice().sort(function(a,b){return b[k]-a[k];});var m=median(v.map(function(r){return r[k];}));
  out.push('By '+esc(MLAB[id])+': highest '+bold(dv(S.detail,v[0].k))+' ('+bold(fmt(id,v[0][k]))+'), lowest '+bold(dv(S.detail,v[v.length-1].k))+' ('+esc(fmt(id,v[v.length-1][k]))+'), median '+esc(fmt(id,m))+'.');
  if(v[v.length-1][k]>0&&!isPct(id)&&v.length>=3)out.push('The top item is '+bold((v[0][k]/v[v.length-1][k]).toFixed(1)+'x')+' the bottom one.');
  var above=v.filter(function(r){return r[k]>m*1.5;}).length;if(v.length>=6&&above)out.push(bold(String(above))+' of '+v.length+' sit more than 50% above the median.');
  return out;}

var INS={};
function insightBox(id,items){INS[id]=(items||[]).filter(Boolean);}
function sideHtml(title,items,sub){return '<div class="side-h"><span>What this shows</span><span class="side-sub">'+esc(sub||'')+'</span></div>'+
  (title?'<p class="side-t">'+title+'</p>':'')+(items.length?'<ul class="side-l">'+items.map(function(t){return '<li>'+t+'</li>';}).join('')+'</ul>':'<p class="side-sub">Nothing stands out in this view.</p>');}
// ── controls: filter dropdowns along the top, settings per visual ──
function sel(id,opts,cur){return '<select id="'+id+'">'+opts.map(function(o){return '<option value="'+esc(o[0])+'"'+(String(o[0])===String(cur)?' selected':'')+'>'+esc(o[1])+'</option>';}).join('')+'</select>';}
function dimOpts(withNone,withTime){var o=withNone?[['','(none)']]:[];C.dims.forEach(function(d,i){o.push([i,d.name]);});if(withTime&&T)o.push(['time',T.name]);return o;}
function buildFilters(){var h='';
  if(T)h+='<span class="dd-l">'+esc(T.name)+'</span>'+sel('ex-from',T.labels.map(function(l,i){return [i,l];}),S.from)+'<span class="dd-l">to</span>'+sel('ex-to',T.labels.map(function(l,i){return [i,l];}),S.to);
  C.dims.forEach(function(d,k){if(d.id)return;var n=S.dimSel[k]?S.dimSel[k].size:d.values.length;
    h+='<details class="dd" data-dd="'+k+'"><summary>'+esc(d.name)+(S.dimSel[k]?' <b>'+n+'</b>':'')+'</summary><div class="menu">'+
      '<div class="menu-top"><button class="linkbtn" data-all="'+k+'">'+(S.dimSel[k]?'Select all':'Clear all')+'</button>'+(d.values.length>10?'<input class="find" data-find="'+k+'" placeholder="Search">':'')+'</div>'+
      '<div class="list" id="ex-l'+k+'">'+d.values.map(function(v,i){return '<label class="opt"><input type="checkbox" data-d="'+k+'" value="'+i+'"'+(!S.dimSel[k]||S.dimSel[k].has(i)?' checked':'')+'><span>'+esc(v)+'</span></label>';}).join('')+'</div></div></details>';});
  $('ex-filters').innerHTML=h;
  if(T){$('ex-from').onchange=function(){S.from=Math.min(+this.value,S.to);S.sel=null;S.zoom=null;buildFilters();render();};
    $('ex-to').onchange=function(){S.to=Math.max(+this.value,S.from);S.sel=null;S.zoom=null;buildFilters();render();};}
  $('ex-filters').querySelectorAll('input[data-d]').forEach(function(cb){cb.onchange=function(){var k=+cb.dataset.d;
    var all=$('ex-l'+k).querySelectorAll('input'),on=new Set();all.forEach(function(x){if(x.checked)on.add(+x.value);});
    S.dimSel[k]=on.size===all.length?null:on;S.sel=null;S.zoom=null;var sm=$('ex-filters').querySelector('[data-dd="'+k+'"] summary');
    sm.innerHTML=esc(C.dims[k].name)+(S.dimSel[k]?' <b>'+on.size+'</b>':'');render();};});
  $('ex-filters').querySelectorAll('[data-all]').forEach(function(b){b.onclick=function(){var k=+b.dataset.all,box=$('ex-l'+k).querySelectorAll('input'),
    anyOff=Array.prototype.some.call(box,function(x){return !x.checked;});box.forEach(function(x){x.checked=anyOff;});S.dimSel[k]=anyOff?null:new Set();S.sel=null;buildFilters();render();};});
  $('ex-filters').querySelectorAll('[data-find]').forEach(function(inp){inp.oninput=function(){var q=inp.value.toLowerCase();
    $('ex-l'+inp.dataset.find).querySelectorAll('label').forEach(function(l){l.style.display=l.textContent.toLowerCase().indexOf(q)>=0?'':'none';});};});}
document.addEventListener('click',function(e){document.querySelectorAll('details.dd[open]').forEach(function(d){if(!d.contains(e.target))d.removeAttribute('open');});});
function buildSettings(){var h='',v=S.view,mo=MET.map(function(m){return [m[0],m[1]];}),sizeOpts=MET.filter(function(m){return !/growth/.test(m[0]);}).map(function(m){return [m[0],m[1]];});
  var lab=function(t){return '</span><span class="pair"><span class="dd-l">'+t+'</span>';};
  if(v==='bubble'){var co=[['','(none)'],['quad','Quadrant']].concat(C.dims.map(function(d,i){return [i,d.name];}));
    h=lab('Cut by')+sel('ex-detail',dimOpts(),S.detail)+lab('Y')+sel('ex-y',mo,S.y)+lab('X')+sel('ex-x',mo,S.x)+lab('Size')+sel('ex-size',sizeOpts,S.size)+lab('Colour')+sel('ex-color',co,S.color==null?'':S.color);}
  else if(v==='tree'){var tc=[['group','Group']].concat(mo);
    h=lab('Group by')+sel('ex-tg',dimOpts(true),S.tmGroup==null?'':S.tmGroup)+lab('Cells')+sel('ex-tc',dimOpts(),S.tmCell)+lab('Size')+sel('ex-ts',sizeOpts.filter(function(o){return !/share|avg/.test(o[0]);}),S.tmSize)+lab('Colour')+sel('ex-tcol',tc,S.tmColor);}
  else if(v==='heat'){h=lab('Rows')+sel('ex-hrow',dimOpts(),S.hmRow)+lab('Columns')+sel('ex-hcol',dimOpts(true,true),S.hmCol==null?'':S.hmCol)+lab('Number')+sel('ex-hmm',MS.map(function(m,i){return [i,m.name];}),S.hmM);}
  else{h=lab('Rows')+sel('ex-detail',dimOpts(),S.detail)+'<span class="dd-l">columns follow the bubble chart’s Y, X and size</span>';}
  $('ex-set').innerHTML=('<span class="pair">'+h+'</span>').replace('<span class="pair"></span>','');
  var bind=function(id,key,cast){var el=$(id);if(el)el.onchange=function(){S[key]=cast(this.value);S.zoom=null;S.page=0;if(/detail|hmRow|hmCol|tmGroup|tmCell/.test(key))S.sel=null;render();};};
  var nn=function(v){return v===''?null:+v;};
  bind('ex-detail','detail',Number);bind('ex-color','color',function(v){return v===''?null:v==='quad'?'quad':+v;});bind('ex-x','x',String);bind('ex-y','y',String);bind('ex-size','size',String);
  bind('ex-tg','tmGroup',nn);bind('ex-tc','tmCell',Number);bind('ex-ts','tmSize',String);bind('ex-tcol','tmColor',String);
  bind('ex-hrow','hmRow',Number);bind('ex-hcol','hmCol',function(v){return v===''?null:v;});bind('ex-hmm','hmM',Number);}
function buildTabs(){var tabs=[['bubble','Bubbles'],['tree','Treemap'],['heat','Heatmap'],['table','Table']];
  $('ex-tabs').innerHTML=tabs.map(function(t){return '<button role="tab" aria-selected="'+(S.view===t[0])+'" class="vt'+(S.view===t[0]?' on':'')+'" data-v="'+t[0]+'">'+t[1]+'</button>';}).join('');
  $('ex-tabs').querySelectorAll('[data-v]').forEach(function(b){b.onclick=function(){S.view=b.dataset.v;S.hover=null;buildTabs();buildSettings();render();};});}
// ── filter chips + KPIs ──
function filterWords(){var w=[];C.dims.forEach(function(d,k){if(S.dimSel[k])w.push({k:'d'+k,t:d.name+': '+(S.dimSel[k].size<=3?Array.from(S.dimSel[k]).map(function(i){return d.values[i];}).join(', '):S.dimSel[k].size+' of '+d.values.length)});});
  if(T&&(S.from>0||S.to<T.labels.length-1))w.push({k:'t',t:T.name+': '+T.labels[S.from]+(S.to!==S.from?' to '+T.labels[S.to]:'')});
  if(S.sel){var s=S.sel,t=dimName(s.dim)+' = '+dv(s.dim,s.val);if(s.col!=null)t+=', '+(s.colDim==='time'?T.labels[s.col]:dimName(+s.colDim)+' = '+dv(+s.colDim,s.col));w.push({k:'sel',t:'Selected: '+t});}
  return w;}
function renderBar(){var w=filterWords();
  $('ex-bar').innerHTML=(w.length?w.map(function(x){return '<span class="fchip">'+esc(x.t)+'<button data-x="'+x.k+'" aria-label="Remove">✕</button></span>';}).join('')+'<button class="linkbtn" id="ex-clear">Clear all</button>':'<span class="muted">Showing everything. Click a bubble, block, cell or row to filter.</span>')+
    '<span class="spacer"></span><button class="btn" id="ex-explain">Explain this view</button>';
  $('ex-bar').querySelectorAll('[data-x]').forEach(function(b){b.onclick=function(){var k=b.dataset.x;
    if(k==='sel')S.sel=null;else if(k==='t'){S.from=0;S.to=T.labels.length-1;}else S.dimSel[+k.slice(1)]=null;buildFilters();render();};});
  if($('ex-clear'))$('ex-clear').onclick=function(){S.dimSel=S.dimSel.map(function(){return null;});S.sel=null;S.zoom=null;if(T){S.from=0;S.to=T.labels.length-1;}buildFilters();render();};
  $('ex-explain').onclick=explain;}
function renderKpis(){var rows=C.rows.filter(function(r){return rowOk(r,false);}),all=total(C.rows),a=total(rows),m=S.hmM,mm=MS[m];
  var tot=val(a,'m:'+m+':total'),share=!mm.rate&&all.s[m]?a.s[m]/all.s[m]*100:null,k=[];
  k.push(['In view: '+mm.name+(mm.rate?' (average)':''),num(tot),share!=null?share.toFixed(1)+'% of all data':a.n.toLocaleString('en-GB')+' rows']);
  if(T){var g=val(a,'m:'+m+':growth');if(g!=null)k.push(['Change '+T.labels[S.from]+' to '+T.labels[S.to],pct(g),'first to last period in view',g>0?'up':g<0?'down':'']);}
  var gd=groupBy(rows,function(r){return r[S.detail];}),best=null,bv=-Infinity;gd.forEach(function(x,key){var v=val(x,'m:'+m+':total');if(v!=null&&v>bv){bv=v;best=key;}});
  if(best!=null)k.push(['Top '+dimName(S.detail),dv(S.detail,best),num(bv)]);
  k.push(['Rows in view',a.n.toLocaleString('en-GB'),'of '+all.n.toLocaleString('en-GB')]);
  $('ex-kpis').innerHTML=k.map(function(x){return '<div class="kpi '+(x[3]||'')+'"><div class="k">'+esc(x[0])+'</div><div class="v">'+esc(x[1])+'</div><div class="n">'+esc(x[2])+'</div></div>';}).join('');
  var kin=[];if(share!=null&&share<99.95)kin.push('This view holds '+bold(share.toFixed(1)+'%')+' of all '+esc(mm.name)+'.');
  INS.kpi=kin;}
// ── rich hover card ──
var card=null;
function showCard(e,html){if(!card)card=$('ex-card');card.innerHTML=html;card.hidden=false;
  var x=e.clientX+16,y=e.clientY+16,w=card.offsetWidth,h=card.offsetHeight;if(x+w>window.innerWidth-8)x=e.clientX-w-16;if(y+h>window.innerHeight-8)y=e.clientY-h-16;
  card.style.left=Math.max(4,x)+'px';card.style.top=Math.max(4,y)+'px';}
function hideCard(){if(card)card.hidden=true;}
// ── bubble chart: quadrant verdicts, labels on the distinctive ones, drag to zoom ──
var W=720,H=430,L=62,R=20,TP=40,B=44,BZ=null;
function quadNames(sx,sy){var gr=/growth/.test(sx);return gr?['Big and growing','Big but slower','Small but growing','Small and slower']:['High on both','High '+shortLab(sy)+', low '+shortLab(sx),'Low '+shortLab(sy)+', high '+shortLab(sx),'Low on both'];}
function shortLab(id){var l=MLAB[id]||'';return l.replace(/ \((total|average)\)$/,'').replace(/ per row$/,'').toLowerCase();}
function quadOf(p,mx,my){return p.x>mx?(p.y>my?0:2):(p.y>my?1:3);}
function bubblePoints(){var rows=rowsFor('bubble'),tot=total(rows),g=groupBy(rows,function(r){return r[S.detail];}),pts=[],sx=S.x,sy=S.y,swap='';
  function collect(){pts=[];g.forEach(function(a,key){var x=val(a,sx,tot),y=val(a,sy,tot),s=val(a,S.size,tot);if(x==null||y==null)return;pts.push({k:key,x:x,y:y,s:s==null?0:Math.abs(s),a:a});});}
  collect();
  if(!pts.length&&/growth/.test(sx+sy)){sx=sx.replace('growth','total');sy=sy.replace('growth','total');
    if(sx===sy)sx=MET.map(function(m){return m[0];}).find(function(id){return id!==sy&&!/growth|share/.test(id);})||'rows';
    collect();if(pts.length)swap=' Growth needs at least two periods in view, so this shows '+MLAB[sx]+' instead.';}
  var mx=pts.length>=4?median(pts.map(function(p){return p.x;})):null,my=pts.length>=4?median(pts.map(function(p){return p.y;})):null;
  pts.forEach(function(p){p.q=mx==null?-1:quadOf(p,mx,my);});
  return {pts:pts,sx:sx,sy:sy,swap:swap,mx:mx,my:my,tot:tot};}
function renderBubble(){var P=bubblePoints(),pts=P.pts,sx=P.sx,sy=P.sy,svg=$('ex-bsvg'),empty=$('ex-bempty');
  $('ex-vtitle').textContent=MLAB[sy]+' against '+MLAB[sx]+', by '+dimName(S.detail);
  $('ex-vhint').textContent='Size is '+MLAB[S.size]+'. Click a bubble or a ranked row to filter; drag across the chart to zoom.'+P.swap;
  var vis=pts;if(S.zoom){var z=S.zoom;vis=pts.filter(function(p){return p.x>=z.x0&&p.x<=z.x1&&p.y>=z.y0&&p.y<=z.y1;});}
  $('ex-zoom').hidden=!S.zoom;
  if(!vis.length){INS.bubble=[];svg.innerHTML='';svg.style.display='none';empty.style.display='';$('ex-vlegend').innerHTML='';renderRank(P,[]);return;}
  svg.style.display='';empty.style.display='none';
  var ext=function(f){return [Math.min.apply(null,vis.map(f)),Math.max.apply(null,vis.map(f))];},ex=ext(function(p){return p.x;}),ey=ext(function(p){return p.y;});
  if(S.zoom){ex=[S.zoom.x0,S.zoom.x1];ey=[S.zoom.y0,S.zoom.y1];}
  var xt=ticks(ex[0],ex[1],6),yt=ticks(ey[0],ey[1],6);
  var X=function(v){return L+(v-xt[0])/((xt[xt.length-1]-xt[0])||1)*(W-L-R);},Y=function(v){return H-B-(v-yt[0])/((yt[yt.length-1]-yt[0])||1)*(H-B-TP);};
  BZ={xt:xt,yt:yt};
  var smax=Math.max.apply(null,pts.map(function(p){return p.s;}))||1,rmax=Math.max(10,Math.min(30,150/Math.sqrt(vis.length||1))),rmin=vis.length>60?3:5;
  var Rr=function(s){return rmin+Math.sqrt(s/smax)*(rmax-rmin);};
  var h='';yt.forEach(function(t){h+='<line class="grid" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text class="tick" x="'+(L-8)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(sy,t))+'</text>';});
  xt.forEach(function(t){h+='<text class="tick" x="'+X(t)+'" y="'+(H-B+18)+'" text-anchor="middle">'+esc(fmt(sx,t))+'</text>';});
  h+='<text class="axislab" x="'+(W-R)+'" y="'+(H-6)+'" text-anchor="end">'+esc(MLAB[sx])+' →</text><text class="axislab" x="'+L+'" y="'+(TP-22)+'">↑ '+esc(MLAB[sy])+'</text>';
  var qn=quadNames(sx,sy);
  if(P.mx!=null){var qx=X(P.mx),qy=Y(P.my);if(qx>L&&qx<W-R)h+='<line class="quad" x1="'+qx+'" x2="'+qx+'" y1="'+TP+'" y2="'+(H-B)+'"/>';if(qy>TP&&qy<H-B)h+='<line class="quad" x1="'+L+'" x2="'+(W-R)+'" y1="'+qy+'" y2="'+qy+'"/>';
    h+='<text class="qlab" x="'+(W-R-4)+'" y="'+(TP+12)+'" text-anchor="end">'+esc(qn[0].toUpperCase())+'</text><text class="qlab" x="'+(L+6)+'" y="'+(TP+12)+'">'+esc(qn[1].toUpperCase())+'</text>'+
       '<text class="qlab" x="'+(W-R-4)+'" y="'+(H-B-6)+'" text-anchor="end">'+esc(qn[2].toUpperCase())+'</text><text class="qlab" x="'+(L+6)+'" y="'+(H-B-6)+'">'+esc(qn[3].toUpperCase())+'</text>';}
  var selKey=S.sel&&S.sel.src==='bubble'?S.sel.val:null;
  vis.sort(function(a,b){return b.s-a.s;});
  // label the distinctive: biggest, the extremes on each axis, and the selected one
  var want=new Set(),by=function(f){return vis.slice().sort(function(a,b){return f(b)-f(a);});};
  by(function(p){return p.s;}).slice(0,4).forEach(function(p){want.add(p.k);});
  [function(p){return p.y;},function(p){return -p.y;},function(p){return p.x;},function(p){return -p.x;}].forEach(function(f){by(f).slice(0,2).forEach(function(p){want.add(p.k);});});
  if(selKey!=null)want.add(selKey);
  var circles='',labels='',placed=[];
  vis.forEach(function(p){var r=Rr(p.s),cx=X(p.x),cy=Y(p.y),cls='b'+(selKey===p.k?' on':selKey!=null?' dim':'');
    var fill=S.color==='quad'?(p.q>=0?QCOL[p.q]:SERIES[0]):colorOf(p.a);
    circles+='<circle class="'+cls+'" data-k="'+p.k+'" cx="0" cy="0" r="'+r.toFixed(1)+'" style="transform:translate('+cx.toFixed(1)+'px,'+cy.toFixed(1)+'px);fill:'+fill+'"/>';});
  vis.filter(function(p){return want.has(p.k);}).slice(0,14).forEach(function(p){var name=dv(S.detail,p.k),cx=X(p.x),ly=Y(p.y)-Rr(p.s)-4;
    if(placed.some(function(q){return Math.abs(q[0]-cx)<64&&Math.abs(q[1]-ly)<12;}))return;placed.push([cx,ly]);
    labels+='<text class="blab" x="'+cx.toFixed(1)+'" y="'+ly.toFixed(1)+'" text-anchor="middle">'+esc(name.length>18?name.slice(0,17)+'…':name)+'</text>';});
  svg.innerHTML=h+'<g>'+circles+'</g>'+labels+'<rect id="ex-zr" class="zr" x="0" y="0" width="0" height="0"/>';
  var byK={};pts.forEach(function(p){byK[p.k]=p;});
  svg.querySelectorAll('circle.b').forEach(function(c){var p=byK[+c.dataset.k];
    c.onclick=function(e){e.stopPropagation();select('bubble',S.detail,p.k,null,null);};
    c.onmousemove=function(e){showCard(e,bubbleCard(p,P));};c.onmouseenter=function(){S.hover={view:'bubble',k:p.k};renderSide();hiRank(p.k);};
    c.onmouseleave=function(){hideCard();S.hover=null;renderSide();hiRank(null);};});
  var leg='';
  if(S.color==='quad'&&P.mx!=null)leg=qn.map(function(n,i){return '<span><i class="sw" style="background:'+QCOL[i]+'"></i>'+esc(n)+'</span>';}).join('');
  else if(S.color!=null&&S.color!=='quad'&&C.dims[S.color].values.length<=8)leg=C.dims[S.color].values.map(function(v,i){return '<span><i class="sw" style="background:'+SERIES[i]+'"></i>'+esc(v)+'</span>';}).join('');
  $('ex-vlegend').innerHTML=leg;
  INS.bubble=bubbleInsights(vis,P.mx||0,P.my||0,sx,sy).map(function(t){if(S.color!=='quad')return t;
    return t.replace(/<span class="tag">([^<]+)<\/span>/,function(m0,q){var i=qn.indexOf(q);return i<0?m0:'<span class="tag" style="background:'+QCOL[i]+';color:#fff">'+q+'</span>';});});
  zoomable(svg,X,Y);
  renderRank(P,pts);}
function bubbleCard(p,P){var name=dv(S.detail,p.k),rank=P.pts.slice().sort(function(a,b){return b.y-a.y;}).findIndex(function(q){return q.k===p.k;})+1;
  return '<div class="cv">'+esc(fmt(P.sy,p.y))+' <span>'+esc(shortLab(P.sy))+'</span></div><div class="cn">'+esc(name)+'</div>'+
    '<div class="cm">'+esc(MLAB[P.sx])+' '+esc(fmt(P.sx,p.x))+' · '+esc(MLAB[S.size])+' '+esc(fmt(S.size,p.s))+'</div>'+
    (p.q>=0?'<div class="cq"><i class="sw" style="background:'+QCOL[p.q]+'"></i>'+esc(quadNames(P.sx,P.sy)[p.q])+'</div>':'')+
    '<div class="cm">#'+rank+' of '+P.pts.length+' on '+esc(shortLab(P.sy))+'</div><div class="cf">Click to filter →</div>';}
function zoomable(svg,X,Y){var start=null,zr=$('ex-zr');
  function pt(e){var b=svg.getBoundingClientRect();return [(e.clientX-b.left)*W/b.width,(e.clientY-b.top)*H/b.height];}
  svg.onmousedown=function(e){if(e.target.tagName==='circle')return;start=pt(e);e.preventDefault();};
  svg.onmousemove=function(e){if(!start)return;var p=pt(e),x=Math.min(p[0],start[0]),y=Math.min(p[1],start[1]);
    zr.setAttribute('x',x);zr.setAttribute('y',y);zr.setAttribute('width',Math.abs(p[0]-start[0]));zr.setAttribute('height',Math.abs(p[1]-start[1]));};
  svg.onmouseup=function(e){if(!start)return;var p=pt(e),a=start;start=null;zr.setAttribute('width',0);
    if(Math.abs(p[0]-a[0])<12||Math.abs(p[1]-a[1])<12)return;
    var xt=BZ.xt,yt=BZ.yt,ix=function(px){return xt[0]+(px-L)/(W-L-R)*(xt[xt.length-1]-xt[0]);},iy=function(py){return yt[0]+(H-B-py)/(H-B-TP)*(yt[yt.length-1]-yt[0]);};
    S.zoom={x0:ix(Math.min(p[0],a[0])),x1:ix(Math.max(p[0],a[0])),y0:iy(Math.max(p[1],a[1])),y1:iy(Math.min(p[1],a[1]))};renderBubble();renderSide();};
  svg.onmouseleave=function(){if(start){start=null;zr.setAttribute('width',0);}};}
// ── ranked list beside the bubbles ──
var PER=15;
function renderRank(P,pts){var box=$('ex-rank');if(!box)return;var list=pts.slice().sort(function(a,b){return b.y-a.y;}),n=list.length,pages=Math.max(1,Math.ceil(n/PER));
  if(S.page>=pages)S.page=pages-1;var selKey=S.sel&&(S.sel.src==='bubble'||S.sel.src==='rank')&&S.sel.dim===S.detail?S.sel.val:null;
  var h='<div class="rk-h"><span>Ranked by '+esc(shortLab(P.sy))+'</span><span>'+n+'</span></div><table class="rk"><thead><tr><th>#</th><th>'+esc(dimName(S.detail))+'</th><th>'+esc(shortLab(P.sy))+'</th><th>'+esc(shortLab(S.size)||'size')+'</th></tr></thead><tbody>';
  list.slice(S.page*PER,S.page*PER+PER).forEach(function(p,i){var fill=S.color==='quad'?(p.q>=0?QCOL[p.q]:SERIES[0]):colorOf(p.a);
    h+='<tr class="r'+(selKey===p.k?' on':'')+'" data-k="'+p.k+'"><td>'+(S.page*PER+i+1)+'</td><td><i class="dot" style="background:'+fill+'"></i>'+esc(dv(S.detail,p.k))+'</td><td>'+esc(fmt(P.sy,p.y))+'</td><td>'+esc(fmt(S.size,p.s))+'</td></tr>';});
  h+='</tbody></table>';
  if(pages>1)h+='<div class="rk-p"><button class="linkbtn" data-pg="-1"'+(S.page?'':' disabled')+'>‹</button>'+(S.page*PER+1)+'–'+Math.min(n,S.page*PER+PER)+' of '+n+'<button class="linkbtn" data-pg="1"'+(S.page<pages-1?'':' disabled')+'>›</button></div>';
  box.innerHTML=h;
  box.querySelectorAll('[data-pg]').forEach(function(b){b.onclick=function(){S.page+=+b.dataset.pg;renderRank(P,pts);};});
  box.querySelectorAll('tr.r').forEach(function(tr){tr.onclick=function(){select('bubble',S.detail,+tr.dataset.k,null,null);};
    tr.onmouseenter=function(){hiBubble(+tr.dataset.k);S.hover={view:'bubble',k:+tr.dataset.k};renderSide();};tr.onmouseleave=function(){hiBubble(null);S.hover=null;renderSide();};});}
function hiRank(k){var box=$('ex-rank');if(!box)return;box.querySelectorAll('tr.r').forEach(function(tr){tr.classList.toggle('hi',k!=null&&+tr.dataset.k===k);});}
function hiBubble(k){$('ex-bsvg').querySelectorAll('circle.b').forEach(function(c){c.classList.toggle('hi',k!=null&&+c.dataset.k===k);});}
// ── treemap: blocks grouped by one category, cells sized by an amount and coloured by a score ──
function squarify(items,x,y,w,h){var out=[],rest=items.filter(function(it){return it.v>0;}).sort(function(a,b){return b.v-a.v;}),sum=rest.reduce(function(a,b){return a+b.v;},0);
  if(!sum||w<=0||h<=0)return out;var sc=w*h/sum;rest.forEach(function(it){it.a=it.v*sc;});var row=[];
  function worst(r,len){if(!r.length)return Infinity;var s=0,mx=0,mn=Infinity;r.forEach(function(it){s+=it.a;mx=Math.max(mx,it.a);mn=Math.min(mn,it.a);});return Math.max(len*len*mx/(s*s),s*s/(len*len*mn));}
  function lay(){var s=row.reduce(function(a,b){return a+b.a;},0);if(w>=h){var cw=s/h,cy=y;row.forEach(function(it){var rh=it.a/cw;out.push({it:it,x:x,y:cy,w:cw,h:rh});cy+=rh;});x+=cw;w-=cw;}
    else{var rh2=s/w,cx=x;row.forEach(function(it){var rw=it.a/rh2;out.push({it:it,x:cx,y:y,w:rw,h:rh2});cx+=rw;});y+=rh2;h-=rh2;}row=[];}
  while(rest.length){var len=Math.min(w,h),c=rest[0];if(!row.length||worst(row.concat([c]),len)<=worst(row,len)){row.push(c);rest.shift();}else lay();}
  if(row.length)lay();return out;}
var TM=null;
function treeData(){var rows=rowsFor('tree'),tot=total(rows),gd=S.tmGroup,cd=S.tmCell;if(gd===cd)gd=null;
  var groups=new Map();rows.forEach(function(r){var g=gd==null?0:r[gd],c=r[cd];if(!groups.has(g))groups.set(g,{k:g,a:acc(),cells:new Map()});var G=groups.get(g);add(G.a,r);
    if(!G.cells.has(c))G.cells.set(c,acc());add(G.cells.get(c),r);});
  var gl=[],all=[];groups.forEach(function(G){var cells=[];G.cells.forEach(function(a,k){var v=val(a,S.tmSize,tot),col=S.tmColor==='group'?null:val(a,S.tmColor,tot);cells.push({k:k,a:a,v:Math.max(v||0,0),c:col,g:G.k});});
    cells.forEach(function(x){all.push(x);});gl.push({k:G.k,a:G.a,v:Math.max(val(G.a,S.tmSize,tot)||0,0),cells:cells,c:S.tmColor==='group'?null:val(G.a,S.tmColor,tot)});});
  var cv=all.map(function(x){return x.c;}).filter(function(v){return v!=null&&isFinite(v);});
  return {gd:gd,cd:cd,groups:gl,cells:all,tot:tot,sum:gl.reduce(function(a,g){return a+g.v;},0),lo:cv.length?Math.min.apply(null,cv):0,hi:cv.length?Math.max.apply(null,cv):1,cmed:median(cv)};}
function renderTree(){var box=$('ex-tree'),M=treeData();TM=M;
  var gname=function(g){return M.gd==null?'All':dv(M.gd,g.k);};
  $('ex-vtitle').textContent=MLAB[S.tmSize]+' by '+(M.gd==null?'':dimName(M.gd)+' and ')+dimName(M.cd);
  $('ex-vhint').textContent=(S.tmColor==='group'?'Colour shows the group. ':'Colour shows '+shortLab(S.tmColor)+', low to high along the scale below. ')+'Hover a block for detail; click it to filter.';
  var W2=box.clientWidth||700,H2=Math.max(360,Math.min(560,Math.round(W2*.62)));box.style.height=H2+'px';
  if(!M.sum){box.innerHTML='<div class="ex-empty">Nothing to size for these filters (sizes must be positive).</div>';INS.tree=[];return;}
  var rects=squarify(M.groups.map(function(g){return {v:g.v,g:g};}),0,0,W2,H2),h='',selKey=S.sel&&S.sel.src==='tree'?S.sel:null;
  var stepOf=function(v){return v==null?null:Math.round((v-M.lo)/((M.hi-M.lo)||1)*(RAMP.length-1));};
  rects.forEach(function(R0,gi){var g=R0.it.g,head=M.gd==null?0:Math.min(22,R0.h*.3),inner=squarify(g.cells.map(function(c){return {v:c.v,c:c};}),R0.x+1,R0.y+head+1,R0.w-2,R0.h-head-2);
    if(head)h+='<div class="tg" data-g="'+g.k+'" style="left:'+R0.x+'px;top:'+R0.y+'px;width:'+R0.w+'px;height:'+head+'px"><span>'+esc(gname(g))+'</span><span>'+esc(num(g.v))+(M.sum?' · '+Math.round(g.v/M.sum*100)+'%':'')+'</span></div>';
    inner.forEach(function(r){var c=r.it.c,st=stepOf(c.c),bg=S.tmColor==='group'?SERIES[gi%SERIES.length]:st==null?'var(--grid)':'var(--r'+st+')',ink=S.tmColor==='group'?'#fff':st==null?'var(--ink)':'var(--ri'+st+')';
      var on=selKey&&selKey.dim===M.cd&&selKey.val===c.k&&(M.gd==null||selKey.col==null||selKey.col===c.g),dim=selKey&&!on;
      var big=r.w>58&&r.h>30,name=dv(M.cd,c.k);
      h+='<div class="tc'+(on?' on':'')+(dim?' dim':'')+'" data-g="'+c.g+'" data-k="'+c.k+'" style="left:'+r.x+'px;top:'+r.y+'px;width:'+Math.max(r.w-1,0)+'px;height:'+Math.max(r.h-1,0)+'px;background:'+bg+';color:'+ink+'">'+
        (big?'<b>'+esc(name)+'</b><span>'+esc(num(c.v))+' · '+(M.sum?(c.v/M.sum*100).toFixed(1)+'%':'')+'</span>':r.w>28&&r.h>16?'<em>'+esc(name.slice(0,4))+'</em>':'')+'</div>';});});
  box.innerHTML=h;
  var byKey={};M.cells.forEach(function(c){byKey[c.g+'|'+c.k]=c;});var gByK={};M.groups.forEach(function(g){gByK[g.k]=g;});
  box.querySelectorAll('.tc').forEach(function(el){var c=byKey[el.dataset.g+'|'+el.dataset.k];
    el.onmousemove=function(e){showCard(e,treeCard(c,M));};el.onmouseenter=function(){S.hover={view:'tree',g:c.g,k:c.k};renderSide();};
    el.onmouseleave=function(){hideCard();S.hover=null;renderSide();};
    el.onclick=function(){if(M.gd==null)select('tree',M.cd,c.k,null,null);else select('tree',M.cd,c.k,c.g,String(M.gd));};});
  box.querySelectorAll('.tg').forEach(function(el){var g=gByK[+el.dataset.g];el.onmouseenter=function(){S.hover={view:'tree',g:g.k};renderSide();};el.onmouseleave=function(){S.hover=null;renderSide();};
    el.onclick=function(){select('tree',M.gd,g.k,null,null);};});
  $('ex-vlegend').innerHTML=S.tmColor==='group'?'':'<span class="ramp"><span class="rl">'+esc(fmt(S.tmColor,M.lo))+'</span>'+RAMP.map(function(c,k){return '<i style="background:var(--r'+k+')"></i>';}).join('')+'<span class="rl">'+esc(fmt(S.tmColor,M.hi))+'</span></span><span>'+esc(MLAB[S.tmColor])+'</span>';
  INS.tree=treeInsights(M,null);}
function treeCard(c,M){var g=M.groups.find(function(x){return x.k===c.g;});
  return '<div class="cv">'+esc(num(c.v))+' <span>'+esc(shortLab(S.tmSize))+'</span></div><div class="cn">'+esc(dv(M.cd,c.k))+'</div>'+
    '<div class="cm">'+(M.gd!=null?esc(dv(M.gd,c.g))+' · ':'')+(M.sum?(c.v/M.sum*100).toFixed(1)+'% of the view':'')+(g&&g.v&&M.gd!=null?' · '+(c.v/g.v*100).toFixed(0)+'% of its group':'')+'</div>'+
    (c.c!=null?'<div class="cm">'+esc(MLAB[S.tmColor])+' <b>'+esc(fmt(S.tmColor,c.c))+'</b> (median '+esc(fmt(S.tmColor,M.cmed))+')</div>':'')+'<div class="cf">Click to filter →</div>';}
function treeInsights(M,hov){var out=[],gn=function(g){return M.gd==null?'All':dv(M.gd,g.k);},sl=shortLab(S.tmSize),cl=S.tmColor==='group'?null:shortLab(S.tmColor);
  var gs=M.groups.slice().sort(function(a,b){return b.v-a.v;});
  if(hov&&hov.g!=null&&M.gd!=null){var g=gs.find(function(x){return x.k===hov.g;});if(!g)return out;
    var cs=g.cells.slice().sort(function(a,b){return b.v-a.v;}),rank=gs.indexOf(g)+1;
    out.push(bold(gn(g))+': '+bold(num(g.v))+' '+esc(sl)+' across '+cs.length+' '+esc(dimName(M.cd))+' values, '+(M.sum?(g.v/M.sum*100).toFixed(0)+'% of the view':'')+(rank===1?' — the largest of '+gs.length+' groups.':', '+ordinal(rank)+' of '+gs.length+'.'));
    if(cs.length)out.push(bold(dv(M.cd,cs[0].k))+' is its largest: '+esc(num(cs[0].v))+', '+(g.v?(cs[0].v/g.v*100).toFixed(0):0)+'% of the group'+(cs[1]?'; then '+bold(dv(M.cd,cs[1].k))+' at '+(g.v?(cs[1].v/g.v*100).toFixed(0):0)+'%':'')+'.');
    if(cs.length>=4){var t3=cs.slice(0,3).reduce(function(a,b){return a+b.v;},0);out.push('Its 3 largest hold '+bold((g.v?t3/g.v*100:0).toFixed(0)+'%')+' of it; '+cs.length+' in all.');}
    if(cl){var gc=g.cells.map(function(c){return c.c;}).filter(function(v){return v!=null;}),gm=median(gc);
      out.push('Its median '+esc(cl)+' is '+bold(fmt(S.tmColor,gm))+' against '+esc(fmt(S.tmColor,M.cmed))+' for the whole view.');
      var meds=gs.map(function(x){return {k:x.k,m:median(x.cells.map(function(c){return c.c;}).filter(function(v){return v!=null;}))};}).sort(function(a,b){return b.m-a.m;});
      var pos=meds.findIndex(function(x){return x.k===g.k;})+1;if(meds.length>1)out.push('Among the '+meds.length+' groups it ranks '+bold(ordinal(pos))+' by median '+esc(cl)+'.');
      var ch=g.cells.filter(function(c){return c.c!=null;}).sort(function(a,b){return b.c-a.c;});if(ch.length>=2)out.push(bold(dv(M.cd,ch[0].k))+' has the group’s highest '+esc(cl)+': '+esc(fmt(S.tmColor,ch[0].c))+'; '+esc(dv(M.cd,ch[ch.length-1].k))+' its lowest: '+esc(fmt(S.tmColor,ch[ch.length-1].c))+'.');}
    return out.slice(0,6);}
  if(M.gd!=null&&gs.length>1)out.push(bold(gn(gs[0]))+' is the largest group: '+bold(num(gs[0].v))+' '+esc(sl)+', '+(M.sum?(gs[0].v/M.sum*100).toFixed(0):0)+'% of the view'+(gs[1]?'; then '+esc(gn(gs[1]))+' at '+(M.sum?(gs[1].v/M.sum*100).toFixed(0):0)+'%':'')+'.');
  var cs2=M.cells.slice().sort(function(a,b){return b.v-a.v;});
  if(cs2.length)out.push(bold(dv(M.cd,cs2[0].k))+(M.gd!=null?' ('+esc(dv(M.gd,cs2[0].g))+')':'')+' is the biggest block: '+bold(num(cs2[0].v))+', '+(M.sum?(cs2[0].v/M.sum*100).toFixed(1):0)+'%.');
  if(cs2.length>=6){var k80=0,acc80=0;while(k80<cs2.length&&acc80<.8*M.sum){acc80+=cs2[k80].v;k80++;}out.push(bold(k80+' of '+cs2.length)+' blocks make 80% of '+esc(sl)+'.');}
  if(cl){var cc=M.cells.filter(function(c){return c.c!=null;}).sort(function(a,b){return b.c-a.c;});
    if(cc.length>=2)out.push('Highest '+esc(cl)+': '+bold(dv(M.cd,cc[0].k))+' ('+esc(fmt(S.tmColor,cc[0].c))+'); lowest: '+esc(dv(M.cd,cc[cc.length-1].k))+' ('+esc(fmt(S.tmColor,cc[cc.length-1].c))+').');
    var big=cs2.slice(0,Math.max(3,Math.round(cs2.length/4))),bm=median(big.map(function(c){return c.c;}).filter(function(v){return v!=null;}));
    if(cc.length>=8)out.push('The biggest blocks have a median '+esc(cl)+' of '+bold(fmt(S.tmColor,bm))+' against '+esc(fmt(S.tmColor,M.cmed))+' overall: '+(bm>M.cmed*1.05?'size goes with higher '+esc(cl)+'.':bm<M.cmed*.95?'the big ones score lower.':'size makes little difference.'));}
  if(M.gd!=null)out.push('<span class="muted">Hover a group heading or block for its own story.</span>');
  return out.slice(0,6);}
function ordinal(n){var s=['th','st','nd','rd'],v=n%100;return n+(s[(v-20)%10]||s[v]||s[0]);}
// ── table ──
function renderTable(){var rows=rowsFor('table'),tot=total(rows),g=groupBy(rows,function(r){return r[S.detail];}),cols=[['x',S.x],['y',S.y],['size',S.size]],list=[];
  g.forEach(function(a,k){list.push({k:k,x:val(a,S.x,tot),y:val(a,S.y,tot),size:val(a,S.size,tot)});});
  var key=S.sortKey;list.sort(function(a,b){if(key==='name')return S.sortDir*String(dv(S.detail,a.k)).localeCompare(dv(S.detail,b.k));return S.sortDir*((a[key]==null?-Infinity:a[key])-(b[key]==null?-Infinity:b[key]));});
  var top=Math.max.apply(null,list.map(function(x){return Math.abs(x.y)||0;}))||1,selKey=S.sel&&S.sel.src==='table'?S.sel.val:null;
  var arrow=function(k){return S.sortKey===k?(S.sortDir<0?' ▼':' ▲'):'';};
  $('ex-vtitle').textContent=dimName(S.detail)+' table';$('ex-vhint').textContent='Click a column to sort, a row to filter.';$('ex-vlegend').innerHTML='';
  var h='<table class="extbl"><thead><tr><th data-s="name">'+esc(dimName(S.detail))+arrow('name')+'</th>'+cols.map(function(c){return '<th data-s="'+c[0]+'">'+esc(MLAB[c[1]])+arrow(c[0])+'</th>';}).join('')+'</tr></thead><tbody>';
  list.slice(0,200).forEach(function(r){h+='<tr class="r'+(selKey===r.k?' on':'')+'" data-k="'+r.k+'"><td>'+esc(dv(S.detail,r.k))+'</td><td>'+esc(fmt(S.x,r.x))+'</td><td><span class="bar" style="width:'+Math.max(2,Math.abs(r.y||0)/top*60).toFixed(0)+'px"></span>'+esc(fmt(S.y,r.y))+'</td><td>'+esc(fmt(S.size,r.size))+'</td></tr>';});
  INS.table=tableInsights(list);
  $('ex-table').innerHTML=h+'</tbody></table>'+(list.length>200?'<p class="hint">Top 200 of '+list.length+'.</p>':'');
  $('ex-table').querySelectorAll('th[data-s]').forEach(function(th){th.onclick=function(){var k=th.dataset.s;S.sortDir=S.sortKey===k?-S.sortDir:-1;S.sortKey=k;renderTable();renderSide();};});
  $('ex-table').querySelectorAll('tr.r').forEach(function(tr){tr.onclick=function(){select('table',S.detail,+tr.dataset.k,null,null);};});}
function renderHeat(){var box=$('ex-hm');$('ex-vhint').textContent='';
  if(S.hmCol==null){insightBox('ex-hins',[]);box.innerHTML='<div class="ex-empty">Pick a column for the heatmap (a date or a second category).</div>';$('ex-vtitle').textContent='Heatmap';$('ex-vlegend').innerHTML='';return;}
  var rows=rowsFor('heat'),m=S.hmM,mm=MS[m],rd=S.hmRow,cd=S.hmCol,isT=cd==='time';
  if(!isT&&+cd===rd){box.innerHTML='<div class="ex-empty">Rows and columns are the same; pick another column.</div>';return;}
  var cols=isT?T.labels.map(function(l,i){return i;}).filter(function(i){return i>=S.from&&i<=S.to;}):C.dims[+cd].values.map(function(v,i){return i;});
  var cell=new Map(),rt=new Map();rows.forEach(function(r){var key=r[rd]+'|'+(isT?r[ND]:r[+cd]);if(!cell.has(key))cell.set(key,acc());add(cell.get(key),r);
    if(!rt.has(r[rd]))rt.set(r[rd],acc());add(rt.get(r[rd]),r);});
  var rkeys=Array.from(rt.keys()).sort(function(a,b){return (val(rt.get(b),'m:'+m+':total')||0)-(val(rt.get(a),'m:'+m+':total')||0);}).slice(0,25);
  if(!isT){var ct=new Map();rows.forEach(function(r){var c=r[+cd];ct.set(c,(ct.get(c)||0)+r[ND+2+2*m]);});cols=cols.filter(function(c){return ct.has(c);}).sort(function(a,b){return ct.get(b)-ct.get(a);}).slice(0,24);}
  var vals=[];rkeys.forEach(function(rk){cols.forEach(function(c){var a=cell.get(rk+'|'+c);if(a){var v=val(a,'m:'+m+':total');if(v!=null)vals.push(v);}});});
  var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);
  $('ex-vtitle').textContent=mm.name+(mm.rate?' (average)':'')+' by '+dimName(rd)+' and '+(isT?T.name:dimName(+cd));
  var cellv=rkeys.map(function(rk){return cols.map(function(c){var a=cell.get(rk+'|'+c);return a?val(a,'m:'+m+':total'):null;});});
  insightBox('ex-hins',heatInsights(rkeys,cols,cellv,isT,cd,rd));
  var s=S.sel,h='<table><thead><tr><th class="rh">'+esc(dimName(rd))+'</th>'+cols.map(function(c){return '<th>'+esc(isT?T.labels[c]:dv(+cd,c))+'</th>';}).join('')+'<th>Total</th></tr></thead><tbody>';
  rkeys.forEach(function(rk){var rowDim=s&&s.src==='heat'&&s.col==null&&s.val!==rk;
    h+='<tr><td class="rh'+(rowDim?' dim':'')+'" data-r="'+rk+'">'+esc(dv(rd,rk))+'</td>';
    cols.forEach(function(c){var a=cell.get(rk+'|'+c),v=a?val(a,'m:'+m+':total'):null;
      if(v==null){h+='<td class="c e"></td>';return;}
      var st=Math.round((v-lo)/((hi-lo)||1)*(RAMP.length-1)),on=s&&s.src==='heat'&&s.val===rk&&s.col===c,dim=s&&s.src==='heat'&&!on&&!(s.col==null&&s.val===rk);
      h+='<td class="c'+(on?' on':'')+(dim?' dim':'')+'" data-r="'+rk+'" data-c="'+c+'" style="background:var(--r'+st+');color:var(--ri'+st+')" data-tip="'+esc(dv(rd,rk)+' · '+(isT?T.labels[c]:dv(+cd,c))+': '+num(v))+'">'+(cols.length<=12?esc(num(v)):'')+'</td>';});
    h+='<td class="tot">'+esc(num(val(rt.get(rk),'m:'+m+':total')))+'</td></tr>';});
  box.innerHTML=h+'</tbody></table>';
  $('ex-vlegend').innerHTML='<span class="ramp">'+esc(num(lo))+' '+RAMP.map(function(c,k){return '<i style="background:var(--r'+k+')"></i>';}).join('')+' '+esc(num(hi))+'</span><span>Click a cell or a row name to filter the other visuals.</span>';
  box.querySelectorAll('td.c[data-c]').forEach(function(td){td.onclick=function(){select('heat',rd,+td.dataset.r,+td.dataset.c,isT?'time':String(+cd));};});
  box.querySelectorAll('td.rh').forEach(function(td){td.onclick=function(){select('heat',rd,+td.dataset.r,null,null);};});}

// ── side panel: the story for the visual on screen, or for what the pointer is on ──
function renderSide(){var v=S.view,items=[],title='',sub='';
  if(v==='bubble'){var hv=S.hover&&S.hover.view==='bubble'?S.hover.k:null;
    if(hv!=null){var P=bubblePoints(),p=P.pts.find(function(q){return q.k===hv;});if(p)items=pointStory(p,P);title=bold(dv(S.detail,hv));}
    else{items=INS.bubble||[];sub=(INS.bubble||[]).length+' notes';}}
  else if(v==='tree'){if(S.hover&&S.hover.view==='tree'&&TM){if(S.hover.k!=null){var c=TM.cells.find(function(x){return x.g===S.hover.g&&x.k===S.hover.k;});items=c?cellStory(c,TM):[];title=bold(dv(TM.cd,S.hover.k));}
      else{items=treeInsights(TM,S.hover);title=TM.gd!=null?bold(dv(TM.gd,S.hover.g)):'';}}else items=INS.tree||[];
    sub=TM?TM.cells.length+' blocks':'';}
  else if(v==='heat')items=INS['ex-hins']||[];
  else items=INS.table||[];
  items=(INS.kpi||[]).concat(items);
  $('ex-side').innerHTML=sideHtml(title,items,sub);}
function pointStory(p,P){var out=[],n=P.pts.length,ys=P.pts.slice().sort(function(a,b){return b.y-a.y;}),xs=P.pts.slice().sort(function(a,b){return b.x-a.x;});
  var ry=ys.findIndex(function(q){return q.k===p.k;})+1,rx=xs.findIndex(function(q){return q.k===p.k;})+1;
  out.push(esc(MLAB[P.sy])+' '+bold(fmt(P.sy,p.y))+': '+bold(ordinal(ry))+' of '+n+'.');
  out.push(esc(MLAB[P.sx])+' '+bold(fmt(P.sx,p.x))+': '+ordinal(rx)+' of '+n+(P.mx!=null?', '+(p.x>P.mx?'above':'below')+' the median of '+esc(fmt(P.sx,P.mx)):'')+'.');
  if(p.q>=0)out.push('Sits in <span class="tag" style="background:'+QCOL[p.q]+';color:#fff">'+esc(quadNames(P.sx,P.sy)[p.q])+'</span>.');
  var sumable=/:total$/.test(P.sy)&&!MS[+P.sy.split(':')[1]].rate;if(sumable){var s=P.pts.reduce(function(a,q){return a+Math.max(q.y,0);},0);if(s)out.push('Holds '+bold((p.y/s*100).toFixed(1)+'%')+' of '+esc(shortLab(P.sy))+' in view.');}
  if(T&&!/growth/.test(P.sx+P.sy)){var gv=val(p.a,'m:'+S.hmM+':growth');if(gv!=null)out.push(esc(MS[S.hmM].name)+' changed '+bold(pct(gv))+' from '+esc(T.labels[S.from])+' to '+esc(T.labels[S.to])+'.');}
  return out;}
function cellStory(c,M){var out=[],cs=M.cells.slice().sort(function(a,b){return b.v-a.v;}),r=cs.indexOf(c)+1;
  out.push(bold(num(c.v))+' '+esc(shortLab(S.tmSize))+': '+bold(ordinal(r))+' of '+cs.length+' blocks, '+(M.sum?(c.v/M.sum*100).toFixed(1):0)+'% of the view.');
  if(M.gd!=null){var g=M.groups.find(function(x){return x.k===c.g;}),gc=g.cells.slice().sort(function(a,b){return b.v-a.v;});out.push(ordinal(gc.indexOf(c)+1)+' of '+gc.length+' in '+bold(dv(M.gd,c.g))+', '+(g.v?(c.v/g.v*100).toFixed(0):0)+'% of the group.');}
  if(c.c!=null){var cc=M.cells.filter(function(x){return x.c!=null;}).sort(function(a,b){return b.c-a.c;}),rc=cc.indexOf(c)+1;
    out.push(esc(MLAB[S.tmColor])+' '+bold(fmt(S.tmColor,c.c))+': '+ordinal(rc)+' of '+cc.length+', '+(c.c>M.cmed?'above':'below')+' the median of '+esc(fmt(S.tmColor,M.cmed))+'.');}
  return out;}
// ── shell ──
function select(src,dim,v,col,colDim){var s=S.sel;
  if(s&&s.src===src&&s.dim===dim&&s.val===v&&s.col===col)S.sel=null;else S.sel={src:src,dim:dim,val:v,col:col,colDim:colDim};render();}
function render(){renderBar();renderKpis();
  ['bubble','tree','heat','table'].forEach(function(v){$('ex-v-'+v).hidden=S.view!==v;});
  if(S.view==='bubble')renderBubble();else if(S.view==='tree')renderTree();else if(S.view==='heat')renderHeat();else renderTable();
  $('ex-rank').hidden=S.view!=='bubble';renderSide();}
function explain(){var f={dims:{}},w=filterWords().map(function(x){return x.t.replace(/^Selected: /,'');});
  C.dims.forEach(function(d,k){if(S.dimSel[k])f.dims[d.name]=Array.from(S.dimSel[k]).map(function(i){return d.values[i];});});
  if(S.sel){var s=S.sel;f.dims[dimName(s.dim)]=[dv(s.dim,s.val)];if(s.col!=null){if(s.colDim==='time'){f.from=T.iso[s.col];f.to=T.iso[s.col];}else f.dims[dimName(+s.colDim)]=[dv(+s.colDim,s.col)];}}
  if(T&&!f.from&&(S.from>0||S.to<T.labels.length-1)){f.from=T.iso[S.from];f.to=T.iso[S.to];}
  var ctx={sheet:D.meta.sheet,style:'explore',measure:MS[S.hmM].name,dim:C.dims[S.detail].id?D.meta.dim:dimName(S.detail),filters:f,
    prompt:w.length?'Explain this view: '+w.join('; '):'Explain this view'};
  try{(window.parent||window.top).postMessage({type:'a2ui_action',action:'ask',data:ctx},'*');}catch(e){}
  $('ex-explain').textContent='Asked: the cards will update';$('ex-explain').classList.add('ghost');}
$('ex-zoom').onclick=function(){S.zoom=null;renderBubble();renderSide();};
var rt=null;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(function(){if(S.view==='tree')renderTree();},150);});
buildFilters();buildTabs();buildSettings();render();
})();

"""


def render(a, brief: str = "") -> str:
    """The explorer's markup, its data and its script (page.CSS supplies the colour tokens).
    `brief` is extra markup for the side panel (the AI summary on the dashboard page)."""
    data = {"cube": a.chart["cube"], "defaults": a.chart["defaults"], "init": a.chart["init"],
            "meta": {"sheet": a.sheet, "dim": a.dim}}
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return f"""<style>{CSS}</style>
<div class="ex"><div class="ex-filters" id="ex-filters" aria-label="Filters"></div><div class="ex-bar" id="ex-bar"></div><div class="ex-kpis" id="ex-kpis"></div>
<div class="ex-top"><div class="ex-tabs" id="ex-tabs" role="tablist" aria-label="Views"></div><div class="ex-set" id="ex-set"></div></div>
<div class="ex-body"><div class="vis"><div class="vhead"><div><h3 id="ex-vtitle"></h3><p class="hint" id="ex-vhint"></p></div><button class="linkbtn" id="ex-zoom" hidden>Reset zoom</button></div>
<div id="ex-v-bubble"><div class="empty" id="ex-bempty" style="display:none">Nothing to show for these filters. Clear a filter or reset the zoom.</div><svg class="bub" id="ex-bsvg" viewBox="0 0 720 430" role="img" aria-label="Bubble chart"></svg></div>
<div id="ex-v-tree" hidden><div class="tm" id="ex-tree" role="img" aria-label="Treemap"></div></div>
<div id="ex-v-heat" hidden><div class="hm" id="ex-hm"></div></div>
<div id="ex-v-table" hidden><div class="tblbox" id="ex-table"></div></div>
<div class="legend" id="ex-vlegend"></div></div>
<aside class="side"><div id="ex-side" aria-live="polite"></div>{brief}<div id="ex-rank"></div></aside></div>
<div id="ex-card" class="hcard" hidden></div></div>
<script type="application/json" id="ex-data">{blob}</script><script>{JS}</script>"""


def title(a) -> tuple[str, str]:
    return (f"Explore {html.unescape(a.measure)}", "Filter along the top, switch views on the tabs, hover for detail and click to cross-filter; "
            "then ask me to explain the view.")
