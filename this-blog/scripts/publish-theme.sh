#!/usr/bin/env bash
# Package the theme and install it into Ghost.
#
# Run this from somewhere that can reach Ghost - GATEWAY-HOST, or a laptop on the
# tailnet. CI cannot: a GitHub runner has no route to blog-admin.kamelhar.net.
#
#   GHOST_ADMIN_KEY=<id:secret> ./scripts/publish-theme.sh
#
# The admin name is behind authentik, which answers an API call with a sign-in
# page rather than JSON, so in practice the API is reached on the NodePort from
# the tailnet -- the same hop publish-content.py documents:
#
#   ssh -f -N -L 30000:GHOST-NODE-IP:30000 GATEWAY-HOST
#   GHOST_ADMIN_KEY=<id:secret> ./scripts/publish-theme.sh \
#       --url http://127.0.0.1:30000 --host blog-admin.kamelhar.net
#
# --env dev|prod sets the url, the host header and which key to read, so the
# usual run is `. ~/.secrets/ghost.env` and `./scripts/publish-theme.sh --env dev`.
#
# The key comes from Ghost admin under Settings -> Integrations -> Add custom
# integration. Keep it in ~/.secrets/ghost.env, not in a shell history.
set -euo pipefail

URL="${GHOST_ADMIN_URL:-https://blog-admin.kamelhar.net}"
HOST_HEADER="${GHOST_HOST_HEADER:-}"
KEY_VAR="GHOST_ADMIN_KEY"
ENV_NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env)  ENV_NAME="$2";    shift 2 ;;
    --url)  URL="$2";         shift 2 ;;
    --host) HOST_HEADER="$2"; shift 2 ;;
    http*)  URL="$1";         shift   ;;  # the old positional form
    *) echo "usage: $0 [--env dev|prod] [--url URL] [--host HOST]" >&2; exit 2 ;;
  esac
done

# Two instances, two namespaces, two MySQLs -- so two owners and two admin keys.
# The difference is three values and a NodePort, and typing those by hand is how
# a dev run ends up at prod. This table is scripts/ghost_target.py's, in sh.
case "$ENV_NAME" in
  dev)  PORT=30001; HOST_HEADER="blog-admin-dev.kamelhar.net"; KEY_VAR="GHOST_ADMIN_KEY_DEV"; FWD="lab:30001" ;;
  prod) PORT=30000; HOST_HEADER="blog-admin.kamelhar.net";     KEY_VAR="GHOST_ADMIN_KEY";     FWD="GHOST-NODE-IP:30000" ;;
  "")   PORT="" ;;
  *) echo "--env takes dev or prod" >&2; exit 2 ;;
esac
if [ -n "$PORT" ]; then
  URL="http://127.0.0.1:$PORT"
  # Both admin names sit behind authentik, so the API is reached through GATEWAY-HOST.
  if ! (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
    echo "nothing is listening on $PORT, so $ENV_NAME is unreachable:" >&2
    echo "  ssh -f -N -L $PORT:$FWD GATEWAY-HOST" >&2
    exit 1
  fi
fi
GHOST_ADMIN_KEY="$(eval "printf '%s' \"\${$KEY_VAR:-}\"")"
export GHOST_ADMIN_KEY
URL="${URL%/}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${GHOST_ADMIN_KEY:?set $KEY_VAR=<id:secret> -- it is in ~/.secrets/ghost.env}"

echo "validating"
npx --yes gscan@latest "$ROOT/theme" --fatal

echo "packaging"
rm -f "$ROOT/measured.zip"
( cd "$ROOT/theme" && zip -qr ../measured.zip . -x '.*' )

echo "installing into $URL${HOST_HEADER:+ (as $HOST_HEADER)}"
TOKEN="$(python3 - <<'PY'
import base64, hashlib, hmac, json, os, time
kid, secret = os.environ["GHOST_ADMIN_KEY"].split(":")
enc = lambda o: base64.urlsafe_b64encode(
    o if isinstance(o, bytes) else json.dumps(o, separators=(",", ":")).encode()
).rstrip(b"=").decode()
now = int(time.time())
head = enc({"alg": "HS256", "typ": "JWT", "kid": kid})
body = enc({"iat": now, "exp": now + 300, "aud": "/admin/"})
sig = enc(hmac.new(bytes.fromhex(secret), f"{head}.{body}".encode(), hashlib.sha256).digest())
print(f"{head}.{body}.{sig}")
PY
)"

# Reaching the pod directly means the Host header decides which surface Ghost
# serves, and Ghost redirects a plain-http admin call to its https admin__url
# unless the proto is declared for it -- the same three headers publish-content.py
# sends. With no --host these are absent and the call goes straight at the name.
HDRS=(-H "Authorization: Ghost $TOKEN" -H "Accept-Version: v5.0")
if [ -n "$HOST_HEADER" ]; then
  HDRS+=(-H "Host: $HOST_HEADER" -H "X-Forwarded-Proto: https" -H "Origin: https://$HOST_HEADER")
fi

curl -fsS -X POST "$URL/ghost/api/admin/themes/upload/" \
  "${HDRS[@]}" \
  -F "file=@$ROOT/measured.zip;type=application/zip" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
if 'errors' in d:
    print('  failed:', d['errors'][0].get('message')); sys.exit(1)
t = d['themes'][0]
print('  installed:', t['name'], '| active:', t.get('active'))
for w in (t.get('warnings') or [])[:5]:
    print('  warning:', w.get('rule'))
"

curl -fsS -X PUT "$URL/ghost/api/admin/themes/measured/activate/" \
  "${HDRS[@]}" >/dev/null
echo "  activated"
