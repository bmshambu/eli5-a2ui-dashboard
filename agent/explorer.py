"""The interactive explorer: a Power BI-style report page inside GE's side panel.

The tab is pre-aggregated in Python (analysis.cube) and embedded as JSON; everything else runs in
the browser, so slicers and clicks respond instantly with no round trip to the agent:
  - slicers: one checklist per category, a period range, and which numbers each visual shows
  - KPI cards that follow every filter, with the share of all data still in view
  - a bubble chart (default: growth against size, one bubble per category, quadrants at the
    medians) and a heatmap (category by period, or by a second category)
  - cross-filtering: click a bubble or a heatmap cell (or a table row) and the other visuals and
    the KPIs filter to it; click again or press Clear to undo
  - a sortable table, and "Explain this view", which sends the current filters to the agent so
    the insight cards are rewritten for exactly what is on screen
The frame's CSP allows inline scripts only (proven in GE), so no charting library is loaded.
"""
import html
import json

CSS = """
.ex{display:grid;grid-template-columns:230px minmax(0,1fr);gap:14px;align-items:start}
.ex-pane{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px;position:sticky;top:8px;max-height:calc(100vh - 16px);overflow:auto}
.ex-pane h4{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:12px 0 6px}
.ex-pane h4:first-child{margin-top:0}
.ex-pane select{width:100%;font:inherit;font-size:13px;padding:4px 6px;border:1px solid var(--line);border-radius:6px;background:var(--page);color:var(--ink);margin-bottom:6px}
.ex-pane label.opt{display:flex;gap:6px;align-items:center;font-size:13px;padding:2px 0;cursor:pointer}
.ex-pane .list{max-height:150px;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:4px 6px;background:var(--page)}
.ex-pane .slab{font-size:12px;color:var(--ink2);margin:6px 0 2px}
.ex-pane input.find{width:100%;font:inherit;font-size:12px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;margin-bottom:4px;background:var(--page);color:var(--ink)}
.ex-pane .row2{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.linkbtn{background:none;border:0;color:var(--accent);font:inherit;font-size:12px;cursor:pointer;padding:0}
.ex-main{min-width:0;display:flex;flex-direction:column;gap:12px}
.ex-bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:13px}
.fchip{background:var(--chip);border-radius:999px;padding:3px 10px;display:inline-flex;gap:6px;align-items:center}
.fchip button{background:none;border:0;color:var(--ink2);cursor:pointer;font-size:13px;padding:0}
.ex-bar .spacer{flex:1}
.btn{border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:8px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}
.btn.ghost{background:var(--surface);color:var(--accent)}
.ex-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.ex-kpis .kpi .v{font-size:21px}
.vis{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.vis h3{font-size:15px;margin:0}.vis .hint{font-size:12px;color:var(--muted);margin:2px 0 6px}
.vgrid{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}.vis{min-width:0}
@media (min-width:1180px){.vgrid{grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)}}
.bub{width:100%;height:auto;display:block;overflow:visible}
.bub circle.b{stroke:var(--surface);stroke-width:1.5;cursor:pointer;transition:transform .45s ease,r .45s ease,opacity .25s}
.bub circle.b:hover{stroke:var(--ink);stroke-width:2}
.bub circle.b.on{stroke:var(--ink);stroke-width:2.5}
.bub .dim{opacity:.18}
.empty{padding:48px 12px;text-align:center;color:var(--muted);font-size:14px;border:1px dashed var(--axis);border-radius:8px;margin:8px 0}
.bub .quad{stroke:var(--axis);stroke-dasharray:4 4}.bub .qlab{font-size:11px;fill:var(--muted)}
.bub .blab{font-size:11px;fill:var(--ink);font-weight:600;pointer-events:none}
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
.tblbox{max-height:340px;overflow:auto}
.ex .legend{margin-top:4px}
.vins{margin:10px 0 0;padding:10px 12px;border-radius:8px;background:var(--page);border:1px solid var(--line)}
.vins b.h{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--accent);margin-bottom:4px}
.vins ul{margin:0;padding-left:18px;font-size:13px;color:var(--ink)}.vins li{margin:3px 0}
.vins .tag{font-size:11px;border-radius:999px;padding:0 7px;margin-right:4px;background:var(--chip);color:var(--ink2)}
.ex-empty{padding:30px;text-align:center;color:var(--muted)}
@media (max-width:760px){.ex{grid-template-columns:1fr}.ex-pane{position:static;max-height:none}}
@media (prefers-reduced-motion:reduce){.bub circle.b{transition:none}}
"""

