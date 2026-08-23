# Tailscale on the UGREEN NAS

Puts the NAS on your [Tailscale](https://tailscale.com) tailnet, so you can
reach it — SSH, UGOS, and the creditbot API on `:8765` — from anywhere,
without port-forwarding anything on the lab router.

Worth doing because the alternative is worse: `NAS_SETUP.md` tells you to keep
port 8765 LAN-only, which is correct, but it also means the kiosk has to live
on the same network as the NAS and you can't check on the bot from home. A
tailnet fixes both without opening a hole in the firewall.

Tailscale runs as its own container in **host networking** mode, so the whole
NAS joins the tailnet — not just the container. Nothing about the bot changes.

## 1. Enable Docker and SSH on the NAS

Both are in UGOS:

- **App Center → Docker** — install it if you haven't already (you have, if
  creditbot is running).
- **Control Panel → Terminal → enable SSH** — you need a shell for step 3.

## 2. Get an auth key

1. Go to <https://login.tailscale.com/admin/settings/keys>.
2. **Generate auth key**. Tick **Reusable** so you can rebuild the container
   later without minting a new one. 90-day expiry is fine — the key is only
   used for the first login.
3. Copy it (`tskey-auth-...`). It's a credential; don't paste it into a commit
   or a Discord channel.

## 3. Run the setup script

SSH into the NAS as your admin user:

```bash
ssh <your-user>@<nas-ip>
cd /volume1/docker/creditbot     # wherever you cloned it
git pull
```

Drop the key into a local env file and run the script:

```bash
cp tailscale/.env.example tailscale/.env
nano tailscale/.env              # set TS_AUTHKEY, and --hostname if you like
sudo ./tailscale/setup-tailscale.sh
```

The script checks for Docker, makes sure `/dev/net/tun` exists (and loads the
`tun` module if UGOS hasn't), starts the container, waits for the NAS to
authenticate, and prints the tailnet IP. It's safe to re-run — that's also how
you apply later `.env` edits.

If `tailscale/.env` doesn't exist yet, the script creates it from the example
and stops so you can fill it in.

## 4. Turn off key expiry

By default a node's key expires in ~6 months and the NAS silently drops off
the tailnet. For a machine that's meant to always be reachable, you don't want
that:

1. <https://login.tailscale.com/admin/machines>
2. Find the NAS (`creditbot-nas` unless you renamed it) → `...` → **Disable
   key expiry**.

The script reminds you at the end. It's the one manual step that actually
matters later.

## 5. Point the kiosk at the tailnet

Once the NAS is on the tailnet, install Tailscale on the kiosk machine too
(same tailnet), then change the kiosk's `.env`:

```diff
-API_URL=http://192.168.1.50:8765
+API_URL=http://creditbot-nas:8765
```

That MagicDNS name follows the NAS around — no more breakage when the DHCP
lease changes or you move the kiosk to another network.

This is the remote counterpart to UGOS's own **Control Panel → Network →
LAN → Customize domain name**, which gives the NAS a `dxp2800-xxxx.local`
name. That one is mDNS: it only resolves for devices on the same LAN
segment, so it won't help the kiosk from another building or you from home.
MagicDNS resolves anywhere on the tailnet. Both can coexist — keep using
`.local:9443` for UGOS on the lab network.

Verify from the kiosk:

```bash
curl http://creditbot-nas:8765/health   # -> {"status":"ok"}
```

If MagicDNS isn't enabled on your tailnet, use the `100.x.y.z` address the
script printed instead.

## Optional: reach the rest of the lab LAN

To get at *other* machines on the lab network (the router, a printer, the
kiosk before it has Tailscale) through the NAS, make it a subnet router. Set
the LAN subnet in `tailscale/.env`:

```bash
TS_ROUTES=192.168.1.0/24
```

Re-run `sudo ./tailscale/setup-tailscale.sh` — it enables IP forwarding for
you — then approve the route in the admin console under the machine's
`...` → **Edit route settings**. Routes do nothing until they're approved.

## If the container approach doesn't work

UGOS is Debian-based, so the native install is a fallback:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=creditbot-nas
```

It's simpler, but a UGOS firmware update can wipe packages installed outside
the App Center, and you'd have to redo it. The container survives that, which
is why it's the default here.

## Troubleshooting

**`couldn't create /dev/net/tun`** — your UGOS kernel may lack TUN support.
Use the native install above, or run the container in userspace mode
(`TS_USERSPACE=true` in `docker-compose.yml`) — but note that userspace mode
only proxies traffic *out* of the container, so the NAS's own ports won't
answer over the tailnet.

**Container restarts in a loop** — almost always a bad or already-consumed
auth key:

```bash
docker logs tailscale
```

Mint a fresh reusable key, update `tailscale/.env`, then:

```bash
docker compose -f tailscale/docker-compose.yml up -d --force-recreate
```

**NAS shows up as a duplicate machine after a restart** — the state volume
isn't persisting. Check that `tailscale/state/` exists next to the compose
file and isn't empty.

**Can't reach `:8765` over the tailnet but the NAS pings** — check the API
container is actually up (`docker compose ps` in the repo root) and that it's
bound to `0.0.0.0`, not `127.0.0.1`. The compose file in this repo already
does the right thing.

## Updating Tailscale later

```bash
cd /volume1/docker/creditbot/tailscale
docker compose pull && docker compose up -d
```

The node identity in `state/` is untouched, so the NAS keeps the same tailnet
IP and hostname.
