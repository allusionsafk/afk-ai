"""Bounded remote metadata for Scout's selected pre-download fit check."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TypeGuard

GIB = 1024**3
MAX_BYTES = 2**53 - 1
MAX_TREE_ROWS = 4096
MAX_CONTEXT = 16_777_216
MAX_PARALLEL = 256
_QUANT = re.compile(r"(?:^|[._-])((?:UD-)?I?Q\d[A-Z0-9_]*)(?=[.-]|$)", re.I)
_SHARD = re.compile(r"(.+)-(\d{5})-of-(\d{5})\.gguf\Z", re.I)
_STANDARD_ATTENTION = frozenset(
    {"llama", "mistral", "mixtral", "qwen2", "qwen3", "qwen3_moe"}
)
# Base block sizes from ggml/src/ggml-common.h; mixed-precision tensors can
# make a real artefact larger. These floors never shrink the existing Q4
# heuristic and remain estimates, never substitutes for file byte metadata.
_QUANT_BYTES_PER_WEIGHT = {
    "Q8_0": 34 / 32,
    "Q8_1": 36 / 32,
    "Q6_K": 210 / 256,
    "Q5_K": 176 / 256,
    "Q5_0": 22 / 32,
    "Q5_1": 24 / 32,
}


def _count(value: object, maximum: int) -> TypeGuard[int]:
    return type(value) is int and 0 < value <= maximum


@dataclass(frozen=True)
class QuantArtefact:
    quant: str
    size_bytes: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.quant, str) or not re.fullmatch(
            r"(?:UD-)?I?Q\d[A-Z0-9_]*", self.quant
        ):
            raise ValueError("invalid quant identity")
        if self.size_bytes is not None and not _count(self.size_bytes, MAX_BYTES):
            raise ValueError("invalid artefact byte count")


@dataclass(frozen=True)
class ArchitectureInfo:
    n_layer: int
    n_head_kv: int
    head_dim: int
    native_context: int | None = None

    def __post_init__(self) -> None:
        if not all(
            _count(value, limit)
            for value, limit in (
                (self.n_layer, 1024),
                (self.n_head_kv, 4096),
                (self.head_dim, 8192),
            )
        ):
            raise ValueError("invalid attention dimensions")
        if self.native_context is not None and not _count(
            self.native_context, MAX_CONTEXT
        ):
            raise ValueError("invalid native context")


def select_artefact(
    tree: list[object], preferences: tuple[str, ...]
) -> QuantArtefact | None:
    """Report bytes only for one unambiguous, complete GGUF file/shard group.

    Sizes are repository metadata, not locally measured weights or runtime
    compatibility. Never price an incomplete shard set as the entire model.
    """
    if len(tree) > MAX_TREE_ROWS:
        return None
    groups: dict[str, list[tuple[str, object]]] = {}
    malformed = False
    for row in tree:
        if not isinstance(row, dict):
            malformed = True
            continue
        path = row.get("path")
        if not isinstance(path, str):
            malformed = True
            continue
        if not path.lower().endswith(".gguf"):
            continue
        if (
            len(path) > 512
            or "\\" in path
            or any(ord(c) < 32 for c in path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            malformed = True
            continue
        name = path.rsplit("/", 1)[-1]
        if name.lower().startswith("mmproj"):
            continue
        match = _QUANT.search(name)
        if match:
            quant = match.group(1).upper()
            size = row.get("size") if row.get("type", "file") == "file" else None
            groups.setdefault(quant, []).append((path, size))
    quant = next((q for q in preferences if q in groups), next(iter(groups), None))
    if quant is None:
        return None
    files = groups[quant]
    paths = [path.casefold() for path, _size in files]
    complete = not malformed and len(paths) == len(set(paths))
    complete = complete and all(_count(size, MAX_BYTES) for _path, size in files)
    shards = [_SHARD.fullmatch(path) for path, _size in files]
    if any(shards):
        parts = [match for match in shards if match is not None]
        totals = {int(match.group(3)) for match in parts}
        complete = complete and len(parts) == len(files) and len(totals) == 1
        complete = complete and len({match.group(1) for match in parts}) == 1
        total = next(iter(totals), 0)
        complete = complete and total == len(files)
        complete = complete and {int(match.group(2)) for match in parts} == set(
            range(1, total + 1)
        )
    else:
        complete = complete and len(files) == 1
    size_bytes = sum(size for _path, size in files if type(size) is int)
    return QuantArtefact(
        quant, size_bytes if complete and _count(size_bytes, MAX_BYTES) else None
    )


def estimated_weight_gb(total_b: float | None, quant: str) -> float | None:
    if (
        isinstance(total_b, bool)
        or not isinstance(total_b, (int, float))
        or not math.isfinite(total_b)
        or not 0 < total_b <= 1_000_000
    ):
        return None
    base = re.sub(r"_(?:S|M|L|XL|XXL)$", "", quant.removeprefix("UD-"))
    block_bytes = _QUANT_BYTES_PER_WEIGHT.get(base, 0)
    return max(round(total_b * 0.6, 1), total_b * 1e9 * block_bytes / GIB)


def parse_architecture(config: object) -> ArchitectureInfo | None:
    """Accept supported conventional attention only; hybrid/MLA stays estimated."""
    if not isinstance(config, dict):
        return None
    model_type = config.get("model_type")
    if not isinstance(model_type, str) or model_type not in _STANDARD_ATTENTION:
        return None
    if any(
        key in config
        for key in ("text_config", "layer_types", "kv_lora_rank", "ssm_cfg")
    ):
        return None
    layers, heads = config.get("num_hidden_layers"), config.get("num_attention_heads")
    kv_heads = config.get("num_key_value_heads", heads)
    if not (_count(layers, 1024) and _count(heads, 4096) and _count(kv_heads, 4096)):
        return None
    if kv_heads > heads or heads % kv_heads:
        return None
    dimension = config.get("head_dim")
    if "head_dim" not in config:
        hidden = config.get("hidden_size")
        if not _count(hidden, 1_048_576) or hidden % heads:
            return None
        dimension = hidden // heads
    native = config.get("max_position_embeddings")
    if not _count(dimension, 8192) or (
        native is not None and not _count(native, MAX_CONTEXT)
    ):
        return None
    return ArchitectureInfo(layers, kv_heads, dimension, native)


def architecture_kv_gb(arch: ArchitectureInfo, *, ctx: int, parallel: int) -> float:
    """Conservative f16 K+V estimate in GiB, not a runtime allocation reading.

    Each layer reserves ctx x KV heads x head dimension for both K and V.
    f16 has two bytes per element; parallel slots multiply that reservation.
    Keep full precision at budget comparisons, including one-byte boundaries.
    """
    if not _count(ctx, MAX_CONTEXT) or not _count(parallel, MAX_PARALLEL):
        raise ValueError("invalid requested context or parallel slots")
    return arch.n_layer * 2 * ctx * arch.n_head_kv * arch.head_dim * 2 * parallel / GIB
