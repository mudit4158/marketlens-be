# MarketLens — Deployment Guide (2-server setup)

## Overview
- **DB server** — TimescaleDB, data on a block storage volume, daily backups to object storage
- **App server** — FastAPI + nginx + SSL, connects to DB server over private network

---

## 1. Provision servers (Hetzner example)

```bash
# Create private network so servers talk without going through public internet
hcloud network create --name marketlens-net --ip-range 10.0.0.0/16
hcloud network add-subnet marketlens-net --type server --network-zone eu-central --ip-range 10.0.0.0/24

# DB server (CX22: 2vCPU, 4GB RAM — sized for TimescaleDB)
hcloud server create --name marketlens-db --type cx22 --image ubuntu-24.04 \
  --network marketlens-net --ssh-key your-ssh-key

# App server (CX11: 2vCPU, 2GB RAM — FastAPI is lightweight)
hcloud server create --name marketlens-app --type cx11 --image ubuntu-24.04 \
  --network marketlens-net --ssh-key your-ssh-key

# Block storage volume for DB data (survives server destruction)
hcloud volume create --name marketlens-db-data --size 20 --server marketlens-db
```

---

## 2. DB Server setup

```bash
ssh root@<DB_SERVER_PUBLIC_IP>

# Mount the block storage volume (Hetzner auto-attaches at /dev/disk/by-id/...)
mkfs.ext4 -F /dev/disk/by-id/scsi-0HC_Volume_<volume-id>
mkdir -p /mnt/db-volume
mount /dev/disk/by-id/scsi-0HC_Volume_<volume-id> /mnt/db-volume
# Persist mount across reboots
echo "/dev/disk/by-id/scsi-0HC_Volume_<volume-id> /mnt/db-volume ext4 defaults 0 0" >> /etc/fstab

mkdir -p /mnt/db-volume/pgdata

# Install Docker
curl -fsSL https://get.docker.com | sh

# Clone your repo (or scp the compose file)
git clone <your-repo-url> /opt/marketlens
cd /opt/marketlens/marketlens-be

# Set up env
cp .env.db.example .env.db
nano .env.db   # fill in POSTGRES_PASSWORD, backup S3 credentials

# Start TimescaleDB + backup sidecar
docker compose -f docker-compose.db.yml --env-file .env.db up -d

# Verify
docker exec marketlens-timescaledb pg_isready -U marketlens -d marketlens
```

### Firewall on DB server
```bash
# Only allow port 5432 from the app server's PRIVATE IP
ufw allow ssh
ufw allow from 10.0.0.x to any port 5432   # replace x with app server's private IP
ufw enable
```

---

## 3. App Server setup

```bash
ssh root@<APP_SERVER_PUBLIC_IP>

# Install Docker
curl -fsSL https://get.docker.com | sh

# Clone repo
git clone <your-repo-url> /opt/marketlens
cd /opt/marketlens/marketlens-be

# Set up env
cp .env.app.example .env.app
nano .env.app
# Key values to set:
#   DATABASE_URL → use DB server's PRIVATE IP (10.0.0.x), not public IP
#   DOMAIN       → api.yourdomain.com
#   SSL_EMAIL    → your email for Let's Encrypt

# Update nginx config with your actual domain
sed -i 's/api.yourdomain.com/api.YOURDOMAIN.com/g' nginx/conf.d/marketlens.conf

# Get SSL certificate (nginx must be able to serve /.well-known on port 80 first)
# Step 1: Start nginx only (HTTP, no SSL yet) with a temporary plain config:
docker compose -f docker-compose.app.yml up -d nginx

# Step 2: Get cert
docker compose -f docker-compose.app.yml --env-file .env.app run --rm certbot

# Step 3: Start everything
docker compose -f docker-compose.app.yml --env-file .env.app up -d
```

### Run migrations + seed (first deploy only)
```bash
docker exec marketlens-api python -m alembic upgrade head
docker exec marketlens-api python -m scripts.seed_instruments
docker exec marketlens-api python -m scripts.backfill_prices --intervals 1d,1h,5m
```

---

## 4. Auto-restart on server reboot

Both compose files use `restart: always` — Docker automatically restarts containers
after a server reboot. No extra config needed.

To verify after a reboot:
```bash
docker ps   # all containers should show "Up X seconds"
```

---

## 5. Restoring from backup

If the DB server is destroyed and you need to restore:

```bash
# 1. Provision a new DB server + attach a new block volume (or reattach the old one if intact)
# 2. If block volume is intact: mount it and docker compose up — data is already there.
# 3. If restoring from pg_dump backup in object storage:

aws s3 cp s3://marketlens-backups/backups/marketlens_20260101_220000.sql.gz /tmp/
gunzip /tmp/marketlens_20260101_220000.sql.gz
docker exec -i marketlens-timescaledb psql -U marketlens -d marketlens \
  < /tmp/marketlens_20260101_220000.sql

# 4. Update app server's .env.app with the new DB server's private IP if it changed
# 5. Restart app: docker compose -f docker-compose.app.yml restart api
```

---

## 6. SSL cert renewal (add to crontab on app server)

```bash
# Run daily at 03:00 — certbot skips renewal if cert isn't due yet (< 30 days to expiry)
crontab -e
# Add:
0 3 * * * cd /opt/marketlens/marketlens-be && docker compose -f docker-compose.app.yml run --rm certbot && docker compose -f docker-compose.app.yml exec nginx nginx -s reload
```

---

## Data persistence layers (summary)

| Layer | Survives container restart | Survives server reboot | Survives server destruction |
|---|---|---|---|
| Docker volume (default) | ✅ | ✅ | ❌ |
| **Bind-mount to block storage** | ✅ | ✅ | ✅ (reattach volume) |
| **pg_dump to object storage** | ✅ | ✅ | ✅ (restore from dump) |
