# Railway Quick Start Guide

## 🚀 Deploy in 5 Minutes

### Step 1: Create Railway Account
Visit https://railway.app and sign up (free tier available)

### Step 2: Deploy from GitHub
1. Click "New Project" → "Deploy from GitHub repo"
2. Select `asman100/BMS-SELECTION-KING`
3. Railway auto-detects and starts building ✓

### Step 3: Set Environment Variable
1. Go to your service → "Variables" tab
2. Add `SECRET_KEY`:
   ```bash
   # Generate secure key (run this locally):
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
3. Paste the generated key

### Step 4: Add Persistent Storage (Optional)
For SQLite database persistence:
1. Service → "Settings" → "Volumes"
2. Click "Add Volume"
3. Mount path: `/data`

### Step 5: Access Your App
1. Wait for deployment to complete (2-3 minutes)
2. Click the generated URL (e.g., `https://bms-selection.up.railway.app`)
3. Login: `admin` / `admin123`
4. **Change password immediately!**

## ✅ That's It!

Your BMS Selection Tool is now live on Railway.

## 📚 Need More Details?
- Full guide: [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md)
- Checklist: [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)

## 🔧 Key Features Enabled
- ✅ Auto-scaling
- ✅ HTTPS by default
- ✅ Zero-downtime deployments
- ✅ Real-time logs
- ✅ WebSocket support (for real-time updates)

## 💡 Pro Tips
1. Use PostgreSQL for production (Railway provides it free in free tier)
2. Enable automatic backups
3. Set up deployment notifications
4. Monitor logs regularly

## 🆘 Issues?
Check [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) troubleshooting section.
