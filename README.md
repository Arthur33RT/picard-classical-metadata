# Classical Metadata for MusicBrainz Picard

Classical Metadata retrieves focused classical-music relationships from
MusicBrainz and writes them as documented `classical_*` tags. The resulting
metadata is intended for browsing and organization in applications such as
JRiver Media Center.

> The current public release, **0.2.7**, supports MusicBrainz Picard **2.13.x**.

## Download

[Download the latest installable plugin ZIP](../../releases/latest/download/picard_classical_metadata.zip)

Download the file named `picard_classical_metadata.zip`. Do not use GitHub's
automatically generated "Source code" archives as Picard installation files.

## Installation

1. Download `picard_classical_metadata.zip` from the latest release.
2. In Picard, open **Options → Plugins**.
3. Install the ZIP without extracting it.
4. Restart Picard if prompted.
5. Enable **Classical Metadata** and confirm the expected version is shown.

When upgrading, remove or disable older copies before installing the new ZIP.
Test a small release and save copied audio files before applying a new version
to a larger collection.

## What it does

The plugin follows MusicBrainz work, recording, release, performer, artist,
place, and source relationships. Its schema covers:

- performed-work identity and resolution status;
- complete, branched, and incomplete work hierarchies;
- composers, writers, arrangers, orchestrators, and catalog numbers;
- conductors, ensembles, performers, roles, and instruments;
- production and recording-session credits, places, and dates;
- source-work evidence and retrieval completeness.

The plugin does not infer authoritative facts from track titles or familiar
names. When MusicBrainz supplies multiple valid work paths, it preserves those
alternatives rather than selecting one by title, language, or path length.

## Configuration

- **Enable Classical Metadata projection** controls all processing.
- **Automatically retrieve metadata** retrieves relationships using Picard's
  configured MusicBrainz server.
- **Accelerate retrieval for a loopback MusicBrainz mirror** enables a tunable
  request profile only for a server running on the same computer.
- **Explorer-map root / shared cache folder** optionally supplies offline maps
  and a shared entity-cache location. It is blank on a clean installation.
- **Write classical tags to files** controls whether the projected fields are
  persistent or available only as hidden Picard preview variables.
- **Persist retrieval statistics and plugin debug status** writes operational
  diagnostic fields. It is disabled by default.

Without an explorer-map root, the plugin keeps its entity cache in Picard's
normal user-data area. Cached entities expire after seven days.

## Documentation

- [Release history](CHANGELOG.md)
- [Tagging schema and field contract](TAGGING_SCHEMA.md)

## Compatibility

| Plugin series | Picard | Plugin API |
| --- | --- | --- |
| 0.2.x | 2.13.x | 2.0 |

Compatibility with a Picard generation is stated explicitly in every release.

## Development

The repository contains the readable plugin source and regression tests. The
installable ZIP attached to each release contains only the runtime package and
embedded user documentation.

Generated caches, Python bytecode, local MusicBrainz explorer output, and test
data are not part of the repository or release package.

## License

This project is licensed under the GNU General Public License, version 2 or (at
your option) any later version. See [LICENSE](LICENSE).

