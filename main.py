import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
import logging
import io
import csv
from datetime import datetime
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Render-safe Pathing
LOGO_PATH = os.path.join(os.getcwd(), "static", "logos")
if os.path.exists(LOGO_PATH):
    app.mount("/logos", StaticFiles(directory=LOGO_PATH), name="logos")

STATIC_PATH = os.path.join(os.getcwd(), "static")
if os.path.exists(STATIC_PATH):
    app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")

# --- DATABASE CONNECTION ---
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://election_v3_db_user:KHjYceeGY0OL5w1RMhVFM18AyRipv9Tl@dpg-d6gnomfkijhs73f1cfe0-a.oregon-postgres.render.com/election_v3_db")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS field_submissions (
                id SERIAL PRIMARY KEY,
                officer_id TEXT,
                state TEXT, 
                lg TEXT, 
                ward TEXT, 
                ward_code TEXT, 
                pu_code TEXT UNIQUE, 
                location TEXT,
                total_accredited INTEGER, 
                valid_votes INTEGER, 
                lat REAL, 
                lon REAL, 
                timestamp TEXT, 
                votes_json TEXT,
                evidence_image BYTEA
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ DB INIT ERROR: {e}")

init_db()

# --- API ENDPOINTS ---

@app.get("/locations/states")
def get_states():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT state FROM polling_units ORDER BY state")
            return [r["state"] for r in cur.fetchall()]

