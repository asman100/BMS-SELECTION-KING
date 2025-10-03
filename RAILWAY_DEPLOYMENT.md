# Railway Deployment Guide

This guide will help you deploy the BMS Selection Tool to Railway.

## Prerequisites

1. A Railway account (sign up at https://railway.app)
2. Git repository connected to Railway

## Deployment Steps

### 1. Create a New Project on Railway

1. Go to https://railway.app/new
2. Click "Deploy from GitHub repo"
3. Select this repository
4. Railway will automatically detect the project and start building

### 2. Configure Environment Variables

Add the following environment variables in the Railway dashboard:

**Required:**
- `SECRET_KEY` - A secure random string for Flask sessions (generate with: `python -c "import secrets; print(secrets.token_hex(32))"`)

**Optional:**
- `FLASK_ENV` - Set to `production` (default)
- `PORT` - Railway sets this automatically, no need to configure

### 3. Add a Volume (Optional but Recommended for SQLite)

If you want to use SQLite with persistent storage:

1. In your Railway project, click on your service
2. Go to the "Variables" tab
3. Click "Add Variable"
4. Add `RAILWAY_VOLUME_MOUNT_PATH` with value `/data`
5. Go to the "Settings" tab
6. Scroll to "Volumes" section
7. Click "Add Volume"
8. Set mount path to `/data`

This ensures your SQLite database persists across deployments.

### 4. Alternative: Use PostgreSQL (Recommended for Production)

For better performance and reliability:

1. In Railway, click "New" → "Database" → "PostgreSQL"
2. Railway will automatically set the `DATABASE_URL` environment variable
3. The app will automatically detect and use PostgreSQL

Note: If using PostgreSQL, you'll need to add `psycopg2-binary` to requirements:
```bash
echo "psycopg2-binary==2.9.10" >> requirements_clean.txt
```

### 5. Deploy

Railway will automatically deploy when you push to your repository. You can also manually trigger a deployment from the Railway dashboard.

### 6. Access Your Application

Once deployed, Railway will provide you with a URL like: `https://your-app-name.up.railway.app`

## File Structure

The following files are required for Railway deployment:

- `Procfile` - Defines how to run the application
- `runtime.txt` - Specifies Python version
- `requirements_clean.txt` - Lists Python dependencies
- `railway.json` - Railway-specific configuration
- `.env.example` - Example environment variables (not used in production)

## Environment Variables Reference

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `SECRET_KEY` | Flask secret key for sessions | Yes | - |
| `DATABASE_URL` | PostgreSQL connection string | No | SQLite |
| `FLASK_ENV` | Environment (development/production) | No | production |
| `PORT` | Port to run the application | No | Set by Railway |
| `RAILWAY_VOLUME_MOUNT_PATH` | Volume mount path for SQLite | No | - |

## Database Initialization

The application automatically initializes the database on first run:
1. Creates all necessary tables
2. Creates a default admin user (username: `admin`, password: `admin123`)
3. **⚠️ IMPORTANT: Change the admin password immediately after first login!**

## Troubleshooting

### Application won't start
- Check the logs in Railway dashboard
- Verify all required environment variables are set
- Ensure `SECRET_KEY` is configured

### Database not persisting
- If using SQLite, ensure you've added a volume at `/data`
- Consider switching to PostgreSQL for better reliability

### SocketIO connection issues
- The app uses WebSockets via Flask-SocketIO
- Railway supports WebSockets automatically
- Ensure CORS is properly configured (already done in app.py)

### Build failures
- Check that all dependencies in `requirements_clean.txt` are available
- Verify Python version in `runtime.txt` matches your local development

## Local Development

To run locally:

```bash
# Install dependencies
pip install -r requirements_clean.txt

# Set environment variables (optional)
export SECRET_KEY="your-secret-key"
export FLASK_ENV="development"

# Run the application
python app.py
```

The app will be available at `http://localhost:5001`

## Security Recommendations

1. **Change default admin password** immediately after deployment
2. Use a strong, random `SECRET_KEY` in production
3. Use PostgreSQL for production (more secure than SQLite with persistent volumes)
4. Regularly backup your database
5. Enable HTTPS (Railway provides this automatically)
6. Review user access regularly in the admin panel

## Monitoring

Railway provides built-in monitoring:
- View logs in real-time
- Monitor CPU and memory usage
- Set up webhooks for deployment notifications

## Updates and Maintenance

To update the deployed application:
1. Push changes to your Git repository
2. Railway automatically detects changes and redeploys
3. Monitor the deployment logs for any issues

## Support

For Railway-specific issues, visit:
- Railway Documentation: https://docs.railway.app
- Railway Discord: https://discord.gg/railway

For application issues, check the repository issues page.
