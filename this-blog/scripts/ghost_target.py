"""Which Ghost a publish script is pointed at.

Two instances, two namespaces, two MySQLs -- so two owners and two admin keys.
The difference between them is three values and a NodePort, and typing those by
hand is how a dev run ends up at prod. Naming the target instead:

    --env dev      the lab copy, tailnet only, drafts
    --env prod     blog.kamelhar.net

Both are reached through an ssh tunnel to GATEWAY-HOST (the admin names sit behind
authentik, which answers an API call with a sign-in page rather than JSON), so
each target knows the port it expects and says how to open it when it is shut.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    name: str
    port: int
    host: str
    key_var: str
    forward: str
    # Ghost refuses settings writes from an integration token ("API tokens do not
    # have permission to access this endpoint"), so anything that edits a setting
    # signs in as the owner instead. Posts and images are fine with the key.
    pw_var: str

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def tunnel(self) -> str:
        return f"ssh -f -N -L {self.port}:{self.forward} GATEWAY-HOST"

    def key(self) -> str:
        k = os.getenv(self.key_var)
        if not k:
            raise SystemExit(
                f"{self.key_var} is not set -- it is in ~/.secrets/ghost.env.\n"
                f"  set -a; . ~/.secrets/ghost.env; set +a"
            )
        return k

    def owner(self) -> tuple[str, str]:
        email, pw = os.getenv("GHOST_OWNER_EMAIL"), os.getenv(self.pw_var)
        if not (email and pw):
            raise SystemExit(
                f"settings need the owner login: GHOST_OWNER_EMAIL and {self.pw_var}\n"
                f"  set -a; . ~/.secrets/ghost.env; set +a"
            )
        return email, pw

    def require_tunnel(self) -> None:
        with socket.socket() as s:
            s.settimeout(2)
            if s.connect_ex(("127.0.0.1", self.port)) != 0:
                raise SystemExit(
                    f"nothing is listening on {self.port}, so {self.name} is unreachable:\n"
                    f"  {self.tunnel}"
                )


TARGETS = {
    "dev": Target("dev", 30001, "blog-admin-dev.kamelhar.net",
                  "GHOST_ADMIN_KEY_DEV", "lab:30001", "GHOST_OWNER_PASSWORD_DEV"),
    "prod": Target("prod", 30000, "blog-admin.kamelhar.net",
                   "GHOST_ADMIN_KEY", "GHOST-NODE-IP:30000", "GHOST_OWNER_PASSWORD"),
}


def from_env():
    """The Job in the cluster names no --env: it is pointed at the Ghost beside it
    by GHOST_ADMIN_URL and GHOST_HOST_HEADER, and signs in with the owner login
    from the namespace's blog-publish secret. -> Target, or None if unset."""
    url, host = os.getenv("GHOST_ADMIN_URL"), os.getenv("GHOST_HOST_HEADER")
    if not (url and host and os.getenv("GHOST_OWNER_EMAIL") and os.getenv("GHOST_OWNER_PASSWORD")):
        return None
    return Target("env", 0, host, "GHOST_ADMIN_KEY", "", "GHOST_OWNER_PASSWORD")


def add_argument(ap) -> None:
    ap.add_argument("--env", choices=sorted(TARGETS), help="dev or prod (sets --url, --host and the key)")


def resolve(args):
    """-> (url, host, key). --env fills all three; --url/--host still work alone."""
    if args.env:
        t = TARGETS[args.env]
        if getattr(args, "dry_run", False):
            return t.url, t.host, os.getenv(t.key_var, "x:00")
        t.require_tunnel()
        return t.url, t.host, t.key()
    key = os.getenv("GHOST_ADMIN_KEY")
    if not key and not getattr(args, "dry_run", False):
        raise SystemExit("set --env dev|prod, or GHOST_ADMIN_KEY=<id:secret>")
    return args.url, args.host, key or "x:00"
