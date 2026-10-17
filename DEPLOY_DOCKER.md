# Deploying Pyshio with Docker & PostgreSQL

This guide explains how to deploy **Pyshio** using Docker, Docker Compose, and PostgreSQL on any Linux VPS (Ubuntu, Debian, AlmaLinux, etc.) or for local development.

---

## Architecture Overview

The containerized stack consists of:
- **`db`**: PostgreSQL 16 Alpine container with persistent storage and automated schema bootstrap (`postgres_schema.sql`).
- **`web`**: Production FastAPI application running on Gunicorn with Uvicorn workers on port `8080`.
- **`jobs`**: Background worker running [jobs.py](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/jobs.py) every 5 minutes (reminders, order timeouts, notifications, cleanups).
- **Persistent Volumes**:
  - `postgres_data`: Persistent PostgreSQL database files.
  - `uploads_data`: Lab reports, prescription uploads, and attachments.

---

## 1. Quick Start (Local or Server)

### Step 1: Clone the repository
```bash
git clone <your-repo-url> pyshio
cd pyshio
```

### Step 2: Configure Environment Variables
Copy [.env.example](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/.env.example) to `.env`:
```bash
cp .env.example .env
```

Edit `.env` with your domain and secure passwords:
```ini
# PostgreSQL
POSTGRES_DB=pyshio_db
POSTGRES_USER=pyshio
POSTGRES_PASSWORD=your_strong_postgres_password

# Platform
PYSHIO_SITE_URL=https://yourdomain.com
PYSHIO_TIMEZONE=Asia/Kolkata
PYSHIO_ADMIN_EMAIL=admin@yourdomain.com
PYSHIO_ADMIN_PASSWORD=your_strong_admin_password

# SMTP Email
SMTP_HOST=smtp.yourdomain.com
SMTP_PORT=465
SMTP_USER=bookings@yourdomain.com
SMTP_PASSWORD=your_smtp_password
SMTP_FROM=bookings@yourdomain.com
```

### Step 3: Build & Start Containers
```bash
docker compose up -d --build
```

### Step 4: Verify Status & Logs
```bash
docker compose ps
docker compose logs -f web
```

Open `http://localhost:8080` (or your VPS IP:8080) in your browser.

---

## 2. Production VPS Setup with Nginx & SSL

On a production VPS, run Docker containers on `127.0.0.1:8080` and use **Nginx** as a reverse proxy for SSL/HTTPS termination.

### 1. Install Nginx and Certbot on Host
```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 2. Configure Nginx Reverse Proxy
Create `/etc/nginx/sites-available/pyshio`:
```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable site and restart Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/pyshio /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 3. Generate Free SSL Certificate
```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

---

## 3. Database Management & Maintenance

### Export Latest PostgreSQL Schema
To regenerate [postgres_schema.sql](file:///home/vishnukantsingh/Desktop/Sunny/claude_practicer/pyshio-website/postgres_schema.sql) after making schema changes:
```bash
python export_postgres.py
```

### Connect to PostgreSQL Shell inside Docker
```bash
docker compose exec db psql -U pyshio -d pyshio_db
```

### Backup PostgreSQL Database
```bash
docker compose exec -T db pg_dump -U pyshio pyshio_db > backup_$(date +%F).sql
```

### Restore PostgreSQL Database
```bash
docker compose exec -T db psql -U pyshio -d pyshio_db < backup.sql
```

### Seed Demo / Test Data (Optional)
```bash
docker compose exec web python seed_demo.py
```
To wipe demo data:
```bash
docker compose exec web python seed_demo.py --wipe
```

---

## 4. Useful Docker Commands

| Command | Action |
|:---|:---|
| `docker compose up -d` | Start all services in background |
| `docker compose stop` | Gracefully stop all services |
| `docker compose down` | Stop and remove containers and network |
| `docker compose logs -f web` | Tail logs of the web app |
| `docker compose logs -f jobs` | Tail logs of the background worker |
| `docker compose restart web` | Restart web service |
