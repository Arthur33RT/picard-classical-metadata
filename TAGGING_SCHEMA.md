# Classical Metadata Tagging Schema 1.1

This document defines the 70 tag names owned by Picard Classical Metadata
0.2.7. Values are Unicode strings or ordered multi-value lists. Picard preview
mode prefixes names with `~`, making them hidden variables rather than file
tags.

The machine-readable registry is `tag_schema.json`.

## Semantic rules

1. A recording can have zero, one, or several performed-work assertions.
2. When assigned works form a strict ancestor/descendant chain, the most
   specific assigned work is primary and its ancestors remain context.
3. Unrelated surviving assignments produce `ambiguous_work`. Candidate fields
   retain every candidate; the projector does not silently declare one
   authoritative.
4. A work can span several recordings. Segment fields describe recordings
   assigned to the same primary work within the release.
5. Explicit partial-performance assertions remain explicit.
6. A recording without a performed-work relationship is `unresolved_work`.
   Track titles are not parsed into authoritative work, cast, or hierarchy data.
7. Only structured relationships establish performer roles, composition roles,
   catalogs, places, dates, and source works.
8. A resolved performed work can have branched or incomplete ancestry. Work
   resolution and hierarchy resolution are separate statuses.

## Retrieval completeness

- `classical_retrieval_status`: `complete`, `partial`,
  `fallback_cached_map`, or `cached_map`
- `classical_retrieval_error_count`: failed entities or traversal errors, when
  present

Retrieval completeness is independent of work resolution. A resolved work can
have incomplete parent or source metadata after a failed lookup.

## Optional operational diagnostics

These five fields are registered but are not persisted by default:

- `classical_plugin_status`
- `classical_retrieval_elapsed_seconds`
- `classical_retrieval_network_requests`
- `classical_retrieval_cache_hits`
- `classical_retrieval_coalesced_lookups`

Picard's log contains the diagnostics regardless of persistence. Disabling
diagnostic persistence removes older saved copies on the next successful
complete refresh.

## Hierarchy evidence

- `classical_hierarchy_status`: `unique`, `branched`, or `incomplete`
- `classical_hierarchy_path_count`: number of enumerated root-to-work paths
- `classical_work_paths`: one compact JSON document containing all enumerated
  paths for the primary projected work
- `classical_source_evidence`: JSON relationship records retaining source owner
  and scope

`classical_work_paths` has this shape:

```json
{
  "paths": [
    {
      "works": [
        {"id": "...", "title": "..."}
      ],
      "edges": [
        {
          "parent_id": "...",
          "child_id": "...",
          "order": 1,
          "attributes": []
        }
      ]
    }
  ],
  "status": "unique"
}
```

Works are ordered root to leaf. A work can also include `disambiguation`,
`type`, and `languages`. Distinct relationships between the same two works
remain distinct edges. Aliases, annotations, ratings, and unrelated source
metadata remain in the cache or semantic map.

The JSON describes the primary projected work. Other performed-work candidates
remain in the candidate fields below; the JSON is not a union of every ambiguous
candidate.

## Identity and resolution

- `classical_schema_version`
- `classical_status`: `resolved`, `ambiguous_work`, `unresolved_work`, or
  `recording_not_in_semantic_map`
- `classical_release_id`
- `classical_recording_id`
- `classical_work`
- `classical_work_id`
- `classical_work_type`
- `classical_candidate_work`
- `classical_candidate_work_id`
- `classical_broader_work_assignment`
- `classical_broader_work_assignment_id`

## Structure and segmentation

- `classical_work_path`: ordered root-to-primary-work titles, present only for
  a unique complete hierarchy
- `classical_work_path_summary`: one readable root-to-work route per enumerated
  path; disambiguations appear in brackets
- `classical_work_path_id`: IDs aligned with `classical_work_path`
- `classical_work_depth`: present only for a unique complete hierarchy
- `classical_parent_work`
- `classical_parent_work_id`
- `classical_part_order`: present only when one unambiguous immediate-parent
  relationship supplies it
- `classical_partial`: `1` or `0`
- `classical_segment_index`
- `classical_segment_count`

On a branched or incomplete hierarchy, singular path, depth, and part-order
claims are omitted. Immediate parents can still contain several values.

Segment values count recordings assigned to the same primary work within the
release, sorted by release track position. They do not claim consecutive tracks
or a particular musical division. `classical_partial` separately retains the
explicit MusicBrainz performance attribute.

## Composition and sources

- `classical_composer`, `classical_composer_id`
- `classical_librettist`, `classical_librettist_id`
- `classical_lyricist`, `classical_lyricist_id`
- `classical_writer`, `classical_writer_id`
- `classical_translator`, `classical_translator_id`
- `classical_arranger`, `classical_arranger_id`
- `classical_orchestrator`, `classical_orchestrator_id`
- `classical_catalog`: `Authority: Identifier`
- `classical_source_work`, `classical_source_work_id`
- `classical_source_kind`
- `classical_source_confidence`

On branched hierarchies, inherited roles and catalogs come only from ancestors
shared by every enumerated path. Source summary tags do not by themselves claim
that the performed work has a direct arrangement or translation relationship;
`classical_source_evidence` identifies the relationship owner and its scope as
`performed_work`, `ancestor`, or `performed_component`.

## Performance and session

- `classical_conductor`, `classical_conductor_id`
- `classical_ensemble`, `classical_ensemble_id`
- `classical_performer`, `classical_performer_id`
- `classical_performer_role`: `Artist [Character]`
- `classical_performer_instrument`: `Artist [Instrument or vocal type]`
- `classical_producer`
- `classical_engineer`
- `classical_balance`
- `classical_editor`
- `classical_mix`
- `classical_mastering`
- `classical_recording_place`, `classical_recording_place_id`
- `classical_recording_area`
- `classical_recording_date_begin`
- `classical_recording_date_end`

An artist explicitly typed Choir or Orchestra is also tagged as an ensemble
when credited through a vocal or instrument relationship. Performer credits
remain. Ensemble classification is not guessed from an artist name.

Dates preserve MusicBrainz precision. A year or year-month remains partial; the
projector never invents a month or day.

## Replacement policy

After a successful complete persisted projection, absent names from the owned
registry are marked for deletion. This permits repeatable refreshes and removes
obsolete plugin-owned values without blanking files first.

The cleanup is not a wildcard deletion. Standard fields, unrelated custom tags,
and unregistered `classical_*` names are untouched. Preview mode, partial or
failed retrieval, unmatched recordings, disabled processing, and missing maps
do not prune saved facts. Picard's preserved-tag rules can override deletion.

## Deliberately excluded from projected tags

- reverse links to other recordings of a work;
- forward arrangements, fantasies, quotations, and derivatives;
- quarantined source relationships;
- roles inferred only from titles;
- guessed work hierarchies;
- universal export of arbitrary MusicBrainz relationships.

The semantic engine can retain evidence that is not projected into file tags.
