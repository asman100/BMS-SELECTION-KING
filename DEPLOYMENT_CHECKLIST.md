# Railway Deployment Checklist

## Pre-Deployment

- [x] Created `runtime.txt` with Python version
- [x] Created/Updated `Procfile` with gunicorn configuration
- [x] Updated `requirements_clean.txt` with all dependencies including gunicorn and eventlet
- [x] Created `railway.json` configuration
- [x] Created `.env.example` with example environment variables
- [x] Updated `app.py` to use environment variables
- [x] Updated `.gitignore` to exclude sensitive files
- [x] Created `RAILWAY_DEPLOYMENT.md` with detailed instructions
- [x] Created `nixpacks.toml` for Railway build configuration

## Railway Setup Steps

### 1. Create Railway Project
- [ ] Sign in to Railway (https://railway.app)
- [ ] Click "New Project"
- [ ] Select "Deploy from GitHub repo"
- [ ] Choose the BMS-SELECTION-KING repository
- [ ] Railway will automatically start building

### 2. Configure Environment Variables
- [ ] Go to your project in Railway
- [ ] Click on the service
- [ ] Go to "Variables" tab
- [ ] Add `SECRET_KEY` variable:
  - Generate a secure key: `python -c "import secrets; print(secrets.token_hex(32))"`
  - Paste the generated key as the value
- [ ] (Optional) Add `FLASK_ENV=production`

### 3. Choose Database Option

**Option A: SQLite with Persistent Volume (Simpler)**
- [ ] In Railway, go to service "Settings"
- [ ] Scroll to "Volumes" section
- [ ] Click "Add Volume"
- [ ] Set mount path to `/data`
- [ ] This ensures your SQLite database persists

**Option B: PostgreSQL (Recommended for Production)**
- [ ] In Railway project, click "New"
- [ ] Select "Database" → "PostgreSQL"
- [ ] Railway automatically creates `DATABASE_URL` variable
- [ ] Add `psycopg2-binary==2.9.10` to `requirements_clean.txt` if using PostgreSQL

### 4. Deploy
- [ ] Railway automatically deploys on git push
- [ ] Or click "Deploy" in Railway dashboard
- [ ] Monitor deployment logs for any errors

### 5. Post-Deployment
- [ ] Visit your Railway URL (e.g., https://your-app.up.railway.app)
- [ ] Log in with default credentials:
  - Username: `admin`
  - Password: `admin123`
- [ ] **IMMEDIATELY** change the admin password
- [ ] Create additional user accounts as needed
- [ ] Test all functionality:
  - [ ] Create a project
  - [ ] Add equipment templates
  - [ ] Add point templates
  - [ ] Create equipment presets
  - [ ] Generate reports

## Troubleshooting

### Build Fails
- Check Railway logs for specific error messages
- Verify `requirements_clean.txt` has all necessary packages
- Ensure `runtime.txt` Python version matches available versions

### Application Won't Start
- Verify `SECRET_KEY` environment variable is set
- Check application logs in Railway
- Ensure Procfile syntax is correct

### Database Issues
- For SQLite: Ensure volume is mounted at `/data`
- For PostgreSQL: Verify `DATABASE_URL` is set
- Check database initialization logs

### Can't Access Application
- Verify deployment is complete (green status in Railway)
- Check that Railway assigned a domain
- Look for any error messages in logs

## Security Checklist

- [ ] Changed default admin password
- [ ] Set strong `SECRET_KEY` (32+ random characters)
- [ ] Using HTTPS (Railway provides automatically)
- [ ] Database backups configured (if using PostgreSQL)
- [ ] Reviewed and approved user access list
- [ ] Disabled debug mode (ensure `FLASK_ENV=production`)

## Monitoring

- [ ] Bookmark your Railway dashboard for easy access
- [ ] Set up Railway notifications (optional)
- [ ] Test the application after each deployment
- [ ] Monitor application logs regularly

## Backup Strategy

### For SQLite (with Volume)
- Railway volumes are backed up automatically
- Download database manually: Access Railway shell and copy `/data/bms_tool.db`

### For PostgreSQL
- Use Railway's built-in backup feature
- Or use `pg_dump` to create manual backups:
  ```bash
  railway run pg_dump $DATABASE_URL > backup.sql
  ```

## Updates

To update the application:
1. Push changes to GitHub
2. Railway automatically detects and redeploys
3. Monitor logs during deployment
4. Test functionality after deployment

## Support

- Railway Docs: https://docs.railway.app
- Railway Discord: https://discord.gg/railway
- Repository Issues: [GitHub Issues Page]

---

**Last Updated:** $(date)
**Deployment Status:** Ready for Railway
