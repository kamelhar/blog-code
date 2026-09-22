#!/usr/bin/env python3
"""Push the site's own settings into Ghost, idempotently.

Ghost ships a new site with its own description, cover image and social
accounts, and every one of them is what a link preview shows until it is
changed: "Thoughts, stories and ideas.", Ghost's stock publication cover,
@ghost. They are settings rather than theme, so no amount of theme work
replaces them -- and they are the same kind of fact as the Work list, true
for a year at a time, so they live here rather than in someone's memory of
which admin form they were typed into.

    GHOST_ADMIN_KEY=<id:secret> python3 scripts/publish-settings.py \
        --url http://127.0.0.1:30000 --host blog-admin.kamelhar.net

--dry-run needs no key and no tunnel. See publish-content.py for the tunnel.
"""
from __future__ import annotations

import argparse, base64, hashlib, hmac, json, mimetypes, os, time, urllib.error, urllib.request
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ghost_target

ROOT = Path(__file__).resolve().parent.parent

# The description is the slogan the theme prints above the rule, and the meta
# description and og:description Ghost writes into every page. One sentence,
# set in one place.
SETTINGS = {
    "description": "Building AI that works in production.",
    # Ghost derives og:image and twitter:image from the site cover when a post
    # has none of its own. The theme has shipped a card for this all along.
    "cover_image": ("@image", "theme/assets/img/social-card.webp"),
    # Ghost writes this into twitter:site and twitter:creator on every page,
    # so a card shared on X credits the account. Facebook, left behind by the
    # installer as Ghost's own, is cleared: an empty value drops the tag.
    "twitter": "@FedeKamelhar",
    "facebook": "",
    # Ghost appends ?ref=blog.kamelhar.net to every outbound link, so a
    # reader who copies one carries this site's name into wherever it goes,
    # and the sites it points at see a query string they did not ask for. The
    # links here are citations; they should read as the address they cite.
    "outbound_link_tagging": False,
}


