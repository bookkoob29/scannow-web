"""SCANNOW Render — Facebook group scanning using Playwright (works on Render)."""
import os, sys, json, re, time, tempfile, base64
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

NOW = datetime.utcnow()
TS = NOW.strftime("%Y%m%d_%H%M")
LAST_SCREENSHOT = {"data": "", "path": "", "timestamp": ""}

GROUPS = [
    ("661207761960248", "Bangkok Expats condo-house"),
    ("2190481404338163", "Bangkok Expats Apts"),
    ("2157264477895406", "BANGKOK EXPATS"),
    ("1445573419202140", "Short/Long Term Rental"),
    ("458098031664389", "Condo rental by Owner"),
    ("828752307285895", "Condo Rent for Expat"),
    ("386316227145323", "Condo Owner"),
]
KWS = ["looking%20for", "%E0%B8%95%E0%B8%B2%E0%B8%A1%E0%B8%AB%E0%B8%B2"]
KW_LABELS = {KWS[0]: "looking", KWS[1]: "ตามหา"}

JS_DEMAND = r'(looking|Looking|มองหา|ตามหา|Seeking|ต้องการเช่า|หา|Apartment Search|กำลังมองหา|อยากได้|หาเช่า|want to rent|Want to rent)'
JS_SUPPLY = r'(Owner post|For Rent|for rent|ให้เช่า|ปล่อยเช่า|ให้เช่าคอนโด|ปล่อยเช่าคอนโด|ขาย|For Sale)'

def clean_text(raw):
    if not raw: return ""
    for marker in ["\nAll reactions:", "All reactions:", "\nMost relevant\n", "\nAnswer as Book", "\nComment as Book"]:
        idx = raw.find(marker)
        if idx > -1: raw = raw[:idx]
    raw = re.sub(r'\nLike\nComment\nShare\s*$', '', raw)
    raw = re.sub(r'\s*Like\s*$', '', raw)
    raw = re.sub(r'\nNo comments yet.*?\.\s*', '', raw)
    raw = re.sub(r'\n\d+\s*$', '', raw)
    return raw.strip()

def is_demand(text):
    if not text: return False
    t = text[:200]
    return bool(re.search(JS_DEMAND, t)) and not bool(re.search(JS_SUPPLY, t))

def extract_contact(text):
    cres = []
    for pat in [r'(?:Line|LINE|ไลน์)\s*[:：]?\s*@?[\w.-]{2,30}', r'WhatsApp\s*[:：]?\s*\+?\d[\d\s-]{6,15}', r'[\w.+-]+@[\w-]+\.[\w.-]+']:
        m = re.search(pat, text, re.I)
        if m: cres.append(m.group(0))
    return ' | '.join(cres) if cres else ''

def take_debug_screenshot(page, label=""):
    """Take a screenshot and save as data URI for debugging via API."""
    global LAST_SCREENSHOT
    try:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        page.screenshot(path=tmp.name, full_page=False)
        with open(tmp.name, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp.name)
        LAST_SCREENSHOT = {
            "data": b64[:50000],  # first 50KB
            "path": tmp.name,
            "timestamp": NOW.isoformat(),
            "label": label
        }
        return True
    except Exception:
        return False

def get_last_screenshot():
    """Return last screenshot for API."""
    return LAST_SCREENSHOT

