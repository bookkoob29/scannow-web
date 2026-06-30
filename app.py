"""SCANNOW Web App — Dashboard, OAuth, Scan Trigger, Telegram Notify."""
import os, sys, json, subprocess, threading, re, time, tempfile, hmac
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader
from authlib.integrations.starlette_client import OAuth

import config
import database as db
import render_scan

# Jinja2 environment (avoids Python 3.14 incompatibility in Jinja2Templates wrapper)
_jinja_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), cache_size=0)

# Inline templates (avoids Jinja2Templates issues on newer Python)
LOGIN_HTML = """<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SCANNOW — Bangkok Condo Demand</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap');
        * { font-family: 'Prompt', sans-serif; }
        body { background: #1a1d23; }
        .card { background: #2a2d35; border-radius: 12px; padding: 24px; }
        .btn-primary { background: #2563EB; color: white; padding: 12px 32px; border-radius: 12px; font-weight: 600; transition: all 0.2s; }
        .btn-primary:hover { background: #1D4ED8; transform: translateY(-1px); }
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; }
        .badge-new { background: #f59e0b; color: #1c1e21; }
        .badge-raw { background: #059669; color: white; }
        .badge-preview { background: #6b7280; color: white; }
        .stat-box { background: #2a2d35; border-radius: 10px; padding: 16px; text-align: center; flex: 1; min-width: 120px; }
        .stat-number { font-size: 28px; font-weight: 700; }
        .scan-btn { background: linear-gradient(135deg, #059669, #047857); color: white; border: none; padding: 16px 48px; border-radius: 16px; font-size: 18px; font-weight: 700; cursor: pointer; transition: all 0.3s; }
        .scan-btn:hover { transform: scale(1.02); box-shadow: 0 8px 25px rgba(5, 150, 105, 0.3); }
        .scan-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .scan-btn.running { animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-4">
    <div class="card max-w-md w-full text-center">
        <div class="text-5xl mb-4">🏢</div>
        <h1 class="text-2xl font-bold text-white mb-2">SCANNOW</h1>
        <p class="text-gray-400 mb-6">Bangkok Condo Demand Intelligence</p>
        <a href="/auth/login" class="btn-primary inline-block">
            เข้าสู่ระบบด้วย Google
        </a>
        <p class="text-gray-500 text-xs mt-4">เฉพาะผู้ได้รับอนุญาตเท่านั้น</p>
    </div>
</body>
</html>
"""
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SCANNOW Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap');
        * { font-family: 'Prompt', sans-serif; }
        body { background: #1a1d23; color: #e4e6eb; }
        .card { background: #2a2d35; border-radius: 12px; padding: 20px; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; }
        .badge-new { background: #f59e0b; color: #1c1e21; }
        .badge-raw { background: #059669; color: white; }
        .badge-preview { background: #6b7280; color: white; }
        .stat-box { background: #2a2d35; border-radius: 10px; padding: 16px; text-align: center; min-width: 120px; }
        .scan-btn { background: linear-gradient(135deg, #059669, #047857); color: white; border: none; padding: 14px 40px; border-radius: 14px; font-size: 16px; font-weight: 700; cursor: pointer; transition: all 0.3s; }
        .scan-btn:hover { transform: scale(1.02); box-shadow: 0 8px 25px rgba(5,150,105,0.3); }
        .scan-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .scan-btn.running { animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.7} }
        .lead-row:hover { background: #374151; }
        .scroll-table { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; padding: 8px 12px; color: #9ca3af; font-weight: 500; border-bottom: 1px solid #374151; white-space: nowrap; }
        td { padding: 8px 12px; border-bottom: 1px solid #1f2128; vertical-align: top; }
        a { color: #60a5fa; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .toast { position: fixed; bottom: 20px; right: 20px; z-index: 9999; padding: 12px 20px; border-radius: 8px; color: white; font-weight: 500; transform: translateY(150%); opacity: 0; transition: all 0.3s; }
        .toast.show { transform: translateY(0); opacity: 1; }
        .toast.success { background: #16A34A; }
        .toast.error { background: #DC2626; }
        .toast.info { background: #2563EB; }
    </style>
</head>
<body class="min-h-screen">
    <nav class="bg-gray-800 border-b border-gray-700 px-4 py-3 flex items-center justify-between">
        <div class="flex items-center gap-3">
            <span class="text-lg font-bold text-white">🏢 SCANNOW</span>
            <span class="text-gray-400 text-sm">Bangkok Condo Demand</span>
        </div>
        <div class="flex items-center gap-3">
            {% if user.picture %}<img src="{{ user.picture }}" class="w-8 h-8 rounded-full">{% endif %}
            <span class="text-gray-300 text-sm">{{ user.name }}</span>
            <a href="/logout" class="text-gray-400 text-sm hover:text-white">ออกจากระบบ</a>
        </div>
    </nav>

    <div class="max-w-6xl mx-auto px-4 py-6">
        <!-- Scan Button + Stats -->
        <div class="flex flex-wrap items-center justify-between gap-4 mb-6">
            <button id="scan-btn" class="scan-btn" onclick="triggerScan()">
                🚀 SCANNOW
            </button>
            <div class="flex flex-wrap gap-3">
                <div class="stat-box"><div class="stat-number text-blue-400">{{ stats.total_leads }}</div><div class="text-gray-400 text-xs">Total Leads</div></div>
                <div class="stat-box"><div class="stat-number text-amber-400">{{ stats.new_leads }}</div><div class="text-gray-400 text-xs">New</div></div>
                <div class="stat-box"><div class="stat-number text-green-400">{{ stats.unique_groups }}</div><div class="text-gray-400 text-xs">Groups</div></div>
            </div>
        </div>

        <!-- Status -->
        <div id="scan-status" class="card mb-6 hidden">
            <p class="text-sm" id="status-text"></p>
        </div>

        <!-- Search -->
        <div class="flex gap-3 mb-4">
            <input id="search-input" type="text" placeholder="ค้นหาชื่อ, กลุ่ม, ข้อความ..." 
                   class="flex-1 px-4 py-2 bg-gray-800 border border-gray-600 rounded-lg text-gray-200 focus:ring-2 focus:ring-blue-500 outline-none text-sm"
                   oninput="debounceSearch()">
            <select id="filter-select" class="px-3 py-2 bg-gray-800 border border-gray-600 rounded-lg text-gray-200 text-sm"
                    onchange="loadLeads()">
                <option value="">ทั้งหมด</option>
                <option value="new">ใหม่เท่านั้น</option>
            </select>
        </div>

        <!-- Leads Table -->
        <div class="card scroll-table">
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>ชื่อ</th>
                        <th>กลุ่ม</th>
                        <th>งบ</th>
                        <th>วันที่</th>
                        <th>สถานะ</th>
                        <th>เนื้อหา</th>
                        <th>ลิงก์</th>
                    </tr>
                </thead>
                <tbody id="leads-tbody">
                    {% for lead in leads %}
                    <tr class="lead-row">
                        <td class="text-gray-400">{{ loop.index }}</td>
                        <td class="font-medium">{{ lead.name }}</td>
                        <td class="text-gray-400 text-xs">{{ lead.group_name[:25] }}</td>
                        <td class="text-green-400 text-sm">{{ lead.budget[:20] }}</td>
                        <td class="text-gray-400 text-xs">{{ lead.posted_date }}</td>
                        <td>
                            {% if lead.is_new %}<span class="badge badge-new">NEW</span>{% endif %}
                            <span class="badge {{ 'badge-raw' if lead.has_raw_text else 'badge-preview' }}">
                                {{ 'RAW' if lead.has_raw_text else 'PREV' }}
                            </span>
                        </td>
                        <td class="text-gray-400 text-xs max-w-xs truncate">{{ lead.full_text[:100] }}...</td>
                        <td class="whitespace-nowrap">
                            {% if lead.post_url %}<a href="{{ lead.post_url }}" target="_blank">🔗 Post</a>{% endif %}
                            {% if lead.profile_id %}<a href="https://www.facebook.com/profile.php?id={{ lead.profile_id }}" target="_blank" class="ml-2">🔍 Profile</a>{% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Scan History -->
        <div class="card mt-6">
            <h3 class="font-bold text-gray-300 mb-3">📋 Scan History</h3>
            <div class="text-sm text-gray-400">
                {% for scan in scans %}
                <div class="flex justify-between py-1 border-b border-gray-700 last:border-0">
                    <span>{{ scan.started_at[:19] }}</span>
                    <span class="{% if scan.status == 'completed' %}text-green-400{% else %}text-red-400{% endif %}">
                        {{ scan.status }} — {{ scan.new_leads }} new / {{ scan.total_leads }} total
                    </span>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        let searchTimer = null;
        
        function showToast(msg, type='info') {
            const t = document.getElementById('toast');
            t.textContent = msg; t.className = 'toast ' + type;
            setTimeout(() => t.classList.add('show'), 50);
            setTimeout(() => t.classList.remove('show'), 3000);
        }

        async function triggerScan() {
            const btn = document.getElementById('scan-btn');
            const statusDiv = document.getElementById('scan-status');
            const statusText = document.getElementById('status-text');
            
            btn.disabled = true; btn.classList.add('running');
            btn.textContent = '⏳ กำลังสแกน...';
            statusDiv.classList.remove('hidden');
            statusText.textContent = 'เริ่มสแกน...';
            
            try {
                const resp = await fetch('/api/scan', { method: 'POST' });
                const data = await resp.json();
                if (data.status === 'already_running') {
                    showToast('❌ สแกนกำลังทำงานอยู่', 'error');
                } else {
                    showToast('✅ เริ่มสแกนแล้ว', 'success');
                    pollStatus();
                }
            } catch (e) {
                showToast('❌ เกิดข้อผิดพลาด', 'error');
                btn.disabled = false; btn.classList.remove('running');
                btn.textContent = '🚀 SCANNOW';
            }
        }

        async function pollStatus() {
            const btn = document.getElementById('scan-btn');
            const statusText = document.getElementById('status-text');
            
            const interval = setInterval(async () => {
                try {
                    const resp = await fetch('/api/scan-status');
                    const data = await resp.json();
                    if (data.last_output) {
                        statusText.textContent = data.last_output.slice(-200);
                    }
                    if (!data.running) {
                        clearInterval(interval);
                        btn.disabled = false; btn.classList.remove('running');
                        btn.textContent = '🚀 SCANNOW';
                        statusText.textContent = data.error ? '❌ ' + data.error : '✅ สแกนเสร็จสมบูรณ์';
                        showToast('✅ สแกนเสร็จแล้ว', 'success');
                        setTimeout(() => location.reload(), 1000);
                    }
                } catch(e) {}
            }, 2000);
        }

        function debounceSearch() {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => loadLeads(), 400);
        }

        document.getElementById('filter-select').addEventListener('change', loadLeads);

        async function loadLeads() {
            const q = document.getElementById('search-input').value;
            const f = document.getElementById('filter-select').value;
            try {
                const resp = await fetch(`/api/leads?search=${encodeURIComponent(q)}&filter=${f}&limit=100`);
                const data = await resp.json();
                const tbody = document.getElementById('leads-tbody');
                tbody.innerHTML = '';
                data.leads.forEach((lead, i) => {
                    const tr = document.createElement('tr'); tr.className = 'lead-row';
                    const maxText = (lead.full_text || '').substring(0, 100);
                    const newBadge = lead.is_new ? '<span class="badge badge-new">NEW</span>' : '';
                    const rawBadge = lead.has_raw_text ? '<span class="badge badge-raw">RAW</span>' : '<span class="badge badge-preview">PREV</span>';
                    const postLink = lead.post_url ? `<a href="${lead.post_url}" target="_blank">🔗 Post</a>` : '';
                    const profLink = lead.profile_id ? `<a href="https://www.facebook.com/profile.php?id=${lead.profile_id}" target="_blank" class="ml-2">🔍 Profile</a>` : '';
                    tr.innerHTML = `
                        <td class="text-gray-400">${i+1}</td>
                        <td class="font-medium">${lead.name || '?'}</td>
                        <td class="text-gray-400 text-xs">${(lead.group_name||'').substring(0,25)}</td>
                        <td class="text-green-400 text-sm">${(lead.budget||'N/A').substring(0,20)}</td>
                        <td class="text-gray-400 text-xs">${lead.posted_date||''}</td>
                        <td>${newBadge} ${rawBadge}</td>
                        <td class="text-gray-400 text-xs max-w-xs truncate">${maxText}...</td>
                        <td class="whitespace-nowrap">${postLink} ${profLink}</td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch(e) {}
        }
    </script>
</body>
</html>
"""

app = FastAPI(title="SCANNOW")

# Session
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)

# OAuth setup
oauth = OAuth()
oauth.register(
    name="google",
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    client_kwargs={"scope": "openid email profile"},
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
)

# Scan lock (prevent concurrent scans)
_scan_lock = threading.Lock()
_scan_status = {"running": False, "last_output": "", "error": ""}

# ─── Auth helpers ───

def get_user(request: Request):
    user = request.session.get("user")
    if user and user.get("email") == config.ALLOWED_EMAIL:
        return user
    return None

def require_user(request: Request):
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=303, detail="Login required")
    return user

# ─── Routes ───

@app.get("/")
async def root(request: Request):
    try:
        user = get_user(request)
        if user:
            return RedirectResponse(url="/dashboard")
        return HTMLResponse(LOGIN_HTML)
    except Exception as e:
        return HTMLResponse(f"<pre>Root error: {e}</pre>", status_code=500)

@app.get("/login")
async def login_page(request: Request):
    if get_user(request):
        return RedirectResponse(url="/dashboard")
    return HTMLResponse(LOGIN_HTML)

@app.get("/auth/login")
async def auth_login(request: Request):
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        return HTMLResponse("""
        <html><body style="background:#1a1d23;color:#e4e6eb;font-family:sans-serif;padding:40px;text-align:center">
        <h2>⚠️ ยังไม่ได้ตั้งค่า Google OAuth</h2>
        <p style="color:#9ca3af;margin:20px 0">กรุณาตั้งค่าตัวแปรแวดล้อมก่อน:</p>
        <pre style="background:#2a2d35;padding:20px;border-radius:10px;text-align:left;display:inline-block">
export GOOGLE_CLIENT_ID='your-client-id'
export GOOGLE_CLIENT_SECRET='your-client-secret'
export GOOGLE_REDIRECT_URI='""" + str(config.GOOGLE_REDIRECT_URI) + """'
cd ~/condo-demand-output/webapp
python3 start.py
        </pre>
        <p style="color:#6b7280;margin-top:20px;font-size:12px">
        1. ไปที่ <a href="https://console.cloud.google.com/apis/credentials" style="color:#60a5fa">Google Cloud Console</a><br>
        2. สร้าง OAuth 2.0 Client ID (Web application)<br>
        3. เพิ่ม Authorized redirect URI: <code style="background:#374151;padding:2px 6px;border-radius:4px">""" + str(config.GOOGLE_REDIRECT_URI) + """/auth/callback</code><br>
        4. คัดลอก Client ID และ Client Secret
        </p>
        </body></html>""", status_code=400)
    redirect_uri = config.GOOGLE_REDIRECT_URI
    print(f"OAuth redirect URI: {redirect_uri}")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")
        if not user_info:
            resp = await oauth.google.get("https://www.googleapis.com/oauth2/v2/userinfo")
            user_info = resp.json()
        email = user_info.get("email", "")
        if email != config.ALLOWED_EMAIL:
            return HTMLResponse("❌ Access denied. Only sorlakom.thana@gmail.com can access.", status_code=403)
        request.session["user"] = {
            "email": email,
            "name": user_info.get("name", email),
            "picture": user_info.get("picture", ""),
        }
        return RedirectResponse(url="/dashboard")
    except Exception as e:
        return HTMLResponse(f"❌ Auth error: {e}", status_code=400)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

# ─── Dashboard ───

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(require_user)):
    stats = db.get_stats()
    scans = db.get_recent_scans(10)
    leads = db.get_leads(limit=50)
    return HTMLResponse(_jinja_env.get_template("dashboard.html").render(
        request=request, user=user, stats=stats, leads=leads, scans=scans,
        scan_status=_scan_status,
    ))

@app.get("/api/stats")
async def api_stats(user=Depends(require_user)):
    stats = db.get_stats()
    return JSONResponse(stats)

@app.get("/api/leads")
async def api_leads(search: str = "", filter: str = "", limit: int = 100, user=Depends(require_user)):
    leads = db.get_leads(search=search, status_filter=filter, limit=limit)
    return JSONResponse({"leads": leads, "count": len(leads)})

@app.get("/api/scans")
async def api_scans(limit: int = 10, user=Depends(require_user)):
    scans = db.get_recent_scans(limit)
    return JSONResponse({"scans": scans})

_scan_status = {"running": False, "last_output": "", "error": ""}

@app.post("/api/scan")
async def trigger_scan(request: Request, user=Depends(require_user)):
    global _scan_status
    if _scan_status["running"]:
        return JSONResponse({"status": "already_running", "message": "Scan already in progress"})
    _scan_status = {"running": True, "last_output": "", "error": ""}
    def run_scan():
        global _scan_status
        try:
            scan_id = db.create_scan()
            import base64, tempfile, json as jmod
            fb_b64 = os.environ.get("FB_COOKIES", "")
            cookies_file = None
            if fb_b64:
                try:
                    cd = jmod.loads(base64.b64decode(fb_b64).decode())
                    t = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
                    jmod.dump(cd, t); t.close(); cookies_file = t.name
                except Exception as ce:
                    _scan_status["error"] = f"Cookie: {ce}"
            leads = render_scan.scan_facebook(cookies_file=cookies_file)
            if cookies_file and os.path.exists(cookies_file): os.unlink(cookies_file)
            for l in leads:
                if not l.get("dk"):
                    n = l.get("n","").strip().lower(); u = l.get("url","")
                    l["dk"] = n + "|" + (u[:120] if u else "")
            new_in_db = db.upsert_leads(scan_id, leads) if leads else 0
            db.finish_scan(scan_id, new_in_db, len(leads), sum(1 for l in leads if l.get("ft")))
            if new_in_db > 0: send_new_leads_telegram()
            _scan_status["running"] = False
            _scan_status["last_output"] = f"✅ Scan: {new_in_db} new / {len(leads)} total"
        except Exception as e:
            _scan_status["running"] = False; _scan_status["error"] = str(e)
            _scan_status["last_output"] = f"❌ {e}"
    threading.Thread(target=run_scan, daemon=True).start()
    return JSONResponse({"status": "started", "message": "🚀 กำลังสแกน 7 กลุ่ม..."})

@app.get("/api/scan-status")
async def scan_status(user=Depends(require_user)):
    return JSONResponse(_scan_status)

@app.get("/api/health")
async def health():
    return {"status": "ok", "db": "postgres" if config.USE_POSTGRES else "sqlite"}

@app.post("/api/ingest")
async def ingest_leads(request: Request, authorization: str = Header(default="")):
    expected_key = config.INGEST_API_KEY
    if expected_key:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = authorization.replace("Bearer ", "").strip()
        if not hmac.compare_digest(token, expected_key):
            raise HTTPException(status_code=403, detail="Invalid API key")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    leads = body.get("leads", [])
    if not leads:
        return JSONResponse({"status": "error", "message": "No leads provided"})
    scan_id = db.create_scan()
    new_count = db.upsert_leads(scan_id, leads)
    db.finish_scan(scan_id, new_count, len(leads), body.get("raw_count", 0))
    if new_count > 0:
        send_new_leads_telegram()
    return JSONResponse({"status": "ok", "new_leads": new_count, "total_leads": len(leads)})

# ─── Startup ───
@app.on_event("startup")
async def startup():
    db.init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
