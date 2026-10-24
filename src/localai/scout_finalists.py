"""Bounded final-fit enrichment; metadata estimates never establish runtime Ready."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from time import monotonic

from localai import model_scout as scout
from localai.scout_categories import CATEGORIES, Category
from localai.scout_evidence import (
    GIB,
    MAX_PARALLEL,
    ArchitectureInfo,
    QuantArtefact,
    architecture_kv_gb,
    estimated_weight_gb,
    parse_architecture,
)

FINALISTS_PER_CATEGORY = 3
MAX_ENRICHED_REPOSITORIES = 6
ENRICHMENT_ADMISSION_SECONDS = 8.0


@dataclass(frozen=True)
class _Evidence:
    repository_id: str
    artefact: QuantArtefact | None
    architecture: ArchitectureInfo | None


def _final_fit(
    candidate: scout.Candidate,
    budget: scout.Budget,
    category: Category,
    parallel: int,
    evidence: _Evidence | None,
) -> scout.Candidate:
    curated = candidate.author == "curated"
    if evidence is not None and evidence.repository_id != candidate.id:
        raise ValueError("finalist evidence repository mismatch")
    artefact = evidence.artefact if evidence else None
    architecture = evidence.architecture if evidence else None
    quant = artefact.quant if artefact else None
    status = (
        "curated" if curated else
        "available" if artefact or architecture else
        "unavailable" if evidence else "not-checked"
    )
    weights = (
        artefact.size_bytes / GIB
        if artefact and artefact.size_bytes is not None
        else estimated_weight_gb(candidate.total, quant or "Q4_K_M")
    )
    weight_source = (
        "repository-bytes"
        if artefact and artefact.size_bytes is not None
        else "parameter-estimate"
    )
    kv_source = "config-f16-estimate" if architecture else "parameter-f16-estimate"
    if weights is None:
        return replace(
            candidate, verdict="Unknown", size_gb=None, fit_stage="final",
            weight_source=weight_source, kv_source=kv_source, quant=quant,
            metadata_status=status,
            fit_why="Unverified curated fallback; size unavailable."
            if curated else "Weight size unavailable; no fit recommendation.",
        )
    kv = (
        architecture_kv_gb(architecture, ctx=category.target_ctx, parallel=parallel)
        if architecture else scout.estimate_kv_gb(
            candidate.total or 70, ctx=category.target_ctx,
            parallel=parallel, kv_factor=1.0,
        )
    )
    demand = weights + kv
    if weights > budget.ram_gb - scout.RAM_HEADROOM_GB:
        verdict = "TooBig"
    elif demand <= budget.vram_gb - scout.VRAM_OVERHEAD_GB:
        verdict = "Good"
    elif demand <= budget.ram_gb - scout.RAM_HEADROOM_GB:
        verdict = "Tight"
    else:
        verdict = "Poor"
    why = (
        f"~{scout.format_num(weights)}GB weights ({weight_source}) + "
        f"{scout.format_num(kv)}GB KV@{category.target_ctx // 1024}k "
        f"({kv_source}, {parallel} parallel); estimated {verdict}; "
        "runtime support, allocation and speed unverified"
    )
    if architecture and architecture.native_context and (
        category.target_ctx > architecture.native_context
    ):
        why += f"; requested context exceeds native {architecture.native_context}"
    if curated:
        why = "Unverified curated fallback. " + why
    elif status == "not-checked":
        why = "Metadata not checked (admission/request cap). " + why
    elif status == "unavailable":
        why = "Usable metadata unavailable; conservative fallback. " + why
    fit = scout.FitEstimate(verdict, weights, kv, why)
    return replace(
        candidate, verdict=verdict, size_gb=round(demand, 1), fit_why=why,
        score=scout.score_for_category(candidate, category, fit), fit_stage="final",
        weight_source=weight_source, kv_source=kv_source, quant=quant,
        weight_gb=weights, kv_gb=kv,
        metadata_status=status,
    )


def collect_final_scout_groups(
    budget: scout.Budget,
    candidates: list[scout.Candidate],
    *,
    parallel: int = 1,
) -> dict[str, scout.CategoryResult]:
    """Rerank at most three discovered finalists per category plus curated seeds.

    Metadata is fetched once per exact repository, at most six repositories
    (12 requests), fairly visiting category first choices before second choices.
    An eight-second admission window stops starting another repository; an
    in-flight tree/config pair may outlast it. Failed or unchecked metadata
    keeps explicitly marked conservative estimates. This is not a total network
    deadline or exhaustive discovery; persisted evidence is never reused.
    """
    if type(parallel) is not int or not 1 <= parallel <= MAX_PARALLEL:
        raise ValueError("invalid parallel slot count")
    if not all(
        type(value) in (int, float) and math.isfinite(value) and value >= 0
        for value in (budget.ram_gb, budget.vram_gb, budget.disk_free_gb)
    ):
        raise ValueError("invalid hardware budget")
    # Exact repository identities keep same-named mirrors from borrowing bytes.
    unique: dict[str, scout.Candidate] = {}
    for candidate in candidates:
        if candidate.author == "curated":
            continue
        try:
            scout._validate_hf_repo(candidate.id)
        except ValueError:
            continue
        unique.setdefault(candidate.id, candidate)
    shortlists: dict[str, list[scout.Candidate]] = {}
    for category in CATEGORIES:
        eligible = [
            candidate for candidate in unique.values()
            if scout.candidate_eligible_for(candidate, category)
        ]
        eligible.sort(key=lambda candidate: (
            -scout.score_for_category(candidate, category, scout.category_fit(
                candidate, budget, ctx=category.target_ctx,
                parallel=parallel, kv_factor=1.0,
            )), candidate.id,
        ))
        shortlists[category.id] = eligible[:FINALISTS_PER_CATEGORY]
    evidence: dict[str, _Evidence] = {}
    ordered: dict[str, scout.Candidate] = {}
    for position in range(FINALISTS_PER_CATEGORY):
        for shortlist in shortlists.values():
            if position < len(shortlist):
                candidate = shortlist[position]
                ordered.setdefault(candidate.id, candidate)
    started = monotonic()
    for candidate in ordered.values():
        if (
            len(evidence) >= MAX_ENRICHED_REPOSITORIES
            or monotonic() - started >= ENRICHMENT_ADMISSION_SECONDS
        ):
            break
        evidence[candidate.id] = _Evidence(
            candidate.id, scout.select_quant_artefact(candidate.id),
            parse_architecture(scout.fetch_hf_config(candidate.id)),
        )
    results: dict[str, scout.CategoryResult] = {}
    for category in CATEGORIES:
        scored: list[scout.Candidate] = []
        dropped: list[tuple[str, str]] = []
        pool = [*shortlists[category.id], *(
            scout._curated_candidate(tag) for tag in category.curated
        )]
        for candidate in pool:
            finalized = _final_fit(
                candidate, budget, category, parallel, evidence.get(candidate.id)
            )
            if finalized.verdict in scout._INFEASIBLE_VERDICTS and not (
                candidate.author == "curated" and finalized.verdict == "Unknown"
            ):
                dropped.append((finalized.name, finalized.fit_why))
            else:
                scored.append(finalized)
        scored.sort(key=lambda candidate: (-candidate.score, candidate.id))
        top = scored[0] if scored else None
        why = (
            scout._compose_category_why(top, category) if top else
            category.note or "No feasible estimate in the bounded shortlist."
        )
        results[category.id] = scout.CategoryResult(
            category.id, top, tuple(scored[1:3]), why, tuple(dropped)
        )
    return results
