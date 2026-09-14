#requires -Version 7.0
<#
  Repairs Open WebUI's per-model Qwen3.6 think-light settings.

  The important split:
    - keep global DEFAULT_MODEL_PARAMS free of think=false, so full-thinking
      models can still think;
    - set think=false only on the Qwen3.6 think-light model IDs;
    - keep memories available as an explicit capability/tool.
#>
[CmdletBinding()]
param(
  [string]$Container = 'afk-localai-open-webui-1',
  [switch]$Restart
)

$ErrorActionPreference = 'Stop'

function Invoke-DockerPython([string]$Code) {
  $out = (& docker exec $Container python -c $Code) 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "docker exec failed for $Container. $out"
  }
  return $out
}

$python = @'
import json
import sqlite3
import time

db = "/app/backend/data/webui.db"
thinklight_ids = [
    # Daily driver: qwen3.5:9b thinks on every turn by default, which is slow and
    # returns empty content under tight token budgets. think=false makes it fast
    # and direct. (No :latest suffix; it is a custom-tagged model.)
    "qwen3.5:9b-32k",
    "qwen3.6-thinklight-grounded:latest",
    "deep-thinking-qwen3.6:latest",
    "web-search-deep-qwen3.6:latest",
]
full_thinking_ids = [
    "qwen3.6-35b-a3b-grounded:latest",
    "full-thinking-qwen3.6:latest",
]

def loads(value, fallback):
    if not value:
        return dict(fallback)
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else dict(fallback)
    except Exception:
        return dict(fallback)

con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
now = int(time.time())

user = con.execute("select id from user order by created_at limit 1").fetchone()
user_id = user["id"] if user else None

cfg = con.execute("select data from config where id=1").fetchone()
if cfg:
    data = loads(cfg["data"], {})
    data.setdefault("memories", {})["enable"] = True
    default_params = data.setdefault("models", {}).setdefault("default_params", {})
    default_params.pop("think", None)
    con.execute(
        "update config set data=?, updated_at=CURRENT_TIMESTAMP where id=1",
        (json.dumps(data, separators=(",", ":")),),
    )

base_params = {"function_calling": "native", "think": False}
base_meta = {
    "description": "LocalAI: memories stay available; Qwen3.6 think-light uses request-level think=false so full-thinking models can still think.",
    "capabilities": {"memory": True},
}

for model_id in thinklight_ids:
    name = model_id.removesuffix(":latest")
    row = con.execute("select params, meta, created_at from model where id=?", (model_id,)).fetchone()
    params = loads(row["params"], {}) if row else {}
    meta = loads(row["meta"], {}) if row else {}
    params.update(base_params)
    meta.update(base_meta)
    created_at = row["created_at"] if row and row["created_at"] else now
    con.execute(
        """
        insert into model (id, user_id, base_model_id, name, params, meta, updated_at, created_at, is_active)
        values (?, ?, null, ?, ?, ?, ?, ?, 1)
        on conflict(id) do update set
          user_id=excluded.user_id,
          name=excluded.name,
          params=excluded.params,
          meta=excluded.meta,
          updated_at=excluded.updated_at,
          is_active=1
        """,
        (
            model_id,
            user_id,
            name,
            json.dumps(params, separators=(",", ":")),
            json.dumps(meta, separators=(",", ":")),
            now,
            created_at,
        ),
    )

for model_id in full_thinking_ids:
    row = con.execute("select params from model where id=?", (model_id,)).fetchone()
    if not row:
        continue
    params = loads(row["params"], {})
    if params.get("think") is False:
        params.pop("think", None)
        con.execute(
            "update model set params=?, updated_at=? where id=?",
            (json.dumps(params, separators=(",", ":")), now, model_id),
        )

con.commit()

for row in con.execute("select id, params from model where id in (%s) order by id" % ",".join("?" for _ in thinklight_ids), thinklight_ids):
    print(f"{row['id']} {row['params']}")

for row in con.execute("select id, params from model where id in (%s) order by id" % ",".join("?" for _ in full_thinking_ids), full_thinking_ids):
    print(f"{row['id']} {row['params']}")

con.close()
'@

Write-Host "[*] Repairing Open WebUI Qwen3.6 think-light rows in $Container..."
Invoke-DockerPython $python | ForEach-Object { Write-Host "    $_" }

if ($Restart) {
  Write-Host "[*] Restarting $Container so Open WebUI reloads persisted settings..."
  & docker restart $Container | Out-Null
}

Write-Host '[OK] Qwen3.6 think-light uses per-model think=false; full-thinking models are not globally disabled.'
