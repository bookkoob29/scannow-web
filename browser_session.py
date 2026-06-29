"""Interactive Browser Session — user sees & interacts with remote Chromium via screenshots."""
import os, json, base64, tempfile, threading
from playwright.sync_api import sync_playwright
from datetime import datetime

class BrowserSession:
    """Manages a Playwright browser that user can interact with via screenshots + click relay."""
    
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self._playwright = None
        self.running = False
        self.error = ""
        self.viewport = {"width": 1280, "height": 720}
    
    def start(self):
        """Launch browser and navigate to Facebook."""
        p = sync_playwright().start()
        self._playwright = p
        
        # Try launching with fallback
        for launch_args in [
            {"headless": True, "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]},
            {"headless": True, "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"], "channel": "chrome"},
        ]:
            try:
                self.browser = p.chromium.launch(**launch_args)
                break
            except:
                continue
        if not self.browser:
            self.browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        
        self.context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36",
            viewport=self.viewport,
            locale="en-US",
            timezone_id="Asia/Bangkok",
            no_viewport=False,
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)
        
        # Navigate to Facebook
        self.page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)
        self.running = True
        return True
    
    def screenshot(self):
        """Take screenshot, return base64 data URI."""
        if not self.page:
            return ""
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            self.page.screenshot(path=tmp.name, full_page=False)
            with open(tmp.name, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            os.unlink(tmp.name)
            return b64
        except:
            return ""
    
    def click(self, x, y):
        """Click at viewport coordinates."""
        if not self.page:
            return False
        try:
            self.page.mouse.click(x, y)
            self.page.wait_for_timeout(500)
            return True
        except:
            return False
    
    def type_text(self, text):
        """Type text into currently focused element."""
        if not self.page:
            return False
        try:
            self.page.keyboard.type(text, delay=50)
            self.page.wait_for_timeout(300)
            return True
        except:
            return False
    
    def press_key(self, key):
        """Press a key (Enter, Tab, Escape, etc.)."""
        if not self.page:
            return False
        try:
            self.page.keyboard.press(key)
            self.page.wait_for_timeout(300)
            return True
        except:
            return False
    
    def get_url(self):
        """Get current page URL."""
        if not self.page:
            return ""
        try:
            return self.page.url
        except:
            return ""
    
    def is_logged_in(self):
        """Check if currently logged into Facebook."""
        if not self.page:
            return False
        try:
            url = self.page.url.lower()
            if "login" in url or "checkpoint" in url:
                return False
            # Check for Facebook feed elements
            has_feed = self.page.evaluate("""() => {
                return document.querySelector('[role="feed"]') !== null ||
                       document.querySelector('[role="main"]') !== null ||
                       document.querySelector('[data-pagelet="root"]') !== null;
            }""")
            return has_feed
        except:
            return False
    
    def run_facebook_scan(self, callback=None):
        """Run the scan using current logged-in session. Calls callback(leads) when done."""
        if not self.page or not self.context:
            return []
        
        from render_scan import GROUPS, KWS, KW_LABELS, is_demand, clean_text, extract_contact
        from datetime import datetime as dt
        
        all_leads = []
        seen_dedup = set()
        NOW = dt.utcnow()
        
        for gid, gname in GROUPS:
            for kw in KWS:
                kwl = KW_LABELS.get(kw, "?")
                url = f"https://www.facebook.com/groups/{gid}/search/?q={kw}"
                
                try:
                    self.page.goto(url, timeout=15000, wait_until="domcontentloaded")
                    self.page.wait_for_timeout(2000)
                except:
                    continue
                
                # Check for login redirect
                try:
                    if "login" in self.page.url.lower():
                        continue
                except:
                    continue
                
                # Collect articles
                try:
                    self.page.wait_for_selector('[role="article"]', timeout=5000)
                except:
                    pass
                self.page.wait_for_timeout(1000)
                
                try:
                    articles = self.page.evaluate("""() => {
                        const arts = document.querySelectorAll('[role="article"]');
                        return Array.from(arts).slice(0,5).map(a => {
                            const links = a.querySelectorAll('a[href*="/groups/"]');
                            const textEl = a.querySelector('[dir="auto"]');
                            const imgs = a.querySelectorAll('img[alt*="profile"], img[alt*="Profile"]');
                            let name = imgs.length > 0 ? imgs[0].alt || '' : '';
                            if (!name && links.length > 0) name = links[0].innerText || links[0].title || '';
                            return {
                                html: textEl ? textEl.innerText : '',
                                link: links.length > 0 ? links[0].href : '',
                                name: name
                            };
                        });
                    }""")
                except Exception:
                    articles = []
                
                for art in articles:
                    raw = art.get("html", "")
                    if not raw or not is_demand(raw): continue
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
                    all_leads.append({
                        "n": name, "g": gname, "t": "Expat",
                        "b": "N/A", "u": "Now", "l": "Bangkok",
                        "p": "", "d": NOW.strftime("%Y-%m-%d"),
                        "c": contact, "f": body, "ft": 1 if raw != text else 0,
                        "url": clean_url or "", "dk": dk,
                    })
        
        if callback:
            callback(all_leads)
        return all_leads
    
    def close(self):
        """Close browser and cleanup."""
        self.running = False
        try:
            if self.page: self.page.close()
        except: pass
        try:
            if self.context: self.context.close()
        except: pass
        try:
            if self.browser: self.browser.close()
        except: pass
        try:
            if self._playwright: self._playwright.stop()
        except: pass
