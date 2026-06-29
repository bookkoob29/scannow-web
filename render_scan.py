"""SCANNOW Render — Facebook group scanning using Playwright (works on Render)."""
import os, sys, json, re, time, tempfile
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

NOW = datetime.utcnow()
TS = NOW.strftime("%Y%m%d_%H%M")

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

# JS filters
JS_DEMAND = r'(looking|Looking|มองหา|ตามหา|Seeking|ต้องการเช่า|หากำลังมองหา|อยากได้|หาเช่า|want to rent|Want to rent)'
JS_SUPPLY = r'(Owner post|For Rent|for rent|ให้เช่า|ปล่อยเช่า|ให้เช่าคอนโด|ปล่อยเช่าคอนโด)'

def clean_text(raw):
    if not raw: return ""
    for marker in ["\nAll reactions:", "All reactions:", "\nMost relevant\n",
                   "\nAnswer as Book", "\nComment as Book"]:
        idx = raw.find(marker)
        if idx > -1: raw = raw[:idx]
    raw = re.sub(r'\nLike\nComment\nShare\s*$', '', raw)
    raw = re.sub(r'\s*Like\s*$', '', raw)
    raw = re.sub(r'\nNo comments yet.*?\.\s*', '', raw)
    raw = re.sub(r'\n\d+\s*$', '', raw)
    return raw.strip()

def is_demand(text):
    if not text: return False
    demand = ['looking', 'Looking', 'มองหา', 'ตามหา', 'Seeking', 'ต้องการเช่า',
              'หา', 'Apartment Search', 'กำลังมองหา', 'อยากได้', 'หาเช่า',
              'want to rent', 'Want to rent']
    supply = ['Owner post', 'For Rent', 'for rent', 'ให้เช่า', 'ปล่อยเช่า',
              'ขาย', 'For Sale', 'ให้เช่าคอนโด', 'ปล่อยเช่าคอนโด']
    t = text[:200]
    return any(kw in t for kw in demand) and not any(k in t for k in supply)

def extract_contact(text):
    cres = []
    for pat in [
        r'(?:Line|LINE|ไลน์)\s*[:：]?\s*@?[\w.-]{2,30}',
        r'WhatsApp\s*[:：]?\s*\+?\d[\d\s-]{6,15}',
        r'[\w.+-]+@[\w-]+\.[\w.-]+',
    ]:
        m = re.search(pat, text, re.I)
        if m: cres.append(m.group(0))
    return ' | '.join(cres) if cres else ''

def scan_facebook(cookies_file=None, db_connector=None):
    """Run Facebook scan. Returns list of leads."""
    all_leads = []
    seen_dedup = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )

        # Load cookies if provided
        if cookies_file and os.path.exists(cookies_file):
            with open(cookies_file) as f:
                cookies = json.load(f)
            ctx.add_cookies(cookies)
            print(f"  Loaded {len(cookies)} cookies")
        else:
            print("  WARNING: No cookies — Facebook will show login page")

        page = ctx.new_page()
        page.set_default_timeout(8000)

        for gid, gname in GROUPS:
            for kw in KWS:
                kwl = KW_LABELS.get(kw, "?")
                url = f"https://www.facebook.com/groups/{gid}/search/?q={kw}"
                print(f"  [{kwl}] {gname}...", end=" ", flush=True)

                try:
                    page.goto(url, timeout=10000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1000)
                except Exception:
                    try: page.close()
                    except: pass
                    page = ctx.new_page()
                    page.set_default_timeout(8000)
                    try:
                        page.goto(url, timeout=10000, wait_until="domcontentloaded")
                        page.wait_for_timeout(1000)
                    except Exception:
                        print("SKIP (timeout)")
                        continue

                # Count + collect
                try:
                    articles = page.evaluate("""() => {
                        const arts = document.querySelectorAll('[role="article"]');
                        return Array.from(arts).slice(0,3).map(a => {
                            const links = a.querySelectorAll('a[href*="/groups/"]');
                            const textEl = a.querySelector('[dir="auto"]');
                            return {
                                html: textEl ? textEl.innerText : '',
                                link: links.length > 0 ? links[0].href : '',
                            };
                        });
                    }""")
                except Exception:
                    articles = []

                if not articles:
                    print("0 found")
                    continue

                count = 0
                for art in articles:
                    raw = art.get("html", "")
                    if not raw: continue
                    if not is_demand(raw): continue

                    text = clean_text(raw)
                    # Extract name: first line before newline
                    lines = text.split("\n")
                    name = lines[0].strip() if lines else "Unknown"
                    body = "\n".join(lines[1:]).strip()

                    # Build dedup key
                    url_key = art.get("link", "")
                    clean_url = re.sub(r'\?.*', '', url_key).strip().lower() if url_key else ""
                    dk = (name.strip().lower() + "|" + clean_url[:120]).strip()
                    if not dk or dk in seen_dedup: continue
                    seen_dedup.add(dk)

                    contact = extract_contact(text)
                    has_raw = 1 if raw != text else 0

                    lead = {
                        "n": name, "g": gname, "t": "Expat",
                        "b": "N/A", "u": "Now", "l": "Bangkok",
                        "p": "", "d": NOW.strftime("%Y-%m-%d"),
                        "c": contact, "f": body, "ft": has_raw,
                        "url": clean_url or "",
                        "dk": dk,
                    }
                    all_leads.append(lead)
                    count += 1

                print(f"{count} found")

        browser.close()

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
