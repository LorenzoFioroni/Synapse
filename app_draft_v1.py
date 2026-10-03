from __future__ import annotations
import json, re, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template_string, request
from rapidfuzz import fuzz

app=Flask(__name__)
BASE='http://localhost:23119/api'
UA='ZoteroLibraryCleaner/0.1 (local research utility)'
AUDIT=Path(__file__).with_name('audit.sqlite3')
state={'server_id':None,'write_key':None,'scan':{}}
DOI_RE=re.compile(r'^10\.\d{4,9}/\S+$',re.I)
ARXIV_RE=re.compile(r'(?:https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/|arxiv:)?((?:\d{4}\.\d{4,5})(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)',re.I)

def audit_db():
    c=sqlite3.connect(AUDIT); c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,ts TEXT,item_key TEXT,action TEXT,before_json TEXT,after_json TEXT,status TEXT,message TEXT)')
    c.commit(); return c

def audit(key,action,before,after,status,message=''):
    c=audit_db(); c.execute('INSERT INTO audit(ts,item_key,action,before_json,after_json,status,message) VALUES(?,?,?,?,?,?,?)',(
        datetime.now(timezone.utc).isoformat(),key,action,json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),status,message)); c.commit(); c.close()

def zreq(method='GET',path='',**kw):
    h={'Zotero-API-Version':'3','User-Agent':UA,'Zotero-Allowed-Request':'ZoteroLibraryCleaner'}
    if state['server_id']: h['Zotero-Server-ID']=state['server_id']
    if method!='GET' and state['write_key']: h['Zotero-API-Key']=state['write_key']
    r=requests.request(method,f'{BASE}/{path.lstrip("/")}',headers=h,timeout=30,**kw)
    if r.headers.get('Zotero-Server-ID'): state['server_id']=r.headers['Zotero-Server-ID']
    if r.status_code>=400: raise RuntimeError(f'Zotero API {r.status_code}: {r.text[:800]}')
    return r.json() if r.content else None

def connect():
    r=requests.get(BASE+'/',headers={'Zotero-API-Version':'3','User-Agent':UA},timeout=5); r.raise_for_status()
    state['server_id']=r.headers.get('Zotero-Server-ID'); return state['server_id']

def authorize():
    if not state['server_id']: connect()
    h={'Zotero-API-Version':'3','Zotero-Server-ID':state['server_id'],'User-Agent':UA,'Zotero-Allowed-Request':'ZoteroLibraryCleaner','Content-Type':'application/json'}
    r=requests.post(BASE+'/local/authorize',headers=h,json={'appName':'Zotero Library Cleaner'},timeout=30)
    if r.status_code>=400: raise RuntimeError(f'Authorization failed ({r.status_code}): {r.text[:800]}')
    state['write_key']=r.json()['key']; return r.json()

def all_items():
    out=[]; start=0
    while True:
        x=zreq('GET',f'users/0/items?format=json&limit=100&start={start}&includeTrashed=1')
        out+=x
        if len(x)<100: return out
        start+=100
        if start>1000000: raise RuntimeError('Aborted: more than 1,000,000 items')

def norm(x): return re.sub(r'\s+',' ',re.sub(r'[\W_]+',' ',(x or '').lower())).strip()
def doi(x): return re.sub(r'^https?://(dx\.)?doi\.org/|^doi:\s*','',x or '',flags=re.I).rstrip(' .;,').strip()
def arxiv_id(x):
    m=ARXIV_RE.search(x or ''); return m.group(1) if m else ''
def names(cs):
    return [c.get('name') or ' '.join(y for y in [c.get('firstName'),c.get('lastName')] if y) for c in cs or []]
def summary(it):
    d=it.get('data',{}); return {'key':it.get('key'),'version':it.get('version'),'itemType':d.get('itemType',''),'title':d.get('title',''),'date':d.get('date',''),'year':(d.get('date') or '')[:4],'doi':doi(d.get('DOI','')),'journal':d.get('publicationTitle',''),'creators':names(d.get('creators',[])),'raw':d,'numChildren':it.get('meta',{}).get('numChildren',0)}