JS = r"""
(function(){
var D=JSON.parse(document.getElementById('ex-data').textContent);
var C=D.cube, ND=C.dims.length, T=C.time, MS=C.measures, NM=MS.length;
var SERIES=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#6250d6','#e34948'];
var RAMP=['#e8f1fd','#cde2fb','#b7d3f6','#9ec5f4','#86b6ef','#6da7ec','#5598e7','#3987e5','#2a78d6','#256abf','#1c5cab','#104281'];
var S={dimSel:C.dims.map(function(d){return null;}),from:0,to:T?T.labels.length-1:0,sel:null,
  detail:D.defaults.detail,color:D.defaults.color,x:D.defaults.x,y:D.defaults.y,size:D.defaults.size,
  hmRow:D.defaults.hmRow,hmCol:D.defaults.hmCol,hmM:D.defaults.hmM,sortKey:'y',sortDir:-1};
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
// ── insights per visual: computed here from what is on screen, so they follow every filter ──
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
// ── pane ──
function buildPane(){var h='';
  h+='<h4>Filters</h4>';
  if(T){h+='<div class="slab">'+esc(T.name)+'</div><div class="row2"><select id="ex-from">'+T.labels.map(function(l,i){return '<option value="'+i+'">'+esc(l)+'</option>';}).join('')+
    '</select><select id="ex-to">'+T.labels.map(function(l,i){return '<option value="'+i+'">'+esc(l)+'</option>';}).join('')+'</select></div>';}
  C.dims.forEach(function(d,k){if(d.id)return;
    h+='<div class="slab">'+esc(d.name)+' <button class="linkbtn" data-all="'+k+'">all</button></div>';
    if(d.values.length>10)h+='<input class="find" data-find="'+k+'" placeholder="Search">';
    h+='<div class="list" id="ex-l'+k+'">'+d.values.map(function(v,i){return '<label class="opt"><input type="checkbox" data-d="'+k+'" value="'+i+'"'+(!S.dimSel[k]||S.dimSel[k].has(i)?' checked':'')+'><span>'+esc(v)+'</span></label>';}).join('')+'</div>';});
  var mo=function(id,cur,allowNone){return '<select id="'+id+'">'+(allowNone?'<option value="">(none)</option>':'')+MET.map(function(m){return '<option value="'+m[0]+'"'+(m[0]===cur?' selected':'')+'>'+esc(m[1])+'</option>';}).join('')+'</select>';};
  var dopt=function(id,cur,withTime,withNone){var o=(withNone?'<option value="">(none)</option>':'')+C.dims.map(function(d,i){return '<option value="'+i+'"'+(String(i)===String(cur)?' selected':'')+'>'+esc(d.name)+'</option>';}).join('');
    if(withTime&&T)o+='<option value="time"'+(cur==='time'?' selected':'')+'>'+esc(T.name)+'</option>';return '<select id="'+id+'">'+o+'</select>';};
  h+='<h4>Bubble chart</h4><div class="slab">One bubble per</div>'+dopt('ex-detail',S.detail)+'<div class="slab">Colour by</div>'+dopt('ex-color',S.color,false,true)+
     '<div class="slab">Across (x)</div>'+mo('ex-x',S.x)+'<div class="slab">Up (y)</div>'+mo('ex-y',S.y)+'<div class="slab">Size</div>'+mo('ex-size',S.size);
  h+='<h4>Heatmap</h4><div class="slab">Rows</div>'+dopt('ex-hrow',S.hmRow)+'<div class="slab">Columns</div>'+dopt('ex-hcol',S.hmCol,true,true)+
     '<div class="slab">Number</div><select id="ex-hmm">'+MS.map(function(m,i){return '<option value="'+i+'"'+(i===S.hmM?' selected':'')+'>'+esc(m.name)+'</option>';}).join('')+'</select>';
  $('ex-pane').innerHTML=h;
  if(T){$('ex-from').value=S.from;$('ex-to').value=S.to;
    $('ex-from').onchange=function(){S.from=Math.min(+this.value,S.to);this.value=S.from;S.sel=null;render();};
    $('ex-to').onchange=function(){S.to=Math.max(+this.value,S.from);this.value=S.to;S.sel=null;render();};}
  $('ex-pane').querySelectorAll('input[data-d]').forEach(function(cb){cb.onchange=function(){var k=+cb.dataset.d;
    var all=$('ex-l'+k).querySelectorAll('input');var on=new Set();all.forEach(function(x){if(x.checked)on.add(+x.value);});
    S.dimSel[k]=on.size===all.length?null:on;S.sel=null;render();};});
  $('ex-pane').querySelectorAll('[data-all]').forEach(function(b){b.onclick=function(){var k=+b.dataset.all;
    var boxes=$('ex-l'+k).querySelectorAll('input'),anyOff=Array.prototype.some.call(boxes,function(x){return !x.checked;});
    boxes.forEach(function(x){x.checked=anyOff;});S.dimSel[k]=anyOff?null:new Set();render();};});
  $('ex-pane').querySelectorAll('[data-find]').forEach(function(inp){inp.oninput=function(){var q=inp.value.toLowerCase();
    $('ex-l'+inp.dataset.find).querySelectorAll('label').forEach(function(l){l.style.display=l.textContent.toLowerCase().indexOf(q)>=0?'':'none';});};});
  var bind=function(id,key,cast){$(id).onchange=function(){S[key]=cast(this.value);if(key==='detail'||key==='hmRow'||key==='hmCol')S.sel=null;render();};};
  bind('ex-detail','detail',Number);bind('ex-color','color',function(v){return v===''?null:+v;});bind('ex-x','x',String);bind('ex-y','y',String);bind('ex-size','size',String);
  bind('ex-hrow','hmRow',Number);bind('ex-hcol','hmCol',function(v){return v===''?null:v;});bind('ex-hmm','hmM',Number);}
// ── filter bar + KPIs ──
function filterWords(){var w=[];C.dims.forEach(function(d,k){if(S.dimSel[k])w.push({k:'d'+k,t:d.name+': '+(S.dimSel[k].size<=3?Array.from(S.dimSel[k]).map(function(i){return d.values[i];}).join(', '):S.dimSel[k].size+' of '+d.values.length)});});
  if(T&&(S.from>0||S.to<T.labels.length-1))w.push({k:'t',t:T.name+': '+T.labels[S.from]+(S.to!==S.from?' to '+T.labels[S.to]:'')});
  if(S.sel){var s=S.sel,t=dimName(s.dim)+' = '+dv(s.dim,s.val);if(s.col!=null)t+=', '+(s.colDim==='time'?T.labels[s.col]:dimName(+s.colDim)+' = '+dv(+s.colDim,s.col));w.push({k:'sel',t:'Selected: '+t});}
  return w;}
function renderBar(){var w=filterWords();
  $('ex-bar').innerHTML=(w.length?w.map(function(x){return '<span class="fchip">'+esc(x.t)+'<button data-x="'+x.k+'" aria-label="Remove">✕</button></span>';}).join('')+'<button class="linkbtn" id="ex-clear">Clear all</button>':'<span style="color:var(--muted)">Showing everything. Click a bubble, a heatmap cell or a table row to filter the rest.</span>')+
    '<span class="spacer"></span><button class="btn" id="ex-explain">Explain this view</button>';
  $('ex-bar').querySelectorAll('[data-x]').forEach(function(b){b.onclick=function(){var k=b.dataset.x;
    if(k==='sel')S.sel=null;else if(k==='t'){S.from=0;S.to=T.labels.length-1;}else S.dimSel[+k.slice(1)]=null;buildPane();render();};});
  if($('ex-clear'))$('ex-clear').onclick=function(){S.dimSel=S.dimSel.map(function(){return null;});S.sel=null;if(T){S.from=0;S.to=T.labels.length-1;}buildPane();render();};
  $('ex-explain').onclick=explain;}
function renderKpis(){var rows=C.rows.filter(function(r){return rowOk(r,false);}),all=total(C.rows),a=total(rows),m=S.hmM,mm=MS[m];
  var tot=val(a,'m:'+m+':total'),share=!mm.rate&&all.s[m]?a.s[m]/all.s[m]*100:null,k=[];
  k.push(['In view: '+mm.name+(mm.rate?' (average)':''),num(tot),share!=null?share.toFixed(1)+'% of all data':a.n.toLocaleString('en-GB')+' rows']);
  if(T){var g=val(a,'m:'+m+':growth');if(g!=null)k.push(['Change '+T.labels[S.from]+' to '+T.labels[S.to],pct(g),'first to last period in view',g>0?'up':g<0?'down':'']);}
  var gd=groupBy(rows,function(r){return r[S.detail];}),best=null,bv=-Infinity;gd.forEach(function(x,key){var v=val(x,'m:'+m+':total');if(v!=null&&v>bv){bv=v;best=key;}});
  if(best!=null)k.push(['Top '+dimName(S.detail),dv(S.detail,best),num(bv)]);
  k.push(['Rows in view',a.n.toLocaleString('en-GB'),'of '+all.n.toLocaleString('en-GB')]);
  var kin=[];if(share!=null&&share<99.95)kin.push('This view holds '+bold(share.toFixed(1)+'%')+' of all '+esc(mm.name)+'.');
  if(T&&share!=null&&share<99.95){var gAll=(function(){var s0=S.sel,ds=S.dimSel;S.sel=null;S.dimSel=S.dimSel.map(function(){return null;});var v=val(total(C.rows.filter(function(r){return rowOk(r,true);})),'m:'+m+':growth');S.sel=s0;S.dimSel=ds;return v;})(),gIn=val(a,'m:'+m+':growth');
    if(gAll!=null&&gIn!=null)kin.push('It changed '+bold(pct(gIn))+' over the period against '+bold(pct(gAll))+' for everything: '+(gIn>gAll+1?'outperforming':gIn<gAll-1?'underperforming':'in line with')+' the whole.');}
  insightBox('ex-kins',kin);
  $('ex-kpis').innerHTML=k.map(function(x){return '<div class="kpi '+(x[3]||'')+'"><div class="k">'+esc(x[0])+'</div><div class="v">'+esc(x[1])+'</div><div class="n">'+esc(x[2])+'</div></div>';}).join('');}
// ── bubble chart ──
var W=720,H=410,L=62,R=20,TP=40,B=44;
function renderBubble(){var rows=rowsFor('bubble'),tot=total(rows),g=groupBy(rows,function(r){return r[S.detail];}),pts=[],sx=S.x,sy=S.y,swap='';
  function collect(){pts=[];g.forEach(function(a,key){var x=val(a,sx,tot),y=val(a,sy,tot),s=val(a,S.size,tot);if(x==null||y==null)return;pts.push({k:key,x:x,y:y,s:s==null?0:Math.abs(s),a:a});});}
  collect();
  // growth needs two periods; when the view is down to one, show the totals instead of an empty chart
  if(!pts.length&&/growth/.test(sx+sy)){sx=sx.replace('growth','total');sy=sy.replace('growth','total');
    if(sx===sy)sx=MET.map(function(m){return m[0];}).find(function(id){return id!==sy&&!/growth|share/.test(id);})||'rows';
    collect();if(pts.length)swap=' Growth needs at least two periods in view, so this shows '+MLAB[sx]+' instead.';}
  var svg=$('ex-bsvg'),empty=$('ex-bempty');
  $('ex-btitle').textContent=MLAB[sy]+' against '+MLAB[sx];
  $('ex-bhint').textContent='One bubble per '+dimName(S.detail)+'; size is '+MLAB[S.size]+'. Click a bubble to filter the other visuals.'+swap;
  if(!pts.length){insightBox('ex-bins',[]);svg.innerHTML='';svg.style.display='none';empty.style.display='';$('ex-blegend').innerHTML='';return;}
  svg.style.display='';empty.style.display='none';
  var xt=ticks(Math.min.apply(null,pts.map(function(p){return p.x;})),Math.max.apply(null,pts.map(function(p){return p.x;})),6),
      yt=ticks(Math.min.apply(null,pts.map(function(p){return p.y;})),Math.max.apply(null,pts.map(function(p){return p.y;})),6);
  var X=function(v){return L+(v-xt[0])/((xt[xt.length-1]-xt[0])||1)*(W-L-R);},Y=function(v){return H-B-(v-yt[0])/((yt[yt.length-1]-yt[0])||1)*(H-B-TP);};
  var smax=Math.max.apply(null,pts.map(function(p){return p.s;}))||1,many=pts.length>60,rmax=many?16:34,rmin=many?3:6;
  var Rr=function(s){return rmin+Math.sqrt(s/smax)*(rmax-rmin);};
  var h='';yt.forEach(function(t){h+='<line class="grid" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text class="tick" x="'+(L-8)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(sy,t))+'</text>';});
  xt.forEach(function(t){h+='<text class="tick" x="'+X(t)+'" y="'+(H-B+18)+'" text-anchor="middle">'+esc(fmt(sx,t))+'</text>';});
  h+='<text class="axislab" x="'+(W-R)+'" y="'+(H-6)+'" text-anchor="end">'+esc(MLAB[sx])+' →</text><text class="axislab" x="'+L+'" y="'+(TP-22)+'">↑ '+esc(MLAB[sy])+'</text>';
  if(pts.length>=4){var mx=median(pts.map(function(p){return p.x;})),my=median(pts.map(function(p){return p.y;}));
    h+='<line class="quad" x1="'+X(mx)+'" x2="'+X(mx)+'" y1="'+TP+'" y2="'+(H-B)+'"/><line class="quad" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(my)+'" y2="'+Y(my)+'"/>';
    var gr=/growth/.test(sx),q=gr?['Big and growing','Big but slower','Small but growing','Small and slower']:['High on both','High up, low across','Low up, high across','Low on both'];
    h+='<text class="qlab" x="'+(W-R-4)+'" y="'+(TP+12)+'" text-anchor="end">'+q[0]+'</text><text class="qlab" x="'+(L+4)+'" y="'+(TP+12)+'">'+q[1]+'</text>'+
       '<text class="qlab" x="'+(W-R-4)+'" y="'+(H-B-6)+'" text-anchor="end">'+q[2]+'</text><text class="qlab" x="'+(L+4)+'" y="'+(H-B-6)+'">'+q[3]+'</text>';}
  var selKey=S.sel&&S.sel.src==='bubble'?S.sel.val:null;
  pts.sort(function(a,b){return b.s-a.s;});
  var circles='',labels='',placed=[];
  pts.forEach(function(p,i){var r=Rr(p.s),cx=X(p.x),cy=Y(p.y),name=dv(S.detail,p.k),cls='b'+(selKey===p.k?' on':selKey!=null?' dim':'');
    var tip=name+' · '+MLAB[sx]+' '+fmt(sx,p.x)+' · '+MLAB[sy]+' '+fmt(sy,p.y)+' · '+MLAB[S.size]+' '+fmt(S.size,p.s);
    circles+='<circle class="'+cls+'" data-k="'+p.k+'" cx="0" cy="0" r="'+r.toFixed(1)+'" style="transform:translate('+cx.toFixed(1)+'px,'+cy.toFixed(1)+'px);fill:'+colorOf(p.a)+';fill-opacity:.78" data-tip="'+esc(tip)+'"/>';
    if(i<10&&!many){var lx=cx,ly=cy-r-4;if(!placed.some(function(q){return Math.abs(q[0]-lx)<70&&Math.abs(q[1]-ly)<13;})){placed.push([lx,ly]);
      labels+='<text class="blab" x="'+lx.toFixed(1)+'" y="'+ly.toFixed(1)+'" text-anchor="middle">'+esc(name.length>16?name.slice(0,15)+'…':name)+'</text>';}}});
  svg.innerHTML=h+'<g>'+circles+'</g>'+labels;
  svg.querySelectorAll('circle.b').forEach(function(c){c.onclick=function(){select('bubble',S.detail,+c.dataset.k,null,null);};});
  var leg='';if(S.color!=null&&C.dims[S.color].values.length<=8){leg=C.dims[S.color].values.map(function(v,i){return '<span><i class="sw" style="background:'+SERIES[i]+'"></i>'+esc(v)+'</span>';}).join('');}
  $('ex-blegend').innerHTML=leg;
  insightBox('ex-bins',bubbleInsights(pts,pts.length>=4?median(pts.map(function(p){return p.x;})):0,pts.length>=4?median(pts.map(function(p){return p.y;})):0,sx,sy));}
// ── heatmap ──
function renderHeat(){var box=$('ex-hm');
  if(S.hmCol==null){insightBox('ex-hins',[]);box.innerHTML='<div class="ex-empty">Pick a column for the heatmap (a date or a second category).</div>';return;}
  var rows=rowsFor('heat'),m=S.hmM,mm=MS[m],rd=S.hmRow,cd=S.hmCol,isT=cd==='time';
  if(!isT&&+cd===rd){box.innerHTML='<div class="ex-empty">Rows and columns are the same; pick another column.</div>';return;}
  var cols=isT?T.labels.map(function(l,i){return i;}).filter(function(i){return i>=S.from&&i<=S.to;}):C.dims[+cd].values.map(function(v,i){return i;});
  var cell=new Map(),rt=new Map();rows.forEach(function(r){var key=r[rd]+'|'+(isT?r[ND]:r[+cd]);if(!cell.has(key))cell.set(key,acc());add(cell.get(key),r);
    if(!rt.has(r[rd]))rt.set(r[rd],acc());add(rt.get(r[rd]),r);});
  var rkeys=Array.from(rt.keys()).sort(function(a,b){return (val(rt.get(b),'m:'+m+':total')||0)-(val(rt.get(a),'m:'+m+':total')||0);}).slice(0,25);
  if(!isT){var ct=new Map();rows.forEach(function(r){var c=r[+cd];ct.set(c,(ct.get(c)||0)+r[ND+2+2*m]);});cols=cols.filter(function(c){return ct.has(c);}).sort(function(a,b){return ct.get(b)-ct.get(a);}).slice(0,24);}
  var vals=[];rkeys.forEach(function(rk){cols.forEach(function(c){var a=cell.get(rk+'|'+c);if(a){var v=val(a,'m:'+m+':total');if(v!=null)vals.push(v);}});});
  var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);
  $('ex-htitle').textContent=mm.name+(mm.rate?' (average)':'')+' by '+dimName(rd)+' and '+(isT?T.name:dimName(+cd));
  var cellv=rkeys.map(function(rk){return cols.map(function(c){var a=cell.get(rk+'|'+c);return a?val(a,'m:'+m+':total'):null;});});
  insightBox('ex-hins',heatInsights(rkeys,cols,cellv,isT,cd,rd));
  var s=S.sel,h='<table><thead><tr><th class="rh">'+esc(dimName(rd))+'</th>'+cols.map(function(c){return '<th>'+esc(isT?T.labels[c]:dv(+cd,c))+'</th>';}).join('')+'<th>Total</th></tr></thead><tbody>';
  rkeys.forEach(function(rk){var rowDim=s&&s.src==='heat'&&s.col==null&&s.val!==rk;
    h+='<tr><td class="rh'+(rowDim?' dim':'')+'" data-r="'+rk+'">'+esc(dv(rd,rk))+'</td>';
    cols.forEach(function(c){var a=cell.get(rk+'|'+c),v=a?val(a,'m:'+m+':total'):null;
      if(v==null){h+='<td class="c e"></td>';return;}
      var st=Math.round((v-lo)/((hi-lo)||1)*(RAMP.length-1)),on=s&&s.src==='heat'&&s.val===rk&&s.col===c,dim=s&&s.src==='heat'&&!on&&!(s.col==null&&s.val===rk);
      h+='<td class="c'+(on?' on':'')+(dim?' dim':'')+'" data-r="'+rk+'" data-c="'+c+'" style="background:'+RAMP[st]+';color:'+(st>=7?'#fff':'#0b0b0b')+'" data-tip="'+esc(dv(rd,rk)+' · '+(isT?T.labels[c]:dv(+cd,c))+': '+num(v))+'">'+(cols.length<=12?esc(num(v)):'')+'</td>';});
    h+='<td class="tot">'+esc(num(val(rt.get(rk),'m:'+m+':total')))+'</td></tr>';});
  box.innerHTML=h+'</tbody></table>';
  $('ex-hlegend').innerHTML='<span class="ramp">'+esc(num(lo))+' '+RAMP.map(function(c){return '<i style="background:'+c+'"></i>';}).join('')+' '+esc(num(hi))+'</span><span>Click a cell or a row name to filter the other visuals.</span>';
  box.querySelectorAll('td.c[data-c]').forEach(function(td){td.onclick=function(){select('heat',rd,+td.dataset.r,+td.dataset.c,isT?'time':String(+cd));};});
  box.querySelectorAll('td.rh').forEach(function(td){td.onclick=function(){select('heat',rd,+td.dataset.r,null,null);};});}
// ── table ──
function renderTable(){var rows=rowsFor('table'),tot=total(rows),g=groupBy(rows,function(r){return r[S.detail];}),cols=[['x',S.x],['y',S.y],['size',S.size]],list=[];
  g.forEach(function(a,k){list.push({k:k,x:val(a,S.x,tot),y:val(a,S.y,tot),size:val(a,S.size,tot)});});
  var key=S.sortKey;list.sort(function(a,b){if(key==='name')return S.sortDir*String(dv(S.detail,a.k)).localeCompare(dv(S.detail,b.k));return S.sortDir*((a[key]==null?-Infinity:a[key])-(b[key]==null?-Infinity:b[key]));});
  var top=Math.max.apply(null,list.map(function(x){return Math.abs(x.y)||0;}))||1,selKey=S.sel&&S.sel.src==='table'?S.sel.val:null;
  var arrow=function(k){return S.sortKey===k?(S.sortDir<0?' ▼':' ▲'):'';};
  var h='<table class="extbl"><thead><tr><th data-s="name">'+esc(dimName(S.detail))+arrow('name')+'</th>'+cols.map(function(c){return '<th data-s="'+c[0]+'">'+esc(MLAB[c[1]])+arrow(c[0])+'</th>';}).join('')+'</tr></thead><tbody>';
  list.slice(0,100).forEach(function(r){h+='<tr class="r'+(selKey===r.k?' on':'')+'" data-k="'+r.k+'"><td>'+esc(dv(S.detail,r.k))+'</td><td>'+esc(fmt(S.x,r.x))+'</td><td><span class="bar" style="width:'+Math.max(2,Math.abs(r.y||0)/top*60).toFixed(0)+'px"></span>'+esc(fmt(S.y,r.y))+'</td><td>'+esc(fmt(S.size,r.size))+'</td></tr>';});
  insightBox('ex-tins',tableInsights(list));
  $('ex-table').innerHTML=h+'</tbody></table>'+(list.length>100?'<p class="hint">Top 100 of '+list.length+'.</p>':'');
  $('ex-table').querySelectorAll('th[data-s]').forEach(function(th){th.onclick=function(){var k=th.dataset.s;S.sortDir=S.sortKey===k?-S.sortDir:-1;S.sortKey=k;renderTable();};});
  $('ex-table').querySelectorAll('tr.r').forEach(function(tr){tr.onclick=function(){select('table',S.detail,+tr.dataset.k,null,null);};});}
function select(src,dim,v,col,colDim){var s=S.sel;
  if(s&&s.src===src&&s.dim===dim&&s.val===v&&s.col===col)S.sel=null;else S.sel={src:src,dim:dim,val:v,col:col,colDim:colDim};render();}
function render(){renderBar();renderKpis();renderBubble();renderHeat();renderTable();}
// ── explain this view: the agent re-analyses exactly these rows ──
function explain(){var f={dims:{}},w=filterWords().map(function(x){return x.t.replace(/^Selected: /,'');});
  C.dims.forEach(function(d,k){if(S.dimSel[k])f.dims[d.name]=Array.from(S.dimSel[k]).map(function(i){return d.values[i];});});
  if(S.sel){var s=S.sel;f.dims[dimName(s.dim)]=[dv(s.dim,s.val)];if(s.col!=null){if(s.colDim==='time'){f.from=T.iso[s.col];f.to=T.iso[s.col];}else f.dims[dimName(+s.colDim)]=[dv(+s.colDim,s.col)];}}
  if(T&&!f.from&&(S.from>0||S.to<T.labels.length-1)){f.from=T.iso[S.from];f.to=T.iso[S.to];}
  var ctx={sheet:D.meta.sheet,style:'explore',measure:MS[S.hmM].name,dim:C.dims[S.detail].id?D.meta.dim:dimName(S.detail),filters:f,
    prompt:w.length?'Explain this view: '+w.join('; '):'Explain this view'};
  try{(window.parent||window.top).postMessage({type:'a2ui_action',action:'ask',data:ctx},'*');}catch(e){}
  $('ex-explain').textContent='Asked: the cards will update';$('ex-explain').classList.add('ghost');}
buildPane();render();
})();
"""


