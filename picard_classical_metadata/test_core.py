import unittest
import json

from core import SemanticMapProjector


def relation(source, target, direction="backward", order=None, attributes=None):
    return {
        "source": {"type": "work", "id": source, "name": source},
        "relationship": {
            "type": "parts", "direction": direction,
            "ordering_key": order, "attributes": attributes or [],
        },
        "target": {"entity_type": "work", "id": target, "name": target},
    }


def performance(recording_id, work_id, partial=False):
    return {
        "source": {"type": "recording", "id": recording_id},
        "relationship_type": "performance",
        "target": {"entity_type": "work", "id": work_id, "name": work_id},
        "attributes_union": ["partial"] if partial else [],
        "preferred_assertion": {"relationship": {}},
        "assertions": [],
    }


class ProjectionTests(unittest.TestCase):
    def test_explicit_vocal_choir_is_also_an_ensemble(self):
        projector = SemanticMapProjector({'recordings': [], 'works': []})
        tags = projector._credit_tags({'reconciled': {'credits': [{
            'relationship_type': 'vocal',
            'target': {'id': 'choir', 'name': 'Vocal Group', 'musicbrainz_type': 'Choir'},
            'attributes_union': ['choir vocals'],
        }]}})
        self.assertEqual(tags['classical_ensemble'], ['Vocal Group'])
        self.assertEqual(tags['classical_performer'], ['Vocal Group'])
        self.assertEqual(tags['classical_performer_instrument'], ['Vocal Group [choir vocals]'])

    def setUp(self):
        self.semantic = {
            "release": {"identity": {"id": "release"}},
            "works": [
                {"identity": {"id": "cycle", "title": "Cycle"}, "provenance": []},
                {"identity": {"id": "opera", "title": "Opera", "type": "Opera"}, "provenance": []},
                {"identity": {"id": "act", "title": "Act I"}, "provenance": []},
                {"identity": {"id": "scene", "title": "Scene I"}, "provenance": ["performed"]},
                {
                    "identity": {"id": "source", "title": "Earlier Theme"},
                    "provenance": ["contextual_source"],
                    "discovered_by": [{
                        "route": "contextual_source",
                        "source_work_id": "scene",
                        "relationship_type": "based on",
                        "source_confidence": "medium",
                    }],
                },
            ],
            "work_hierarchy": [
                relation("opera", "cycle", order=2),
                relation("act", "opera", order=1, attributes=["act"]),
                relation("scene", "act", order=3),
            ],
            "composition_metadata": [{
                "source": {"id": "scene"},
                "relationship": {"type": "composer"},
                "target": {"id": "composer", "name": "Composer"},
            }],
            "work_genealogy": {},
            "catalogs": [{
                "source": {"id": "opera"},
                "relationship": {"attribute_values": {"number": "Op. 1"}},
                "target": {"name": "Catalogue"},
            }],
            "recordings": [
                {
                    "identity": {"id": "r1", "title": "Part 1"},
                    "tracks": [{"medium_position": 1, "track_position": 1}],
                    "reconciled": {
                        "performed_works": [performance("r1", "scene", True), performance("r1", "act")],
                        "credits": [], "session": [],
                    },
                },
                {
                    "identity": {"id": "r2", "title": "Part 2"},
                    "tracks": [{"medium_position": 1, "track_position": 2}],
                    "reconciled": {
                        "performed_works": [performance("r2", "scene", True)],
                        "credits": [], "session": [],
                    },
                },
                {
                    "identity": {"id": "r3", "title": "Unlinked"},
                    "tracks": [{"medium_position": 1, "track_position": 3}],
                    "reconciled": {
                        "performed_works": [], "credits": [], "session": [],
                    },
                },
            ],
        }
        self.projector = SemanticMapProjector(self.semantic)

    def test_most_specific_work_and_path(self):
        tags = self.projector.project_recording("r1")
        self.assertEqual(tags["classical_work"], "Scene I")
        self.assertEqual(tags["classical_work_path"], ["Cycle", "Opera", "Act I", "Scene I"])
        self.assertEqual(tags["classical_broader_work_assignment"], ["Act I"])
        self.assertEqual(tags["classical_part_order"], "3")

    def test_partial_segment_and_source(self):
        first = self.projector.project_recording("r1")
        second = self.projector.project_recording("r2")
        self.assertEqual(first["classical_partial"], "1")
        self.assertEqual(first["classical_segment_index"], "1")
        self.assertEqual(second["classical_segment_index"], "2")
        self.assertEqual(second["classical_segment_count"], "2")
        self.assertEqual(first["classical_source_work"], ["Earlier Theme"])

    def test_roles_and_catalog(self):
        tags = self.projector.project_recording("r1")
        self.assertEqual(tags["classical_composer"], ["Composer"])
        self.assertEqual(tags["classical_catalog"], ["Catalogue: Op. 1"])

    def test_unresolved_is_not_guessed(self):
        tags = self.projector.project_recording("r3")
        self.assertEqual(tags["classical_status"], "unresolved_work")
        self.assertNotIn("classical_work", tags)

    def test_branch_preserves_both_paths_without_selecting_translation(self):
        self.semantic['works'].append({'identity': {
            'id': 'translation', 'title': 'German Opera',
            'disambiguation': 'German translation'}})
        self.semantic['work_hierarchy'].append(relation('scene', 'translation'))
        tags = SemanticMapProjector(self.semantic).project_recording('r1')
        self.assertEqual(tags['classical_status'], 'resolved')
        self.assertEqual(tags['classical_hierarchy_status'], 'branched')
        self.assertEqual(tags['classical_parent_work_id'], ['act', 'translation'])
        self.assertNotIn('classical_work_path', tags)
        paths = json.loads(tags['classical_work_paths'])['paths']
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[1]['works'][0]['disambiguation'], 'German translation')
        self.assertEqual(tags['classical_work_path_summary'], [
            'Cycle → Opera → Act I → Scene I',
            'German Opera [German translation] → Scene I'])

    def test_compact_document_on_unique_path_preserves_duplicate_titles(self):
        self.semantic['works'][0]['identity'].update(
            title='Opera', aliases=[{'name': 'Unwanted alias'}], annotation='Unwanted note')
        tags = SemanticMapProjector(self.semantic).project_recording('r1')
        document = json.loads(tags['classical_work_paths'])
        self.assertEqual(document['status'], 'unique')
        self.assertEqual(len(document['paths']), 1)
        self.assertEqual(tags['classical_work_path'][:2], ['Opera', 'Opera'])
        self.assertEqual(len(tags['classical_work_path']), len(tags['classical_work_path_id']))
        self.assertEqual(document['paths'][0]['works'][0], {'id': 'cycle', 'title': 'Opera'})
        self.assertNotIn('Unwanted', tags['classical_work_paths'])
        self.assertEqual(document['paths'][0]['edges'][0]['parent_id'], 'cycle')
        self.assertNotIn('id', document['paths'][0]['edges'][0])

    def test_cycle_is_incomplete_and_finite(self):
        self.semantic['work_hierarchy'].append(relation('cycle', 'scene'))
        tags = SemanticMapProjector(self.semantic).project_recording('r1')
        self.assertEqual(tags['classical_hierarchy_status'], 'incomplete')
        self.assertNotIn('classical_work_depth', tags)

    def test_source_evidence_identifies_owner(self):
        tags = self.projector.project_recording('r1')
        evidence = json.loads(tags['classical_source_evidence'][0])
        self.assertEqual(evidence['owner_work_id'], 'scene')
        self.assertEqual(evidence['scope'], 'performed_work')


if __name__ == "__main__":
    unittest.main()
