# Deploying Pyshio Backend to Render

This guide walks you through deploying the **Pyshio Backend API** and **PostgreSQL Database** on [Render](https://render.com).

Render natively supports our Blueprint specification (`render.yaml`), allowing you to provision the backend web service, managed PostgreSQL database, and cron worker in a single step.

---

## Architecture on Render

* **Web Service (`pyshio-backend`)**: Python FastAPI app running on Gunicorn with Uvicorn workers.
* **Database (`pyshio-postgres`)**: Managed PostgreSQL 16 database automatically linked to the web service via `DATABASE_URL`.
* **Cron Worker (`pyshio-jobs`)**: Runs [jobs.py](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/backend/jobs.py) every 5 minutes to process expiring orders, follow-up reminders, and notifications.

---

## Step-by-Step Deployment

### Option A: 1-Click Blueprint (Recommended)

1. **Push your code to GitHub / GitLab**.
2. Log in to your [Render Dashboard](https://dashboard.render.com).
3. Click **New +** > **Blueprint**.
4. Connect your repository.
5. Render will automatically read [render.yaml](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/render.yaml) from the repository root:
   - It will show the **pyshio-postgres** database, **pyshio-backend** web service, and **pyshio-jobs** cron worker.
6. Click **Apply**.
7. Render will provision the PostgreSQL database, run database initialization from `postgres_schema.sql`, and launch the API!

---

### Option B: Manual Setup on Render

If you prefer to configure each component manually:

#### 1. Create PostgreSQL Database
1. In Render Dashboard, click **New +** > **PostgreSQL**.
2. Name: `pyshio-postgres`.
3. Database: `pyshio_db`.
4. User: `pyshio`.
5. Click **Create Database**.
6. Copy the **Internal Database URL**.

#### 2. Create the Backend Web Service
1. In Render Dashboard, click **New +** > **Web Service**.
2. Connect your Git repository.
3. Configure the settings:
   - **Name:** `pyshio-backend`
   - **Root Directory:** `backend`
   - **Environment:** `Python`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -w 2 -k uvicorn.workers.UvicornWorker server:app --bind 0.0.0.0:$PORT`
4. In **Environment Variables**, add:
   - `DATABASE_URL`: *(paste the Internal Database URL from Step 1)*
   - `PYSHIO_TIMEZONE`: `Asia/Kolkata`
   - `PYSHIO_ADMIN_EMAIL`: `admin@yourdomain.com`
   - `PYSHIO_ADMIN_PASSWORD`: `<strong-admin-password>`
   - `PYSHIO_SITE_URL`: `https://your-frontend.vercel.app` (or your Render URL)
   - `CORS_ORIGINS`: `https://*.vercel.app,http://localhost:3000`
5. Click **Deploy Web Service**.

#### 3. Create the Cron Job (Background Worker)
1. In Render Dashboard, click **New +** > **Cron Job**.
2. Connect the same repository.
3. Configure:
   - **Name:** `pyshio-jobs`
   - **Root Directory:** `backend`
   - **Command:** `python jobs.py`
   - **Schedule:** `*/5 * * * *` (every 5 minutes)
4. Add environment variables:
   - `DATABASE_URL`: *(same Internal Database URL)*
   - `PYSHIO_TIMEZONE`: `Asia/Kolkata`
5. Click **Create Cron Job**.

---

## Your Render Backend URL

Once deployed, Render provides a public HTTPS URL (e.g., `https://pyshio-backend.onrender.com`).
Copy this URL to connect your **Vercel Frontend**!
