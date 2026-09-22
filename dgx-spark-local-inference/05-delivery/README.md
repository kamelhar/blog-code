# Step 5 — delivery

Nothing is applied by hand. `applicationsets.yaml` holds two Argo CD
ApplicationSets that scan the repository for a marker file and generate an
Application for each one found:

```
apps/*/overlays/prod/deploy.yaml        -> the hub cluster
apps/*/overlays/<cluster>-*/deploy.yaml -> other clusters
```

`deploy.yaml` is the marker for vLLM: five lines saying where it runs. Adding a
workload to the machine is a kustomize overlay plus that file. Point `repoURL`
at your own repository.
