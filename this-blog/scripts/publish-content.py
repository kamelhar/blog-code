#!/usr/bin/env python3
"""Push content/*.md into Ghost, idempotently.

The Markdown in content/ is the source; Ghost's copy in MySQL is a rendering of
it. This script makes that true in practice rather than in principle: run it and
Ghost matches the repository. A file already in Ghost is updated in place -- the
slug is the identity, so re-running changes nothing that has not changed.

    GHOST_ADMIN_KEY=<id:secret> python3 scripts/publish-content.py [--dry-run]

Reaching Ghost. The admin name sits behind authentik, which answers an API call
with a sign-in page rather than JSON, so the API is reached on the NodePort from
somewhere on the tailnet:

    ssh -f -N -L 30000:GHOST-NODE-IP:30000 GATEWAY-HOST
    GHOST_ADMIN_KEY=... python3 scripts/publish-content.py \
        --url http://127.0.0.1:30000 --host blog-admin.kamelhar.net

Front matter it reads:
    title, subtitle, date, tags, slug, type (post|page), cover, card, thumb

`cover` becomes the post's feature image, `card` its og:image. Both, and every
/static/img/... in the body, are uploaded to Ghost from theme/assets/img/ and
rewritten to the URL Ghost gives back, so a post does not depend on the theme
that happened to be installed when it was published.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ghost_target

import markdown
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT, IMG = ROOT / "content", ROOT / "theme" / "assets" / "img"
STATIC_RE = re.compile(r"/static/img/([A-Za-z0-9_.-]+)")


# ---------------------------------------------------------------- transport
def token(key: str) -> str:
    kid, secret = key.split(":")
    enc = lambda o: base64.urlsafe_b64encode(  # noqa: E731
        o if isinstance(o, bytes) else json.dumps(o, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    now = int(time.time())
    head = enc({"alg": "HS256", "typ": "JWT", "kid": kid})
    body = enc({"iat": now, "exp": now + 300, "aud": "/admin/"})
    sig = enc(hmac.new(bytes.fromhex(secret), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


class Ghost:
    def __init__(self, url: str, host: str | None, key: str):
        self.url, self.host, self.key = url.rstrip("/"), host, key

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Ghost {token(self.key)}", "Accept-Version": "v5.0"}
        if self.host:
            # The pod decides which surface to serve from the Host header, and refuses
            # to treat a plain-http hop as insecure when the proto is declared.
            h["Host"] = self.host
            h["X-Forwarded-Proto"] = "https"
            h["Origin"] = f"https://{self.host}"
        return h

    def call(self, method: str, path: str, payload: dict | None = None, allow_404: bool = False) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method)
        for k, v in self._headers().items():
            req.add_header(k, v)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 404 and allow_404:
                return {}
            body = e.read().decode(errors="replace")
            try:
                msg = json.loads(body)["errors"][0].get("message", body)
            except Exception:
                msg = body[:300]
            raise SystemExit(f"{method} {path} -> {e.code}: {msg}")

    def upload(self, path: Path) -> str:
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="ref"\r\n\r\n',
            path.name.encode(),
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        req = urllib.request.Request(f"{self.url}/ghost/api/admin/images/upload/", data=body, method="POST")
        for k, v in self._headers().items():
            req.add_header(k, v)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())["images"][0]["url"]
        except urllib.error.HTTPError as e:
            raise SystemExit(f"upload {path.name} -> {e.code}: {e.read().decode(errors='replace')[:200]}")


# ---------------------------------------------------------------- content
def parse(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}, raw
    _, fm, body = raw.split("---", 2)
    return yaml.safe_load(fm) or {}, body.lstrip("\n")


def render(body: str) -> str:
    return markdown.markdown(
        body,
        # toc gives every heading an id. Nothing renders a sidebar from it; it is
        # there so an in-page contents list has something to link to, which a long
        # article needs and a short one simply does not use.
        extensions=["extra", "sane_lists", "admonition", "toc"],
        output_format="html",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("GHOST_ADMIN_URL", "https://blog-admin.kamelhar.net"))
    ap.add_argument("--host", default=os.getenv("GHOST_HOST_HEADER"))
    ghost_target.add_argument(ap)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("files", nargs="*", help="default: every .md in content/")
    args = ap.parse_args()

    url, host, key = ghost_target.resolve(args)
    g = Ghost(url, host, key)

    files = [Path(f) for f in args.files] or sorted(CONTENT.glob("*.md"))
    uploaded: dict[str, str] = {}

    # Ghost stores an upload under a fresh name every time -- a second run of the same
    # file lands as name-1.webp -- so uploading unconditionally is not idempotent, it is
    # a slow leak of near-identical images. Whatever this post already references is
    # therefore read back first and reused, keyed by the stem Ghost derived from our
    # filename (`d2_system.webp` -> `d2_system-1.webp` -> stem `d2_system`).
    GHOST_IMG = re.compile(r"(?:https?://[^\s\"')]+)?/content/images/[^\s\"')]+")

    def stem_of(url: str) -> str:
        base = url.rsplit("/", 1)[-1].split("?")[0]
        base = base.rsplit(".", 1)[0]
        return re.sub(r"-\d+$", "", base)

    def learn(*blobs: object) -> None:
        for blob in blobs:
            for url in GHOST_IMG.findall(str(blob or "")):
                # a size-transformed URL (/size/w1200/) is a derivative, not the original
                if "/content/images/size/" in url:
                    continue
                uploaded.setdefault(stem_of(url) + ".webp", url)
                uploaded.setdefault(stem_of(url) + ".png", url)

    def image_url(name: str) -> str:
        if name not in uploaded:
            src = IMG / name
            if not src.is_file():
                print(f"  ! missing image {name}", file=sys.stderr)
                return f"/static/img/{name}"
            uploaded[name] = f"<uploaded:{name}>" if args.dry_run else g.upload(src)
            print(f"  uploaded {name} -> {uploaded[name]}")
        return uploaded[name]

    for path in files:
        meta, body = parse(path)
        slug = meta.get("slug") or path.stem
        kind = "pages" if meta.get("type") == "page" else "posts"
        print(f"{path.name} -> {kind[:-1]} '{slug}'")

        existing = None
        if not args.dry_run:
            # `fields` filters the response, so the images have to be asked for by name:
            # without feature_image and og_image here they come back absent and get
            # re-uploaded on every run, which is the leak this is meant to stop.
            found = g.call("GET", f"/ghost/api/admin/{kind}/slug/{slug}/"
                                  f"?fields=id,updated_at,feature_image,og_image"
                                  f"&formats=html", allow_404=True)
            existing = (found.get(kind) or [None])[0]
            if existing:
                learn(existing.get("html"), existing.get("feature_image"), existing.get("og_image"))

        html = STATIC_RE.sub(lambda m: image_url(m.group(1)), render(body))
        payload: dict[str, object] = {
            "title": meta.get("title") or slug,
            "slug": slug,
            "html": html,
            "status": "published",
        }
        if meta.get("subtitle"):
            payload["custom_excerpt"] = meta["subtitle"]
        if meta.get("date"):
            payload["published_at"] = f"{meta['date']}T12:00:00.000Z"
        if meta.get("cover"):
            payload["feature_image"] = image_url(STATIC_RE.search(meta["cover"]).group(1))
        if meta.get("card"):
            payload["og_image"] = payload["twitter_image"] = image_url(
                STATIC_RE.search(meta["card"]).group(1)
            )
        # A page's `portrait` is a picture of whoever the page is about. Ghost has no
        # such field, and its feature image is the nearest true thing: page.hbs renders
        # a page's feature image as a 104px portrait rather than a masthead.
        if kind == "pages" and meta.get("portrait"):
            payload["feature_image"] = image_url(STATIC_RE.search(meta["portrait"]).group(1))
            payload["feature_image_alt"] = meta.get("title") or slug
        if meta.get("cover_alt"):
            payload["feature_image_alt"] = meta["cover_alt"][:190]
        if kind == "posts" and meta.get("tags"):
            payload["tags"] = [{"name": t} for t in meta["tags"]]

        if args.dry_run:
            print(f"  would publish: {json.dumps({k: v for k, v in payload.items() if k != 'html'})}")
            print(f"  html: {len(html)} bytes")
            continue

        if existing:
            payload["updated_at"] = existing["updated_at"]  # Ghost's collision check
            out = g.call("PUT", f"/ghost/api/admin/{kind}/{existing['id']}/?source=html", {kind: [payload]})
            print(f"  updated {out[kind][0]['url']}")
        else:
            out = g.call("POST", f"/ghost/api/admin/{kind}/?source=html", {kind: [payload]})
            print(f"  created {out[kind][0]['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