@app.get("/locations/lgas/{state}")
def get_lgas(state: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT lg FROM polling_units WHERE state = %s ORDER BY lg", (state,))
            return [r["lg"] for r in cur.fetchall()]

@app.get("/locations/wards/{state}/{lg}")
def get_wards(state: str, lg: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ward, ward_code FROM polling_units WHERE state = %s AND lg = %s ORDER BY ward", (state, lg))
            return [{"name": r["ward"], "code": r["ward_code"]} for r in cur.fetchall()]

@app.get("/locations/pus/{state}/{lg}/{ward}")
def get_pus(state: str, lg: str, ward: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT location, pu_code FROM polling_units WHERE state = %s AND lg = %s AND ward = %s", (state, lg, ward))
            return [{"location": r["location"], "pu_code": r["pu_code"]} for r in cur.fetchall()]

@app.post("/submit")
async def submit(
    officer_id: str = Form(...),
    state: str = Form(...),
    lg: str = Form(...),
    ward: str = Form(...),
    ward_code: str = Form(...),
    pu_code: str = Form(...),
    location: str = Form(...),
    total_accredited: int = Form(...),
    valid_votes: int = Form(...),
    lat: float = Form(...),
    lon: float = Form(...),
    votes_data: str = Form(...),
    evidence: UploadFile = File(...)
):
    try:
        img_bytes = await evidence.read()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO field_submissions (
                    officer_id, state, lg, ward, ward_code, pu_code, location,
                    total_accredited, valid_votes, lat, lon, timestamp, votes_json, evidence_image
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                    officer_id, state, lg, ward, ward_code, pu_code, location,
                    total_accredited, valid_votes, lat, lon, datetime.now().isoformat(), votes_data, img_bytes
                ))
                conn.commit()
        return {"status": "success", "message": "Result & Evidence Uploaded Successfully"}
    except psycopg2.IntegrityError:
        return {"status": "error", "message": "Duplicate Entry: This PU has already submitted results."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/submissions")
async def get_dashboard_data():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM field_submissions ORDER BY timestamp DESC")
            data = []
            for r in cur.fetchall():
                v = json.loads(r['votes_json']) if isinstance(r['votes_json'], str) else r['votes_json']
                data.append({
                    "pu_name": r['location'], "state": r['state'], "lga": r['lg'], "ward": r['ward'],
                    "latitude": r['lat'], "longitude": r['lon'],
                    "votes_party_ACCORD": v.get("ACCORD", 0), "votes_party_APC": v.get("APC", 0),
                    "votes_party_PDP": v.get("PDP", 0), "votes_party_ADC": v.get("ADC", 0)
                })
            return data

@app.get("/", response_class=HTMLResponse)
async def index():
    parties = ["ACCORD", "AA", "AAC", "ADC", "ADP", "APC", "APGA", "APM", "APP", "BP", "LP", "NNPP", "NRM", "PDP", "PRP", "SDP", "YPP", "ZLP"]
    party_cards = "".join([f'''
        <div class="col-4 col-md-2 mb-2">
            <div class="p-2 border rounded text-center bg-white shadow-sm">
                <img src="/logos/{p}.png" onerror="this.src='https://via.placeholder.com/30?text={p}'" style="height:30px">
                <small class="d-block fw-bold">{p}</small>
                <input type="number" class="form-control form-control-sm party-v text-center" data-p="{p}" value="0" oninput="calculateTotals()">
            </div>
        </div>''' for p in parties])
    return INDEX_HTML.replace("{party_cards}", party_cards)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML

# --- INDEX_HTML (STRUCTURE & CSS PRESERVED EXACTLY) ---
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IMOLE YOUTH ACCORD MOBILIZATION</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(rgba(0,0,0,0.6), rgba(0,0,0,0.6)), url('/static/bg.png'); background-size: cover; background-attachment: fixed; min-height: 100vh; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .navbar { background: rgba(0, 135, 81, 0.9) !important; color: white; border-bottom: 4px solid #ffc107; }
        .card { background: rgba(255, 255, 255, 0.95) !important; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); border: none; }
        .section-label { font-size: 0.75rem; font-weight: bold; color: #008751; text-transform: uppercase; border-left: 3px solid #ffc107; padding-left: 10px; margin-bottom: 15px; display: block; }
        .party-v { border: 1px solid #ced4da; border-radius: 4px; padding: 4px; font-weight: bold; }
        .btn-success { background-color: #008751; border: none; }
        .btn-success:hover { background-color: #006b41; }
    </style>
</head>
<body>
    <nav class="navbar py-2 mb-4 text-center">
        <div class="container d-flex justify-content-center align-items-center">
            <img src="/logos/ACCORD.png" style="height: 40px; margin-right: 15px;">
            <h5 class="mb-0 fw-bold">OFFICIAL FIELD COLLATION PORTAL</h5>
        </div>
    </nav>

    <div class="container pb-5" style="max-width: 850px;">
        <div id="loginArea" class="card p-5 text-center mx-auto" style="max-width: 400px; margin-top: 50px;">
            <h5 class="mb-3 text-success">FIELD OFFICER LOGIN</h5>
            <p class="small text-muted mb-4">Enter Unit ID (Ward-PU) to start</p>
            <input type="text" id="oid" class="form-control mb-3 text-center py-2" placeholder="e.g. 05-001">
            <button class="btn btn-success w-100 py-2 fw-bold" onclick="start()">VALIDATE UNIT ACCESS</button>
        </div>

        <div id="formArea" class="d-none">
            <div class="card p-4">
                <span class="section-label">1. Polling Unit Identification</span>
                <div class="row g-2">
                    <div class="col-4">
                        <select id="s" class="form-select" onchange="loadLGAsDash()">
                            <option value="">STATE</option>
                        </select>
                    </div>
                    <div class="col-4">
                        <select id="l" class="form-select" onchange="loadWardsDash()">
                            <option value="">LGA</option>
                        </select>
                    </div>
                    <div class="col-4">
                        <select id="w" class="form-select" onchange="loadPUs()">
                            <option value="">WARD</option>
                        </select>
                    </div>
                    <div class="col-12 mt-2">
                        <select id="p" class="form-select" onchange="fillPU()">
                            <option value="">SELECT POLLING UNIT</option>
                        </select>
                    </div>
                </div>
                <div class="row mt-3 g-2">
                    <div class="col-4"><small class="text-muted">Ward Code</small><input type="text" id="wc" class="form-control bg-light" readonly></div>
                    <div class="col-4"><small class="text-muted">PU Code</small><input type="text" id="pc" class="form-control bg-light" readonly></div>
                    <div class="col-4"><small class="text-muted">Location</small><input type="text" id="loc" class="form-control bg-light" readonly></div>
                </div>
            </div>

            <div class="card p-4">
                <span class="section-label">2. Official Scorecard (Enter Votes)</span>
                <div class="row g-2">
                    {party_cards}
                </div>
            </div>

            <div class="card p-4">
                <span class="section-label">3. Audit Data & Court Evidence</span>
                <div class="row g-3">
                    <div class="col-md-6">
                        <label class="small text-muted fw-bold">Total Accredited Voters</label>
                        <input type="number" id="ta" class="form-control" placeholder="From BVAS" oninput="calculateTotals()">
                    </div>
                    <div class="col-md-6">
                        <label class="small text-muted fw-bold">Total Valid Votes (Calculated)</label>
                        <input type="number" id="tc" class="form-control bg-light fw-bold text-success" readonly>
                    </div>
                    <div class="col-12 mt-3">
                        <label class="small fw-bold text-success">Capture & Upload Official EC8A Result Sheet</label>
                        <input type="file" id="evidence" class="form-control" accept="image/*">
                        <p class="x-small text-muted mt-1" style="font-size: 0.7rem;">Capture image clearly. This serves as official evidence.</p>
                    </div>
                </div>
            </div>

            <div class="d-flex gap-2 mb-4">
                <button class="btn btn-outline-light flex-grow-1 py-3" onclick="getGPS()">FIX GPS LOCATION</button>
                <button class="btn btn-success flex-grow-1 py-3 fw-bold" onclick="finalSubmit()">UPLOAD PU RESULT</button>
            </div>
        </div>
    </div>

    <script>
        let lat, lon, officerId, puData = [], wardData = [];

        function start() {
            officerId = document.getElementById('oid').value;
            if(!officerId) return alert("Please enter Unit ID");
            document.getElementById('loginArea').classList.add('d-none');
            document.getElementById('formArea').classList.remove('d-none');
            fetch('/locations/states').then(r=>r.json()).then(data=>{
                const s = document.getElementById('s');
                data.forEach(item => s.add(new Option(item.toUpperCase(), item)));
            });
        }

        function loadLGAsDash() {
            fetch('/locations/lgas/'+encodeURIComponent(document.getElementById('s').value)).then(r=>r.json()).then(data=>{
                const l = document.getElementById('l'); l.innerHTML = '<option value="">LGA</option>';
                data.forEach(item => l.add(new Option(item.toUpperCase(), item)));
            });
        }

        function loadWardsDash() {
            fetch(`/locations/wards/${encodeURIComponent(document.getElementById('s').value)}/${encodeURIComponent(document.getElementById('l').value)}`)
            .then(r=>r.json()).then(data=>{
                wardData = data;
                const w = document.getElementById('w'); w.innerHTML = '<option value="">WARD</option>';
                data.forEach(item => w.add(new Option(item.name.toUpperCase(), item.name)));
            });
        }

        function loadPUs() {
            const w = document.getElementById('w').value;
            const wardObj = wardData.find(x => x.name === w);
            document.getElementById('wc').value = wardObj ? wardObj.code : '';
            fetch(`/locations/pus/${encodeURIComponent(document.getElementById('s').value)}/${encodeURIComponent(document.getElementById('l').value)}/${encodeURIComponent(w)}`)
            .then(r=>r.json()).then(data=>{
                puData = data;
                const p = document.getElementById('p'); p.innerHTML = '<option value="">SELECT POLLING UNIT</option>';
                data.forEach((item, idx) => p.add(new Option(item.location.toUpperCase(), idx)));
            });
        }

        function fillPU() {
            const sel = puData[document.getElementById('p').value];
            document.getElementById('pc').value = sel.pu_code;
            document.getElementById('loc').value = sel.location.toUpperCase();
        }

        function calculateTotals() {
            let valid = 0; document.querySelectorAll('.party-v').forEach(i => valid += parseInt(i.value || 0));
            document.getElementById('tc').value = valid;
        }

        function getGPS() { 
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(p => { 
                    lat=p.coords.latitude; lon=p.coords.longitude; alert("GPS Fixed: " + lat + "," + lon); 
                }, () => alert("GPS Error: Enable location services."));
            }
        }

        async function finalSubmit() {
            if(!lat) return alert("Please Fix GPS location first.");
            const evidenceImg = document.getElementById('evidence').files[0];
            if(!evidenceImg) return alert("Please capture EC8A photo for evidence.");
            
            const v = {}; document.querySelectorAll('.party-v').forEach(i => v[i.dataset.p] = parseInt(i.value || 0));
            
            const fd = new FormData();
            fd.append('officer_id', officerId);
            fd.append('state', document.getElementById('s').value);
            fd.append('lg', document.getElementById('l').value);
            fd.append('ward', document.getElementById('w').value);
            fd.append('ward_code', document.getElementById('wc').value);
            fd.append('pu_code', document.getElementById('pc').value);
            fd.append('location', document.getElementById('loc').value);
            fd.append('total_accredited', document.getElementById('ta').value);
            fd.append('valid_votes', document.getElementById('tc').value);
            fd.append('lat', lat); 
            fd.append('lon', lon);
            fd.append('votes_data', JSON.stringify(v));
            fd.append('evidence', evidenceImg);

            const res = await fetch('/submit', { method: 'POST', body: fd });
            const out = await res.json();
            alert(out.message);
            if(out.status === 'success') location.reload();
        }
    </script>
</body>
</html>
"""

# --------------------------------------------------
# DASHBOARD_HTML (REMAINS EXACTLY AS PROVIDED)
# --------------------------------------------------
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Accord Situation Room - Final Build</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.0.0"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
    <style>
        :root { --bg: #0d0d0d; --panel: #161616; --gold: #ffc107; --border: #333; --text: #e0e0e0; --pdp: #d9534f; --apc: #0b3d91; }
        body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', sans-serif; height: 100vh; margin: 0; overflow: hidden; display: flex; flex-direction: column; }
        select option { background-color: #161616 !important; color: white !important; }
        .navbar-custom { border-bottom: 1px solid var(--gold); padding: 0 15px; display: flex; align-items: center; background: var(--bg); height: 75px; gap: 15px; }
        .brand-section { min-width: 180px; }
        .brand-title { font-size: 13px; font-weight: bold; color: white; letter-spacing: 1px; }
        .brand-sub { font-size: 10px; color: var(--gold); font-weight: bold; }
        .nav-kpi-group { display: flex; flex: 1; align-items: center; justify-content: center; gap: 12px; }
        .party-box { display: flex; align-items: center; background: rgba(255,255,255,0.05); border: 1px solid var(--border); padding: 5px 15px; min-width: 140px; height: 62px; gap: 12px; }
        .party-box img { height: 42px; width: 42px; border-radius: 50%; object-fit: contain; background: white; }
        .party-info label { font-size: 9px; color: #888; text-transform: uppercase; margin: 0; font-weight: bold; display: block; }
        .party-info span { font-size: 18px; font-weight: 900; color: white; line-height: 1; }
        .box-accord { border-top: 4px solid var(--gold); }
        .box-apc { border-top: 4px solid var(--apc); }
        .box-pdp { border-top: 4px solid var(--pdp); }
        .box-margin { border-top: 4px solid #555; }
        .filter-group { display: flex; align-items: center; gap: 8px; }
        .filter-item { border-left: 1px solid var(--border); padding-left: 10px; }
        .filter-item label { color: var(--gold); font-size: 9px; text-transform: uppercase; display: block; font-weight: bold; }
        .filter-item select { background: transparent; color: #fff; border: none; font-size: 12px; outline: none; cursor: pointer; font-weight: bold; }
        .main-container { display: flex; flex: 1; gap: 10px; padding: 10px; overflow: hidden; height: calc(100vh - 75px); }
        .col-side { width: 320px; display: flex; flex-direction: column; gap: 10px; }
        .col-center { flex: 1; display: flex; flex-direction: column; gap: 10px; }
        .widget { background: var(--panel); border: 1px solid var(--border); padding: 12px; display: flex; flex-direction: column; border-radius: 4px; position: relative; }
        .widget-title { color: var(--gold); font-size: 10px; font-weight: bold; border-bottom: 1px solid var(--border); margin-bottom: 8px; padding-bottom: 4px; text-transform: uppercase; display: flex; justify-content: space-between; }
        .map-wrapper { flex: 1; position: relative; background: #000; border-radius: 4px; overflow: hidden; }
        #map { position: absolute; top: 0; bottom: 0; left: 0; right: 0; height: 100% !important; }
        .pu-list { flex: 1; overflow-y: auto; }
        .pu-card { border-bottom: 1px solid var(--border); padding: 12px 10px; cursor: pointer; transition: background 0.2s; }
        .pu-card:hover { background: rgba(255, 193, 7, 0.05); }
        .pu-card.active { background: rgba(255, 193, 7, 0.15); border-left: 3px solid var(--gold); }
        .pu-card b { color: var(--gold); font-size: 13px; display: block; margin-bottom: 4px; }
        .pu-loc { font-size: 10px; color: #bbb; display: block; margin-bottom: 8px; }
        .pu-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; background: rgba(0,0,0,0.5); padding: 8px; border-radius: 4px; pointer-events: none; }
        .grid-val { text-align: center; }
        .grid-val small { font-size: 8px; color: #888; display: block; text-transform: uppercase; }
        .grid-val span { font-size: 12px; font-weight: bold; }
        .incident-alert { color: #ff4444; font-size: 10px; font-weight: bold; margin-top: 8px; padding: 6px; background: rgba(255,0,0,0.1); border-radius: 3px; }
        .chart-container { height: 160px; position: relative; }
        .big-total-box { border: 2px solid var(--gold); text-align: center; padding: 15px; }
        .big-val { font-size: 48px; font-weight: 900; color: white; line-height: 1; margin: 5px 0; }
        .ts-box { font-size: 9px; color: #888; text-transform: uppercase; margin-top: 4px; letter-spacing: 1px; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: var(--gold); border-radius: 10px; }
    </style>
</head>
<body>
<nav class="navbar-custom">
    <div class="brand-section">
        <div class="brand-title">ELECTION SITUATION ROOM</div>
        <div class="brand-sub">ACCORD CONSOLIDATED VIEW</div>
    </div>
    <div class="nav-kpi-group">
        <div class="party-box box-accord">
            <img src="/static/logos/ACCORD.png" onerror="this.src='https://via.placeholder.com/42?text=A'">
            <div class="party-info"><label>ACCORD</label><span id="nav-ACCORD">0</span></div>
        </div>
        <div class="party-box box-apc">
            <img src="/static/logos/APC.png" onerror="this.src='https://via.placeholder.com/42?text=APC'">
            <div class="party-info"><label>APC</label><span id="nav-APC">0</span></div>
        </div>
        <div class="party-box box-pdp">
            <img src="/static/logos/PDP.png" onerror="this.src='https://via.placeholder.com/42?text=PDP'">
            <div class="party-info"><label>PDP</label><span id="nav-PDP">0</span></div>
        </div>
        <div class="party-box box-margin">
            <div class="party-info" style="text-align:center; width:100%"><label>LEAD MARGIN</label><span id="nav-Margin" style="color:var(--gold)">0</span></div>
        </div>
    </div>
    <div class="filter-group">
        <div class="filter-item"><label>State</label><select id="stateFilter"><option value="">All States</option></select></div>
        <div class="filter-item"><label>LGA</label><select id="lgaFilter"><option value="">All LGAs</option></select></div>
        <div class="filter-item"><label>Ward</label><select id="wardFilter"><option value="">All Wards</option></select></div>
    </div>
</nav>

<div class="main-container">
    <div class="col-side">
        <div class="widget" style="padding: 8px;">
            <input type="text" id="puSearch" placeholder="🔍 Search Polling Units..." onkeyup="refreshData()" 
                   style="background:#222; border:none; color:white; padding:10px; font-size:12px; width:100%; border-radius:4px;">
        </div>
        <div class="widget pu-list">
            <div class="widget-title">
                Live Result Feed
                <span id="resetFeed" onclick="refreshData()" style="color:var(--gold); cursor:pointer; font-size:9px;">RESET VIEW</span>
            </div>
            <div id="puContainer"></div>
        </div>
    </div>
    <div class="col-center">
        <div class="widget map-wrapper"><div id="map"></div></div>
        <div class="widget" style="height: 85px; text-align:center; justify-content:center;">
            <div style="font-size:11px; color:var(--gold); font-weight:bold; text-transform:uppercase;">Reporting Coverage</div>
            <div id="unitCount" style="font-size: 36px; font-weight: 900; color: white;">0 Units Reporting</div>
        </div>
    </div>
    <div class="col-side">
        <div class="widget">
            <div class="widget-title" id="chartLabel">Vote Distribution %</div>
            <div class="chart-container"><canvas id="pieChart"></canvas></div>
        </div>
        <div class="widget">
            <div class="widget-title">Candidate Comparison</div>
            <div style="height: 100px;"><canvas id="barChart"></canvas></div>
        </div>
        <div class="widget big-total-box">
            <div style="color:var(--gold); font-size:11px; font-weight:bold; text-transform:uppercase;">Total Accord Aggregate</div>
            <div id="totalAccordBig" class="big-val">0</div>
            <div class="ts-box" id="lastUpdateTS">Last Updated: --:--:--</div>
        </div>
    </div>
</div>

<script>
Chart.register(ChartDataLabels);
let map = L.map('map', {zoomControl: false, attributionControl: false}).setView([9.082, 8.675], 6);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(map);
let markers = [], charts = {}, globalData = [];

async function initDashboard() {
    try {
        const states = await (await fetch("/locations/states")).json();
        const sS = document.getElementById("stateFilter");
        states.forEach(s => sS.add(new Option(s, s)));
    } catch(e) { console.error(e); }
    refreshData();
}

function focusOnUnit(puName) {
    const unit = globalData.find(d => d.pu_name === puName);
    if (!unit) return;
    document.querySelectorAll('.pu-card').forEach(c => c.classList.remove('active'));
    event.currentTarget.classList.add('active');
    if (unit.latitude && unit.longitude) {
        map.flyTo([unit.latitude, unit.longitude], 14, { duration: 1.5 });
    }
    document.getElementById('chartLabel').innerText = "Unit View: " + puName;
    updateCharts(unit.votes_party_ACCORD || 0, unit.votes_party_APC || 0, unit.votes_party_PDP || 0);
}

document.getElementById("stateFilter").onchange = async (e) => {
    const lS = document.getElementById("lgaFilter");
    lS.innerHTML = '<option value="">All LGAs</option>';
    if(e.target.value) {
        const lgas = await (await fetch("/locations/lgas/"+e.target.value)).json();
        lgas.forEach(l => lS.add(new Option(l, l)));
    }
    refreshData();
};

document.getElementById("lgaFilter").onchange = async (e) => {
    const state = document.getElementById("stateFilter").value;
    const wS = document.getElementById("wardFilter");
    wS.innerHTML = '<option value="">All Wards</option>';
    if(e.target.value) {
        const wards = await (await fetch(`/locations/wards/${state}/${e.target.value}`)).json();
        wards.forEach(w => {
            let name = (typeof w === 'string') ? w : (w.name || "Unknown");
            wS.add(new Option(name, name));
        });
    }
    refreshData();
};

document.getElementById("wardFilter").onchange = () => refreshData();

async function refreshData() {
    try {
        const res = await fetch("/submissions");
        let data = await res.json();
        globalData = data; 
        const sF = document.getElementById("stateFilter").value;
        const lF = document.getElementById("lgaFilter").value;
        const wF = document.getElementById("wardFilter").value;
        const sT = document.getElementById("puSearch").value.toLowerCase();
        if(sF) data = data.filter(d => d.state === sF);
        if(lF) data = data.filter(d => d.lga === lF);
        if(wF) data = data.filter(d => d.ward === wF);
        if(sT) data = data.filter(d => d.pu_name.toLowerCase().includes(sT));
        document.getElementById('chartLabel').innerText = "Vote Distribution %";
        updateUI(data);
    } catch(e) { console.error(e); }
}

function updateUI(data) {
    let tA = 0, tAPC = 0, tPDP = 0, listHtml = "";
    markers.forEach(m => map.removeLayer(m));
    markers = [];
    data.forEach(d => {
        const vA = d.votes_party_ACCORD || 0;
        const vAPC = d.votes_party_APC || 0;
        const vPDP = d.votes_party_PDP || 0;
        tA += vA; tAPC += vAPC; tPDP += vPDP;
        listHtml += `
            <div class="pu-card" onclick="focusOnUnit('${d.pu_name}')">
                <b>${d.pu_name}</b>
                <span class="pu-loc">📍 ${d.ward}, ${d.lga}</span>
                <div class="pu-grid">
                    <div class="grid-val"><small>ACC</small><span style="color:var(--gold)">${vA.toLocaleString()}</span></div>
                    <div class="grid-val"><small>APC</small><span>${vAPC.toLocaleString()}</span></div>
                    <div class="grid-val"><small>PDP</small><span>${vPDP.toLocaleString()}</span></div>
                </div>
                ${d.incident_type ? `<div class="incident-alert">🚨 ${d.incident_type}</div>` : ""}
            </div>`;
        if(d.latitude && d.longitude) {
            markers.push(L.circleMarker([d.latitude, d.longitude], {
                radius: 7, color: '#ffc107', fillColor: '#ffc107', fillOpacity: 0.8, weight: 2
            }).addTo(map));
        }
    });
    document.getElementById("puContainer").innerHTML = listHtml;
    document.getElementById("nav-ACCORD").innerText = tA.toLocaleString();
    document.getElementById("nav-APC").innerText = tAPC.toLocaleString();
    document.getElementById("nav-PDP").innerText = tPDP.toLocaleString();
    document.getElementById("nav-Margin").innerText = (tA - Math.max(tAPC, tPDP)).toLocaleString();
    document.getElementById("unitCount").innerText = `${data.length} Units Reporting`;
    document.getElementById("totalAccordBig").innerText = tA.toLocaleString();
    document.getElementById("lastUpdateTS").innerText = "Last Updated: " + new Date().toLocaleTimeString();
    if (markers.length > 0) {
        const group = new L.featureGroup(markers);
        map.fitBounds(group.getBounds(), {padding:[40,40]});
    }
    updateCharts(tA, tAPC, tPDP);
}

function updateCharts(a, apc, pdp) {
    const total = a + apc + pdp;
    const labels = ['ACCORD', 'APC', 'PDP'];
    const colors = ['#ffc107', '#0b3d91', '#d9534f'];
    const values = [a, apc, pdp];
    if(charts.bar) charts.bar.destroy();
    charts.bar = new Chart(document.getElementById('barChart'), {
        type: 'bar',
        data: { labels: labels, datasets: [{ data: values, backgroundColor: colors }] },
        options: {
            maintainAspectRatio: false,
            plugins: { datalabels: { display: false }, legend: { display: false } },
            scales: { y: { grid: { color: '#222' }, ticks: { color: '#555', font: { size: 8 } } }, x: { ticks: { color: '#888', font: { size: 9 } } } }
        }
    });
    if(charts.pie) charts.pie.destroy();
    charts.pie = new Chart(document.getElementById('pieChart'), {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
        plugins: [{
            id: 'centerText',
            beforeDraw: (chart) => {
                const { ctx, chartArea: { top, bottom, left, right } } = chart;
                ctx.save();
                const centerX = (left + right) / 2;
                const centerY = (top + bottom) / 2;
                ctx.textAlign = "center"; ctx.textBaseline = "middle";
                ctx.font = "bold 10px Segoe UI"; ctx.fillStyle = "#888";
                ctx.fillText("TOTAL", centerX, centerY - 10);
                ctx.font = "bold 16px Segoe UI"; ctx.fillStyle = "white";
                ctx.fillText(total.toLocaleString(), centerX, centerY + 8);
                ctx.restore();
            }
        }],
        options: {
            cutout: '78%',
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'right', labels: { color: '#888', font: { size: 9 } } },
                datalabels: {
                    color: '#fff',
                    font: { weight: 'bold', size: 10 },
                    formatter: (val) => total > 0 ? (val/total*100).toFixed(1) + '%' : ''
                }
            }
        }
    });
}
initDashboard();
setInterval(refreshData, 20000);
</script>
</body></html>
"""
