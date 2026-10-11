"""Classical Metadata semantic-map projection engine.

This module deliberately has no Picard imports.  It converts a MusicBrainz
Release X-Ray V2.5 semantic map into deterministic per-recording tags.  Picard,
command-line tools, and future dashboard code can therefore share one policy.
"""

from collections import defaultdict
import json


SCHEMA_VERSION = "1.1"
TAG_PREFIX = "classical_"


def _list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _unique(values):
    seen = set()
    result = []
    for value in values:
        if value is None:
            continue
        value = str(value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _relation_parent_child(entry):
    """Return (parent_id, child_id, order, attributes), or None."""
    source = entry.get("source") or {}
    target = entry.get("target") or {}
    relation = entry.get("relationship") or {}
    if source.get("type") != "work" or target.get("entity_type") != "work":
        return None
    source_id = source.get("id")
    target_id = target.get("id")
    if not source_id or not target_id:
        return None
    relation_type = str(relation.get("type") or "").casefold()
    direction = str(relation.get("direction") or "").casefold()
    if relation_type == "parts":
        parent_id, child_id = (
            (target_id, source_id) if direction == "backward"
            else (source_id, target_id)
        )
    elif relation_type == "part of":
        parent_id, child_id = (
            (source_id, target_id) if direction == "forward"
            else (target_id, source_id)
        )
    else:
        return None
    return (
        parent_id,
        child_id,
        relation.get("ordering_key"),
        _list(relation.get("attributes")),
    )


class SemanticMapProjector:
    """Index a V2.5 semantic map and project one recording at a time."""

    def __init__(self, semantic_map):
        self.data = semantic_map
        self.release_id = (
            (semantic_map.get("release") or {}).get("identity") or {}
        ).get("id")
        self.works = {
            (item.get("identity") or {}).get("id"): item
            for item in semantic_map.get("works", [])
            if (item.get("identity") or {}).get("id")
        }
        self.recordings = {
            (item.get("identity") or {}).get("id"): item
            for item in semantic_map.get("recordings", [])
            if (item.get("identity") or {}).get("id")
        }
        self.parents = defaultdict(list)
        self.children = defaultdict(list)
        self._index_hierarchy()
        self.composition_roles = self._index_composition_roles()
        self.creative_roles = self._index_creative_roles()
        self.catalogs = self._index_catalogs()
        self.sources = self._index_sources()
        self.component_scope = self._index_component_scope()
        self.recording_work_ids = {
            recording_id: self._most_specific_work_ids(
                self._assigned_work_ids(recording)
            )
            for recording_id, recording in self.recordings.items()
        }
        self.work_recording_order = self._index_work_recording_order()

    def _index_hierarchy(self):
        seen = set()
        for entry in self.data.get("work_hierarchy", []):
            edge = _relation_parent_child(entry)
            if not edge:
                continue
            parent_id, child_id, order, attributes = edge
            key = (parent_id, child_id, order, tuple(attributes))
            if key in seen:
                continue
            seen.add(key)
            self.parents[child_id].append({
                "id": parent_id, "order": order, "attributes": attributes,
            })
            self.children[parent_id].append({
                "id": child_id, "order": order, "attributes": attributes,
            })

    def _index_composition_roles(self):
        roles = defaultdict(lambda: defaultdict(list))
        supported = {"composer", "librettist", "lyricist", "writer", "translator"}
        for entry in self.data.get("composition_metadata", []):
            source_id = (entry.get("source") or {}).get("id")
            relation_type = str(
                (entry.get("relationship") or {}).get("type") or ""
            ).casefold()
            target = entry.get("target") or {}
            if source_id and relation_type in supported and target.get("name"):
                roles[source_id][relation_type].append(target)
        return roles

    def _index_creative_roles(self):
        roles = defaultdict(lambda: defaultdict(list))
        genealogy = self.data.get("work_genealogy") or {}
        for group in genealogy.values():
            for entry in group:
                source_id = (entry.get("source") or {}).get("id")
                relation = entry.get("relationship") or {}
                relation_type = str(relation.get("type") or "").casefold()
                target = entry.get("target") or {}
                if (
                    source_id
                    and target.get("entity_type") == "artist"
                    and relation_type in {"arranger", "orchestrator"}
                    and target.get("name")
                ):
                    roles[source_id][relation_type].append(target)
        return roles

    def _index_catalogs(self):
        result = defaultdict(list)
        for entry in self.data.get("catalogs", []):
            source_id = (entry.get("source") or {}).get("id")
            authority = (entry.get("target") or {}).get("name")
            values = (entry.get("relationship") or {}).get("attribute_values") or {}
            numbers = []
            for key, value in values.items():
                if str(key).casefold() != "number":
                    continue
                numbers.extend(_list(value))
            if source_id and authority:
                for number in numbers:
                    result[source_id].append({
                        "authority": str(authority), "number": str(number),
                    })
        return result

    def _index_sources(self):
        result = defaultdict(list)
        for work_id, item in self.works.items():
            identity = item.get("identity") or {}
            for discovery in item.get("discovered_by", []):
                route = discovery.get("route")
                source_work_id = discovery.get("source_work_id")
                if route not in {"canonical_source", "contextual_source"}:
                    continue
                if not source_work_id:
                    continue
                result[source_work_id].append({
                    "id": work_id,
                    "title": identity.get("title"),
                    "kind": route,
                    "relationship": discovery.get("relationship_type"),
                    "confidence": discovery.get("source_confidence"),
                    "owner_work_id": source_work_id,
                    "direction": discovery.get("direction"),
                    "relationship_type_id": discovery.get("relationship_type_id"),
                    "supporting_evidence": discovery.get("source_evidence") or [],
                })
        return result

    def _index_component_scope(self):
        result = defaultdict(set)
        for work_id, item in self.works.items():
            if "performed_component" not in item.get("provenance", []):
                continue
            for discovery in item.get("discovered_by", []):
                if discovery.get("route") == "performed_component":
                    source_id = discovery.get("source_work_id")
                    if source_id:
                        result[source_id].add(work_id)
        return result

    def _assigned_work_facts(self, recording):
        return (
            (recording.get("reconciled") or {}).get("performed_works")
            or recording.get("performed_works")
            or []
        )

    def _assigned_work_ids(self, recording):
        return _unique(
            (fact.get("target") or {}).get("id")
            for fact in self._assigned_work_facts(recording)
        )

    def _ancestors(self, work_id):
        result = set()
        stack = [work_id]
        while stack:
            current = stack.pop()
            for parent in self.parents.get(current, []):
                parent_id = parent["id"]
                if parent_id in result or parent_id == work_id:
                    continue
                result.add(parent_id)
                stack.append(parent_id)
        return result

    def _most_specific_work_ids(self, work_ids):
        work_ids = _unique(work_ids)
        ancestors_by_work = {
            work_id: self._ancestors(work_id) for work_id in work_ids
        }
        return [
            work_id for work_id in work_ids
            if not any(
                work_id in ancestors_by_work[other_id]
                and other_id not in ancestors_by_work[work_id]
                for other_id in work_ids if other_id != work_id
            )
        ]

    def _hierarchy_paths(self, work_id):
        """Enumerate bounded paths without inventing a preferred edition."""
        stack = [(work_id, [work_id])]
        paths = set()
        incomplete = False
        states = 0
        while stack and states < 4096 and len(paths) < 128:
            current, reverse_path = stack.pop()
            states += 1
            parents = sorted({edge['id'] for edge in self.parents.get(current, [])})
            if not parents:
                paths.add(tuple(reversed(reverse_path)))
            elif len(reverse_path) >= 32:
                incomplete = True
                paths.add(tuple(reversed(reverse_path)))
            else:
                for parent in reversed(parents):
                    if parent in reverse_path:
                        incomplete = True
                        paths.add(tuple(reversed(reverse_path)))
                    else:
                        stack.append((parent, reverse_path + [parent]))
        return [list(path) for path in sorted(paths)], incomplete or bool(stack)

    def _best_path(self, work_id):
        paths, _ = self._hierarchy_paths(work_id)
        return paths[0] if paths else [work_id]

    def _work_identity(self, work_id):
        return (self.works.get(work_id) or {}).get("identity") or {"id": work_id}

    def _work_title(self, work_id):
        identity = self._work_identity(work_id)
        return str(identity.get("title") or work_id or "")

    def _edge_to_parent(self, child_id, parent_id):
        return next(
            (edge for edge in self.parents.get(child_id, []) if edge["id"] == parent_id),
            None,
        )

    def _track_key(self, recording):
        tracks = recording.get("tracks") or []
        if not tracks:
            return (10**9, 10**9, "")
        track = min(tracks, key=lambda item: (
            item.get("medium_position") or 10**9,
            item.get("track_position") or 10**9,
            str(item.get("track_number") or ""),
        ))
        return (
            track.get("medium_position") or 10**9,
            track.get("track_position") or 10**9,
            str(track.get("track_number") or ""),
        )

    def _index_work_recording_order(self):
        groups = defaultdict(list)
        for recording_id, work_ids in self.recording_work_ids.items():
            if len(work_ids) != 1:
                continue
            groups[work_ids[0]].append(recording_id)
        result = {}
        for work_id, recording_ids in groups.items():
            ordered = sorted(
                recording_ids, key=lambda item: self._track_key(self.recordings[item])
            )
            for index, recording_id in enumerate(ordered, start=1):
                result[(work_id, recording_id)] = (index, len(ordered))
        return result

    def _role_tags(self, path):
        tags = {}
        for role in ("composer", "librettist", "lyricist", "writer", "translator"):
            people = []
            ids = []
            for work_id in reversed(path):
                for target in self.composition_roles[work_id].get(role, []):
                    people.append(target.get("name"))
                    ids.append(target.get("id"))
            if people:
                tags[TAG_PREFIX + role] = _unique(people)
                tags[TAG_PREFIX + role + "_id"] = _unique(ids)
        for role in ("arranger", "orchestrator"):
            people = []
            ids = []
            for work_id in reversed(path):
                for target in self.creative_roles[work_id].get(role, []):
                    people.append(target.get("name"))
                    ids.append(target.get("id"))
            if people:
                tags[TAG_PREFIX + role] = _unique(people)
                tags[TAG_PREFIX + role + "_id"] = _unique(ids)
        return tags

    def _credit_tags(self, recording):
        tags = {}
        credits = (recording.get("reconciled") or {}).get("credits") or []
        conductors = []
        conductor_ids = []
        ensembles = []
        ensemble_ids = []
        performers = []
        performer_ids = []
        performer_roles = []
        performer_instruments = []
        production = defaultdict(list)
        for fact in credits:
            relation_type = str(fact.get("relationship_type") or "").casefold()
            target = fact.get("target") or {}
            name = target.get("name")
            target_id = target.get("id")
            if not name:
                continue
            if relation_type == "conductor":
                conductors.append(name)
                conductor_ids.append(target_id)
            elif relation_type in {"performing orchestra", "orchestra", "choir"}:
                ensembles.append(name)
                ensemble_ids.append(target_id)
            elif relation_type in {"vocal", "performer", "instrument"}:
                # Explicit MusicBrainz Choir/Orchestra subtype is evidence;
                # never infer an ensemble merely from words in the name.
                subtype = str(target.get('musicbrainz_type') or '').casefold()
                if subtype in {'choir', 'orchestra'}:
                    ensembles.append(name)
                    ensemble_ids.append(target_id)
                performers.append(name)
                performer_ids.append(target_id)
                attributes = fact.get("attributes_union") or []
                assertion = fact.get("preferred_assertion") or {}
                relation = assertion.get("relationship") or {}
                credits_map = relation.get("attribute_credits") or {}
                role_values = _unique(credits_map.values())
                performer_roles.extend(f"{name} [{role}]" for role in role_values)
                performer_instruments.extend(
                    f"{name} [{attribute}]" for attribute in attributes
                    if attribute and attribute not in role_values
                )
            elif relation_type in {"producer", "engineer", "balance", "editor", "mix", "mastering"}:
                production[relation_type].append(name)
        if conductors:
            tags[TAG_PREFIX + "conductor"] = _unique(conductors)
            tags[TAG_PREFIX + "conductor_id"] = _unique(conductor_ids)
        if ensembles:
            tags[TAG_PREFIX + "ensemble"] = _unique(ensembles)
            tags[TAG_PREFIX + "ensemble_id"] = _unique(ensemble_ids)
        if performers:
            tags[TAG_PREFIX + "performer"] = _unique(performers)
            tags[TAG_PREFIX + "performer_id"] = _unique(performer_ids)
        if performer_roles:
            tags[TAG_PREFIX + "performer_role"] = _unique(performer_roles)
        if performer_instruments:
            tags[TAG_PREFIX + "performer_instrument"] = _unique(performer_instruments)
        for role, people in production.items():
            tags[TAG_PREFIX + role] = _unique(people)
        return tags

    def _session_tags(self, recording):
        tags = {}
        sessions = (recording.get("reconciled") or {}).get("session") or []
        places = []
        place_ids = []
        areas = []
        begins = []
        ends = []
        for fact in sessions:
            target = fact.get("target") or {}
            entity_type = target.get("entity_type")
            if entity_type == "place":
                places.append(target.get("name"))
                place_ids.append(target.get("id"))
            elif entity_type == "area":
                areas.append(target.get("name"))
            relation = (
                (fact.get("preferred_assertion") or {}).get("relationship") or {}
            )
            begins.append(relation.get("begin"))
            ends.append(relation.get("end"))
        if places:
            tags[TAG_PREFIX + "recording_place"] = _unique(places)
            tags[TAG_PREFIX + "recording_place_id"] = _unique(place_ids)
        if areas:
            tags[TAG_PREFIX + "recording_area"] = _unique(areas)
        if begins:
            tags[TAG_PREFIX + "recording_date_begin"] = _unique(begins)
        if ends:
            tags[TAG_PREFIX + "recording_date_end"] = _unique(ends)
        return tags

    def project_recording(self, recording_id):
        recording = self.recordings.get(recording_id)
        if not recording:
            return {
                TAG_PREFIX + "schema_version": SCHEMA_VERSION,
                TAG_PREFIX + "status": "recording_not_in_semantic_map",
            }
        facts = self._assigned_work_facts(recording)
        assigned_ids = self._assigned_work_ids(recording)
        specific_ids = self.recording_work_ids.get(recording_id, [])
        tags = {
            TAG_PREFIX + "schema_version": SCHEMA_VERSION,
            TAG_PREFIX + "release_id": self.release_id,
            TAG_PREFIX + "recording_id": recording_id,
        }
        if not specific_ids:
            tags[TAG_PREFIX + "status"] = "unresolved_work"
            tags.update(self._credit_tags(recording))
            tags.update(self._session_tags(recording))
            return tags

        ambiguous = len(specific_ids) > 1
        primary_id = specific_ids[0]
        paths, incomplete = self._hierarchy_paths(primary_id)
        paths = paths or [[primary_id]]
        # Only shared ancestors may supply inherited scalar facts on a branch.
        shared = set(paths[0]).intersection(*(set(item) for item in paths[1:]))
        path = [item for item in paths[0] if item in shared]
        identities = [self._work_identity(work_id) for work_id in path]
        tags[TAG_PREFIX + "status"] = "ambiguous_work" if ambiguous else "resolved"
        tags[TAG_PREFIX + "work"] = self._work_title(primary_id)
        tags[TAG_PREFIX + "work_id"] = primary_id
        primary_type = self._work_identity(primary_id).get("type")
        if primary_type:
            tags[TAG_PREFIX + "work_type"] = primary_type
        tags[TAG_PREFIX + "work_path"] = [self._work_title(item) for item in path]
        tags[TAG_PREFIX + "work_path_id"] = list(path)
        tags[TAG_PREFIX + "work_depth"] = str(max(0, len(path) - 1))
        tags[TAG_PREFIX + "hierarchy_status"] = (
            'incomplete' if incomplete else 'branched' if len(paths) > 1 else 'unique'
        )
        tags[TAG_PREFIX + "hierarchy_path_count"] = str(len(paths))
        summaries = []
        structured_paths = []
        for candidate in paths:
            works = []
            labels = []
            for item in candidate:
                identity = self._work_identity(item)
                work = {'id': item, 'title': self._work_title(item)}
                for key in ('disambiguation', 'type', 'languages'):
                    if identity.get(key):
                        work[key] = identity[key]
                works.append(work)
                label = work['title']
                if work.get('disambiguation'):
                    label += ' [' + work['disambiguation'] + ']'
                labels.append(label)
            edges = []
            for parent, child in zip(candidate, candidate[1:]):
                # Retain every distinct relationship between these nodes.
                for edge in sorted(self.parents.get(child, []),
                                   key=lambda item: json.dumps(item, sort_keys=True)):
                    if edge['id'] == parent:
                        edges.append({'parent_id': parent, 'child_id': child,
                                      'order': edge['order'], 'attributes': edge['attributes']})
            structured_paths.append({'works': works, 'edges': edges})
            summaries.append(' → '.join(labels))
        tags[TAG_PREFIX + 'work_path_summary'] = summaries
        # One JSON document, not multiple JSON values joined with semicolons.
        tags[TAG_PREFIX + 'work_paths'] = json.dumps(
            {'paths': structured_paths, 'status': tags[TAG_PREFIX + 'hierarchy_status']},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if len(paths) > 1 or incomplete:
            for suffix in ('work_path', 'work_path_id', 'work_depth'):
                tags.pop(TAG_PREFIX + suffix, None)
        if len(paths) == 1 and not incomplete and len(path) > 1:
            tags[TAG_PREFIX + "parent_work"] = self._work_title(path[-2])
            tags[TAG_PREFIX + "parent_work_id"] = path[-2]
            edge = self._edge_to_parent(primary_id, path[-2])
            if edge and edge.get("order") is not None:
                tags[TAG_PREFIX + "part_order"] = str(edge["order"])
        elif self.parents.get(primary_id):
            parent_ids = sorted({edge['id'] for edge in self.parents[primary_id]})
            tags[TAG_PREFIX + 'parent_work'] = [self._work_title(item) for item in parent_ids]
            tags[TAG_PREFIX + 'parent_work_id'] = parent_ids
        if ambiguous:
            tags[TAG_PREFIX + "candidate_work"] = [
                self._work_title(work_id) for work_id in specific_ids
            ]
            tags[TAG_PREFIX + "candidate_work_id"] = specific_ids

        partial_ids = {
            (fact.get("target") or {}).get("id")
            for fact in facts
            if "partial" in {
                str(value).casefold() for value in fact.get("attributes_union", [])
            }
        }
        tags[TAG_PREFIX + "partial"] = "1" if primary_id in partial_ids else "0"
        segment = self.work_recording_order.get((primary_id, recording_id))
        if segment and segment[1] > 1:
            tags[TAG_PREFIX + "segment_index"] = str(segment[0])
            tags[TAG_PREFIX + "segment_count"] = str(segment[1])

        tags.update(self._role_tags(path))
        tags.update(self._credit_tags(recording))
        tags.update(self._session_tags(recording))

        catalog_values = []
        for work_id in reversed(path):
            for catalog in self.catalogs.get(work_id, []):
                catalog_values.append(
                    f"{catalog['authority']}: {catalog['number']}"
                )
        if catalog_values:
            tags[TAG_PREFIX + "catalog"] = _unique(catalog_values)

        source_scope = {item for candidate in paths for item in candidate}
        for work_id in list(source_scope):
            source_scope.update(self.component_scope.get(work_id, set()))
        source_items = []
        for work_id in sorted(source_scope):
            source_items.extend(self.sources.get(work_id, []))
        if source_items:
            tags[TAG_PREFIX + 'source_evidence'] = [json.dumps(dict(
                item, scope='performed_work' if item['owner_work_id'] == primary_id
                else 'ancestor' if any(item['owner_work_id'] in candidate for candidate in paths)
                else 'performed_component'), ensure_ascii=False, sort_keys=True)
                for item in source_items]
            tags[TAG_PREFIX + "source_work"] = _unique(
                item.get("title") for item in source_items
            )
            tags[TAG_PREFIX + "source_work_id"] = _unique(
                item.get("id") for item in source_items
            )
            tags[TAG_PREFIX + "source_kind"] = _unique(
                item.get("kind") for item in source_items
            )
            tags[TAG_PREFIX + "source_confidence"] = _unique(
                item.get("confidence") for item in source_items
            )

        if assigned_ids != specific_ids:
            tags[TAG_PREFIX + "broader_work_assignment"] = _unique(
                self._work_title(work_id)
                for work_id in assigned_ids if work_id not in specific_ids
            )
            tags[TAG_PREFIX + "broader_work_assignment_id"] = _unique(
                work_id for work_id in assigned_ids if work_id not in specific_ids
            )
        return tags

    def project_release(self):
        return {
            "format": "Classical Metadata Tag Projection",
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "recordings": {
                recording_id: self.project_recording(recording_id)
                for recording_id in self.recordings
            },
        }