def scan_facebook(cookies_file=None, db_connector=None):
    """Run Facebook scan. Returns list of leads."""
    global LAST_SCREENSHOT
    all_leads = []
    seen_dedup = set()
    LAST_SCREENSHOT = {"data": "", "path": "", "timestamp": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        print(f"  Browser launched")
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Bangkok",
        )

        # Load cookies
        if cookies_file and os.path.exists(cookies_file):
            with open(cookies_file) as f:
                cookies = json.load(f)
            ctx.add_cookies(cookies)
            print(f"  Loaded {len(cookies)} cookies")
        else:
            print("  WARNING: No cookies")

        page = ctx.new_page()
        page.set_default_timeout(15000)

        # Step 1: Visit facebook.com first to establish session
        print("  Navigating to facebook.com...", end=" ", flush=True)
        try:
            page.goto("https://www.facebook.com/", timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            print("OK")
        except Exception as e:
            print(f"WARN: {str(e)[:40]}")
            page.goto("https://mbasic.facebook.com/", timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

        # Check if we're logged in
        page_url = page.url.lower()
        if "login" in page_url or "checkpoint" in page_url:
            print("  ⚠️ Login page detected — cookies may be invalid")
            take_debug_screenshot(page, "login_page")
            # Try mbasic as fallback
            print("  Trying mbasic.facebook.com...")
            page.goto("https://mbasic.facebook.com/", timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            page_url = page.url.lower()
            if "login" in page_url:
                print("  ❌ Still showing login page")
                take_debug_screenshot(page, "login_page_mbasic")
        else:
            print("  ✅ Logged in (feed loaded)")

        # Step 2: Scan each group
        for gid, gname in GROUPS:
            for kw in KWS:
                kwl = KW_LABELS.get(kw, "?")
                url = f"https://www.facebook.com/groups/{gid}/search/?q={kw}"
                print(f"  [{kwl}] {gname}...", end=" ", flush=True)

                try:
                    page.goto(url, timeout=15000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                except Exception:
                    print("SKIP (timeout)")
                    continue

                # Check page content — if no articles, take screenshot
                try:
                    page_title = page.title()
                    page_url_now = page.url.lower()
                    if "login" in page_url_now or "checkpoint" in page_url_now:
                        print("LOGIN REDIRECT")
                        take_debug_screenshot(page, f"login_{gid}_{kwl}")
                        continue
                except:
                    pass

                # Wait a bit for dynamic content
                try:
                    page.wait_for_selector('[role="article"]', timeout=5000)
                except:
                    pass  # might not exist in search results

                page.wait_for_timeout(1000)

                # Collect articles
                try:
                    articles = page.evaluate("""() => {
                        const arts = document.querySelectorAll('[role="article"]');
                        const results = [];
                        for (let a of arts) {
                            const links = a.querySelectorAll('a[href*="/groups/"]');
                            const textEl = a.querySelector('[dir="auto"]');
                            const imgs = a.querySelectorAll('img[alt*="profile"], img[alt*="Profile"]');
                            let name = '';
                            if (imgs.length > 0) name = imgs[0].alt || '';
                            if (!name && links.length > 0) name = links[0].innerText || links[0].title || '';
                            results.push({
                                html: textEl ? textEl.innerText : '',
                                link: links.length > 0 ? links[0].href : '',
                                name: name
                            });
                        }
                        return results.slice(0, 5);
                    }""")
                except Exception as e:
                    articles = []
                    print(f"evaluate err: {str(e)[:40]}", end=" ")

                if not articles:
                    print("0 found", end="")
                    # Take screenshot for debugging
                    take_debug_screenshot(page, f"noresults_{gid}_{kwl}")
                    print(" 📸")
                    continue
                print(f"", end="")

                count = 0
                for art in articles:
                    raw = art.get("html", "")
                    if not raw: continue
                    if not is_demand(raw): continue

                    text = clean_text(raw)
                    lines = text.split("\n")
                    name = art.get("name", "") or (lines[0].strip() if lines else "Unknown")
                    body = "\n".join(lines[1:]).strip()

                    url_key = art.get("link", "")
                    clean_url = re.sub(r'\?.*', '', url_key).strip().lower() if url_key else ""
                    dk = (name.strip().lower() + "|" + clean_url[:120]).strip()
                    if not dk or dk in seen_dedup: continue
                    seen_dedup.add(dk)

                    contact = extract_contact(text)
                    has_raw = 1 if raw != text else 0

                    all_leads.append({
                        "n": name, "g": gname, "t": "Expat",
                        "b": "N/A", "u": "Now", "l": "Bangkok",
                        "p": "", "d": NOW.strftime("%Y-%m-%d"),
                        "c": contact, "f": body, "ft": has_raw,
                        "url": clean_url or "", "dk": dk,
                    })
                    count += 1

                print(f" → {count} leads")

        browser.close()

    print(f"\n  ✅ Total leads found: {len(all_leads)}")
    return all_leads


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookies", help="Path to cookies JSON file")
    parser.add_argument("--output", help="Output JSON file path")
    args = parser.parse_args()

    leads = scan_facebook(cookies_file=args.cookies)
    print(f"\nTotal leads: {len(leads)}")
    if args.output:
        with open(args.output, "w") as f:
            json.dump(leads, f, indent=2)
        print(f"Saved to {args.output}")