def scan(items):
    ss=[summary(x) for x in items]; dm={}; tm={}
    for s in ss:
        if s['doi']: dm.setdefault(s['doi'].lower(),[]).append(s['key'])
        if s['title']: tm.setdefault(norm(s['title']),[]).append(s['key'])
    problems=[]
    for s in ss:
        f=[]; d=s['raw']; typ=s['itemType']
        if not s['title'].strip(): f.append('missing title')
        if typ in {'journalArticle','conferencePaper'} and not s['creators']: f.append('missing creators')
        if s['doi'] and not DOI_RE.match(s['doi']): f.append('malformed DOI')
        if d.get('DOI') and doi(d['DOI'])!=d['DOI'].strip(): f.append('DOI needs normalization')
        if s['doi'] and len(dm.get(s['doi'].lower(),[]))>1: f.append('duplicate DOI')
        if s['title'] and len(tm.get(norm(s['title']),[]))>1: f.append('exact duplicate title')
        if typ=='webpage' and s['doi']: f.append('webpage with DOI')
        if typ=='journalArticle' and not s['journal']: f.append('missing journal')
        if not s['date'] and typ in {'journalArticle','conferencePaper','bookSection'}: f.append('missing date')
        if f: problems.append({'item':s,'flags':f})
    near=[]; blocks={}
    for s in ss:
        if s['title']: blocks.setdefault(norm(s['title'])[:8],[]).append(s)
    for b in blocks.values():
        if len(b)>80: continue
        for i in range(len(b)):
            for j in range(i+1,len(b)):
                a,c=b[i],b[j]
                if a['key']==c['key']: continue
                q=fuzz.token_set_ratio(a['title'],c['title'])
                if q>=92 and not (a['doi'] and c['doi'] and a['doi'].lower()==c['doi'].lower()): near.append({'a':a,'b':c,'score':round(q,1)})
    return {'count':len(ss),'problems':problems,'near_duplicates':near[:5000]}

def crossref(x):
    x=doi(x); r=requests.get('https://api.crossref.org/works/'+requests.utils.quote(x,safe=''),headers={'User-Agent':UA},timeout=20)
    if r.status_code!=200: raise RuntimeError(f'Crossref returned {r.status_code}')
    m=r.json()['message']; cs=[{'creatorType':'author','firstName':a.get('given',''),'lastName':a.get('family','')} for a in m.get('author',[])]
    year=''
    for k in ('published-print','published-online','issued','created'):
        p=m.get(k,{}).get('date-parts',[])
        if p and p[0]: year=str(p[0][0]); break
    return {'source':'Crossref','title':(m.get('title') or [''])[0],'creators':cs,'date':year,'publicationTitle':(m.get('container-title') or [''])[0],'volume':m.get('volume',''),'issue':m.get('issue',''),'pages':m.get('page',''),'DOI':x,'url':m.get('URL','')}

def arxiv(x):
    x=arxiv_id(x); r=requests.get('https://export.arxiv.org/api/query',params={'id_list':x},headers={'User-Agent':UA},timeout=20)
    if r.status_code!=200: raise RuntimeError(f'arXiv returned {r.status_code}')
    import xml.etree.ElementTree as ET
    root=ET.fromstring(r.text); ns={'a':'http://www.w3.org/2005/Atom'}; e=root.find('a:entry',ns)
    if e is None: raise RuntimeError('arXiv item not found')
    title=' '.join(e.findtext('a:title',default='',namespaces=ns).split()); date=e.findtext('a:published',default='',namespaces=ns)[:10]
    cs=[]
    for a in e.findall('a:author',ns):
        n=a.findtext('a:name',default='',namespaces=ns).strip(); p=n.rsplit(' ',1); cs.append({'creatorType':'author','firstName':p[0] if len(p)==2 else '','lastName':p[-1]})
    return {'source':'arXiv','title':title,'creators':cs,'date':date,'url':f'https://arxiv.org/abs/{x}'}

def changes(cur,ext):
    m={'title':'title','date':'date','publicationTitle':'publicationTitle','volume':'volume','issue':'issue','pages':'pages','DOI':'DOI','url':'url'}; out=[]
    for a,b in m.items():
        if ext.get(a) and str(ext[a]).strip()!=str(cur['raw'].get(b,'')).strip(): out.append({'field':b,'old':cur['raw'].get(b,''),'new':ext[a]})
    if ext.get('creators') and [norm(x) for x in names(ext['creators'])]!=[norm(x) for x in names(cur['raw'].get('creators',[]))]: out.append({'field':'creators','old':cur['raw'].get('creators',[]),'new':ext['creators']})
    return out

def apply_item(key,version,chs):
    if not state['write_key']: authorize()
    latest=zreq('GET',f'users/0/items/{key}?format=json')
    if latest.get('version')!=version: raise RuntimeError(f'Item changed since scan (scanned {version}, current {latest.get("version")}). Rescan.')
    before=latest['data'].copy(); payload=latest['data'].copy()
    for c in chs: payload[c['field']]=c['new']
    h={'If-Unmodified-Since-Version':str(version),'Content-Type':'application/json'}
    try: result=zreq('PUT',f'users/0/items/{key}',headers=h,json=payload)
    except RuntimeError as e:
        if '401' not in str(e): raise
        authorize(); result=zreq('PUT',f'users/0/items/{key}',headers=h,json=payload)
    audit(key,'update',before,payload,'applied'); return result

