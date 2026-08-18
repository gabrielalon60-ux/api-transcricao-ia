# Release topology

`compose.release.yml` is a staging/production contract, not a local-development replacement. It requires immutable `name@sha256:<digest>` application, PostgreSQL, and WUZAPI images; an existing edge network; and secrets supplied by an approved secret store.

Only Orchestrator joins the external edge network. Platform PostgreSQL, Transcription, Bot DF, Database Writer, and the WUZAPI administration surface publish no host ports. The edge proxy and TLS configuration are environment-owned G10-B inputs.

Render only with an approved environment file:

```sh
python scripts/operations/gate10_preflight.py --env-file /protected/release.env --compose-file deploy/compose.release.yml
docker compose --env-file /protected/release.env -f deploy/compose.release.yml config --quiet
```

Rendering does not authorize deployment. See the deployment and rollback runbooks.
