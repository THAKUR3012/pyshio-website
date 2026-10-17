# Deploying Pyshio Frontend to Vercel

This guide explains how to deploy the **Pyshio Frontend** to [Vercel](https://vercel.com) and connect it to your **Render Backend API**.

---

## How It Works

* The [frontend/](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/frontend/) folder contains pure static HTML, JavaScript (ES6+), and Tailwind CSS.
* [vercel.json](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/frontend/vercel.json) automatically proxies `/api/*` calls from the Vercel edge to your Render backend.
* Because calls are proxied under the same Vercel domain, **authentication cookies work seamlessly without cross-origin blocking**!

---

## Step-by-Step Deployment

### Step 1: Update your Backend URL in `vercel.json`

Open [frontend/vercel.json](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/frontend/vercel.json) (and root [vercel.json](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/vercel.json)):

Replace `https://pyshio-backend.onrender.com` with your real Render backend URL:

```json
{
  "version": 2,
  "cleanUrls": true,
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://your-actual-backend.onrender.com/api/:path*"
    }
  ]
}
```

Commit and push your changes to GitHub.

---

### Step 2: Deploy on Vercel

#### Option A: Via Vercel Web Dashboard (Recommended)
1. Log in to [Vercel](https://vercel.com).
2. Click **Add New...** > **Project**.
3. Import your GitHub repository.
4. In **Project Configuration**:
   - **Framework Preset:** `Other`
   - **Root Directory:** Click `Edit` and select `frontend` (or leave default if deploying the whole monorepo).
   - **Build and Output Settings:** Leave default (no build command needed).
5. Click **Deploy**.

#### Option B: Via Vercel CLI
If you have `vercel` CLI installed:
```bash
cd frontend
vercel --prod
```

---

### Step 3: Link Vercel Domain in Render Backend

In your [Render Dashboard](https://dashboard.render.com):
1. Open your `pyshio-backend` web service.
2. Go to **Environment**.
3. Update `PYSHIO_SITE_URL`:
   ```ini
   PYSHIO_SITE_URL=https://your-pyshio-app.vercel.app
   ```
4. Save and deploy.

---

## Verification

1. Open your live Vercel URL (e.g. `https://your-app.vercel.app`).
2. Register a new user or log in with the admin credentials you set in Render.
3. Test search, doctor registration, and pharmacy quotes. All API traffic flows automatically from Vercel to your Render backend and PostgreSQL database.
