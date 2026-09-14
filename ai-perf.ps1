#requires -Version 7.0
<#
  ai-perf.ps1 - Performance/context guard for the localai stack.

  This catches the drift that caused the old slow paths: oversized contexts,
  missing Ollama GPU/KV-cache tuning, Qwen3.6 think loops, and loaded models
  that are not staying on the GPU.
#>
[CmdletBinding()]
param(
  [ValidateRange(1024, 32768)]
  [int]$MaxDailyContext = 8192,

  [ValidateRange(1024, 32768)]
  [int]$MaxThinkLightContext = 4096,

  [switch]$Strict
)

$ErrorActionPreference = 'SilentlyContinue'
$Root = $PSScriptRoot
$Ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
$Ok = 0
$Warn = 0
$Fail = 0

function Line([string]$Status, [string]$Name, [string]$Detail) {
  $color = switch ($Status) {
    'OK' { 'Green' }
    'WARN' { 'Yellow' }
    'FAIL' { 'Red' }
    default { 'Gray' }
  }
  if ($Status -eq 'OK') { $script:Ok++ }
  elseif ($Status -eq 'WARN') { $script:Warn++ }
  elseif ($Status -eq 'FAIL') { $script:Fail++ }
  Write-Host ("[{0}] {1,-24} {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

. (Join-Path $Root 'ai-common.ps1')   # shared Invoke-AiProcess (was inlined below)
function Invoke-ProcessCaptured([string]$FilePath, [string[]]$ArgumentList = @(), [int]$TimeoutSec = 30) {
  return Invoke-AiProcess $FilePath $ArgumentList $TimeoutSec $Root
}

function Invoke-OllamaApi([string]$Path, [hashtable]$Body = $null, [int]$TimeoutSec = 10) {
  $uri = "http://localhost:11434$Path"
  if ($Body) {
    $json = $Body | ConvertTo-Json -Depth 8
    return Invoke-RestMethod -Uri $uri -Method Post -Body $json -ContentType 'application/json' -TimeoutSec $TimeoutSec
  }
  return Invoke-RestMethod -Uri $uri -TimeoutSec $TimeoutSec
}

function Get-PersistedEnv([string]$Name) {
  $process = [Environment]::GetEnvironmentVariable($Name, 'Process')
  $user = [Environment]::GetEnvironmentVariable($Name, 'User')
  $machine = [Environment]::GetEnvironmentVariable($Name, 'Machine')
  [pscustomobject]@{
    Name = $Name
    Value = if ($process) { $process } elseif ($user) { $user } else { $machine }
    Process = $process
    User = $user
    Machine = $machine
  }
}

function Test-EnvEquals([string]$Name, [string]$Expected) {
  $row = Get-PersistedEnv $Name
  if ([string]$row.Value -eq $Expected) {
    Line 'OK' $Name $Expected
  } elseif ($row.Value) {
    Line 'WARN' $Name "expected $Expected; current $($row.Value)"
  } else {
    Line 'WARN' $Name "missing; expected $Expected"
  }
}

function Read-DefaultModel {
  $compose = Join-Path $Root 'docker-compose.yml'
  if (-not (Test-Path -LiteralPath $compose)) { return '' }
  $text = Get-Content -LiteralPath $compose -Raw
  $m = [regex]::Match($text, 'DEFAULT_MODELS=([^\s]+)')
  if ($m.Success) { return $m.Groups[1].Value.Trim() }
  return ''
}

function Read-TaskModel {
  $compose = Join-Path $Root 'docker-compose.yml'
  if (-not (Test-Path -LiteralPath $compose)) { return '' }
  $text = Get-Content -LiteralPath $compose -Raw
  $m = [regex]::Match($text, 'TASK_MODEL=([^\r\n]*)')
  if ($m.Success) { return $m.Groups[1].Value.Trim() }
  return $null
}

function Read-ComposeText {
  $compose = Join-Path $Root 'docker-compose.yml'
  if (-not (Test-Path -LiteralPath $compose)) { return '' }
  return Get-Content -LiteralPath $compose -Raw
}

function Read-DefaultModelParams([string]$ComposeText) {
  if (-not $ComposeText) { return $null }
  $m = [regex]::Match($ComposeText, 'DEFAULT_MODEL_PARAMS=(\{[^\r\n]+\})')
  if (-not $m.Success) { return $null }
  try { return $m.Groups[1].Value | ConvertFrom-Json } catch { return $null }
}

function Test-OpenWebUIRequestParams([string]$ComposeText) {
  $params = Read-DefaultModelParams $ComposeText
  if ($null -eq $params) {
    Line 'WARN' 'Open WebUI params' 'DEFAULT_MODEL_PARAMS missing or invalid'
    return
  }

  $streamOk = [bool]$params.stream_response
  $keep = [string]$params.keep_alive
  $keepOk = $keep -and $keep -notin @('0','0s','0m')
  $globalThinkOff = $params.PSObject.Properties.Name -contains 'think' -and $params.think -eq $false

  if ($streamOk -and $keepOk -and -not $globalThinkOff) {
    Line 'OK' 'Open WebUI params' "stream_response=true, keep_alive=$keep"
  } else {
    $missing = @()
    if (-not $streamOk) { $missing += 'stream_response=true' }
    if (-not $keepOk) { $missing += 'request keep_alive=30m' }
    if ($globalThinkOff) { $missing += 'remove global think=false' }
    Line 'WARN' 'Open WebUI params' ('missing: ' + ($missing -join ', '))
  }
}

function Test-OpenWebUIMemories([string]$ComposeText) {
  if ($ComposeText -match '(?m)^\s*-\s*ENABLE_MEMORIES=True\s*$') {
    Line 'OK' 'Open WebUI memories' 'enabled; think-light uses per-model think=false'
  } else {
    Line 'WARN' 'Open WebUI memories' 'ENABLE_MEMORIES=True missing; memory tools may be unavailable'
  }
}

function Test-OpenWebUIThinkLightRows {
  $code = @'
import json
import sqlite3
import sys

db = "/app/backend/data/webui.db"
thinklight = [
    "qwen3.6-thinklight-grounded:latest",
    "deep-thinking-qwen3.6:latest",
    "web-search-deep-qwen3.6:latest",
]
full = [
    "qwen3.6-35b-a3b-grounded:latest",
    "full-thinking-qwen3.6:latest",
]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
bad = []
for model_id in thinklight:
    row = con.execute("select params from model where id=?", (model_id,)).fetchone()
    params = json.loads(row["params"]) if row and row["params"] else {}
    if params.get("think") is not False:
        bad.append(model_id + " missing think=false")
for model_id in full:
    row = con.execute("select params from model where id=?", (model_id,)).fetchone()
    params = json.loads(row["params"]) if row and row["params"] else {}
    if params.get("think") is False:
        bad.append(model_id + " should be allowed to think")
con.close()
if bad:
    print("; ".join(bad))
    sys.exit(1)
print("think-light only")
'@
  $result = Invoke-ProcessCaptured 'docker' @('exec', 'afk-localai-open-webui-1', 'python', '-c', $code) 20
  if ($result.Code -eq 0) {
    Line 'OK' 'Open WebUI thinking' 'think=false only on Qwen3.6 think-light rows'
  } else {
    Line 'WARN' 'Open WebUI thinking' "run ai-openwebui-thinklight.ps1; $($result.Text)"
  }
}

Write-Host "==== localai performance guard ====  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

if (Test-Path -LiteralPath $Ollama) {
  Line 'OK' 'Ollama binary' $Ollama
} else {
  Line 'FAIL' 'Ollama binary' "missing: $Ollama"
}

$defaultModel = Read-DefaultModel
if ($defaultModel -eq 'qwen2.5-grounded') {
  Line 'OK' 'Daily default' 'qwen2.5-grounded'
} elseif ($defaultModel) {
  Line 'WARN' 'Daily default' "$defaultModel is configured; qwen2.5-grounded is the responsive default"
} else {
  Line 'WARN' 'Daily default' 'could not read DEFAULT_MODELS from docker-compose.yml'
}

$taskModel = Read-TaskModel
if ($taskModel -eq '') {
  Line 'OK' 'Task model' 'blank; Open WebUI tasks use the current chat model'
} elseif ($taskModel) {
  Line 'WARN' 'Task model' "$taskModel can evict the active chat model after background tasks"
} else {
  Line 'WARN' 'Task model' 'could not read TASK_MODEL from docker-compose.yml'
}

Test-EnvEquals 'OLLAMA_FLASH_ATTENTION' '1'
Test-EnvEquals 'OLLAMA_KV_CACHE_TYPE' 'q8_0'
Test-EnvEquals 'OLLAMA_KEEP_ALIVE' '30m'

# Ollama RAG embeddings load a 2nd model; >=2 lets the embed model coexist with the
# chat model instead of evicting it (reload churn) on every search. nomic is ~274MB.
$maxLoaded = Get-PersistedEnv 'OLLAMA_MAX_LOADED_MODELS'
$mlmValue = 0
if ($maxLoaded.Value -and [int]::TryParse([string]$maxLoaded.Value, [ref]$mlmValue)) {
  if ($mlmValue -ge 2) {
    Line 'OK' 'OLLAMA_MAX_LOADED_MODELS' "$mlmValue (chat + embed can coexist)"
  } else {
    Line 'WARN' 'OLLAMA_MAX_LOADED_MODELS' "$mlmValue; Ollama RAG embeddings need >=2 or the embed model evicts chat"
  }
} elseif ($maxLoaded.Value) {
  Line 'WARN' 'OLLAMA_MAX_LOADED_MODELS' "not numeric: $($maxLoaded.Value)"
} else {
  Line 'WARN' 'OLLAMA_MAX_LOADED_MODELS' 'missing; Ollama RAG embeddings need >=2 (embed model evicts chat at 1)'
}

$composeText = Read-ComposeText
Test-OpenWebUIRequestParams $composeText
Test-OpenWebUIMemories $composeText
Test-OpenWebUIThinkLightRows

$ctx = Get-PersistedEnv 'OLLAMA_CONTEXT_LENGTH'
$ctxValue = 0
if ($ctx.Value -and [int]::TryParse([string]$ctx.Value, [ref]$ctxValue)) {
  if ($ctxValue -le $MaxDailyContext) {
    Line 'OK' 'OLLAMA_CONTEXT_LENGTH' "$ctxValue (daily ceiling $MaxDailyContext)"
  } else {
    Line 'WARN' 'OLLAMA_CONTEXT_LENGTH' "$ctxValue is above daily ceiling $MaxDailyContext; CPU spill risk"
  }
} elseif ($ctx.Value) {
  Line 'WARN' 'OLLAMA_CONTEXT_LENGTH' "not numeric: $($ctx.Value)"
} else {
  Line 'WARN' 'OLLAMA_CONTEXT_LENGTH' "missing; expected $MaxDailyContext"
}

$origins = Get-PersistedEnv 'OLLAMA_ORIGINS'
# WebBrain (the supported browser agent) connects direct to Ollama once its
# extension origin is allowlisted here.
if ([string]$origins.Value -match 'chrome-extension://ljhijonmfahplgbbacgcfnaihbjljhhb') {
  Line 'OK' 'WebBrain origin' 'extension origin is allowed'
} elseif ($origins.Value) {
  Line 'WARN' 'WebBrain origin' 'extension origin missing from OLLAMA_ORIGINS'
} else {
  Line 'WARN' 'WebBrain origin' 'OLLAMA_ORIGINS missing'
}

$ollamaApiReady = $false
try {
  Invoke-OllamaApi '/api/tags' $null 5 | Out-Null
  $ollamaApiReady = $true
  Line 'OK' 'Ollama API' 'http://localhost:11434'
} catch {
  Line 'WARN' 'Ollama API' 'not reachable; live model checks skipped'
}

if ($ollamaApiReady) {
  try {
    $think = Invoke-OllamaApi '/api/show' @{ model = 'qwen3.6-thinklight-grounded' } 15
    $thinkText = ([string]$think.modelfile) + "`n" + ([string]$think.parameters)
    $hasCtx = $thinkText -match "(?m)^\s*(PARAMETER\s+)?num_ctx\s+$MaxThinkLightContext\s*$"
    # 1536, not 512: clients that omit think:false spend budget on thinking first,
    # and a 512 cap left zero tokens for the answer (verified 2026-07-03).
    $hasPredict = $thinkText -match '(?m)^\s*(PARAMETER\s+)?num_predict\s+1536\s*$'
    $hasPromptThinkPrefill = $thinkText -match '(?s)<\|im_start\|>assistant\s*<think>\s*</think>'
    if ($hasCtx -and $hasPredict -and -not $hasPromptThinkPrefill) {
      Line 'OK' 'Qwen3.6 think-light' "num_ctx=$MaxThinkLightContext, num_predict=1536, Cherry-safe template"
    } else {
      $missing = @()
      if (-not $hasCtx) { $missing += "num_ctx $MaxThinkLightContext" }
      if (-not $hasPredict) { $missing += 'num_predict 1536' }
      if ($hasPromptThinkPrefill) { $missing += 'remove prompt-level think prefill' }
      Line 'WARN' 'Qwen3.6 think-light' ('missing: ' + ($missing -join ', '))
    }
  } catch {
    Line 'WARN' 'Qwen3.6 think-light' $_.Exception.Message
  }

  try {
    $ps = Invoke-OllamaApi '/api/ps' $null 10
    $loaded = @($ps.models)
    if ($loaded.Count -eq 0) {
      Line 'OK' 'Loaded models' 'none loaded; no active GPU spill'
    } else {
      # Disk sizes let each loaded model split weights vs context cost.
      $diskSizes = @{}
      try {
        $tags = Invoke-OllamaApi '/api/tags' $null 10
        foreach ($m in @($tags.models)) {
          if ($m.name) { $diskSizes[[string]$m.name] = [double]$m.size }
        }
      } catch {
        $diskSizes = @{}
      }
      $totalVramAttr = 0.0
      foreach ($model in $loaded) {
        $label = if ($model.name) { [string]$model.name } elseif ($model.model) { [string]$model.model } else { 'loaded model' }
        $size = [double]$model.size
        $sizeVram = [double]$model.size_vram
        if ($size -gt 0 -and $sizeVram -gt 0) {
          $totalVramAttr += $sizeVram
          $pct = [math]::Round(($sizeVram / $size) * 100, 0)
          $sizeGb = [math]::Round($size / 1GB, 1)
          $vramGb = [math]::Round($sizeVram / 1GB, 1)
          $segments = @("$pct% GPU ($vramGb/$sizeGb GB)")
          $weights = if ($diskSizes.ContainsKey($label)) { [double]$diskSizes[$label] } else { 0.0 }
          if ($weights -gt 0) {
            # loaded - disk = graph/runtime overhead only. Measured 2026-07-15:
            # /api/ps size EXCLUDES the KV cache (9b@64k reported +0.3 GB while
            # nvidia-smi showed ~3 GB more) - KV is reported separately below.
            $segments += "weights $([math]::Round($weights / 1GB, 1)) GB"
            $overhead = [math]::Max($size - $weights, 0.0)
            $segments += "overhead $([math]::Round($overhead / 1GB, 1)) GB"
          }
          $cpuBytes = [math]::Max($size - $sizeVram, 0.0)
          if ($cpuBytes -gt (0.05 * 1GB)) {
            $segments += "CPU side $([math]::Round($cpuBytes / 1GB, 1)) GB"
          }
          if ($model.context_length -and [double]$model.context_length -gt 0) {
            $segments += "num_ctx $([int][double]$model.context_length)"
          }
          $detail = ($segments -join ', ')
          if ($pct -ge 98) {
            Line 'OK' "$label residency" $detail
          } else {
            Line 'WARN' "$label residency" "$detail; CPU spill likely"
          }
        } else {
          Line 'WARN' "$label residency" 'API did not report size_vram/size'
        }
      }
      # KV cache is invisible in /api/ps accounting (see comment above): show it
      # as the gap between GPU-reported use and Ollama-attributed VRAM
      # (gap = KV cache + any non-Ollama GPU apps).
      if ($totalVramAttr -gt 0) {
        try {
          $smiKv = Invoke-ProcessCaptured 'nvidia-smi' @('--query-gpu=memory.used', '--format=csv,noheader,nounits') 15
          if ($smiKv.Code -eq 0 -and $smiKv.Text) {
            $usedBytes = [double]((("$($smiKv.Text)" -split "`n")[0]).Trim()) * 1MB
            $gap = $usedBytes - $totalVramAttr
            if ($usedBytes -gt 0 -and $gap -gt (0.2 * 1GB)) {
              Line 'OK' 'Context/KV cache' "~$([math]::Round($gap / 1GB, 1)) GB GPU beyond Ollama-attributed (KV cache + other GPU apps)"
            }
          }
        } catch { }
      }
    }
  } catch {
    Line 'WARN' 'Loaded models' $_.Exception.Message
  }
}

try {
  $smi = Invoke-ProcessCaptured 'nvidia-smi' @('--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits') 15
  if ($smi.Code -eq 0 -and $smi.Text) {
    $parts = (("$($smi.Text)" -split "`n")[0] -split ',')
    $used = [double]$parts[0].Trim()
    $total = [double]$parts[1].Trim()
    $free = $total - $used
    $usedGb = [math]::Round($used / 1024, 1)
    $totalGb = [math]::Round($total / 1024, 1)
    $freeGb = [math]::Round($free / 1024, 1)
    if ($freeGb -lt 0.8) {
      Line 'WARN' 'GPU headroom' "$usedGb/$totalGb GB used; only $freeGb GB free"
    } else {
      Line 'OK' 'GPU headroom' "$usedGb/$totalGb GB used; $freeGb GB free"
    }
  } else {
    Line 'WARN' 'GPU headroom' 'nvidia-smi returned nothing'
  }
} catch {
  Line 'WARN' 'GPU headroom' 'nvidia-smi not available'
}

Write-Host ("`nSummary: {0} OK, {1} WARN, {2} FAIL" -f $Ok, $Warn, $Fail)
if ($Fail -gt 0) { exit 1 }
if ($Strict -and $Warn -gt 0) { exit 1 }
exit 0
