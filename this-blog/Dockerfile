# Ghost, plus the one thing here that is ours.
#
# Ghost itself stays upstream and unmodified -- this adds no layer to it beyond
# the theme. The theme cannot simply be COPYed to its final path: Ghost keeps
# content on a PVC mounted at /var/lib/ghost/content, and a mount masks whatever
# the image had there. So the theme ships beside that path and an initContainer
# copies it in before Ghost starts (see apps/blog/base/ghost.yaml in gitops).
#
# Building it this way is what lets the blog deploy like every other app here:
# CI builds an image, gitops takes the tag, Argo pulls it. Nothing reaches into
# the tailnet, and a theme change is a commit with a sha behind it rather than a
# zip someone uploaded.
FROM ghost:6-alpine

# gscan runs in CI, not here: a theme that fails it must fail the pull request,
# not the image build, and the image build is not where you want to find out.
COPY --chown=node:node theme /opt/measured
