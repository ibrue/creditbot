# Running Creditbot on a UGREEN NAS (UGOS)

This runs two containers on the NAS via Docker Compose:

- **creditbot** — the Discord bot
- **creditbot-api** — a small HTTP API on port `8765` that the
  facial-recognition kiosk uses to check people in/out (same database,
  same credit rules)

The SQLite database lives in a `data/` folder next to the compose file, so
it survives container updates.

## 1. Enable Docker on the NAS

1. Open UGOS in your browser (`https://<nas-ip>:9443` or the UGREEN app).
2. Open the **App Center** and install **Docker** (UGREEN's Docker app).
3. Optional but handy: enable **SSH** under Control Panel → Terminal so you
   can run commands directly on the NAS.

## 2. Put the project on the NAS

**Option A — SSH (recommended):**

```bash
ssh <your-user>@<nas-ip>
cd /volume1/docker        # or wherever you keep container projects
git clone https://github.com/ibrue/creditbot.git
cd creditbot
```

**Option B — File app:** download this repo as a ZIP, upload it with the
UGOS Files app to a shared folder (e.g. `docker/creditbot`), and use that
path below.

## 3. Configure

```bash
cp .env.example .env
nano .env
```

Fill in at least:

- `DISCORD_TOKEN` — your bot token
- `CHECKIN_CHANNEL_ID` / `ANNOUNCEMENTS_CHANNEL_ID`
- `KIOSK_API_KEY` — a long random string for the kiosk
  (`python3 -c "import secrets; print(secrets.token_hex(32))"` or just
  mash the keyboard — it only has to match the kiosk's `.env`)
- `TZ` — your timezone (night-owl/weekend bonuses use local time),
  e.g. `America/Chicago`

**Already have a database?** Copy your existing `social_credit.db` into a
`data/` folder in the project: `mkdir -p data && cp /path/to/social_credit.db data/`.
Otherwise a fresh one is created automatically.

## 4. Build and start

```bash
docker compose up -d --build
```

Check that both containers are happy:

```bash
docker compose logs -f          # Ctrl+C to stop watching
curl http://localhost:8765/health   # -> {"status":"ok"}
```

You can also see and manage both containers in UGOS's Docker app
(they'll appear under Containers as `creditbot` and `creditbot-api`).

## 5. Updating later

```bash
cd /volume1/docker/creditbot
git pull
docker compose up -d --build
```

The database in `data/` is untouched by updates.

## 6. Kiosk connectivity

The kiosk machine talks to `http://<nas-ip>:8765`. Give the NAS a static
IP (or DHCP reservation) on your router so the address doesn't change.
Keep port 8765 LAN-only — don't port-forward it on your router.

Next: set up the kiosk itself → [kiosk/README.md](kiosk/README.md)
