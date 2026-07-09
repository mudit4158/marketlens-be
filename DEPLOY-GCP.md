# MarketLens — GCP Deployment Guide

## Architecture

```
[ Your Local Machine ]
  DBeaver → SSH tunnel → marketlens-db:5432
  Browser → https://api.yourdomain.com

[ GCP VPC (asia-south1 / Mumbai) ]
  ├── marketlens-db  (e2-small, 2GB RAM)
  │     TimescaleDB in Docker
  │     20GB Persistent SSD disk → /mnt/disks/pgdata
  │     Internal IP: 10.128.x.x
  │
  └── marketlens-app  (e2-micro, 1GB RAM)
        FastAPI + nginx + SSL
        DATABASE_URL → 10.128.x.x:5432 (private VPC)
        Public IP → api.yourdomain.com
```

DB and app talk over GCP's internal VPC (free, low-latency, no public exposure).
You access the DB from local via SSH tunnel — no port 5432 exposed to the internet.

---

## Prerequisites

- GCP account with billing enabled
- `gcloud` CLI installed locally (`gcloud auth login` done)
- A domain name with DNS you can edit
- Your repo cloned on both VMs (or files copied via scp)

Install gcloud CLI: https://cloud.google.com/sdk/docs/install

---

## Step 1 — GCP Project & API setup

```bash
# Set your project (replace with your project ID)
gcloud config set project YOUR_PROJECT_ID

# Enable required APIs
gcloud services enable compute.googleapis.com
```

---

## Step 2 — Create the VPC firewall rules

```bash
# Allow SSH from anywhere (needed for local tunnel access)
gcloud compute firewall-rules create allow-ssh \
  --allow tcp:22 \
  --source-ranges 0.0.0.0/0 \
  --description "SSH access for all"

# Allow HTTP + HTTPS on app VM (for nginx/certbot)
gcloud compute firewall-rules create allow-http-https \
  --allow tcp:80,tcp:443 \
  --source-ranges 0.0.0.0/0 \
  --target-tags marketlens-app \
  --description "Public web traffic"

# Allow port 5432 only within the VPC internal network (app VM → DB VM)
gcloud compute firewall-rules create allow-postgres-internal \
  --allow tcp:5432 \
  --source-ranges 10.128.0.0/9 \
  --target-tags marketlens-db \
  --description "Postgres access within VPC only"
```

---

## Step 3 — Create the DB VM + Persistent Disk

```bash
# Create the persistent disk (survives VM deletion)
gcloud compute disks create marketlens-db-disk \
  --size 20GB \
  --type pd-ssd \
  --zone asia-south1-a

# Create the DB VM
gcloud compute instances create marketlens-db \
  --zone asia-south1-a \
  --machine-type e2-small \
  --image-family ubuntu-2404-lts-amd64 \
  --image-project ubuntu-os-cloud \
  --disk name=marketlens-db-disk,auto-delete=no \
  --tags marketlens-db \
  --boot-disk-size 20GB \
  --boot-disk-type pd-standard
```

> `--disk auto-delete=no` ensures the data disk is NOT deleted when the VM is deleted.

---

## Step 4 — Set up the DB VM

```bash
# SSH into the DB VM
gcloud compute ssh marketlens-db --zone asia-south1-a
```

Inside the DB VM:

```bash
# ── Install Docker ────────────────────────────────────────────────────────────
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# ── Mount the persistent disk ─────────────────────────────────────────────────
# Find the disk device name (usually /dev/sdb)
lsblk

# Format the disk (ONLY on first setup — skip if restoring existing data)
sudo mkfs.ext4 -F /dev/sdb

# Mount it
sudo mkdir -p /mnt/disks/pgdata
sudo mount /dev/sdb /mnt/disks/pgdata

# Make mount persist across reboots
echo UUID=$(sudo blkid -s UUID -o value /dev/sdb) /mnt/disks/pgdata ext4 defaults,nofail 0 2 | sudo tee -a /etc/fstab

# Create the postgres data directory
sudo mkdir -p /mnt/disks/pgdata
sudo chmod 777 /mnt/disks/pgdata

# ── Clone the repo ────────────────────────────────────────────────────────────
git clone https://github.com/mudit4158/marketlens-be.git /opt/marketlens
cd /opt/marketlens

# ── Set up environment ────────────────────────────────────────────────────────
cp .env.db.example .env.db
nano .env.db
# Fill in:
#   POSTGRES_PASSWORD=<strong password>
#   BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY, BACKUP_S3_BUCKET (GCS HMAC keys)

# ── Start TimescaleDB ─────────────────────────────────────────────────────────
docker compose -f docker-compose.db.yml --env-file .env.db up -d

# Verify it's healthy
docker ps
docker exec marketlens-timescaledb pg_isready -U marketlens -d marketlens
```

Note the DB VM's **internal IP** (shown in GCP Console or via `hostname -I`). You'll use it in the app VM's `DATABASE_URL`.

---

## Step 5 — Run migrations + seed + backfill (from DB VM)

```bash
# Still on the DB VM
cd /opt/marketlens

# Run migrations
docker run --rm \
  --env-file .env.db \
  -e DATABASE_URL=postgresql+psycopg2://marketlens:$(grep POSTGRES_PASSWORD .env.db | cut -d= -f2)@timescaledb:5432/marketlens \
  --network host \
  $(docker build -q .) \
  python -m alembic upgrade head

# Easier: start a temporary api container linked to the db network
docker compose -f docker-compose.db.yml --env-file .env.db exec timescaledb bash
# Inside: psql -U marketlens -d marketlens -c "\dt"  (verify tables after running from app VM)
```

