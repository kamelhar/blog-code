# The blog itself

What runs [blog.kamelhar.net](https://blog.kamelhar.net): a stock Ghost, a
theme, and the machinery that keeps the writing in git rather than only in a
database.

```
theme/      the "measured" theme (Handlebars + CSS), gscan-clean on Ghost 6
scripts/    publish-content.py  Markdown -> Ghost, by slug, idempotent
            publish-settings.py the site's own settings, same idea
            ghost_target.py     which instance a run is pointed at
            publish-theme.sh    package and upload the theme by hand
            cover.py            generates a post's banner and og:card
k8s/        Ghost, MySQL, and the Job that publishes
Dockerfile          Ghost + theme
Dockerfile.publish  the scripts + the writing
```

Internal addresses have been replaced with placeholders
(`GATEWAY-HOST`, `GHOST-NODE-IP`); everything else is as it runs.

## The idea worth stealing

Ghost keeps posts in MySQL. That is fine until you want the writing diffable,
reviewable in a pull request, and portable to whatever comes after Ghost. So
the Markdown in git is the source and Ghost's copy is a rendering of it:
`publish-content.py` upserts every file by slug, uploads the images each one
references and rewrites the paths to whatever URL Ghost returns.

It is idempotent by construction, which is what lets it run on every deploy
rather than when someone remembers.

## Two things that cost me a day each

**Publishing needs a trigger Argo can see.** The publish Job started as an Argo
PostSync hook. Argo does not diff hook resources — it reports one as
`status=None` — so the hook only ran when something *else* in the application
changed. Publishing the writing therefore required rolling Ghost, and an
attempt to bump only the publisher's image left the application Synced with the
Job still on the previous build and nothing publishing at all. It is a tracked
resource now, in sync-wave 10 with `Replace=true`: its image is a real diff, so
it fires on its own, and Ghost is left running. See `k8s/publish-job.yaml`.

**Ghost sometimes rejects a session it just issued.** Signing in and calling the
admin API immediately returns 403 "Unable to determine the authenticated user"
often enough to matter — measured against a live instance, three sign-ins each
followed by an immediate call gave 200, 200, 403, and the failing one succeeded
on retry with the *same cookie*. By hand you never notice; in a Job that runs on
every deploy it is a failed rollout. `publish-settings.py` retries through a 403
and re-authenticates once before giving up.

Settings also cannot be written with an integration token at all — Ghost
refuses — so anything touching them holds a real staff session.

## Not a product

This is one person's blog, published so the parts above can be read rather than
described. There is no installer and no support.
