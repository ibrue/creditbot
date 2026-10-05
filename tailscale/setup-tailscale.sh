#!/usr/bin/env bash
#
# Bring Tailscale up on the UGREEN NAS (UGOS).
#
#   sudo ./tailscale/setup-tailscale.sh
#
# Safe to re-run: it re-checks the prerequisites and reconciles the container
# against docker-compose.yml, so it doubles as the "apply my .env edits" and
# "did this survive the reboot?" command.

set -euo pipefail

cd "$(dirname "$0")"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[31merror\033[0m %s\n' "$*" >&2; exit 1; }

bold "Checking prerequisites"

# --- docker ------------------------------------------------------------------
command -v docker >/dev/null 2>&1 ||
  die "docker not found. Install the Docker app from the UGOS App Center first."

if ! docker info >/dev/null 2>&1; then
  die "can't talk to the docker daemon. Re-run with sudo, or add your user to the docker group."
fi
ok "docker is running"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "neither 'docker compose' nor 'docker-compose' is available."
fi
ok "compose: ${COMPOSE[*]}"

# --- /dev/net/tun ------------------------------------------------------------
# Kernel-mode tailscaled needs a tun device. UGOS ships the module but doesn't
# always have it loaded on a fresh boot.
if [ ! -c /dev/net/tun ]; then
  warn "/dev/net/tun missing, trying to load the tun module"
  modprobe tun 2>/dev/null || true
  if [ ! -c /dev/net/tun ]; then
    mkdir -p /dev/net
    mknod /dev/net/tun c 10 200 2>/dev/null || true
    chmod 600 /dev/net/tun 2>/dev/null || true
  fi
  [ -c /dev/net/tun ] ||
    die "couldn't create /dev/net/tun. Your UGOS kernel may not have TUN support built in;
      fall back to the userspace/native option in TAILSCALE_SETUP.md."
fi
ok "/dev/net/tun present"

# Make the module load stick across reboots.
if [ -d /etc/modules-load.d ] && ! grep -qxs tun /etc/modules-load.d/tailscale.conf 2>/dev/null; then
  echo tun > /etc/modules-load.d/tailscale.conf 2>/dev/null &&
    ok "tun will load on boot (/etc/modules-load.d/tailscale.conf)" ||
    warn "couldn't persist the tun module load; re-run this script after a reboot"
fi

# --- .env --------------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  die ".env didn't exist, so I created tailscale/.env from the example.
      Put your auth key in it (TS_AUTHKEY=tskey-auth-...) and re-run.
      Generate one at https://login.tailscale.com/admin/settings/keys"
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a

case "${TS_AUTHKEY:-}" in
  ""|*REPLACE-ME*)
    die "TS_AUTHKEY is still the placeholder in tailscale/.env.
      Generate a key at https://login.tailscale.com/admin/settings/keys and put it there." ;;
esac
ok "auth key is set"

# --- IP forwarding, only if we're acting as a subnet router ------------------
if [ -n "${TS_ROUTES:-}" ]; then
  if [ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)" != "1" ]; then
    warn "TS_ROUTES is set but IP forwarding is off — enabling it"
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true
    printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
      > /etc/sysctl.d/99-tailscale.conf 2>/dev/null ||
      warn "couldn't persist the sysctl; it'll reset on reboot"
  fi
  ok "advertising routes: ${TS_ROUTES}"
fi

# --- start -------------------------------------------------------------------
echo
bold "Starting Tailscale"
"${COMPOSE[@]}" up -d

# --- wait for it to authenticate ---------------------------------------------
echo
printf 'Waiting for the NAS to join the tailnet'
TSIP=""
for _ in $(seq 1 60); do
  TSIP="$(docker exec tailscale tailscale ip -4 2>/dev/null | head -n1 || true)"
  [ -n "$TSIP" ] && break
  printf '.'
  sleep 2
done
echo

[ -n "$TSIP" ] || die "timed out waiting for a tailnet address. Logs:
      docker logs tailscale"

echo
bold "Tailscale is up"
docker exec tailscale tailscale status || true

echo
bold "This NAS"
echo "  tailnet IP    ${TSIP}"
echo "  creditbot API http://${TSIP}:8765/health"
echo
echo "Next:"
echo "  1. Open https://login.tailscale.com/admin/machines and disable key expiry"
echo "     on this node, so the NAS doesn't drop off the tailnet in ~6 months."
if [ -n "${TS_ROUTES:-}" ]; then
  echo "  2. Approve the advertised subnet route(s) on that same page"
  echo "     (... menu -> Edit route settings)."
fi
