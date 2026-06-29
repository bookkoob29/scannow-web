"""SCANNOW Web App — Dashboard, OAuth, Scan Trigger, Telegram Notify."""
import os, sys, json, subprocess, threading, re, time, tempfile, hmac
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth

import config
import database as db

app = FastAPI(title="SCANNOW Web", version="1.0.0")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
        if not user:
            return templates.TemplateResponse("login.html", {"request": request})
        return RedirectResponse(url="/dashboard")
    except Exception as e:
        return HTMLResponse(f"<pre>Root error: {e}</pre>", status_code=500)

@app.get("/login")
async def login_page(request: Request):
    if get_user(request):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("login.html", {"request": request})

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
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "stats": stats,
        "scans": scans,
        "leads": leads,
        "scan_status": _scan_status,
    })

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

# ─── SCANNOW Trigger ───

_status_history = []

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
            output_lines = []
            
            # Run the scan script
            result = subprocess.run(
                [sys.executable, config.SCAN_SCRIPT, "--web-mode"],
                capture_output=True, text=True, timeout=180,
                cwd=os.path.dirname(config.SCAN_SCRIPT),
            )
            output = result.stdout + result.stderr
            output_lines.append(output)
            _scan_status["last_output"] = output[-500:]
            
            # Parse output for lead count
            new_count = 0
            total_count = 0
            raw_count = 0
            for line in output.split("\n"):
                m = re.search(r"Scanned:\s*(\d+)\s+new.*?(\d+)\s+RAW", line)
                if m:
                    new_count = int(m.group(1))
                    raw_count = int(m.group(2))
                m = re.search(r"Merged:\s*(\d+)\s+total.*?(\d+)\s+new", line)
                if m:
                    total_count = int(m.group(1))
            
            # Sync leads from persistent storage to DB
            leads_file = os.path.expanduser("~/.hermes/data/scannow_leads.json")
            if os.path.exists(leads_file):
                with open(leads_file) as f:
                    stored = json.load(f)
                leads_list = list(stored.values()) if isinstance(stored, dict) else stored
                
                # Add dedup keys to leads
                for lead in leads_list:
                    name = lead.get("n","").strip().lower()
                    url = lead.get("url","")
                    if url:
                        clean_url = re.sub(r'\?.*', '', url.strip())
                        lead["dk"] = name + "|" + clean_url
                    else:
                        lead["dk"] = name + lead.get("f","")[:120]
                
                new_in_db = db.upsert_leads(scan_id, leads_list)
            else:
                new_in_db = 0
            
            db.finish_scan(scan_id, new_in_db, total_count, raw_count)
            
            # Send Telegram notification for new leads
            if new_in_db > 0:
                send_new_leads_telegram()
            
            _scan_status["running"] = False
            
        except subprocess.TimeoutExpired:
            _scan_status["running"] = False
            _scan_status["error"] = "Scan timed out (180s)"
            db.finish_scan(scan_id, 0, 0, 0, error="Timeout")
        except Exception as e:
            _scan_status["running"] = False
            _scan_status["error"] = str(e)
            db.finish_scan(scan_id, 0, 0, 0, error=str(e))
    
    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()
    return JSONResponse({"status": "started", "message": "Scan started"})

@app.get("/api/scan-status")
async def scan_status(user=Depends(require_user)):
    return JSONResponse(_scan_status)

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
