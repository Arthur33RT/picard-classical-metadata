"""Exercise the Picard adapter without requiring Qt or an installed Picard.

The settings double deliberately has no dict.get(): this reproduces Picard 2's
ConfigSection API and catches the integration bug fixed in 0.1.3.
"""

import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


class PicardSettings:
    def __init__(self):
        self.values = {
            "classical_metadata_enabled": True,
            "classical_metadata_map_root": "",
            "classical_metadata_persist_tags": True,
            "classical_metadata_auto_retrieve": True,
            "classical_metadata_persist_diagnostics": False,
            "classical_metadata_local_mirror_acceleration": False,
            "classical_metadata_local_mirror_concurrency": 8,
            "classical_metadata_local_mirror_delay_ms": 10,
        }

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value


class DeletionMetadata(dict):
    """Picard-style deletion tombstones, even for absent track keys."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deleted_tags = set()

    def __delitem__(self, name):
        self.pop(name, None)
        self.deleted_tags.add(name)

    def __setitem__(self, name, value):
        super().__setitem__(name, value)
        self.deleted_tags.discard(name)


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = PicardSettings()
        picard = ModuleType("picard")
        picard.config = SimpleNamespace(setting=cls.settings)
        picard.log = SimpleNamespace(
            debug=lambda *a, **k: None,
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        )
        config = ModuleType("picard.config")
        config.BoolOption = config.IntOption = config.TextOption = lambda *a: None
        metadata = ModuleType("picard.metadata")
        metadata.register_track_metadata_processor = lambda *a, **k: None
        metadata.register_album_metadata_processor = lambda *a, **k: None
        const = ModuleType('picard.const')
        const.USER_PLUGIN_DIR = tempfile.gettempdir()
        plugin = ModuleType("picard.plugin")
        plugin.PluginPriority = SimpleNamespace(LOW=-100)
        options = ModuleType("picard.ui.options")
        options.OptionsPage = object
        options.register_options_page = lambda *a: None
        ratecontrol = SimpleNamespace(
            REQUEST_DELAY_MINIMUM={}, REQUEST_DELAY={},
            CONGESTION_WINDOW_SIZE={},
        )
        ratecontrol.set_minimum_delay = (
            lambda hostkey, delay: ratecontrol.REQUEST_DELAY_MINIMUM.__setitem__(hostkey, delay)
        )
        webservice = ModuleType("picard.webservice")
        webservice.ratecontrol = ratecontrol
        cls.ratecontrol = ratecontrol
        widgets = ModuleType("PyQt5.QtWidgets")
        for name in (
            "QCheckBox", "QFileDialog", "QHBoxLayout", "QLabel", "QLineEdit",
            "QPushButton", "QSpinBox", "QVBoxLayout", "QWidget",
        ):
            setattr(widgets, name, object)
        cls.modules = patch.dict(sys.modules, {
            "picard": picard,
            "picard.config": config,
            "picard.metadata": metadata,
            "picard.const": const,
            "picard.plugin": plugin,
            "picard.ui": ModuleType("picard.ui"),
            "picard.ui.options": options,
            "picard.webservice": webservice,
            "PyQt5": ModuleType("PyQt5"),
            "PyQt5.QtWidgets": widgets,
        })
        cls.modules.start()
        folder = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "adapter_test_plugin", folder / "__init__.py",
            submodule_search_locations=[str(folder)],
        )
        cls.adapter = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.adapter
        spec.loader.exec_module(cls.adapter)

    @classmethod
    def tearDownClass(cls):
        cls.modules.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings["classical_metadata_enabled"] = True
        self.settings["classical_metadata_auto_retrieve"] = True
        self.settings["classical_metadata_persist_tags"] = True
        self.settings["classical_metadata_persist_diagnostics"] = False
        self.settings["classical_metadata_local_mirror_acceleration"] = False
        self.settings["classical_metadata_local_mirror_concurrency"] = 8
        self.settings["classical_metadata_local_mirror_delay_ms"] = 10
        self.settings["classical_metadata_map_root"] = self.temp.name
        self.adapter.MAP_CACHE = self.adapter.SemanticMapCache()
        self.adapter.BROKERS.clear()
        self.adapter._RATECONTROL_ORIGINALS.clear()
        self.ratecontrol.REQUEST_DELAY_MINIMUM.clear()
        self.ratecontrol.REQUEST_DELAY.clear()
        self.ratecontrol.CONGESTION_WINDOW_SIZE.clear()
        folder = Path(self.temp.name) / "release"
        folder.mkdir()
        self.map_file = folder / "SEMANTIC_MAP.json"
        self.map_file.write_text(json.dumps({
            "release": {"identity": {"id": "release"}},
            "recordings": [{"identity": {"id": "recording"}}],
            "works": [],
        }), encoding="utf-8")

    def process(self, metadata):
        self.adapter.process_track(
            SimpleNamespace(id="release"), metadata,
            {"id": "track", "recording": {"id": "recording"}},
            {"id": "release"},
        )

    def test_picard_settings_and_persistent_projection(self):
        metadata = {"musicbrainz_albumid": "release", "musicbrainz_recordingid": "recording"}
        self.process(metadata)
        self.assertEqual(metadata["~classical_plugin_status"], "projected:unresolved_work")
        self.assertEqual(metadata["classical_schema_version"], "1.1")
        self.assertEqual(metadata["classical_retrieval_status"], "cached_map")

    def test_plugin_imports_directly_from_zip(self):
        package_name = 'zip_install_test_plugin'
        folder = Path(self.adapter.__file__).resolve().parent
        archive = Path(self.temp.name) / 'plugin.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
            for name in ('__init__.py', 'core.py', 'retrieval.py', 'semantic.py',
                         'tag_schema.json'):
                stream.write(folder / name, package_name + '/' + name)
        sys.path.insert(0, str(archive))
        try:
            module = importlib.import_module(package_name)
            self.assertEqual(len(module.OWNED_TAGS), 70)
            self.assertEqual(module.PLUGIN_VERSION, '0.2.7')
        finally:
            sys.path.remove(str(archive))
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + '.'):
                    del sys.modules[name]

    def test_operational_diagnostics_are_opt_in(self):
        album = SimpleNamespace(
            id="release", _classical_retrieval_status="complete",
            _classical_diagnostics={"elapsed_seconds": 1.25, "network_requests": 3,
                                    "cache_hits": 4, "coalesced_lookups": 2},
            _classical_errors=[],
        )
        metadata = {"musicbrainz_albumid": "release", "musicbrainz_recordingid": "recording"}
        self.adapter.process_track(album, metadata, {}, {})
        self.assertEqual(metadata["classical_retrieval_status"], "complete")
        self.assertNotIn("classical_retrieval_elapsed_seconds", metadata)
        self.assertEqual(metadata["~classical_plugin_status"], "projected:unresolved_work")

        self.settings["classical_metadata_persist_diagnostics"] = True
        metadata = DeletionMetadata(
            musicbrainz_albumid="release", musicbrainz_recordingid="recording"
        )
        self.adapter.process_track(album, metadata, {}, {})
        self.assertEqual(metadata["classical_plugin_status"], "projected:unresolved_work")
        self.assertNotIn("classical_plugin_status", metadata.deleted_tags)
        self.assertEqual(metadata["classical_retrieval_elapsed_seconds"], "1.25")
        self.assertEqual(metadata["classical_retrieval_network_requests"], "3")

    def test_disabling_diagnostics_removes_previous_persisted_values(self):
        metadata = DeletionMetadata(
            classical_plugin_status="old",
            classical_retrieval_elapsed_seconds="9.9",
            classical_retrieval_network_requests="99",
        )
        self.adapter._write_tags(metadata, {
            "classical_status": "resolved", "classical_retrieval_status": "complete"
        })
        self.assertIn("classical_plugin_status", metadata.deleted_tags)
        self.assertIn("classical_retrieval_elapsed_seconds", metadata.deleted_tags)
        self.assertIn("classical_retrieval_network_requests", metadata.deleted_tags)

    def test_refresh_deletes_disk_only_obsolete_tags(self):
        metadata = DeletionMetadata(title='Title', composer='Composer',
                                    classical_personal_note='Keep me')
        tags = {'classical_status': 'resolved', 'classical_hierarchy_status': 'branched',
                'classical_work_paths': ['path1', 'path2']}
        self.adapter._write_tags(metadata, tags)
        disk = {'classical_work_path': 'Old German path', 'classical_work_depth': '1',
                'classical_part_order': '1', 'classical_performer_role': 'Old cast',
                'title': 'Title', 'composer': 'Composer',
                'classical_personal_note': 'Keep me'}
        for name in metadata.deleted_tags:
            disk.pop(name, None)
        disk.update(metadata)
        for name in ('classical_work_path', 'classical_work_depth', 'classical_part_order',
                     'classical_performer_role'):
            self.assertNotIn(name, disk)
        self.assertEqual(disk['classical_work_paths'], ['path1', 'path2'])
        self.assertEqual(disk['classical_personal_note'], 'Keep me')
        self.assertEqual(disk['composer'], 'Composer')

    def test_replacement_resets_values_and_removes_hidden_preview(self):
        metadata = DeletionMetadata(classical_performer=['Old'],
                                    **{'~classical_work_path': ['Old hidden']})
        self.adapter._write_tags(metadata, {'classical_performer': ['New']})
        self.assertEqual(metadata['classical_performer'], ['New'])
        self.assertNotIn('classical_performer', metadata.deleted_tags)
        self.assertNotIn('~classical_work_path', metadata)

    def test_hidden_preview_does_not_delete_saved_tags(self):
        self.settings['classical_metadata_persist_tags'] = False
        metadata = DeletionMetadata(classical_work_path=['Saved'])
        self.adapter._write_tags(metadata, {'classical_hierarchy_status': 'branched'})
        self.assertEqual(metadata['classical_work_path'], ['Saved'])
        self.assertNotIn('classical_work_path', metadata.deleted_tags)
        self.assertEqual(metadata['~classical_hierarchy_status'], 'branched')

    def test_disabled_projection_leaves_owned_values_alone(self):
        self.settings['classical_metadata_enabled'] = False
        metadata = DeletionMetadata(classical_work='Saved')
        self.process(metadata)
        self.assertEqual(metadata['classical_work'], 'Saved')
        self.assertNotIn('classical_work', metadata.deleted_tags)

    def test_missing_map_leaves_owned_values_alone(self):
        self.map_file.unlink()
        metadata = DeletionMetadata(classical_work='Saved')
        self.process(metadata)
        self.assertEqual(metadata['classical_work'], 'Saved')
        self.assertNotIn('classical_work', metadata.deleted_tags)

    def test_partial_or_unmatched_projection_does_not_prune_saved_facts(self):
        for tags in ({'classical_status': 'recording_not_in_semantic_map'},
                     {'classical_status': 'resolved', 'classical_retrieval_status': 'partial'}):
            metadata = DeletionMetadata(classical_work='Saved')
            self.adapter._write_tags(metadata, tags)
            self.assertEqual(metadata['classical_work'], 'Saved')
            self.assertNotIn('classical_work', metadata.deleted_tags)

    def test_hidden_projection_and_node_id_fallback(self):
        self.settings["classical_metadata_persist_tags"] = False
        metadata = {}
        self.process(metadata)
        self.assertEqual(metadata["~classical_plugin_status"], "projected:unresolved_work")
        self.assertNotIn("classical_schema_version", metadata)
        self.assertEqual(metadata["~classical_recording_id"], "recording")

    def test_missing_map_status_is_visible(self):
        self.map_file.unlink()
        metadata = {}
        self.process(metadata)
        self.assertEqual(metadata["~classical_plugin_status"], "semantic_map_not_found")

    def test_runtime_error_status_is_visible(self):
        self.map_file.write_text("not JSON", encoding="utf-8")
        metadata = {}
        self.process(metadata)
        self.assertEqual(metadata["~classical_plugin_status"], "projection_error:JSONDecodeError")

    def test_disabled_status_is_visible(self):
        self.settings["classical_metadata_enabled"] = False
        metadata = {}
        self.process(metadata)
        self.assertEqual(metadata["~classical_plugin_status"], "disabled")

    def make_album(self, server='http://localhost:5000/ws/2'):
        pending, finalized = [], []
        api = SimpleNamespace(
            base_url=SimpleNamespace(toString=lambda: server),
            get=lambda path, callback, **kw: pending.append((path, callback, kw)),
        )
        tagger = SimpleNamespace(mb_api=api, albums={})
        album = SimpleNamespace(id='release', tagger=tagger, _requests=1, loaded=False,
                                _finalize_loading=lambda error: finalized.append(error))
        tagger.albums['release'] = album
        return album, pending, finalized

    def test_loopback_acceleration_applies_configured_profile(self):
        self.settings['classical_metadata_local_mirror_acceleration'] = True
        album, pending, finalized = self.make_album()
        self.adapter.process_album(album, {}, {})
        broker = next(iter(self.adapter.BROKERS.values()))
        self.assertEqual(broker.concurrency, 8)
        self.assertEqual(self.ratecontrol.REQUEST_DELAY_MINIMUM[('localhost', 5000)], 10)
        self.assertEqual(self.ratecontrol.REQUEST_DELAY[('localhost', 5000)], 10)
        self.assertEqual(self.ratecontrol.CONGESTION_WINDOW_SIZE[('localhost', 5000)], 8.0)
        _, callback, _ = pending.pop()
        callback(None, None, 'offline')
        self.assertEqual(finalized, [None])
        self.assertTrue(album._classical_diagnostics['local_mirror_acceleration'])
        self.assertEqual(album._classical_diagnostics['request_delay_ms'], 10)
        self.assertEqual(album._classical_diagnostics['concurrency'], 8)

        self.settings['classical_metadata_local_mirror_acceleration'] = False
        other, pending, finalized = self.make_album()
        self.adapter.process_album(other, {}, {})
        self.assertEqual(self.ratecontrol.REQUEST_DELAY_MINIMUM[('localhost', 5000)], 1000)
        self.assertEqual(self.ratecontrol.REQUEST_DELAY[('localhost', 5000)], 1000)
        self.assertEqual(self.ratecontrol.CONGESTION_WINDOW_SIZE[('localhost', 5000)], 1.0)
        _, callback, _ = pending.pop()
        callback(None, None, 'offline')

    def test_loopback_url_detection_has_no_optional_dependency(self):
        cases = {
            'http://localhost:5000/ws/2': ('localhost', 5000),
            'http://localhost./ws/2': ('localhost', 80),
            'http://127.0.0.25:5000/ws/2': ('127.0.0.25', 5000),
            'http://[::1]:5000/ws/2': ('::1', 5000),
            'http://[0:0:0:0:0:0:0:1]/ws/2': ('0:0:0:0:0:0:0:1', 80),
            'https://musicbrainz.org/ws/2': None,
            'http://192.168.1.2:5000/ws/2': None,
            'not a url': None,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(self.adapter._loopback_hostkey(url), expected)

    def test_acceleration_is_ignored_for_remote_server(self):
        self.settings['classical_metadata_local_mirror_acceleration'] = True
        album, pending, finalized = self.make_album('https://musicbrainz.org/ws/2')
        self.adapter.process_album(album, {}, {})
        broker = next(iter(self.adapter.BROKERS.values()))
        self.assertEqual(broker.concurrency, 4)
        self.assertFalse(self.ratecontrol.REQUEST_DELAY_MINIMUM)
        _, callback, _ = pending.pop()
        callback(None, None, 'offline')
        self.assertEqual(finalized, [None])

    def test_async_album_hold_and_projection(self):
        album, pending, finalized = self.make_album()
        self.adapter.process_album(album, {}, {'id': 'release'})
        self.assertEqual(album._requests, 2)
        self.assertFalse(finalized)
        path, callback, kw = pending.pop()
        self.assertEqual(path, '/release/release')
        self.assertEqual(kw['parse_response_type'], 'json')
        callback({'id': 'release', 'media': [{'tracks': [
            {'recording': {'id': 'recording'}}]}]}, None, None)
        path, callback, kw = pending.pop()
        callback({'id': 'recording', 'relations': []}, None, None)
        self.assertEqual(album._requests, 1)
        self.assertEqual(finalized, [None])
        metadata = {'musicbrainz_albumid': 'release', 'musicbrainz_recordingid': 'recording'}
        self.adapter.process_track(album, metadata, {}, {})
        self.assertEqual(metadata['classical_retrieval_status'], 'complete')
        self.assertEqual(metadata['~classical_plugin_status'], 'projected:unresolved_work')

    def test_failed_retrieval_falls_back_and_releases_hold(self):
        album, pending, finalized = self.make_album()
        self.adapter.process_album(album, {}, {})
        _, callback, _ = pending.pop()
        callback(None, SimpleNamespace(errorString=lambda: 'offline'), 1)
        self.assertEqual(album._requests, 1)
        self.assertEqual(album._classical_retrieval_status, 'fallback_cached_map')
        self.assertEqual(finalized, [None])

    def test_removed_album_does_not_finalize(self):
        album, pending, finalized = self.make_album()
        self.adapter.process_album(album, {}, {})
        album.tagger.albums.clear()
        _, callback, _ = pending.pop()
        callback(None, None, 'offline')
        self.assertEqual(album._requests, 1)
        self.assertFalse(finalized)


if __name__ == "__main__":
    unittest.main()
