"""승격 산출물(s02 place + s03 hub)을 울산 지도 위에 올리는 뷰어를 생성한다.

출력: viewers/place_hub_map_before.html (Leaflet + OSM 타일, 데이터 임베드)
- place 레벨: hub 분류 색상(클라이언트 재판정 슬라이더 — θ는 산출물 고정 45°), D=0 증거부재 링
- pole 레벨: [place|pole] 병렬 문법 — 병합선 오버레이
- 진단 오버레이: under_merge(같은 이름 분리)·alias(다른 이름 근접) — override 리뷰 재료
사용법: python tools/build_place_hub_map.py  (루트에서)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def latest(stage: str, scope: str) -> Path:
    d = ROOT / "artifacts" / stage / scope
    v = json.loads((d / "_latest.json").read_text(encoding="utf-8"))["version"]
    return d / v


p0 = latest("s00_ingest", "before")
p2 = latest("s02_place", "before")
p3 = latest("s03_hub", "before")

places = pd.read_parquet(p2 / "places.parquet")
pmap = pd.read_parquet(p2 / "pole_place_map.parquet")
poles = pd.read_parquet(p0 / "poles.parquet")
metrics = pd.read_parquet(p3 / "place_metrics.parquet")
qual = pd.read_parquet(p3 / "hub_qualification.parquet")
ledges = pd.read_parquet(p3 / "l_space_place_edges.parquet")
gap = pd.read_csv(p3 / "diag_lspace_gap.csv")
d_um = pd.read_csv(p2 / "diag_under_merge.csv")
d_al = pd.read_csv(p2 / "diag_alias.csv")

pl = (places.merge(metrics[["place_id", "D", "A", "L", "L_star"]], on="place_id")
      .merge(qual[["place_id", "hub_class"]], on="place_id")
      .merge(gap[["place_id", "reason"]], on="place_id", how="left"))
idx = {pid: i for i, pid in enumerate(pl["place_id"])}

P = [[round(r.lat_centroid, 6), round(r.lon_centroid, 6), r.place_name, int(r.n_poles),
      round(float(r.span_m), 1), int(r.D), int(r.A), int(r.L),
      r.reason if isinstance(r.reason, str) else ""]
     for r in pl.itertuples()]

E = [[idx[r.place_a], idx[r.place_b], int(r.n_routes)] for r in ledges.itertuples()
     if r.place_a in idx and r.place_b in idx]

pole_xy = poles.set_index("pole_id")[["pole_name", "lat", "lon"]]
PO = []
for r in pmap.itertuples():
    if r.pole_id in pole_xy.index and r.place_id in idx:
        q = pole_xy.loc[r.pole_id]
        PO.append([round(float(q.lat), 6), round(float(q.lon), 6),
                   str(q.pole_name), str(r.pole_id)[-4:], idx[r.place_id]])

UM = [[idx[r.place_id_a], idx[r.place_id_b], round(float(r.gap_m), 1), r.name_norm]
      for r in d_um.itertuples() if r.place_id_a in idx and r.place_id_b in idx]
AL = [[idx[r.place_id_a], idx[r.place_id_b], round(float(r.gap_m), 1), r.name_a, r.name_b]
      for r in d_al.itertuples() if r.place_id_a in idx and r.place_id_b in idx]

data = json.dumps({"P": P, "E": E, "PO": PO, "UM": UM, "AL": AL},
                  ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

meta = (f"s02 {p2.name} · s03 {p3.name} · place {len(P):,} · L-space 엣지 {len(E):,} · "
        f"pole {len(PO):,} · θ=45°(ADR-009 고정) · universe General 170 · backbone 137/170")

html = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BTS place·hub 지도 (before)</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{height:100%;margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
#map{position:absolute;inset:0}
#panel{position:absolute;top:10px;left:10px;z-index:1000;width:270px;background:#fcfcfbee;
 border:1px solid rgba(11,11,11,.12);border-radius:10px;padding:12px;font-size:12.5px;
 max-height:calc(100% - 40px);overflow-y:auto;box-shadow:0 4px 16px rgba(0,0,0,.15)}
#panel h1{font-size:14px;margin:0 0 2px}
#panel .meta{color:#898781;font-size:10.5px;margin-bottom:8px;line-height:1.5}
#panel h2{font-size:11px;color:#898781;text-transform:uppercase;letter-spacing:.04em;margin:10px 0 4px}
label.row{display:flex;align-items:center;gap:6px;padding:2px 0;cursor:pointer}
label.row .sw{width:10px;height:10px;border-radius:3px;flex:none}
label.row .cnt{margin-left:auto;color:#898781;font-variant-numeric:tabular-nums}
.sl{display:flex;align-items:center;gap:6px;padding:1px 0}
.sl span.k{width:118px;color:#52514e}
.sl input{flex:1}
.sl span.v{width:18px;text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
#search{width:100%;box-sizing:border-box;font:inherit;padding:5px 8px;border:1px solid rgba(11,11,11,.15);border-radius:7px;margin-bottom:2px}
.note{color:#898781;font-size:10.5px;line-height:1.5;margin-top:8px;border-top:1px solid rgba(11,11,11,.08);padding-top:6px}
.leaflet-popup-content{font-size:12.5px;line-height:1.5}
.leaflet-popup-content b{font-size:13px}
.pc{color:#52514e;font-size:11.5px}
</style></head><body>
<div id="map"></div>
<div id="panel">
 <h1>BTS place·hub 지도 <span style="color:#898781;font-weight:400">before</span></h1>
 <div class="meta">__META__</div>
 <input id="search" type="search" placeholder="place 이름 검색 (Enter)">
 <h2>레이어</h2>
 <label class="row"><input type="checkbox" id="ly-place" checked><span class="sw" style="background:#2a78d6"></span>place (hub 분류색)<span class="cnt" id="c-place"></span></label>
 <label class="row"><input type="checkbox" id="ly-edge"><span class="sw" style="background:#c3c2b7"></span>L-space 엣지<span class="cnt" id="c-edge"></span></label>
 <label class="row"><input type="checkbox" id="ly-pole"><span class="sw" style="background:#199e70"></span>pole + 병합선<span class="cnt" id="c-pole"></span></label>
 <label class="row"><input type="checkbox" id="ly-um"><span class="sw" style="background:#eda100"></span>진단: 같은 이름 분리<span class="cnt" id="c-um"></span></label>
 <label class="row"><input type="checkbox" id="ly-al"><span class="sw" style="background:#9085e9"></span>진단: 다른 이름 근접<span class="cnt" id="c-al"></span></label>
 <h2>자격 술어 빌더 (모양까지 조합 가능)</h2>
 <div id="blk-c" style="border:1px solid rgba(11,11,11,.1);border-radius:8px;padding:6px 8px;margin-bottom:6px">
  <label class="row" style="font-weight:600"><input type="checkbox" id="c-on" checked>1군 (CROSSING/HUB)</label>
  <div class="sl"><span class="k"><input type="checkbox" id="c-dOn" checked> D ≥</span><input type="range" id="c-d" min="1" max="10" value="3"><span class="v" id="cv-d">3</span></div>
  <div class="sl"><span class="k"><select id="c-aMode"><option value="off">A 사용 안 함</option><option value="ge" selected>A ≥</option><option value="le">A ≤</option></select></span><input type="range" id="c-a" min="1" max="6" value="3"><span class="v" id="cv-a">3</span></div>
  <div class="sl"><span class="k"><input type="checkbox" id="c-lOn"> L* ≥</span><input type="range" id="c-l" min="1" max="10" value="5"><span class="v" id="cv-l">5</span></div>
 </div>
 <div id="blk-t" style="border:1px solid rgba(11,11,11,.1);border-radius:8px;padding:6px 8px;margin-bottom:6px">
  <label class="row" style="font-weight:600"><input type="checkbox" id="t-on" checked>2군 (TERMINAL) — 끄면 단일 술어 모드</label>
  <div class="sl"><span class="k"><input type="checkbox" id="t-dOn" checked> D ≥</span><input type="range" id="t-d" min="1" max="10" value="5"><span class="v" id="tv-d">5</span></div>
  <div class="sl"><span class="k"><select id="t-aMode"><option value="off">A 사용 안 함</option><option value="ge">A ≥</option><option value="le" selected>A ≤</option></select></span><input type="range" id="t-a" min="1" max="6" value="2"><span class="v" id="tv-a">2</span></div>
  <div class="sl"><span class="k"><input type="checkbox" id="t-lOn" checked> L* ≥</span><input type="range" id="t-l" min="1" max="10" value="5"><span class="v" id="tv-l">5</span></div>
 </div>
 <div class="sl"><span class="k">L* 게이트 D ≥</span><input type="range" id="s-g" min="1" max="6" value="3"><span class="v" id="v-g">3</span></div>
 <div id="formula" style="font-size:11px;color:#52514e;background:#f0efec;border-radius:6px;padding:5px 7px;margin-top:4px;line-height:1.5"></div>
 <h2>집계 (현재 임계)</h2>
 <label class="row"><span class="sw" style="background:#2a78d6"></span>1군 (CROSSING/HUB)<span class="cnt" id="n-cross"></span></label>
 <label class="row"><span class="sw" style="background:#e34948"></span>2군 (TERMINAL)<span class="cnt" id="n-term"></span></label>
 <label class="row"><span class="sw" style="background:#898781"></span>일반 place<span class="cnt" id="n-none"></span></label>
 <label class="row"><span class="sw" style="background:#fff;border:2px solid #eb6834;box-sizing:border-box"></span>D=0 (증거 부재)<span class="cnt" id="n-d0"></span></label>
 <div class="note">술어 모양(조건 조합)까지 클라이언트에서 바꿀 수 있다 — A "사용 안 함"이면 기하 무관 술어. 단 A 값 자체는 산출물 고정(θ=45°, ADR-009): θ 변경·L 정의 변형은 s03 재실행 필요.
 D=0은 backbone 미커버(ADR-010) 등 증거 부재이지 고립 판정이 아님. 진단 오버레이는
 override 검토 재료이며 병합 당위를 뜻하지 않음.</div>
</div>
<script>
const DATA=__DATA__;
const P=DATA.P,E=DATA.E,PO=DATA.PO,UM=DATA.UM,AL=DATA.AL;
const map=L.map('map',{preferCanvas:true});
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,
 attribution:'&copy; OpenStreetMap'}).addTo(map);
map.fitBounds(P.map(p=>[p[0],p[1]]));
const cv=L.canvas({padding:.3});

function lstar(p,g){return p[5]>=g?p[7]:0}
function cls(p,th){
 if(passes(p,th.c,th.g))return'CROSSING';
 if(passes(p,th.t,th.g))return'TERMINAL';
 return'NONE'}
const COL={CROSSING:'#2a78d6',TERMINAL:'#e34948',NONE:'#898781'};

function rd(id){return +document.getElementById(id).value}
function ck(id){return document.getElementById(id).checked}
function sel(id){return document.getElementById(id).value}
function block(pre){return{on:ck(pre+'-on'),dOn:ck(pre+'-dOn'),d:rd(pre+'-d'),
 aMode:sel(pre+'-aMode'),a:rd(pre+'-a'),lOn:ck(pre+'-lOn'),l:rd(pre+'-l')}}
let th={c:block('c'),t:block('t'),g:3};
function passes(p,b,g){
 if(!b.on)return false;
 if(b.dOn&&p[5]<b.d)return false;
 if(b.aMode==='ge'&&p[6]<b.a)return false;
 if(b.aMode==='le'&&p[6]>b.a)return false;
 if(b.lOn&&lstar(p,g)<b.l)return false;
 return b.dOn||b.aMode!=='off'||b.lOn}
function fml(b,name){
 if(!b.on)return name+' = (꺼짐)';
 const c=[];if(b.dOn)c.push('D≥'+b.d);
 if(b.aMode==='ge')c.push('A≥'+b.a);if(b.aMode==='le')c.push('A≤'+b.a);
 if(b.lOn)c.push('L*≥'+b.l);
 return name+' = '+(c.join(' ∧ ')||'(조건 없음)')}
function showFormula(){document.getElementById('formula').innerHTML=
 fml(th.c,th.t.on?'CROSSING':'HUB(단일)')+'<br>'+fml(th.t,'TERMINAL')+'<br>L* = L if D≥'+th.g+' else 0'}
const placeLayer=L.layerGroup().addTo(map);
const markers=P.map((p,i)=>{
 const m=L.circleMarker([p[0],p[1]],{renderer:cv});
 m.bindPopup(()=>{
  const c=cls(p,th);
  return `<b>${p[2]}</b> <span class="pc">(${c==='NONE'?(p[5]===0?'D=0':'일반'):c})</span><br>
   <span class="pc">D=${p[5]} · A=${p[6]} · L=${p[7]} · L*=${lstar(p,th.g)}<br>
   pole ${p[3]}개 · 공간 범위 ${p[4]}m${p[8]?`<br>D=0 사유: ${p[8]}`:''}</span>`});
 return m});
function styleAll(){
 let n={CROSSING:0,TERMINAL:0,NONE:0,D0:0};
 P.forEach((p,i)=>{
  const c=cls(p,th);n[c]++;if(p[5]===0)n.D0++;
  const m=markers[i];
  if(p[5]===0){m.setStyle({radius:3,color:'#eb6834',weight:1.5,fillColor:'#fff',fillOpacity:.15})}
  else{m.setStyle({radius:c==='NONE'?2.5:4+Math.sqrt(p[5])*1.6,color:'#fff',weight:c==='NONE'?0:1,
   fillColor:COL[c],fillOpacity:c==='NONE'?.45:.9})}});
 document.getElementById('n-cross').textContent=n.CROSSING;
 document.getElementById('n-term').textContent=n.TERMINAL;
 document.getElementById('n-none').textContent=n.NONE-n.D0;
 document.getElementById('n-d0').textContent=n.D0;}
markers.forEach(m=>placeLayer.addLayer(m));
styleAll();

const edgeLayer=L.layerGroup(E.map(e=>L.polyline([[P[e[0]][0],P[e[0]][1]],[P[e[1]][0],P[e[1]][1]]],
 {renderer:cv,color:'#898781',weight:.6+Math.log1p(e[2])*.5,opacity:.4})
 .bindPopup(`L-space 인접 · 경유 노선 ${e[2]}개`)));
const poleLayer=L.layerGroup(PO.flatMap(q=>[
 L.circleMarker([q[0],q[1]],{renderer:cv,radius:2,color:'#199e70',weight:0,fillColor:'#199e70',fillOpacity:.7})
  .bindPopup(`<b>${q[2]}</b> <span class="pc">pole ‥${q[3]} → ${P[q[4]][2]}</span>`),
 L.polyline([[q[0],q[1]],[P[q[4]][0],P[q[4]][1]]],{renderer:cv,color:'#199e70',weight:.7,opacity:.45})]));
const umLayer=L.layerGroup(UM.map(u=>L.polyline([[P[u[0]][0],P[u[0]][1]],[P[u[1]][0],P[u[1]][1]]],
 {color:'#eda100',weight:2.5,dashArray:'5 4',opacity:.9})
 .bindPopup(`<b>같은 이름 분리</b> '${u[3]}'<br><span class="pc">클러스터 간 ${u[2]}m — override 검토 후보</span>`)));
const alLayer=L.layerGroup(AL.map(a=>L.polyline([[P[a[0]][0],P[a[0]][1]],[P[a[1]][0],P[a[1]][1]]],
 {color:'#9085e9',weight:2.5,dashArray:'2 4',opacity:.9})
 .bindPopup(`<b>다른 이름 근접</b><br><span class="pc">'${a[3]}' ↔ '${a[4]}' · ${a[2]}m — alias 후보(제안)</span>`)));

document.getElementById('c-place').textContent=P.length;
document.getElementById('c-edge').textContent=E.length;
document.getElementById('c-pole').textContent=PO.length;
document.getElementById('c-um').textContent=UM.length;
document.getElementById('c-al').textContent=AL.length;
const LY={'ly-place':placeLayer,'ly-edge':edgeLayer,'ly-pole':poleLayer,'ly-um':umLayer,'ly-al':alLayer};
for(const[id,layer]of Object.entries(LY)){
 document.getElementById(id).addEventListener('change',ev=>{
  ev.target.checked?map.addLayer(layer):map.removeLayer(layer)})}

function rewire(){th={c:block('c'),t:block('t'),g:rd('s-g')};
 for(const pre of['c','t'])for(const k of['d','a','l'])
  document.getElementById(pre+'v-'+k).textContent=rd(pre+'-'+k);
 document.getElementById('v-g').textContent=rd('s-g');
 showFormula();styleAll()}
for(const id of['c-on','c-dOn','c-aMode','c-lOn','t-on','t-dOn','t-aMode','t-lOn'])
 document.getElementById(id).addEventListener('change',rewire);
for(const id of['c-d','c-a','c-l','t-d','t-a','t-l','s-g'])
 document.getElementById(id).addEventListener('input',rewire);
showFormula();

document.getElementById('search').addEventListener('keydown',ev=>{
 if(ev.key!=='Enter')return;
 const q=ev.target.value.trim();if(!q)return;
 const i=P.findIndex(p=>p[2].includes(q));
 if(i>=0){map.setView([P[i][0],P[i][1]],16);markers[i].openPopup()}});
</script></body></html>"""

html = html.replace("__META__", meta).replace("__DATA__", data)
out = ROOT / "viewers" / "place_hub_map_before.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html, encoding="utf-8")
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print(f"생성: {out} ({out.stat().st_size // 1024} KB) - {meta}")