def token(key: str) -> str:
    kid, secret = key.split(":")
    enc = lambda o: base64.urlsafe_b64encode(
        o if isinstance(o, bytes) else json.dumps(o, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    now = int(time.time())
    head, body = enc({"alg": "HS256", "typ": "JWT", "kid": kid}), enc(
        {"iat": now, "exp": now + 300, "aud": "/admin/"}
    )
    sig = enc(hmac.new(bytes.fromhex(secret), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


class Ghost:
    def __init__(self, url: str, host: str | None, key: str):
        self.url, self.host, self.key = url.rstrip("/"), host, key
        self.cookie: str | None = None
        self._target = None  # set by sign_in, so a retry can re-authenticate

    def sign_in(self, target) -> None:
        """Ghost refuses a settings write from an integration token, so this
        script holds a staff session instead.

        Ghost 6 verifies a new device: the first sign-in answers 403 with a
        6-digit code emailed to the owner, and the code is only good against the
        cookie that request set. So the partial cookie is kept, and --code
        completes it on the next run. Once verified the device is trusted and
        the cached session is enough, so this asks for a code roughly never."""
        self._target = target
        cache = Path.home() / ".cache" / f"ghost-session-{target.name}"
        cache.parent.mkdir(parents=True, exist_ok=True)

        if args_code := getattr(self, "_code", None):
            if not cache.exists():
                raise SystemExit("--code with no pending sign-in; run once without it first")
            self.cookie = cache.read_text().strip()
            self._call("/ghost/api/admin/session/verify/", {"token": args_code}, "PUT")
            cache.write_text(self.cookie); cache.chmod(0o600)
            print("  device verified")
            return

        if cache.exists():
            self.cookie = cache.read_text().strip()
            try:
                self._req("/ghost/api/admin/users/me/", retry=False)
                return
            except urllib.error.HTTPError:
                self.cookie = None

        email, password = target.owner()
        try:
            self._call("/ghost/api/admin/session/", {"username": email, "password": password}, "POST")
            cache.write_text(self.cookie or ""); cache.chmod(0o600)
        except urllib.error.HTTPError as e:
            err = json.loads(e.read()).get("errors", [{}])[0]
            if err.get("code") != "2FA_NEW_DEVICE_DETECTED":
                raise
            # The cookie the code is good against rides on the 403 itself, and
            # urlopen raises before _call can read it -- so take it from the
            # error. Caching an empty string here is why --code then 401s.
            got = (e.headers.get("Set-Cookie") or "").split(";")[0]
            if not got:
                raise SystemExit("Ghost asked for a code but set no session cookie")
            self.cookie = got
            cache.write_text(self.cookie); cache.chmod(0o600)
            raise SystemExit(
                f"Ghost emailed a 6-digit code to {email}. Re-run with it:\n"
                f"  python3 scripts/publish-settings.py --env {target.name} --code NNNNNN"
            )

    def _call(self, path: str, body: dict, method: str):
        """Like _req, but keeps whatever session cookie comes back."""
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(body).encode(), method=method)
        for k, v in self._headers().items():
            req.add_header(k, v)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as r:
            got = (r.headers.get("Set-Cookie") or "").split(";")[0]
            if got:
                self.cookie = got
            return r.status

    def _headers(self) -> dict[str, str]:
        # Once signed in the session is the credential, and the token has to go:
        # Ghost sees an Authorization header first and answers as if the session
        # were not there, which is the 403 this script started with.
        h = {"Accept-Version": "v5.0"}
        if self.cookie:
            h["Cookie"] = self.cookie
        else:
            h["Authorization"] = f"Ghost {token(self.key)}"
        if self.host:
            # Same three as publish-content.py: reaching the pod directly, the Host
            # header picks the surface and Ghost redirects a plain-http admin call
            # to its https admin__url unless the proto is declared.
            h["Host"] = self.host
            h["X-Forwarded-Proto"] = "https"
            h["Origin"] = f"https://{self.host}"
        return h

    def _req(self, path: str, data: bytes | None = None, method: str = "GET",
             ctype: str | None = None, retry: bool = True):
        """One authenticated call, retried through a 403 that means nothing.

        Ghost intermittently answers a request carrying a session it issued
        seconds earlier with 403 "Unable to determine the authenticated user",
        and the identical request with the identical cookie then succeeds.
        Measured against a live instance: three sign-ins followed by an
        immediate call gave 200, 200, 403, and the failing one succeeded on the
        next try without signing in again. So the session is valid and Ghost
        occasionally fails to resolve it.

        By hand this is invisible -- you re-run the script. In a Job that runs
        on every deploy it is a failed rollout, which is exactly what it was.
        Hence a few attempts with a growing pause, and one fresh sign-in before
        the last of them in case the session really has gone."""
        last: urllib.error.HTTPError | None = None
        for attempt in range(4 if retry else 1):
            req = urllib.request.Request(f"{self.url}{path}", data=data, method=method)
            for k, v in self._headers().items():
                req.add_header(k, v)
            if ctype:
                req.add_header("Content-Type", ctype)
            try:
                with urllib.request.urlopen(req) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if not retry or e.code != 403:
                    raise
                last = e
                # Third failure: the session may genuinely be gone rather than
                # unresolved, so get a new one before the final attempt.
                if attempt == 2 and self._target is not None:
                    print("  session not resolving; signing in again")
                    self.cookie = None
                    self.sign_in(self._target)
                else:
                    print(f"  403 on {path} (attempt {attempt + 1}), retrying")
                time.sleep(1.5 * (attempt + 1))
        assert last is not None
        raise last

    def settings(self) -> dict[str, object]:
        return {s["key"]: s["value"] for s in self._req("/ghost/api/admin/settings/")["settings"]}

    def put_settings(self, changes: dict[str, object]) -> None:
        body = json.dumps({"settings": [{"key": k, "value": v} for k, v in changes.items()]}).encode()
        self._req("/ghost/api/admin/settings/", body, "PUT", "application/json")

    def upload(self, path: Path) -> str:
        b = "----ghost" + os.urandom(8).hex()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = (
            f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode() + path.read_bytes() + f"\r\n--{b}--\r\n".encode()
        )
        return self._req("/ghost/api/admin/images/upload/", body, "POST", f"multipart/form-data; boundary={b}")["images"][0]["url"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("GHOST_ADMIN_URL", "https://blog-admin.kamelhar.net"))
    ap.add_argument("--host", default=os.getenv("GHOST_HOST_HEADER"))
    ghost_target.add_argument(ap)
    ap.add_argument("--code", help="the 6-digit code Ghost emails when it does not know this device")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url, host, key = ghost_target.resolve(args)
    g = Ghost(url, host, key)
    if not args.dry_run:
        target = ghost_target.TARGETS[args.env] if args.env else ghost_target.from_env()
        if target is None:
            raise SystemExit("settings need --env dev|prod, or GHOST_ADMIN_URL, GHOST_HOST_HEADER, "
                             "GHOST_OWNER_EMAIL and GHOST_OWNER_PASSWORD (the owner login differs per instance)")
        g._code = args.code
        g.sign_in(target)

    current = {} if args.dry_run else g.settings()
    changes: dict[str, object] = {}

    for k, want in SETTINGS.items():
        if isinstance(want, tuple) and want[0] == "@image":
            local = ROOT / want[1]
            if not local.exists():
                raise SystemExit(f"missing {local}")
            have = str(current.get(k) or "")
            # An upload lands under a fresh name every time, so a cover already
            # pointing at this file's stem is left exactly as it is.
            if local.stem in have:
                print(f"  {k}: already {have}")
                continue
            want = f"<uploaded:{local.name}>" if args.dry_run else g.upload(local)
        # Ghost reads a cleared setting back as null, not "", so comparing them
        # raw re-sends the same empty value on every run.
        same = (current.get(k) or "") == (want or "") if isinstance(want, str) else current.get(k) == want
        if same and not args.dry_run:
            print(f"  {k}: unchanged")
            continue
        changes[k] = want
        print(f"  {k}: {current.get(k)!r} -> {want!r}")

    if not changes:
        print("nothing to change")
        return 0
    if args.dry_run:
        print("dry run: nothing sent")
        return 0
    g.put_settings(changes)
    print(f"updated {len(changes)} setting(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
