import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
import logging
import io
import csv
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# --- CONFIGURATION --- [cite: 1]
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI()

# Mount static directories for logos and background images [cite: 1]
LOGO_PATH = os.path.join(os.getcwd(), "static", "logos")
if os.path.exists(LOGO_PATH):
    app.mount("/logos", StaticFiles(directory=LOGO_PATH), name="logos")

STATIC_PATH = os.path.join(os.getcwd(), "static")
if os.path.exists(STATIC_PATH):
    app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")

# --- DATABASE CONNECTION --- [cite: 1, 2]
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
                pu_code TEXT,
                location TEXT,
                reg_voters INTEGER,
                total_accredited INTEGER,
                valid_votes INTEGER,
                rejected_votes INTEGER,
                total_cast INTEGER,
                lat REAL,
                lon REAL,
                timestamp TEXT,
                votes_json TEXT,
                UNIQUE(pu_code)
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print(" ✅ Table created successfully")
    except Exception as e:
        print(f" ❌ DB INIT ERROR: {e}")

init_db() # [cite: 5]

# --- API ENDPOINTS --- [cite: 6-11]
@app.get("/api/states")
def get_states():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT state FROM polling_units ORDER BY state")
            rows = cur.fetchall()
            return [r["state"] for r in rows]

@app.get("/api/lgas/{state}")
def get_lgas(state: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT lg FROM polling_units WHERE state = %s ORDER BY lg", (state,))
            rows = cur.fetchall()
            return [r["lg"] for r in rows]

@app.get("/api/wards/{state}/{lg}")
def get_wards(state: str, lg: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ward, ward_code FROM polling_units WHERE state = %s AND lg = %s ORDER BY ward", (state, lg))
            rows = cur.fetchall()
            return [{"name": r["ward"], "code": r["ward_code"]} for r in rows]

@app.get("/api/pus/{state}/{lg}/{ward}")
def get_pus(state: str, lg: str, ward: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT location, pu_code FROM polling_units WHERE state = %s AND lg = %s AND ward = %s", (state, lg, ward))
            rows = cur.fetchall()
            return [{"location": r["location"], "pu_code": r["pu_code"]} for r in rows]

@app.post("/submit")
async def submit(data: dict):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO field_submissions (
                    officer_id, state, lg, ward, ward_code, pu_code, location,
                    reg_voters, total_accredited, valid_votes, rejected_votes, total_cast,
                    lat, lon, timestamp, votes_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                    data['officer_id'], data['state'], data['lg'], data['ward'], data['ward_code'],
                    data['pu_code'], data['location'], data['reg_voters'], data['total_accredited'],
                    data['valid_votes'], data['rejected_votes'], data['total_cast'],
                    data['lat'], data['lon'], datetime.now().isoformat(), json.dumps(data['votes'])
                ))
                conn.commit()
                return {"status": "success", "message": "Result Uploaded Successfully"}
    except psycopg2.IntegrityError:
        return {"status": "error", "message": "REJECTED: A submission for this Polling Unit already exists."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/ai_interpret")
async def ai_interpret(data: dict):
    acc, apc, pdp, adc = data.get('ACCORD', 0), data.get('APC', 0), data.get('PDP', 0), data.get('ADC', 0)
    total = acc + apc + pdp + adc
    if total == 0:
        return {"analysis": "SYSTEM STATUS: Awaiting live data stream for comparative trend analysis."}
    share = (acc / total) * 100
    top_rival = max({"APC": apc, "PDP": pdp, "ADC": adc}, key=lambda k: {"APC": apc, "PDP": pdp, "ADC": adc}[k])
    margin = acc - max(apc, pdp, adc)
    analysis = (f"STATISTICAL AUDIT: Accord maintains a {share:.1f}% vote share. "
                f"It is {'Leading' if margin > 0 else 'Trailing'} {top_rival} by {abs(margin):,} votes.")
    return {"analysis": analysis}

@app.get("/export/csv")
async def export_csv():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM field_submissions ORDER BY timestamp DESC")
            rows = cur.fetchall()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Timestamp", "Officer ID", "State", "LGA", "Ward", "PU Code", "Location", "ACCORD", "APC", "PDP", "ADC"])
            for r in rows:
                v = json.loads(r['votes_json']) if isinstance(r['votes_json'], str) else r['votes_json']
                writer.writerow([r['timestamp'], r['officer_id'], r['state'], r['lg'], r['ward'], r['pu_code'], r['location'], 
                                 v.get("ACCORD", 0), v.get("APC", 0), v.get("PDP", 0), v.get("ADC", 0)])
            output.seek(0)
            return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=election_results.csv"})

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
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>IMOLE YOUTH ACCORD MOBILIZATION</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ background: linear-gradient(rgba(0,0,0,0.6), rgba(0,0,0,0.6)), url('/static/bg.png'); background-size: cover; background-attachment: fixed; min-height: 100vh; }}
            .navbar {{ background: rgba(0, 135, 81, 0.9) !important; color: white; border-bottom: 4px solid #ffc107; }}
            .card {{ background: rgba(255, 255, 255, 0.95) !important; border-radius: 12px; margin-bottom: 20px; }}
            .section-label {{ font-size: 0.75rem; font-weight: bold; color: #008751; border-left: 3px solid #ffc107; padding-left: 10px; display: block; margin-bottom: 15px; }}
        </style>
    </head>
    <body>
        <nav class="navbar py-2 mb-4 text-center"><h5>OFFICIAL FIELD COLLATION</h5></nav>
        <div class="container pb-5" style="max-width: 850px;">
            <div id="loginArea" class="card p-5 text-center mx-auto" style="max-width: 400px;">
                <h6>Enter Officer ID</h6>
                <input type="text" id="oid" class="form-control mb-3 text-center">
                <button class="btn btn-success w-100" onclick="start()">Validate Access</button>
            </div>
            <div id="formArea" class="d-none">
                <div class="card p-4">
                    <span class="section-label">1. Polling Unit Selection</span>
                    <div class="row g-2">
                        <div class="col-4"><select id="s" class="form-select" onchange="loadLGAs()"><option value="">STATE</option></select></div>
                        <div class="col-4"><select id="l" class="form-select" onchange="loadWards()"><option value="">LGA</option></select></div>
                        <div class="col-4"><select id="w" class="form-select" onchange="loadPUs()"><option value="">WARD</option></select></div>
                        <div class="col-12 mt-2"><select id="p" class="form-select" onchange="fillPU()"><option value="">SELECT POLLING UNIT</option></select></div>
                    </div>
                </div>
                <div class="card p-4"><span class="section-label">2. Party Scorecard</span><div class="row g-2">{party_cards}</div></div>
                <div class="card p-4">
                    <span class="section-label">3. Audit</span>
                    <div class="row g-3">
                        <div class="col-6"><label class="small">Accredited</label><input type="number" id="ta" class="form-control" oninput="calculateTotals()"></div>
                        <div class="col-6"><label class="small">Total Cast</label><input type="number" id="tc" class="form-control" readonly></div>
                    </div>
                    <div id="auditStatus" class="mt-3 p-2 rounded text-center d-none small fw-bold"></div>
                </div>
                <button class="btn btn-outline-dark w-100 mb-3" onclick="getGPS()">Fix GPS</button>
                <button class="btn btn-success btn-lg w-100 py-3 fw-bold" onclick="finalSubmit()">UPLOAD RESULT</button>
            </div>
        </div>
        <script>
            let lat, lon, officerId, puData = [], wardData = [];
            function start() {{
                officerId = document.getElementById('oid').value;
                if(!officerId) return;
                document.getElementById('loginArea').classList.add('d-none');
                document.getElementById('formArea').classList.remove('d-none');
                fetch('/api/states').then(r=>r.json()).then(data=>{{
                    const s = document.getElementById('s');
                    data.forEach(item => s.add(new Option(item.toUpperCase(), item)));
                }});
            }}
            function loadLGAs() {{
                fetch('/api/lgas/'+encodeURIComponent(document.getElementById('s').value)).then(r=>r.json()).then(data=>{{
                    const l = document.getElementById('l'); l.innerHTML = '<option value="">LGA</option>';
                    data.forEach(item => l.add(new Option(item.toUpperCase(), item)));
                }});
            }}
            function loadWards() {{
                fetch(`/api/wards/${{encodeURIComponent(document.getElementById('s').value)}}/${{encodeURIComponent(document.getElementById('l').value)}}`).then(r=>r.json()).then(data=>{{
                    wardData = data;
                    const w = document.getElementById('w'); w.innerHTML = '<option value="">WARD</option>';
                    data.forEach(item => w.add(new Option(item.name.toUpperCase(), item.name)));
                }});
            }}
            function loadPUs() {{
                fetch(`/api/pus/${{encodeURIComponent(document.getElementById('s').value)}}/${{encodeURIComponent(document.getElementById('l').value)}}/${{encodeURIComponent(document.getElementById('w').value)}}`).then(r=>r.json()).then(data=>{{
                    puData = data;
                    const p = document.getElementById('p'); p.innerHTML = '<option value="">SELECT PU</option>';
                    data.forEach((item, idx) => p.add(new Option(item.location.toUpperCase(), idx)));
                }});
            }}
            function fillPU() {{
                const sel = puData[document.getElementById('p').value];
                document.getElementById('loc').value = sel.location.toUpperCase();
            }}
            function calculateTotals() {{
                let valid = 0;
                document.querySelectorAll('.party-v').forEach(i => valid += parseInt(i.value || 0));
                document.getElementById('tc').value = valid;
                const acc = parseInt(document.getElementById('ta').value || 0);
                const msg = document.getElementById('auditStatus');
                msg.classList.toggle('d-none', acc === 0);
                if (acc > 0 && valid > acc) {{
                    msg.innerHTML = " ⚠️ ERROR: Over-voting!";
                    msg.className = "mt-3 p-2 bg-danger text-white rounded text-center small fw-bold";
                }} else if (valid > 0 && valid === acc) {{
                    msg.innerHTML = " ✅ AUDIT BALANCED";
                    msg.className = "mt-3 p-2 bg-success text-white rounded text-center small fw-bold";
                }}
            }}
            function getGPS() {{ navigator.geolocation.getCurrentPosition(pos => {{ lat = pos.coords.latitude; lon = pos.coords.longitude; alert("GPS Fixed!"); }}); }}
            async function finalSubmit() {{
                const v = {{}};
                document.querySelectorAll('.party-v').forEach(i => v[i.dataset.p] = parseInt(i.value || 0));
                const payload = {{ officer_id: officerId, state: document.getElementById('s').value, lg: document.getElementById('l').value, ward: document.getElementById('w').value, pu_code: "N/A", location: "N/A", reg_voters: 0, total_accredited: parseInt(document.getElementById('ta').value || 0), valid_votes: 0, rejected_votes: 0, total_cast: parseInt(document.getElementById('tc').value || 0), lat, lon, votes: v }};
                const res = await fetch('/submit', {{ method: 'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify(payload)}});
                const out = await res.json();
                alert(out.message);
                if(out.status === 'success') location.reload();
            }}
        </script>
    </body>
    </html>
    """
    # --- DASHBOARD LOGIC --- [cite: 580-622]
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Accord Situation Room</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { background: #0d0d0d; color: #e0e0e0; font-family: sans-serif; }
            .navbar-custom { border-bottom: 1px solid #ffc107; background: #161616; padding: 15px; }
            .kpi-card { background: #1a1a1a; border-left: 4px solid #ffc107; padding: 15px; margin-bottom: 20px; }
        </style>
    </head>
    <body>
        <div class="navbar-custom"><h3>ACCORD SITUATION ROOM</h3></div>
        <div class="container mt-4">
            <div class="row">
                <div class="col-md-3"><div class="kpi-card"><h5>ACCORD</h5><h2 id="accord_total">0</h2></div></div>
                <div class="col-md-3"><div class="kpi-card" style="border-left-color: #0b3d91;"><h5>APC</h5><h2 id="apc_total">0</h2></div></div>
                <div class="col-md-3"><div class="kpi-card" style="border-left-color: #d9534f;"><h5>PDP</h5><h2 id="pdp_total">0</h2></div></div>
                <div class="col-md-3"><div class="kpi-card" style="border-left-color: #006400;"><h5>ADC</h5><h2 id="adc_total">0</h2></div></div>
            </div>
            <div id="results_list" class="mt-4"></div>
        </div>
        <script>
            async function refresh() {
                const res = await fetch('/submissions');
                const data = await res.json();
                let acc = 0, apc = 0, pdp = 0, adc = 0;
                let html = '<table class="table table-dark"><thead><tr><th>PU Name</th><th>ACCORD</th><th>APC</th><th>PDP</th></tr></thead><tbody>';
                data.forEach(d => {
                    acc += d.votes_party_ACCORD; apc += d.votes_party_APC;
                    pdp += d.votes_party_PDP; adc += d.votes_party_ADC;
                    html += `<tr><td>${d.pu_name}</td><td>${d.votes_party_ACCORD}</td><td>${d.votes_party_APC}</td><td>${d.votes_party_PDP}</td></tr>`;
                });
                document.getElementById('accord_total').innerText = acc.toLocaleString();
                document.getElementById('apc_total').innerText = apc.toLocaleString();
                document.getElementById('pdp_total').innerText = pdp.toLocaleString();
                document.getElementById('adc_total').innerText = adc.toLocaleString();
                document.getElementById('results_list').innerHTML = html + '</tbody></table>';
            }
            setInterval(refresh, 5000);
            refresh();
        </script>
    </body>
    </html>
    """

@app.get("/submissions")
async def get_dashboard_data():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM field_submissions ORDER BY timestamp DESC")
            rows = cur.fetchall()
            data = []
            for r in rows:
                v = json.loads(r['votes_json']) if isinstance(r['votes_json'], str) else r['votes_json']
                data.append({
                    "pu_name": r.get('location'), "state": r.get('state'),
                    "votes_party_ACCORD": v.get("ACCORD", 0), "votes_party_APC": v.get("APC", 0),
                    "votes_party_PDP": v.get("PDP", 0), "votes_party_ADC": v.get("ADC", 0)
                })
            return data
