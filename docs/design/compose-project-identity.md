# Compose project identity, and the data that moved with it

## What went wrong

The shipped compose file declared:

```yaml
name: localai
```

A private engineering workbench on the development machine ships its own compose
file that declares the same name. Docker Compose has no concept of "whose"
project a name is, so both resolved to **one project**:

- the same container names (`localai-open-webui-1`, …),
- the same network (`localai_default`),
- and the same volumes (`localai_open-webui`, `localai_searxng-data`).

Starting AFK LocalAI therefore recreated the workbench's containers and mounted
the workbench's Open WebUI database — a database created on 2026-06-07 and
migrated by whatever Open WebUI version had last run against it.

The result was not a crash. The Open WebUI backend started, served the static
frontend happily, and returned **HTTP 500 from `/api/config`** because a JSON
column in that inherited database could not be deserialized. The browser loaded
the page, its first API call failed, and Open WebUI rendered:

> Open WebUI Backend Required — you're using an unsupported method (frontend
> only). Please serve the WebUI from the backend.

Meanwhile the product reported "Your local AI is ready", because readiness was
derived from prerequisites and process exit codes and had never asked the
backend anything.

Measured evidence:

| | before | after |
| --- | --- | --- |
| `/api/config` | 500 | 200 |
| Open WebUI | mutable `:main` | 0.11.0, digest-pinned |
| containers | `localai-*` (shared) | `afk-localai-*` |
| volumes | `localai_open-webui` (foreign, 2026-06-07) | `afk-localai_open-webui` |

## What changed

`name: afk-localai`. Containers, network and volumes are now AFK-specific, so no
other checkout on the machine can resolve to this project.

Open WebUI is pinned by digest rather than `:main`. A certified installer cannot
promise a working product if its core service image can change underneath it
between qualification and the user's first run — and a newer image silently
migrating an existing database is exactly how the failure above became possible.

Ownership is still decided by the compose **config-file path** label, never by
the project name — see `src/localai/afk_ownership.py`. That was already true and
is unchanged; this makes the two projects genuinely separate rather than merely
distinguishable after the fact.

## If you had data in the old volumes

Renaming the project does **not** delete anything. Data written by an earlier
release still exists under the old names:

```
localai_open-webui
localai_searxng-data
```

They are simply no longer mounted by AFK LocalAI. To inspect one:

```bash
docker run --rm -v localai_open-webui:/data:ro busybox ls -la /data
```

To copy the old Open WebUI database into the new volume, **stop AFK LocalAI
first**, then:

```bash
docker run --rm -v localai_open-webui:/from:ro -v afk-localai_open-webui:/to busybox \
  sh -c "cp -a /from/. /to/"
```

Two cautions:

- On a machine that also runs the private workbench, `localai_open-webui` is
  probably the **workbench's** data, not yours. Copying it in is what caused the
  failure this document describes.
- Open WebUI migrates its schema forward on start and does not support
  downgrade. Copy a database only into an Open WebUI at least as new as the one
  that wrote it.

Do not delete the old volumes as cleanup. They are the only copy of whatever was
in them.
