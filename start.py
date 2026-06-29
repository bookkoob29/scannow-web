#!/usr/bin/env python3
"""Start SCANNOW Web App with ngrok tunnel."""
import os, sys, subprocess, time, json, urllib.request

WEB_PORT = 8080
NGROK_AUTH_TOKEN = os.environ.get("NGROK_AUTH_TOKEN", "")

def get_ngrok_url():
    try:
        resp = urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=3)
        data = json.loads(resp.read())
        for tunnel in data.get("tunnels", []):
            if tunnel.get("proto") == "https":
                return tunnel["public_url"]
    except:
        pass
    return None

def main():
    print("=" * 60)
    print("  SCANNOW Web App")
    print("=" * 60)

    print("\nStarting ngrok tunnel...")
    ngrok_cmd = ["ngrok", "http", str(WEB_PORT), "--log=stdout"]
    if NGROK_AUTH_TOKEN:
        ngrok_cmd.insert(1, f"--authtoken={NGROK_AUTH_TOKEN}")
    ngrok_proc = subprocess.Popen(ngrok_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    public_url = None
    for i in range(15):
        time.sleep(1)
        public_url = get_ngrok_url()
        if public_url:
            break

    if public_url:
        print(f"  Public URL: {public_url}")
        # Auto-set redirect URI env var
        os.environ["GOOGLE_REDIRECT_URI"] = f"{public_url}/auth/callback"
        print(f"  GOOGLE_REDIRECT_URI set to: {public_url}/auth/callback")
        
        if not os.environ.get("GOOGLE_CLIENT_ID"):
            print(f"\n  ⚠️  ยังไม่ได้ตั้งค่า Google OAuth")
            print(f"  ให้ทำดังนี้:")
            print(f"  1. ไปที่ https://console.cloud.google.com/apis/credentials")
            print(f"  2. สร้าง OAuth 2.0 Client ID (Web application)")
            print(f"  3. เพิ่ม Authorized redirect URI:")
            print(f"     {public_url}/auth/callback")
            print(f"  4. รัน: export GOOGLE_CLIENT_ID='...' GOOGLE_CLIENT_SECRET='...'")
            print(f"  5. รัน: python3 start.py อีกครั้ง")
        else:
            print(f"  ✅ Google OAuth configured!")
    else:
        print(f"  ngrok URL not detected. Local URL: http://localhost:{WEB_PORT}")

    print(f"\nStarting web server on port {WEB_PORT}...\n")
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        subprocess.run([sys.executable, "-m", "uvicorn", "app:app",
            "--host", "0.0.0.0", "--port", str(WEB_PORT), "--reload"])
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        ngrok_proc.terminate()
        print("Done.")

if __name__ == "__main__":
    main()
