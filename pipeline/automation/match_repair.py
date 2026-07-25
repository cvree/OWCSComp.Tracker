"""
match_repair.py — repair matches missing export metadata (Phase D2.1).

Root cause this closes: export_data.build_public_payload only ever surfaces a
match through two paths — a completed pipeline/ingest_map.py CV run, or
export_data._discovered_window_matches (which requires competition_id or
lifecycle_status to be set, and only looks inside the rolling discovery
window). A match entered before the Phase B automation columns existed (no
CV run either) has neither signal and silently vanishes from every public
surface — calendar, match directory, tournament pages, team history, search,
stats — even though it is a completely real, evidenced match.

This module never invents a fact. It backfills exactly two things, and only
when the evidence already sitting in the row supports it:

  * `fixture_kind` — 'production' vs 'synthetic'. Evidence: the row's own
    `source_ref`/`faceit_match_id` (pipeline/sample_data.json seeds every
    demo match as 'sample:<id>' / 'sample-faceit-<id>' — that prefix IS the
    evidence, not a guess). Absent any such marker, a row is 'production' —
    the safe default that keeps every real match visible instead of hiding
    it on suspicion.
  * `lifecycle_status` — derived ONLY from the match's own, already
    CHECK-constrained `status` field (upcoming/live/final/unknown), which
    whoever entered the row already committed to. `final` -> `finished`,
    `live` -> `live`, `upcoming` -> `scheduled`. `status='unknown'` has no
    safe mapping and is left for a human (never guessed) — the row's
    `blockingIssue` says so explicitly instead of silently doing nothing.

Both writes are idempotent (WHERE guards mean an already-set field is never
touched — this can never overwrite a value FACEIT sync, a human, or a
previous repair run already wrote) and reversible (nothing here deletes a
row or any evidence; a `lifecycle_source`/`lifecycle_repaired_at` pair
records provenance for every backfilled value).

This module never writes hero compositions, map results, or team facts.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

# Evidence, not inference: pipeline/sample_data.json is the ONLY writer that
# uses these prefixes (see init_db.py --with-sample). A row bearing one of
# them IS a demo/seed fixture, never a guess based on match content.
_SYNTHETIC_SOURCE_REF_PREFIXES = ("sample:",)
_SYNTHETIC_FACEIT_ID_PREFIXES = ("sample-faceit-",)

# The only lifecycle values this module will ever backfill, and ONLY from the
# match's own pre-existing `status` field — never from cross-referencing
# other matches/teams. 'unknown' intentionally has no entry: there is no safe
# mapping, so it stays unresolved and visible as needs-review.
_STATUS_TO_LIFECYCLE = {
    "final": "finished",
    "live": "live",
    "upcoming": "scheduled",
}

REPAIR_SOURCE = "status-field-backfill"


def _now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat()


def classify_fixture_kind(row: Any) -> str:
    """'production' or 'synthetic', from evidence already on the row.

    An explicit existing value always wins (never re-classified once set —
    a human can override 'synthetic' back to 'production' or vice versa and
    that decision sticks). A fresh row defaults to 'production': the goal is
    "never silently hide a real match," so absence of the sample-fixture
    marker is itself the safe answer, not an unknown.
    """
    existing = _rv(row, "fixture_kind")
    if existing in ("production", "synthetic"):
        return existing
    source_ref = _rv(row, "source_ref") or ""
    faceit_id = _rv(row, "faceit_match_id") or ""
    if any(source_ref.startswith(p) for p in _SYNTHETIC_SOURCE_REF_PREFIXES):
        return "synthetic"
    if any(faceit_id.startswith(p) for p in _SYNTHETIC_FACEIT_ID_PREFIXES):
        return "synthetic"
    return "production"


def infer_lifecycle_status(row: Any) -> tuple[str | None, str | None]:
    """(value, reason) backfill for a missing lifecycle_status.

    Returns (None, None) when the row's `status` doesn't map to a safe
    value (status='unknown', or something outside the CHECK set) — the
    caller must leave the field alone and flag the row for review rather
    than invent a lifecycle.
    """
    status = (_rv(row, "status") or "").lower()
    value = _STATUS_TO_LIFECYCLE.get(status)
    if value is None:
        return None, None
    return value, REPAIR_SOURCE


def _rv(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _blocking_reason(row: Any, fixture_kind: str, has_lifecycle: bool,
                     proposed_lifecycle: str | None) -> str | None:
    if fixture_kind == "synthetic":
        return None  # intentionally excluded — not a gap
    if has_lifecycle:
        return None
    if proposed_lifecycle is not None:
        return None  # this run's repair resolves it
    status = _rv(row, "status")
    return (f"lifecycle_status is unset and match.status={status!r} has no "
            f"safe automatic mapping — needs a human decision "
            f"(scheduled/live/finished/cancelled/forfeit/postponed/unknown)")


def audit_matches(con, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """One row per match describing its current state and any proposed
    repair. Pure read — never writes. Used by both `match-audit` and as the
    dry-run body of `match-repair`."""
    rows = []
    for m in con.execute("SELECT * FROM matches ORDER BY id"):
        fixture_kind = classify_fixture_kind(m)
        current_lifecycle = _rv(m, "lifecycle_status")
        proposed_lifecycle, proposed_reason = (None, None)
        if fixture_kind == "production" and not current_lifecycle:
            proposed_lifecycle, proposed_reason = infer_lifecycle_status(m)
        rows.append({
            "id": m["id"],
            "teamA": m["team_a"],
            "teamB": m["team_b"],
            "date": m["date"],
            "status": m["status"],
            "currentFixtureKind": _rv(m, "fixture_kind"),
            "classifiedFixtureKind": fixture_kind,
            "fixtureKindNeedsWrite": _rv(m, "fixture_kind") != fixture_kind,
            "currentCompetitionId": _rv(m, "competition_id"),
            "currentLifecycleStatus": current_lifecycle,
            "proposedLifecycleStatus": proposed_lifecycle,
            "proposedLifecycleSource": proposed_reason,
            "blockingIssue": _blocking_reason(
                m, fixture_kind, bool(current_lifecycle), proposed_lifecycle),
        })
    return rows


def repair_matches(con, write: bool = False,
                   now: dt.datetime | None = None) -> dict[str, Any]:
    """Idempotent match-metadata repair.

    Dry-run (write=False, the default) makes zero changes and returns the
    same report a write run would have acted on. Write mode only ever sets a
    currently-NULL fixture_kind or lifecycle_status — an already-populated
    value (from FACEIT sync, a human, or a previous repair run) is never
    touched, which is what makes repeated runs a no-op. Never touches
    hero_stints/map_results/comp_snapshots/team rows; never deletes a row.
    """
    now_iso = _now_iso(now)
    rows = audit_matches(con, now)
    repaired_ids: list[str] = []
    classified_ids: list[str] = []   # fixture_kind bookkeeping only — cosmetic
                                      # provenance, not counted as a "repair"
                                      # since NULL already behaves as production
    unresolved_ids: list[str] = []
    synthetic_ids: list[str] = []
    already_ok_ids: list[str] = []

    for r in rows:
        if r["classifiedFixtureKind"] == "synthetic":
            synthetic_ids.append(r["id"])
            if write and r["fixtureKindNeedsWrite"]:
                con.execute(
                    "UPDATE matches SET fixture_kind='synthetic' "
                    "WHERE id=? AND fixture_kind IS NULL", (r["id"],))
                classified_ids.append(r["id"])
            continue

        if write and r["fixtureKindNeedsWrite"]:
            con.execute(
                "UPDATE matches SET fixture_kind='production' "
                "WHERE id=? AND fixture_kind IS NULL", (r["id"],))
            classified_ids.append(r["id"])

        if r["proposedLifecycleStatus"] is not None:
            if write:
                con.execute(
                    """UPDATE matches SET lifecycle_status=?, lifecycle_source=?,
                       lifecycle_repaired_at=?
                       WHERE id=? AND lifecycle_status IS NULL""",
                    (r["proposedLifecycleStatus"], r["proposedLifecycleSource"],
                     now_iso, r["id"]))
            repaired_ids.append(r["id"])
        elif r["blockingIssue"]:
            unresolved_ids.append(r["id"])
        else:
            already_ok_ids.append(r["id"])

    if write:
        con.commit()

    return {
        "generatedAt": now_iso,
        "dryRun": not write,
        "totalMatches": len(rows),
        "repaired": sorted(set(repaired_ids)),
        "classified": sorted(set(classified_ids)),
        "unresolved": [{"id": r["id"], "blockingIssue": r["blockingIssue"]}
                       for r in rows if r["id"] in unresolved_ids],
        "synthetic": sorted(synthetic_ids),
        "alreadyOk": sorted(already_ok_ids),
        "rows": rows,
    }


def format_repair_report(report: dict[str, Any]) -> str:
    mode = "DRY RUN (no writes)" if report["dryRun"] else "WRITE MODE (committed)"
    lines = [f"Match repair — {mode}, {report['totalMatches']} match(es) audited:",
             f"  repaired this run : {len(report['repaired'])}",
             f"  already ok        : {len(report['alreadyOk'])}",
             f"  synthetic (hidden by design) : {len(report['synthetic'])}",
             f"  needs review (unresolved)    : {len(report['unresolved'])}"]
    if report["repaired"]:
        lines.append("")
        lines.append("Repaired:")
        for r in report["rows"]:
            if r["id"] in report["repaired"]:
                bits = []
                if r["fixtureKindNeedsWrite"]:
                    bits.append(f"fixture_kind -> {r['classifiedFixtureKind']}")
                if r["proposedLifecycleStatus"]:
                    bits.append(f"lifecycle_status -> {r['proposedLifecycleStatus']} "
                               f"(via {r['proposedLifecycleSource']})")
                lines.append(f"  - {r['id']}: {', '.join(bits)}")
    if report["unresolved"]:
        lines.append("")
        lines.append("Needs a human (unresolved):")
        for u in report["unresolved"]:
            lines.append(f"  - {u['id']}: {u['blockingIssue']}")
    return "\n".join(lines)