> Tip: It's simpler to run migrations + seed from the **app VM** once it's connected to the DB. See Step 7.

---

## Step 6 — Create the App VM

```bash
# Back on your LOCAL machine
gcloud compute instances create marketlens-app \
  --zone asia-south1-a \
  --machine-type e2-micro \
  --image-family ubuntu-2404-lts-amd64 \
  --image-project ubuntu-os-cloud \
  --tags marketlens-app \
  --boot-disk-size 20GB \
  --boot-disk-type pd-standard
```

Get the app VM's **external IP**:
```bash
gcloud compute instances describe marketlens-app \
  --zone asia-south1-a \
  --format "get(networkInterfaces[0].accessConfigs[0].natIP)"
```

Point your domain's **A record** to this IP before continuing (certbot needs it).

---

## Step 7 — Set up the App VM

```bash
# SSH into the app VM
gcloud compute ssh marketlens-app --zone asia-south1-a
```

Inside the app VM:

```bash
# ── Install Docker ────────────────────────────────────────────────────────────
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# ── Clone the repo ────────────────────────────────────────────────────────────
git clone https://github.com/mudit4158/marketlens-be.git /opt/marketlens
cd /opt/marketlens

# ── Set up environment ────────────────────────────────────────────────────────
cp .env.app.example .env.app
nano .env.app
# Key values:
#   DATABASE_URL=postgresql+psycopg2://marketlens:<password>@<DB-INTERNAL-IP>:5432/marketlens
#   DOMAIN=api.yourdomain.com
#   SSL_EMAIL=your@email.com
#   CORS_ALLOWED_ORIGINS=https://yourdomain.com

# Update nginx config with your actual domain
sed -i 's/api.yourdomain.com/api.YOURDOMAIN.com/g' nginx/conf.d/marketlens.conf

# ── Get SSL certificate (Step 1: start nginx on HTTP only) ───────────────────
# Temporarily use a plain HTTP nginx config for certbot webroot challenge
docker compose -f docker-compose.app.yml up -d nginx

# ── Get SSL certificate (Step 2: run certbot) ────────────────────────────────
docker compose -f docker-compose.app.yml --env-file .env.app run --rm certbot

# ── Start everything ─────────────────────────────────────────────────────────
docker compose -f docker-compose.app.yml --env-file .env.app up -d

# ── Run migrations + seed + backfill (first deploy only) ─────────────────────
docker exec marketlens-api python -m alembic upgrade head
docker exec marketlens-api python -m scripts.seed_instruments
docker exec marketlens-api python -m scripts.backfill_prices --intervals 1d
# Backfill intraday after daily is done (takes longer)
docker exec marketlens-api python -m scripts.backfill_prices --intervals 1h,5m

# ── Verify ────────────────────────────────────────────────────────────────────
curl https://api.yourdomain.com/health
```

---

## Step 8 — SSL cert auto-renewal

Add a cron job on the app VM:

```bash
crontab -e
# Add this line (runs daily at 03:00):
0 3 * * * cd /opt/marketlens && docker compose -f docker-compose.app.yml --env-file .env.app run --rm certbot && docker compose -f docker-compose.app.yml exec nginx nginx -s reload
```

---

## Step 9 — Access DB from your local machine (DBeaver)

Use an SSH tunnel — no port 5432 is ever exposed to the public internet.

```bash
# Run this on your LOCAL machine (keep it running while using DBeaver)
gcloud compute ssh marketlens-db --zone asia-south1-a -- -L 5433:localhost:5432 -N

# Or with standard SSH (once you have the external IP):
ssh -i ~/.ssh/google_compute_engine -L 5433:localhost:5432 \
  <your-gcp-username>@<DB-VM-EXTERNAL-IP> -N
```

Then in **DBeaver**:
- Host: `localhost`
- Port: `5433`
- Database: `marketlens`
- Username: `marketlens`
- Password: (from your `.env.db`)

> The tunnel forwards your local port 5433 → DB VM port 5432. DBeaver connects to localhost:5433 as if it were the remote DB.

---

## Updating the app (future deploys)

```bash
# On the app VM
cd /opt/marketlens
git pull origin main
docker compose -f docker-compose.app.yml --env-file .env.app up -d --build api
```

---

## Cost estimate (asia-south1 / Mumbai)

| Resource | Spec | Cost/mo |
|---|---|---|
| marketlens-db | e2-small (2vCPU, 2GB) | ~$7 |
| marketlens-app | e2-micro (2vCPU, 1GB) | ~$3.50 |
| DB Persistent SSD | 20GB pd-ssd | ~$3.40 |
| App boot disk | 20GB pd-standard | ~$0.80 |
| Egress (outbound) | ~10GB/mo | ~$1 |
| **Total** | | **~$15–16/mo** |

> e2-micro has a free tier (us-central1/us-east1/us-west1 only). For Mumbai (asia-south1) there is no free tier.

---

## Data persistence summary

| What | Survives container restart | Survives VM restart | Survives VM deletion |
|---|---|---|---|
| GCP Persistent Disk (pgdata) | ✅ | ✅ | ✅ (disk preserved separately) |
| Daily pg_dump → GCS | ✅ | ✅ | ✅ |
| Docker volume (default) | ✅ | ✅ | ❌ |