def render(a) -> str:
    """The explorer's markup, its data and its script (page.CSS supplies the colour tokens)."""
    data = {"cube": a.chart["cube"], "defaults": a.chart["defaults"], "init": a.chart["init"],
            "meta": {"sheet": a.sheet, "dim": a.dim}}
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return f"""<style>{CSS}</style>
<div class="ex"><aside class="ex-pane" id="ex-pane" aria-label="Filters and visual settings"></aside>
<div class="ex-main"><div class="ex-bar" id="ex-bar"></div><div class="ex-kpis" id="ex-kpis"></div><div class="vins" id="ex-kins"></div>
<div class="vgrid"><div class="vis"><h3 id="ex-btitle"></h3><p class="hint" id="ex-bhint"></p>
<div class="empty" id="ex-bempty" style="display:none">Nothing to show for these filters. Clear a filter to bring bubbles back.</div><svg class="bub" id="ex-bsvg" viewBox="0 0 720 410" role="img" aria-label="Bubble chart"></svg><div class="legend" id="ex-blegend"></div><div class="vins" id="ex-bins"></div></div>
<div class="vis"><h3 id="ex-htitle"></h3><div class="legend" id="ex-hlegend"></div><div class="hm" id="ex-hm"></div><div class="vins" id="ex-hins"></div></div></div>
<div class="vis"><h3>Table</h3><p class="hint">Click a column to sort, a row to filter the visuals.</p><div class="vins" id="ex-tins"></div><div class="tblbox" id="ex-table"></div></div>
</div></div>
<script type="application/json" id="ex-data">{blob}</script><script>{JS}</script>"""


def title(a) -> tuple[str, str]:
    return (f"Explore {html.unescape(a.measure)}", "Filter with the slicers, click any bubble, cell or row to cross-filter, "
            "then ask me to explain the view.")
