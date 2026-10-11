# Changelog

## 0.2.7 — ZIP resource loading

- Replaced direct filesystem access to `tag_schema.json` with the package
  loader's ZIP-aware resource reader.
- Added a regression test that imports the complete plugin directly from a ZIP,
  matching Picard's normal plugin installation mode.

Tag behavior, schema 1.1, retrieval optimization, and cache compatibility are
unchanged.

## 0.2.6 — Picard packaged-runtime compatibility

- Removed the `ipaddress` import, which is absent from Picard 2.13.3's frozen
  Python runtime and prevented normal UI installation of 0.2.5.
- Replaced it with self-contained loopback recognition for `localhost`, the
  `127.0.0.0/8` range, `::1`, and expanded IPv6 loopback.

Retrieval behavior, tag schema 1.1, and the `v0.2` entity cache are unchanged.

## 0.2.5 — accelerated local-mirror retrieval

- Reused projection-complete recordings embedded in release responses, with
  per-recording fallback when required relationship data is missing.
- Removed 352 of 657 entity calls across a 12-release, 352-recording cached
  replay, with zero projected tag differences.
- Added an explicit loopback-only acceleration profile with configurable
  concurrency and dispatch delay. The starting profile is 8 requests / 10 ms.
- Kept public and other non-loopback MusicBrainz servers on Picard's normal
  request profile.
- Added crawl diagnostics for embedded-recording reuse, fallback requests, and
  active concurrency/profile settings.

The tag schema remains 1.1 and the persistent entity cache remains `v0.2`.

## 0.2.4 — operational diagnostics are opt-in

- Added a Picard option for persisting plugin status and four retrieval
  timing/request counters. It is off by default.
- Kept retrieval status and error count in the normal completeness metadata.
- A successful complete refresh removes previously persisted operational fields
  when the option is off.
- Changed the JRiver field installer to create 65 core fields by default and all
  70 fields with `-IncludeDiagnostics`.
- Reconciled the public guides, schema categories, and JRiver mapping.

The tag schema remains 1.1 and the entity cache remains compatible at `v0.2`.

## 0.2.3 — compact hierarchy paths

- Added `classical_work_path_summary`, with one readable complete route per
  unique, branched, or incomplete path.
- Changed `classical_work_paths` to one compact JSON document containing all
  enumerated paths and their status.
- Preserved repeated titles at distinct nodes and distinct edges between the
  same work identities.

This introduced tag schema 1.1 and intentionally replaced the schema 1.0 path
serialization.

## 0.2.2 — repeatable tag replacement

- Added an explicit registry of 70 plugin-owned tag names.
- Successful complete projections delete obsolete registered values, including
  disk-only values Picard would otherwise preserve.
- Kept preview, partial, failed, unmatched, disabled, and missing-map runs from
  pruning saved facts.

## 0.2.1 — branch-preserving hierarchy projection

- Separated performed-work resolution from hierarchy resolution.
- Preserved alternative parent paths instead of selecting one by title or path
  length.
- Added source evidence with relationship owner and scope.
- Added retrieval timing and request diagnostics.

## 0.2.0 — automatic focused retrieval

- Added asynchronous MusicBrainz retrieval through Picard's configured server.
- Added shared in-flight lookup coalescing and a seven-day persistent entity
  cache.
- Kept existing explorer semantic maps as offline fallback.
