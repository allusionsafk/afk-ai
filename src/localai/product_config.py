"""Where an installed AFK AI keeps what, and who owns it.

An installation has two roots and they must never be confused:

``program_root``
    ``%LOCALAPPDATA%\\Programs\\AFK LocalAI``. PRODUCT RUNTIME. Written only by
    Setup, replaced wholesale by an update, verified against the shipped
    ``payload-manifest.json``. Nothing at runtime writes here.

``data_root``
    ``%LOCALAPPDATA%\\AFK LocalAI``. AFK-owned state that must survive repair and
    update: setup checkpoints, the product runtime configuration (chosen model,
    generated service secret), lifecycle logs and diagnostics.

The previous provisioning path wrote a ``.env`` secret, a rewritten
``docker-compose.yml``, ``logs/`` and pip metadata INTO the program root. An update
then silently replaced the configuration, the payload stopped matching its own
manifest, and an uninstall could not remove the directory because Setup had never
installed those files. This module is the single place the engine resolves both
roots, and it refuses layouts where one root sits inside the other.

User content (Open WebUI accounts, chats, documents) lives in AFK's own Docker
volumes and is deliberately not modelled as a file here: nothing in the product
path deletes it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

COMPOSE_FILENAME = "docker-compose.yml"
RUNTIME_ENV_FILENAME = "runtime.env"
INSTALLER_STATE_FILENAME = "installer-state.json"

# Keys the product writes to its runtime configuration. Anything else found in
# the file is preserved but never interpreted.
DEFAULT_MODEL_KEY = "AFK_DEFAULT_MODEL"
SEARXNG_SECRET_KEY = "SEARXNG_SECRET"

# Values that may be written into the compose env file. Secrets are generated
# here and never logged; model tags are validated so a hostile or corrupt state
# file cannot smuggle a newline (a second variable) into the env file.
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MODEL_TAG = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,63}(?:/[a-z0-9][a-z0-9._-]{0,63}){0,2}"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
)

_RUNTIME_ENV_HEADER = (
    "# Managed by AFK AI. Runtime configuration for this installation.\n"
    "# Kept outside the program folder so repair and update preserve it.\n"
)


class LayoutError(ValueError):
    """The requested roots do not describe a safe AFK AI installation."""


@dataclass(frozen=True)
class ProductLayout:
    program_root: Path
    data_root: Path

    @property
    def compose_file(self) -> Path:
        return self.program_root / COMPOSE_FILENAME

    @property
    def runtime_root(self) -> Path:
        return self.program_root / "runtime" / "python"

    @property
    def state_root(self) -> Path:
        return self.data_root / "State"

    @property
    def config_root(self) -> Path:
        return self.data_root / "Config"

    @property
    def logs_root(self) -> Path:
        return self.data_root / "Logs"

    @property
    def diagnostics_root(self) -> Path:
        return self.data_root / "Diagnostics"

    @property
    def runtime_env(self) -> Path:
        return self.config_root / RUNTIME_ENV_FILENAME

    @property
    def installer_state(self) -> Path:
        return self.state_root / INSTALLER_STATE_FILENAME

    @property
    def legacy_env(self) -> Path:
        """The secret file the pre-runtime provisioning left in the program root."""
        return self.program_root / ".env"


def _is_within(child: Path, parent: Path) -> bool:
    child_text = os.path.normcase(str(child))
    parent_text = os.path.normcase(str(parent)).rstrip("\\/")
    return child_text == parent_text or child_text.startswith(parent_text + os.sep)


def resolve_layout(program_root: str | Path, data_root: str | Path) -> ProductLayout:
    """Validate and normalise the two roots an installed product runs with."""
    program = Path(program_root)
    data = Path(data_root)
    if not program.is_absolute() or not data.is_absolute():
        raise LayoutError("Program and data roots must be absolute paths.")
    program = Path(os.path.normpath(program))
    data = Path(os.path.normpath(data))
    if _is_within(data, program) or _is_within(program, data):
        raise LayoutError(
            "The data folder must not be inside the program folder (or vice "
            "versa): an update replaces the program folder wholesale."
        )
    return ProductLayout(program_root=program, data_root=data)


def valid_model_tag(value: str | None) -> bool:
    return bool(value) and _MODEL_TAG.fullmatch(str(value)) is not None


# ---------------------------------------------------------------- runtime.env


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a compose-style env file. Unreadable or malformed lines are skipped."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if _ENV_KEY.fullmatch(key):
            values[key] = value.strip()
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Atomically replace ``path``. Refuses keys or values that could inject."""
    for key, value in values.items():
        if not _ENV_KEY.fullmatch(key):
            raise ValueError(f"Refusing to write env key {key!r}.")
        if any(ch in value for ch in "\r\n\0"):
            raise ValueError(f"Refusing to write a multi-line value for {key}.")
    body = _RUNTIME_ENV_HEADER + "".join(
        f"{key}={values[key]}\n" for key in sorted(values)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=".runtime-env-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if read_env_file(Path(temporary)) != values:
            raise OSError("Runtime configuration did not survive a read-back.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


# ------------------------------------------------------------- model selection


def _installer_state_model(layout: ProductLayout) -> str | None:
    try:
        payload = json.loads(layout.installer_state.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    chat = models.get("chat") if isinstance(models, dict) else None
    tag = chat.get("tag") if isinstance(chat, dict) else None
    return str(tag) if valid_model_tag(tag) else None


def configured_model(layout: ProductLayout) -> str | None:
    """The chat model this installation was set up to use, if one is recorded.

    ``runtime.env`` is authoritative once written; setup's checkpoint is the
    fallback for an installation that chose its model before this file existed.
    An invalid value is treated as "not configured", never passed through.
    """
    value = read_env_file(layout.runtime_env).get(DEFAULT_MODEL_KEY)
    if valid_model_tag(value):
        return value
    return _installer_state_model(layout)


@dataclass(frozen=True)
class ConfigResult:
    changed: bool
    migrated_legacy_secret: bool
    removed_legacy_env: bool


def ensure_runtime_config(
    layout: ProductLayout, *, model: str | None = None
) -> ConfigResult:
    """Create or update ``runtime.env`` without ever losing a value.

    - A secret is generated once and then kept.
    - A legacy program-root ``.env`` secret is carried over, and the legacy file
      is removed only after the migrated value has been read back from its new
      home. The program folder then matches its manifest again.
    - ``model`` (when given) must be a valid tag.
    """
    if model is not None and not valid_model_tag(model):
        raise ValueError(f"Refusing to configure invalid model tag {model!r}.")

    current = read_env_file(layout.runtime_env)
    desired = dict(current)
    legacy = read_env_file(layout.legacy_env)
    migrated = False

    if not desired.get(SEARXNG_SECRET_KEY):
        legacy_secret = legacy.get(SEARXNG_SECRET_KEY, "")
        if re.fullmatch(r"[A-Za-z0-9_-]{16,256}", legacy_secret):
            desired[SEARXNG_SECRET_KEY] = legacy_secret
            migrated = True
        else:
            desired[SEARXNG_SECRET_KEY] = secrets.token_hex(24)
    if model is not None:
        desired[DEFAULT_MODEL_KEY] = model
    elif not valid_model_tag(desired.get(DEFAULT_MODEL_KEY)):
        recorded = _installer_state_model(layout)
        if recorded:
            desired[DEFAULT_MODEL_KEY] = recorded
        else:
            desired.pop(DEFAULT_MODEL_KEY, None)

    changed = desired != current
    if changed:
        write_env_file(layout.runtime_env, desired)

    removed = False
    if layout.legacy_env.is_file():
        stored = read_env_file(layout.runtime_env).get(SEARXNG_SECRET_KEY)
        held = legacy.get(SEARXNG_SECRET_KEY)
        # Only a file whose secret is now safely held elsewhere (or that holds
        # no secret at all) is removed. Anything else is left for a human.
        if held is None or held == stored:
            keys = set(legacy)
            if keys <= {SEARXNG_SECRET_KEY}:
                try:
                    layout.legacy_env.unlink()
                    removed = True
                except OSError:
                    removed = False
    return ConfigResult(
        changed=changed, migrated_legacy_secret=migrated, removed_legacy_env=removed
    )
