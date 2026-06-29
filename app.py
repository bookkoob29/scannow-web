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

@app.post("/api/scan")
async def trigger_scan(request: Request, user=Depends(require_user)):
    """Run Facebook scan using cookies from FB_COOKIES env var."""
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

async def scan_status(user=Depends(require_user)):
    return JSONResponse(_scan_status)

@app.get("/api/scan/screenshot")
async def scan_screenshot(user=Depends(require_user)):
    """Debug: return last scan screenshot as base64."""
    ss = render_scan.get_last_screenshot()
    if ss and ss.get("data"):
        return HTMLResponse(f'<img src="data:image/png;base64,{ss["data"]}" style="max-width:100%"/><p>{ss.get("label","")} @ {ss.get("timestamp","")}</p>')
    return JSONResponse({"status": "no_screenshot"})

# ─── Telegram Notification ───

def send_new_leads_telegram():
    """Send HTML of new leads via Telegram."""
    new_leads = db.get_new_leads()
    if not new_leads:
        return
    
    # Build a mini HTML for new leads only
    now = datetime.now().strftime("%Y%m%d_%H%M")
    cards = []
    for i, lead in enumerate(new_leads, 1):
        name = lead.get("name","?")
        group = lead.get("group_name","?")
        budget = lead.get("budget","N/A")
        url = lead.get("post_url","")
        text = lead.get("full_text","")[:200]
        
        pu = f'https://www.facebook.com/profile.php?id={lead.get("profile_id","")}' if lead.get("profile_id") else ""
        pb = f'<a href="{pu}" style="color:#60a5fa" target="_blank">Profile</a>' if pu else ""
        pl = f'<a href="{url}" style="color:#34d399" target="_blank">View Post</a>' if url else ""
        
        cards.append(
            '<div style="background:#2a2d35;border-radius:10px;padding:12px;margin:8px 0">'
            f'<h3 style="color:#60a5fa;margin:0;font-size:14px">#{i} {name}'
            f'<span style="background:#f59e0b;color:#1c1e21;padding:0px 6px;border-radius:3px;font-size:9px;font-weight:700;margin-left:4px">NEW</span></h3>'
            f'<div style="color:#9ca3af;font-size:12px;margin-top:6px">'
            f'<span style="background:#374151;color:#9ca3af;padding:2px 6px;border-radius:4px;font-size:10px">{group}</span>'
            f' | <b style="color:#34d399">{budget}</b><br>'
            f'{text}</div>'
            f'<div style="margin-top:4px;font-size:11px">{pb} {pl}</div></div>'
        )
    
    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f'<title>SCANNOW New Leads {now}</title></head>'
        f'<body style="background:#1a1d23;color:#e4e6eb;font-family:sans-serif;padding:15px">'
        f'<h2 style="color:#fff">SCANNOW — New Leads</h2>'
        f'<p style="color:#9ca3af">{now} | {len(new_leads)} new</p>'
        + "".join(cards) + "</body></html>"
    )
    
    # Write to temp file and send
    tmp = os.path.join(tempfile.gettempdir(), f"scannow_new_{now}.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    
    os.system(f"python3 {config.TELEGRAM_SENDER} {tmp}")
    os.remove(tmp)
    
    # Mark as notified
    db.mark_leads_notified(len(new_leads))


# ─── Health check (for Render) ───

@app.get("/api/health")
async def health():
    return {"status": "ok", "db": "postgres" if config.USE_POSTGRES else "sqlite"}


# ─── Ingest API (local cron pushes results here) ───

@app.post("/api/ingest")
async def ingest_leads(request: Request, authorization: str = Header(default="")):
    # Verify API key
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
    
    # Send Telegram notification for new leads
    if new_count > 0:
        send_new_leads_telegram()
    
    return JSONResponse({
        "status": "ok",
        "scan_id": scan_id,
        "new_leads": new_count,
        "total_leads": len(leads),
    })


# ─── Startup ───
@app.on_event("startup")
async def startup():
    db.init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
