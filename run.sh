#!/bin/bash
export GOOGLE_CLIENT_ID='305344891021-uvdugj6rm67g97sl7cgskul2sqrolhhb.apps.googleusercontent.com'
export GOOGLE_CLIENT_SECRET='GOCSPX...Lsb5'
export GOOGLE_REDIRECT_URI='https://dermatographic-farmerlike-amiya.ngrok-free.dev/auth/callback'
cd ~/condo-demand-output/webapp
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8080
