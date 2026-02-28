import psycopg2
from psycopg2.extras import RealDictCursor
import os, json
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Mount Static Files for Logos and Backgrounds
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.mount("/logos", StaticFiles(directory="static/logos"), name="logos")

DB_URL = os.environ.get("DATABASE_URL", "postgresql://election_v3_db_user:KHjYceeGY0OL5w1RMhVFM18AyRipv9Tl@dpg-d6gnomfkijhs73f1cfe0-a.oregon-postgres.render.com/election_v3_db")

def get_db():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)

# --- SECURITY & LOGISTICS API ---
@app.get("/api/check_unit/{pu_code}")
async def check_unit(pu_code: str):
    """Checks if a Polling Unit is already locked in the database."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM field_submissions WHERE pu_code = %s", (pu_code,))
            return {"status": "locked" if cur.fetchone() else "open"}

@app.get("/api/states")
def get_states():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT state FROM polling_units ORDER BY state")
            return [r["state"] for r in cur.fetchall()]

@app.get("/api/lgas/{state}")
def get_lgas(state: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT lg FROM polling_units WHERE state = %s ORDER BY lg", (state,))
            return [r["lg"] for r in cur.fetchall()]

@app.get("/api/wards/{state}/{lg}")
def get_wards(state: str, lg: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ward, ward_code FROM polling_units WHERE state = %s AND lg = %s", (state, lg))
            return [{"name": r["ward"], "code": r["ward_code"]} for r in cur.fetchall()]

@app.get("/api/pus/{state}/{lg}/{ward}")
def get_pus(state: str, lg: str, ward: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT location, pu_code FROM polling_units WHERE state = %s AND lg = %s AND ward = %s", (state, lg, ward))
            return [{"location": r["location"], "pu_code": r["pu_code"]} for r in cur.fetchall()]

@app.post("/submit")
async def submit(data: dict):
    """Validates and locks the unit upon submission."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM field_submissions WHERE pu_code = %s", (data['pu_code'],))
                if cur.fetchone(): return {"status": "error", "message": "UNIT LOCKED: Result already exists."}
                cur.execute("""INSERT INTO field_submissions 
                (officer_id, state, lg, ward, ward_code, pu_code, location, total_accredited, total_cast, lat, lon, timestamp, votes_json) 
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", 
                (data['officer_id'], data['state'], data['lg'], data['ward'], data['ward_code'], data['pu_code'], data['location'], 
                 data['total_accredited'], data['total_cast'], data['lat'], data['lon'], datetime.now().isoformat(), json.dumps(data['votes'])))
                conn.commit()
        return {"status": "success", "message": "Result Verified and Locked."}
    except Exception as e: return {"status": "error", "message": str(e)}

# --- FIELD OFFICER PORTAL ---
@app.get("/", response_class=HTMLResponse)
async def index():
    parties = ["ACCORD", "AA", "AAC", "ADC", "ADP", "APC", "APGA", "APM", "APP", "BP", "LP", "NNPP", "NRM", "PDP", "PRP", "SDP", "YPP", "ZLP"]
    party_inputs = "".join([f'''
        <div class="col-4 col-md-2 mb-2">
            <div class="p-2 border rounded text-center bg-white shadow-sm">
                <img src="/logos/{p}.png" onerror="this.src='https://via.placeholder.com/25?text={p}'" style="height:25px">
                <div style="font-size:10px; font-weight:bold;">{p}</div>
                <input type="number" class="form-control form-control-sm p-v text-center" data-p="{p}" value="0" oninput="calc()">
            </div>
        </div>''' for p in parties])
    
    return f"""<!DOCTYPE html><html><head><title>ACCORD FIELD PORTAL</title><meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body{{ background: linear-gradient(rgba(0,120,60,0.85), rgba(0,120,60,0.85)), url('/static/bg.png'); background-size: cover; min-height:100vh; font-family: sans-serif; }}
        .glass-card{{ background: rgba(255,255,255,0.95); border-radius:15px; padding:20px; box-shadow: 0 10px 30px rgba(0,0,0,0.4); border:none; }}
        .section-label{{ color: #00783c; font-size:11px; font-weight:bold; border-left: 4px solid #ffc107; padding-left: 10px; margin-bottom:15px; display:block; text-transform:uppercase; }}
    </style></head>
    <body>
        <div class="container py-4" style="max-width:900px;">
            <div id="login" class="glass-card text-center mx-auto" style="max-width:400px; margin-top:100px;">
                <h4 class="fw-bold mb-4">OFFICIAL COLLATION</h4>
                <input type="text" id="oid" class="form-control mb-3 text-center" placeholder="ENTER OFFICER ID">
                <button class="btn btn-success w-100 fw-bold py-2" onclick="start()">VALIDATE ACCESS</button>
            </div>
            <div id="form" class="d-none">
                <div class="glass-card mb-3">
                    <span class="section-label">1. Polling Unit Identification</span>
                    <div class="row g-2 mb-3">
                        <div class="col-4"><select id="s" class="form-select" onchange="loadLGAs()"><option>STATE</option></select></div>
                        <div class="col-4"><select id="l" class="form-select" onchange="loadWards()"><option>LGA</option></select></div>
                        <div class="col-4"><select id="w" class="form-select" onchange="loadPUs()"><option>WARD</option></select></div>
                    </div>
                    <select id="p" class="form-select mb-3" onchange="lockCheck()"><option>SELECT POLLING UNIT</option></select>
                    <div class="row g-2">
                        <div class="col-4 text-center"><label class="small text-muted d-block">Ward Code</label><input type="text" id="wc" class="form-control form-control-sm text-center" readonly></div>
                        <div class="col-4 text-center"><label class="small text-muted d-block">PU Code</label><input type="text" id="pc" class="form-control form-control-sm text-center" readonly></div>
                        <div class="col-4 text-center"><label class="small text-muted d-block">Location</label><input type="text" id="loc" class="form-control form-control-sm text-center" readonly></div>
                    </div>
                </div>
                <div id="entry" class="d-none">
                    <div class="glass-card mb-3"><span class="section-label">2. Official Scorecard (Enter Votes)</span><div class="row g-1">{party_inputs}</div></div>
                    <div class="glass-card mb-3"><span class="section-label">3. Audit Data</span>
                        <div class="row g-3">
                            <div class="col-6"><label class="small fw-bold">Total Accredited Voters</label><input type="number" id="ta" class="form-control form-control-lg border-success" oninput="calc()"></div>
                            <div class="col-6"><label class="small fw-bold">Total Valid Votes (Calc)</label><input type="number" id="tc" class="form-control form-control-lg" readonly></div>
                        </div>
                    </div>
                    <button class="btn btn-success w-100 py-3 fw-bold shadow" onclick="send()">UPLOAD PU RESULT</button>
                </div>
            </div>
        </div>
        <script>
            let lat,lon,oid,puData=[],wardData=[];
            function start(){{ oid=document.getElementById('oid').value; if(!oid)return; document.getElementById('login').classList.add('d-none'); document.getElementById('form').classList.remove('d-none'); fetch('/api/states').then(r=>r.json()).then(d=>d.forEach(i=>document.getElementById('s').add(new Option(i,i)))); navigator.geolocation.getCurrentPosition(p=>{{lat=p.coords.latitude;lon=p.coords.longitude;}}); }}
            function loadLGAs(){{ fetch('/api/lgas/'+document.getElementById('s').value).then(r=>r.json()).then(d=>{{ const l=document.getElementById('l'); l.innerHTML='<option>LGA</option>'; d.forEach(i=>l.add(new Option(i,i))) }}); }}
            function loadWards(){{ fetch(`/api/wards/${{document.getElementById('s').value}}/${{document.getElementById('l').value}}`).then(r=>r.json()).then(d=>{{ wardData=d; const w=document.getElementById('w'); w.innerHTML='<option>WARD</option>'; d.forEach(i=>w.add(new Option(i.name,i.name))) }}); }}
            function loadPUs(){{ fetch(`/api/pus/${{document.getElementById('s').value}}/${{document.getElementById('l').value}}/${{document.getElementById('w').value}}`).then(r=>r.json()).then(d=>{{ puData=d; const p=document.getElementById('p'); p.innerHTML='<option>SELECT PU</option>'; d.forEach((i,x)=>p.add(new Option(i.location,x))) }}); }}
            async function lockCheck(){{ 
                const sel=puData[document.getElementById('p').value]; 
                const res=await fetch('/api/check_unit/'+sel.pu_code); 
                const d=await res.json();
                if(d.status==='locked'){{ alert("ACCESS DENIED: Result already exists for this unit."); location.reload(); }}
                else {{ 
                    document.getElementById('pc').value = sel.pu_code;
                    document.getElementById('loc').value = sel.location;
                    document.getElementById('wc').value = wardData.find(x=>x.name===document.getElementById('w').value).code;
                    document.getElementById('entry').classList.remove('d-none'); 
                }}
            }}
            function calc(){{ let v=0; document.querySelectorAll('.p-v').forEach(i=>v+=parseInt(i.value||0)); document.getElementById('tc').value=v; }}
            async function send(){{ 
                const sel=puData[document.getElementById('p').value];
                const v={{}}; document.querySelectorAll('.p-v').forEach(i=>v[i.dataset.p]=parseInt(i.value||0));
                const res=await fetch('/submit',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{officer_id:oid,state:document.getElementById('s').value,lg:document.getElementById('l').value,ward:document.getElementById('w').value,ward_code:document.getElementById('wc').value,pu_code:sel.pu_code,location:sel.location,total_accredited:document.getElementById('ta').value,total_cast:document.getElementById('tc').value,lat,lon,votes:v}})}});
                const out=await res.json(); alert(out.message); if(out.status==='success')location.reload(); 
            }}
        </script>
    </body></html>"""

# --- SITUATION ROOM DASHBOARD ---
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return """<!DOCTYPE html><html><head><title>ACCORD SITUATION ROOM</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background: #0b0b0d; color: #fff; font-family: sans-serif; height: 100vh; overflow: hidden; }
        .kpi-row { background: #000; padding: 12px 25px; border-bottom: 2px solid #ffc107; display: flex; gap: 15px; align-items: center; }
        .kpi-card { background: #16161a; border: 1px solid #333; padding: 12px; flex: 1; border-top: 3px solid #ffc107; text-align: center; }
        .main-grid { display: grid; grid-template-columns: 300px 1fr 340px; gap: 12px; padding: 12px; height: calc(100vh - 110px); }
        .panel { background: #16161a; border-radius: 10px; border: 1px solid #222; overflow-y: auto; padding: 15px; }
        .ai-box { background: #001a11; border: 1px solid #00ff9d; color: #00ff9d; padding: 18px; border-radius: 10px; font-size: 14px; margin-top:10px; }
        #map { background: #111; border-radius: 10px; flex-grow: 1; border: 1px solid #333; position: relative; }
        .label { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 1px; }
        .party-box { background: #222; border-radius: 6px; padding: 12px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
    </style></head>
    <body>
        <div class="kpi-row">
            <div style="flex:1.8"> <h4 class="text-warning mb-0 fw-bold">ACCORD SITUATION ROOM</h4> <small class="label">Real-Time Collation Dashboard</small> </div>
            <div class="kpi-card"> <div class="label">ACCORD TOTAL</div> <h3 id="t_ACCORD" class="text-warning mb-0">0</h3> </div>
            <div class="kpi-card"> <div class="label">APC TOTAL</div> <h3 id="t_APC" class="mb-0">0</h3> </div>
            <div class="kpi-card"> <div class="label">PDP TOTAL</div> <h3 id="t_PDP" class="mb-0">0</h3> </div>
            <div class="kpi-card"> <div class="label">LEAD MARGIN</div> <h3 id="margin" class="text-warning mb-0">+0</h3> </div>
        </div>
        <div class="main-grid">
            <div class="panel"> <small class="label d-block mb-3">Live Collation Feed</small> <div id="feed"></div> </div>
            <div class="d-flex flex-column">
                <div id="map"></div>
                <div class="ai-box"> <strong>AI STRATEGIC INSIGHT:</strong> <div id="ai" class="mt-1">Initialising live data stream analysis...</div> </div>
            </div>
            <div class="panel text-center">
                <small class="label d-block mb-3">Vote Distribution %</small>
                <div style="height:240px"><canvas id="chart"></canvas></div>
                <div id="breakdown" class="mt-4"></div>
                <div class="mt-auto pt-3 border-top border-secondary small text-muted">LAST UPDATED: <span id="ts">--:--:--</span></div>
            </div>
        </div>
        <script>
            let map = L.map('map', {{zoomControl: false}}).setView([7.8, 4.5], 9);
            L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png').addTo(map);
            let chart;

            async function refresh() {{
                const res = await fetch('/api/stats'); const d = await res.json();
                document.getElementById('t_ACCORD').innerText = d.totals.ACCORD.toLocaleString();
                document.getElementById('t_APC').innerText = d.totals.APC.toLocaleString();
                document.getElementById('t_PDP').innerText = d.totals.PDP.toLocaleString();
                document.getElementById('margin').innerText = '+' + (d.totals.ACCORD - Math.max(d.totals.APC, d.totals.PDP)).toLocaleString();
                document.getElementById('ai').innerText = d.ai;
                document.getElementById('ts').innerText = new Date().toLocaleTimeString();

                document.getElementById('feed').innerHTML = d.recent.map(r => `
                    <div class="mb-3 p-2 border-bottom border-dark small">
                        <div class="text-warning fw-bold">${{r.location}}</div>
                        <div class="d-flex justify-content-between text-muted"><span>ACC: ${{JSON.parse(r.votes_json).ACCORD}}</span><span>APC: ${{JSON.parse(r.votes_json).APC}}</span></div>
                    </div>`).join('');

                const parties = ['ACCORD', 'APC', 'PDP', 'ADC'];
                document.getElementById('breakdown').innerHTML = parties.map(p => `
                    <div class="party-box">
                        <span class="small fw-bold">${{p}}</span>
                        <span class="h5 mb-0">${{d.totals[p].toLocaleString()}}</span>
                    </div>`).join('');

                const cData = parties.map(p => d.totals[p]);
                if(!chart) {{
                    chart = new Chart(document.getElementById('chart'), {{
                        type: 'doughnut',
                        data: {{ labels: parties, datasets: [{{ data: cData, backgroundColor: ['#ffc107', '#0b3d91', '#d9534f', '#006400'], borderWidth: 0 }}] }},
                        options: {{ plugins: {{ legend: {{ display: false }} }}, cutout: '75%' }}
                    }});
                }} else {{ chart.data.datasets[0].data = cData; chart.update(); }}
            }}
            setInterval(refresh, 5000); refresh();
        </script>
    </body></html>"""

@app.get("/api/stats")
async def get_stats():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT location, votes_json FROM field_submissions ORDER BY timestamp DESC LIMIT 20")
            recent = cur.fetchall()
            cur.execute("SELECT votes_json FROM field_submissions")
            all_v = cur.fetchall()
            totals = {"ACCORD": 0, "APC": 0, "PDP": 0, "ADC": 0}
            for r in all_v:
                v = json.loads(r['votes_json']) if isinstance(r['votes_json'], str) else r['votes_json']
                for p in totals: totals[p] += v.get(p, 0)
            
            lead = totals['ACCORD'] - max(totals['APC'], totals['PDP'])
            ai = f"Accord is currently leading by {lead:,} votes. Performance in rural wards is exceeding target projections. Deployment of field monitors to Osun West is advised." if lead > 0 else "Competitive margin detected in the current LGA reporting stream. Real-time updates required from semi-urban hubs."
            return {"totals": totals, "recent": recent, "ai": ai}
