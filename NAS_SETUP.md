# Running Creditbot on a UGREEN NAS (UGOS)

This runs two containers on the NAS via Docker Compose:

- **creditbot** — the Discord bot
- **creditbot-api** — a small HTTP API on port `8765` that the
  facial-recognition kiosk uses to check people in/out (same database,
  same credit rules)

The SQLite database lives in a `data/` folder next to the compose file, so
it survives container updates.

## What you get

Two containers, plus a website:

- **creditbot** — the Discord bot
- **creditbot-api** — HTTP on port `8765`, serving both the kiosk API and
  the **web client** at `http://<nas-ip>:8765/app`

The web client is a check-in page any computer or phone can open — sign in
with the shared lab password, pick your name, and check in or out. Same
database, same credit rules as Discord and the kiosk.

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

To reach the NAS (and the API) from outside the lab without port-forwarding,
put it on a Tailscale tailnet → [TAILSCALE_SETUP.md](TAILSCALE_SETUP.md).

Next: set up the kiosk itself → [kiosk/README.md](kiosk/README.md)


## The web client

Once the containers are up, open **`http://<nas-ip>:8765/app`** from any
computer or phone on the network.

Set `WEB_PASSWORD` in `.env` first — until you do, the page loads but every
sign-in is refused. Pick something long:

```bash
python -c "import secrets; print(secrets.token_urlsafe(18))"
```

| Setting | Default | What it does |
|---|---|---|
| `WEB_PASSWORD` | *(unset)* | The shared password. Unset = nobody can sign in. |
| `WEB_ENABLED` | `1` | `0` turns the web client off; the kiosk API keeps working. |
| `WEB_HTTPS` | `0` | `1` when reached over HTTPS, so cookies are marked Secure. |
| `WEB_TRUST_PROXY` | `0` | `1` only if a reverse proxy you control sets `X-Forwarded-For`. Left at `0`, the header is ignored so nobody can reset their own rate limit. |
| `WEB_SECRET` | *(generated)* | Signing key for session cookies. Generated and kept beside the database if unset. |

**One shared password means one shared identity.** Anyone who knows it can
check *any* member in or out — there is nothing stopping someone checking
in a friend who isn't there. That is fine for a trusting lab; it is not a
security boundary. Change it when someone leaves the team, and keep the
kiosk for anything you want tied to a real face.

Sessions last 12 hours, cookies are signed and `HttpOnly`, and repeated
wrong passwords are rate-limited to 8 attempts per 15 minutes per client.

### Reaching it from outside the lab

**Use Tailscale — do not port-forward 8765.** A raw port-forward puts the
lab's credit system on the public internet behind one shared password,
where it will be found by scanners within hours.

1. Install Tailscale on the NAS → [TAILSCALE_SETUP.md](TAILSCALE_SETUP.md).
2. Install it on the phones and laptops that should reach the site.
3. Open `http://<nas-tailscale-name>:8765/app` from anywhere.

Everything then rides Tailscale's encrypted private network — the port is
never exposed publicly, and only devices you have added can reach it.

If you later put it behind an HTTPS reverse proxy (Tailscale Serve, Caddy,
Cloudflare Tunnel), set `WEB_HTTPS=1` so session cookies are marked Secure.