HTML=r'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Zotero Cleaner</title><style>
body{font:14px system-ui;margin:0;background:#f5f6f8;color:#202124}header{background:#202124;color:#fff;padding:20px 28px}main{max-width:1500px;margin:auto;padding:20px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:15px}.panel,.card{background:#fff;border-radius:10px;padding:15px;box-shadow:0 1px 3px #0002;margin-bottom:15px}button,input{padding:8px;border:1px solid #aaa;border-radius:6px}button{cursor:pointer}.primary{background:#1769aa;color:#fff}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.big{font-size:24px;font-weight:700}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}th{background:#eee}.badge{display:inline-block;padding:3px 7px;border-radius:12px;background:#ffd9d9;margin:2px;font-size:11px}.ok{background:#d9f7df}.small{font-size:12px;color:#666}.mono{font-family:monospace}.diff{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.diff div{background:#f0f2f4;padding:8px;border-radius:6px}@media(max-width:800px){.cards{grid-template-columns:repeat(2,1fr)}.diff{grid-template-columns:1fr}}
</style></head><body><header><h1>Zotero Library Cleaner</h1><div>Conservative, local, review-before-write workflow.</div></header><main><div class=toolbar><button class=primary onclick=scan()>Scan library</button><button onclick=connect()>Test connection</button><button onclick=auth()>Authorize edits</button><input id=doi placeholder="DOI"><button onclick=doiLookup()>Crossref</button><input id=arxiv placeholder="arXiv ID"><button onclick=arxivLookup()>arXiv</button></div><div id=status></div><div class=cards><div class=card>Items<div id=count class=big>—</div></div><div class=card>Flagged<div id=problems class=big>—</div></div><div class=card>Near duplicates<div id=near class=big>—</div></div><div class=card>Audit entries<div id=aud class=big>—</div></div></div><div class=panel><h2>Flagged items</h2><input id=filter placeholder="filter" oninput=renderProblems()><div id=pt></div></div><div class=panel><h2>Near-duplicate candidates</h2><div id=nt></div></div><div class=panel><h2>Lookup / proposed changes</h2><div id=lookup></div></div><div class=panel><h2>Audit log</h2><div id=at></div></div></main><script>
let S={problems:[],near_duplicates:[]}; const $=x=>document.getElementById(x); const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(u,o={}){let r=await fetch(u,{headers:{'Content-Type':'application/json'},...o}),j=await r.json().catch(()=>({error:r.statusText}));if(!r.ok)throw Error(j.error||r.statusText);return j} function msg(x,b=false){$('status').innerHTML='<span class="badge '+(b?'':'ok')+'">'+esc(x)+'</span>'}
async function connect(){try{let j=await api('/api/connect');msg('Connected. Server ID '+j.server_id)}catch(e){msg(e.message,true)}} async function auth(){try{await api('/api/authorize',{method:'POST'});msg('Write authorization granted')}catch(e){msg(e.message,true)}}
async function scan(){msg('Scanning Zotero…');try{let j=await api('/api/scan',{method:'POST'});S=j.scan;$('count').textContent=S.count;$('problems').textContent=S.problems.length;$('near').textContent=S.near_duplicates.length;renderProblems();renderNear();await audit();msg('Scan complete')}catch(e){msg(e.message,true)}}
function renderProblems(){let q=($('filter').value||'').toLowerCase(),rows=S.problems.filter(x=>JSON.stringify(x).toLowerCase().includes(q));let h='<table><tr><th>Item</th><th>Flags</th><th>Metadata</th><th></th></tr>';for(let p of rows){let s=p.item;h+=`<tr><td><b>${esc(s.title||'(untitled)')}</b><br><span class=small>${esc(s.key)} · ${esc(s.itemType)}</span></td><td>${p.flags.map(x=>'<span class=badge>'+esc(x)+'</span>').join('')}</td><td>Authors: ${esc(s.creators.join('; '))}<br>Year: ${esc(s.year)}<br>DOI: <span class=mono>${esc(s.doi)}</span><br>Journal: ${esc(s.journal)}</td><td><button onclick="lookupItem('${s.key}')">Lookup</button></td></tr>`}h+='</table>';$('pt').innerHTML=rows.length?h:'<p>No flagged items.</p>'}
function renderNear(){let h='<table><tr><th>Similarity</th><th>A</th><th>B</th></tr>';for(let x of S.near_duplicates)h+=`<tr><td>${x.score}%</td><td>${esc(x.a.title)}<br>${esc(x.a.doi||x.a.key)}</td><td>${esc(x.b.title)}<br>${esc(x.b.doi||x.b.key)}</td></tr>`;h+='</table>';$('nt').innerHTML=S.near_duplicates.length?h:'<p>No candidates.</p>'}
async function lookupItem(key){let p=S.problems.find(x=>x.item.key===key),s=p.item;try{if(!s.doi){$('lookup').innerHTML='<p>This item has no DOI. Use the arXiv box if applicable.</p>';return}let j=await api('/api/crossref',{method:'POST',body:JSON.stringify({doi:s.doi})});renderLookup(s,j.metadata)}catch(e){msg(e.message,true)}}
function renderLookup(cur,e){let h='<h3>'+esc(e.source)+'</h3><div class=diff>';for(let f of ['title','date','publicationTitle','volume','issue','pages','DOI','url'])if(e[f])h+=`<div><b>${f}</b><br>${esc(e[f])}</div>`;h+='</div><h3>Authors</h3><pre>'+esc(JSON.stringify(e.creators||[],null,2))+'</pre>';if(cur){let map={title:'title',date:'date',publicationTitle:'publicationTitle',volume:'volume',issue:'issue',pages:'pages',DOI:'DOI',url:'url'},c=[];for(let a in map)if(e[a]&&String(e[a]).trim()!=String(cur.raw[map[a]]||'').trim())c.push({field:map[a],old:cur.raw[map[a]]||'',new:e[a]});if(e.creators&&JSON.stringify(e.creators)!=JSON.stringify(cur.raw.creators||[]))c.push({field:'creators',old:cur.raw.creators||[],new:e.creators});h+='<h3>Proposed changes</h3>';h+=c.length?'<pre>'+esc(JSON.stringify(c,null,2))+'</pre><button class=primary onclick=applyChanges('+JSON.stringify(cur.key)+','+cur.version+','+JSON.stringify(c)+')>Apply changes</button>':'<p>No differences.</p>'}$('lookup').innerHTML=h}
async function applyChanges(k,v,c){if(!confirm('Apply these changes to Zotero?'))return;try{await api('/api/item/'+encodeURIComponent(k),{method:'PUT',body:JSON.stringify({version:v,changes:c})});msg('Applied '+k);await scan()}catch(e){msg(e.message,true)}}
async function doiLookup(){try{let j=await api('/api/crossref',{method:'POST',body:JSON.stringify({doi:$('doi').value})});renderLookup(null,j.metadata)}catch(e){msg(e.message,true)}} async function arxivLookup(){try{let j=await api('/api/arxiv',{method:'POST',body:JSON.stringify({id:$('arxiv').value})});renderLookup(null,j.metadata)}catch(e){msg(e.message,true)}}
async function audit(){try{let j=await api('/api/audit');$('aud').textContent=j.rows.length;let h='<table><tr><th>Time</th><th>Item</th><th>Action</th><th>Status</th></tr>';for(let x of j.rows)h+=`<tr><td>${esc(x.ts)}</td><td>${esc(x.item_key)}</td><td>${esc(x.action)}</td><td>${esc(x.status)}</td></tr>`;$('at').innerHTML=h+'</table>'}catch(e){}}
audit();
</script></body></html>'''

@app.get('/')
def home(): return render_template_string(HTML)
@app.get('/api/connect')
def api_connect():
    try:return jsonify({'server_id':connect()})
    except Exception as e:return jsonify(error=str(e)),502
@app.post('/api/authorize')
def api_auth():
    try:return jsonify(authorize())
    except Exception as e:return jsonify(error=str(e)),502
@app.post('/api/scan')
def api_scan():
    try:
        connect(); result=scan(all_items()); state['scan']=result; return jsonify(scan=result)
    except Exception as e:return jsonify(error=str(e)),502
@app.post('/api/crossref')
def api_crossref():
    try:return jsonify(metadata=crossref((request.json or {}).get('doi','')))
    except Exception as e:return jsonify(error=str(e)),400
@app.post('/api/arxiv')
def api_arxiv():
    try:return jsonify(metadata=arxiv((request.json or {}).get('id','')))
    except Exception as e:return jsonify(error=str(e)),400
@app.put('/api/item/<key>')
def api_item(key):
    try:
        b=request.json or {}; return jsonify(result=apply_item(key,int(b['version']),b.get('changes',[])))
    except Exception as e:
        audit(key,'update',{}, {},'failed',str(e)); return jsonify(error=str(e)),409
@app.get('/api/audit')
def api_audit():
    c=audit_db(); rows=[dict(x) for x in c.execute('SELECT ts,item_key,action,status,message FROM audit ORDER BY id DESC LIMIT 500')]; c.close(); return jsonify(rows=rows)

if __name__=='__main__':
    print('Open http://127.0.0.1:5000')
    app.run(host='127.0.0.1',port=5000,debug=False)
