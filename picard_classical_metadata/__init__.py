"""MusicBrainz Picard 2 adapter for the Classical Metadata plugin."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from PyQt5.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

from picard import config, log
from picard.config import BoolOption, IntOption, TextOption
from picard.metadata import register_track_metadata_processor, register_album_metadata_processor
from picard.const import USER_PLUGIN_DIR
from picard.plugin import PluginPriority
from picard.ui.options import OptionsPage, register_options_page

from .core import SemanticMapProjector
from .retrieval import EntityBroker, ReleaseCrawl, INCLUDES


PLUGIN_NAME = "Classical Metadata"
PLUGIN_AUTHOR = "Classical Metadata Project"
PLUGIN_DESCRIPTION = (
    "Automatically retrieves focused classical metadata using Picard's "
    "MusicBrainz server, with shared caching and classical_* tags. Existing "
    "Release X-Ray V2.5 maps remain available as an offline fallback."
)
PLUGIN_VERSION = "0.2.7"
PLUGIN_API_VERSIONS = ["2.0"]
PLUGIN_LICENSE = "GPL-2.0-or-later"
PLUGIN_LICENSE_URL = "https://www.gnu.org/licenses/gpl-2.0.html"


OPT_ENABLED = "classical_metadata_enabled"
OPT_MAP_ROOT = "classical_metadata_map_root"
OPT_PERSIST = "classical_metadata_persist_tags"
OPT_AUTO = "classical_metadata_auto_retrieve"
OPT_DIAGNOSTICS = "classical_metadata_persist_diagnostics"
OPT_LOCAL_ACCEL = "classical_metadata_local_mirror_acceleration"
OPT_LOCAL_CONCURRENCY = "classical_metadata_local_mirror_concurrency"
OPT_LOCAL_DELAY_MS = "classical_metadata_local_mirror_delay_ms"

DEFAULT_CONCURRENCY = 4
_RATECONTROL_ORIGINALS = {}


class SemanticMapCache:
    def __init__(self):
        self.root = None
        self.paths = {}
        self.projectors = {}

    def _set_root(self, root):
        root = str(root or "").strip()
        if root != self.root:
            self.root = root
            self.paths.clear()
            self.projectors.clear()

    def _find(self, release_id):
        self._set_root(config.setting[OPT_MAP_ROOT])
        if not self.root or not release_id:
            return None
        if release_id in self.paths:
            return self.paths[release_id]
        root = Path(self.root).expanduser()
        candidates = [
            root / release_id / "SEMANTIC_MAP.json",
            root / (release_id + " - default") / "SEMANTIC_MAP.json",
        ]
        path = next((item for item in candidates if item.is_file()), None)
        if path is None and root.is_dir():
            path = next(
                (
                    item for item in root.rglob("SEMANTIC_MAP.json")
                    if item.parent.name.startswith(release_id)
                ),
                None,
            )
        self.paths[release_id] = path
        return path

    def projector(self, release_id):
        path = self._find(release_id)
        if path is None:
            return None
        cache_key = (release_id, str(path), path.stat().st_mtime_ns)
        if cache_key not in self.projectors:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.projectors = {
                key: value for key, value in self.projectors.items()
                if key[0] != release_id
            }
            self.projectors[cache_key] = SemanticMapProjector(data)
        return self.projectors[cache_key]


MAP_CACHE = SemanticMapCache()
BROKERS = {}


def _package_text(name):
    """Read a packaged resource from either a directory or Picard's ZIP."""
    path = str(Path(__file__).with_name(name))
    loader = globals().get('__loader__')
    if loader is not None and hasattr(loader, 'get_data'):
        try:
            return loader.get_data(path).decode('utf-8')
        except (OSError, UnicodeError):
            pass
    return Path(path).read_text(encoding='utf-8')


OWNED_TAGS = frozenset(
    name
    for names in json.loads(_package_text('tag_schema.json'))['tags'].values()
    for name in names
)


def _bounded_setting(name, default, minimum, maximum):
    try:
        value = int(config.setting[name])
    except (KeyError, TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _loopback_hostkey(server):
    """Return Picard's (host, port) rate-control key for a loopback URL."""
    try:
        parsed = urlsplit(server)
        host = (parsed.hostname or '').rstrip('.').casefold()
        ipv4 = host.split('.')
        ipv4_loopback = (
            len(ipv4) == 4
            and ipv4[0] == '127'
            and all(part.isdigit() and 0 <= int(part) <= 255 for part in ipv4)
        )
        if host not in {'localhost', '::1', '0:0:0:0:0:0:0:1'} and not ipv4_loopback:
            return None
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        return host, port
    except (ValueError, AttributeError):
        return None


def _restore_ratecontrol(hostkey, ratecontrol):
    original = _RATECONTROL_ORIGINALS.pop(hostkey, None)
    if original is None:
        return
    minimum, delay, window = original
    ratecontrol.REQUEST_DELAY_MINIMUM[hostkey] = minimum
    ratecontrol.REQUEST_DELAY[hostkey] = delay
    ratecontrol.CONGESTION_WINDOW_SIZE[hostkey] = window


def _network_profile(server):
    """Apply an explicit loopback-only rate profile and return crawl settings."""
    concurrency = _bounded_setting(
        OPT_LOCAL_CONCURRENCY, 8, 1, 32
    )
    delay_ms = _bounded_setting(OPT_LOCAL_DELAY_MS, 10, 0, 1000)
    hostkey = _loopback_hostkey(server)
    requested = bool(config.setting[OPT_LOCAL_ACCEL])
    if not hostkey:
        if requested:
            log.warning(
                '%s: local-mirror acceleration ignored for non-loopback server %r',
                PLUGIN_NAME, server,
            )
        return False, DEFAULT_CONCURRENCY, None
    if not requested and hostkey not in _RATECONTROL_ORIGINALS:
        return False, DEFAULT_CONCURRENCY, None
    try:
        from picard.webservice import ratecontrol
        required = (
            'REQUEST_DELAY_MINIMUM', 'REQUEST_DELAY', 'CONGESTION_WINDOW_SIZE',
            'set_minimum_delay',
        )
        if not all(hasattr(ratecontrol, name) for name in required):
            raise AttributeError('Picard rate-control API is incomplete')
        if not requested:
            _restore_ratecontrol(hostkey, ratecontrol)
            return False, DEFAULT_CONCURRENCY, None
        if hostkey not in _RATECONTROL_ORIGINALS:
            _RATECONTROL_ORIGINALS[hostkey] = (
                ratecontrol.REQUEST_DELAY_MINIMUM.get(hostkey, 1000),
                ratecontrol.REQUEST_DELAY.get(hostkey, 1000),
                ratecontrol.CONGESTION_WINDOW_SIZE.get(hostkey, 1.0),
            )
        ratecontrol.set_minimum_delay(hostkey, delay_ms)
        # Picard's public setter changes the floor but not the current adaptive
        # delay. Set the existing per-host state as well so an explicit local
        # profile takes effect before this crawl, rather than ramping down from
        # the public-service default over several requests.
        ratecontrol.REQUEST_DELAY[hostkey] = delay_ms
        ratecontrol.CONGESTION_WINDOW_SIZE[hostkey] = float(concurrency)
        return True, concurrency, delay_ms
    except (ImportError, AttributeError, TypeError, ValueError) as error:
        log.warning('%s: unable to apply local-mirror acceleration: %s',
                    PLUGIN_NAME, error)
        return False, DEFAULT_CONCURRENCY, None


def process_album(album, metadata, release_node):
    """Hold album finalization until one asynchronous focused crawl completes."""
    album._classical_projector = None
    album._classical_errors = []
    album._classical_diagnostics = {}
    album._classical_retrieval_status = "cached_map"
    if not config.setting[OPT_ENABLED] or not config.setting[OPT_AUTO]:
        return
    api = album.tagger.mb_api
    server = api.base_url.toString()
    accelerated, concurrency, delay_ms = _network_profile(server)
    folder = (Path(config.setting[OPT_MAP_ROOT]) / '.classical_cache' / 'v0.2'
              if config.setting[OPT_MAP_ROOT] else
              Path(USER_PLUGIN_DIR).parent / 'classical_metadata_cache' / 'v0.2')
    key = server, str(folder), id(api), concurrency
    if key not in BROKERS:
        def fetch(entity, mbid, callback):
            def received(document, http, error):
                callback(document, http.errorString() if error and http else str(error) if error else None)
            api.get(
                '/' + entity + '/' + mbid, received,
                unencoded_queryargs={'inc': '+'.join(INCLUDES[entity]), 'fmt': 'json'},
                parse_response_type='json', mblogin=False, priority=False,
            )
        BROKERS[key] = EntityBroker(server, folder, fetch, concurrency=concurrency)
    broker = BROKERS[key]
    album._requests += 1
    released = False
    def is_current():
        return album.tagger.albums.get(album.id) is album
    def progress(value):
        if is_current():
            _set_status(metadata, value)
            log.debug('%s: %s %s', PLUGIN_NAME, album.id, value)
    def completed(semantic, errors):
        nonlocal released
        if released:
            return
        released = True
        try:
            if is_current():
                album._classical_errors = errors
                album._classical_diagnostics = crawl.diagnostics()
                album._classical_diagnostics.update({
                    'local_mirror_acceleration': accelerated,
                    'request_delay_ms': delay_ms,
                })
                log.info('%s: %s diagnostics %s', PLUGIN_NAME, album.id,
                         json.dumps(album._classical_diagnostics, sort_keys=True))
                if semantic:
                    album._classical_projector = SemanticMapProjector(semantic)
                    album._classical_retrieval_status = 'partial' if errors else 'complete'
                else:
                    album._classical_projector = MAP_CACHE.projector(album.id)
                    album._classical_retrieval_status = 'fallback_cached_map' if album._classical_projector else 'failed'
                _set_status(metadata, 'retrieval:' + album._classical_retrieval_status)
                log.info('%s: %s retrieval %s (%d errors; cache hits=%d)',
                         PLUGIN_NAME, album.id, album._classical_retrieval_status,
                         len(errors), broker.hits)
                for error in errors:
                    log.warning('%s: %s', PLUGIN_NAME, error)
                if broker.cache_errors:
                    log.warning('%s: cache could not persist some entities: %s',
                                PLUGIN_NAME, broker.cache_errors[-1])
        except Exception as error:
            album._classical_errors = errors + [{'error': str(error)}]
            album._classical_retrieval_status = 'failed'
            log.exception('%s: unable to finalize retrieval', PLUGIN_NAME)
        finally:
            album._requests -= 1
            if is_current() and not album.loaded:
                album._finalize_loading(None)
    crawl = ReleaseCrawl(
        album.id, broker, completed, progress, concurrency=concurrency
    )
    album._classical_crawl = crawl
    try:
        progress('loading:starting')
        crawl.start()
    except Exception as error:
        completed(None, [{'error': str(error)}])


def _first(metadata, key):
    value = metadata.get(key)
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _write_tags(metadata, tags):
    """Replace a successful projection, including on-disk deletion tombstones.

    Picard deliberately preserves disk tags missing from new metadata unless
    they are explicitly deleted. Delete absent known names even when they are
    not present on this track metadata object. Hidden preview must not delete
    persisted file tags.
    """
    persistent = config.setting[OPT_PERSIST]
    replace = (tags.get('classical_status') != 'recording_not_in_semantic_map'
               and tags.get('classical_retrieval_status') not in {'partial', 'failed'})
    for name in OWNED_TAGS:
        hidden = '~' + name
        if hidden in metadata:
            del metadata[hidden]
        if persistent and replace and name not in tags:
            try:
                del metadata[name]
            except KeyError:
                pass  # plain dict test doubles, unlike Picard Metadata
    for name, value in tags.items():
        output_name = name if persistent else "~" + name
        if isinstance(value, list):
            metadata[output_name] = value
        elif value is not None:
            metadata[output_name] = str(value)


def _node_get(node, *keys):
    """Safely walk Picard's dict-like MusicBrainz response nodes."""
    current = node
    for key in keys:
        if current is None:
            return None
        try:
            current = current.get(key)
        except (AttributeError, TypeError):
            try:
                current = current[key]
            except (KeyError, TypeError):
                return None
    return current


def _set_status(metadata, value):
    """Expose adapter health in the same namespace as projected tags."""
    name = "classical_plugin_status"
    if not config.setting[OPT_PERSIST] or not config.setting[OPT_DIAGNOSTICS]:
        name = "~" + name
    metadata[name] = value


def process_track(album, metadata, track_node, release_node):
    # Write this before any lookup. Its presence proves that Picard called us.
    _set_status(metadata, "processor_called")
    if not config.setting[OPT_ENABLED]:
        _set_status(metadata, "disabled")
        return
    release_id = (
        _first(metadata, "musicbrainz_albumid")
        or _node_get(release_node, "id")
        or getattr(album, "id", None)
        or _node_get(getattr(album, "album", None), "id")
    )
    recording_id = (
        _first(metadata, "musicbrainz_recordingid")
        or _node_get(track_node, "recording", "id")
        or _node_get(track_node, "id")
        or getattr(album, "id", None)
    )
    if not release_id or not recording_id:
        _set_status(metadata, "identifiers_missing")
        log.warning(
            "%s: processor called without release/recording IDs (%r, %r)",
            PLUGIN_NAME, release_id, recording_id,
        )
        return
    try:
        projector = getattr(album, '_classical_projector', None)
        if projector is None:
            projector = MAP_CACHE.projector(release_id)
        if projector is None:
            _set_status(metadata, "semantic_map_not_found")
            log.warning(
                "%s: no SEMANTIC_MAP.json found for release %s under %r",
                PLUGIN_NAME, release_id, config.setting[OPT_MAP_ROOT],
            )
            return
        tags = projector.project_recording(recording_id)
        retrieval_status = getattr(album, '_classical_retrieval_status', 'cached_map')
        tags['classical_retrieval_status'] = retrieval_status
        if config.setting[OPT_DIAGNOSTICS]:
            diagnostics = getattr(album, '_classical_diagnostics', {})
            for key in ('elapsed_seconds', 'network_requests', 'cache_hits', 'coalesced_lookups'):
                if key in diagnostics:
                    tags['classical_retrieval_' + key] = str(diagnostics[key])
        errors = getattr(album, '_classical_errors', [])
        if errors:
            tags['classical_retrieval_error_count'] = str(len(errors))
        projected_status = "projected:" + tags.get("classical_status", "unknown")
        if config.setting[OPT_DIAGNOSTICS]:
            tags['classical_plugin_status'] = projected_status
        _write_tags(metadata, tags)
        _set_status(metadata, projected_status)
    except Exception as error:
        log.exception("%s: unable to project %s", PLUGIN_NAME, recording_id)
        _set_status(metadata, "projection_error:" + type(error).__name__)


class ClassicalMetadataOptionsPage(OptionsPage):
    NAME = "classical_metadata"
    TITLE = "Classical Metadata"
    PARENT = "plugins"
    options = [
        BoolOption("setting", OPT_ENABLED, True),
        TextOption("setting", OPT_MAP_ROOT, ""),
        BoolOption("setting", OPT_PERSIST, True),
        BoolOption("setting", OPT_AUTO, True),
        BoolOption("setting", OPT_DIAGNOSTICS, False),
        BoolOption("setting", OPT_LOCAL_ACCEL, False),
        IntOption("setting", OPT_LOCAL_CONCURRENCY, 8),
        IntOption("setting", OPT_LOCAL_DELAY_MS, 10),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Enable Classical Metadata projection")
        layout.addWidget(self.enabled)
        self.auto = QCheckBox("Automatically retrieve metadata using Picard's MusicBrainz server")
        layout.addWidget(self.auto)
        self.local_accel = QCheckBox(
            "Accelerate retrieval for a loopback MusicBrainz mirror"
        )
        layout.addWidget(self.local_accel)
        local_row = QHBoxLayout()
        local_row.addWidget(QLabel("Local concurrency:"))
        self.local_concurrency = QSpinBox()
        self.local_concurrency.setRange(1, 32)
        local_row.addWidget(self.local_concurrency)
        local_row.addWidget(QLabel("Delay between requests (ms):"))
        self.local_delay = QSpinBox()
        self.local_delay.setRange(0, 1000)
        local_row.addWidget(self.local_delay)
        layout.addLayout(local_row)
        layout.addWidget(QLabel("Optional explorer-map root / shared cache folder:"))
        row = QHBoxLayout()
        self.map_root = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.map_root)
        row.addWidget(browse)
        layout.addLayout(row)
        self.persist = QCheckBox(
            "Write classical_* tags to files (clear for hidden preview variables)"
        )
        layout.addWidget(self.persist)
        self.diagnostics = QCheckBox(
            "Persist retrieval statistics and plugin debug status"
        )
        layout.addWidget(self.diagnostics)
        layout.addWidget(QLabel(
            "Local acceleration applies only to localhost/loopback servers. "
            "Entity cache lifetime: 7 days. Clear automatic retrieval to use existing maps only."
        ))
        layout.addStretch(1)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select MusicBrainz Release X-Ray output folder",
            self.map_root.text(),
        )
        if path:
            self.map_root.setText(path)

    def load(self):
        self.enabled.setChecked(config.setting[OPT_ENABLED])
        self.map_root.setText(config.setting[OPT_MAP_ROOT])
        self.persist.setChecked(config.setting[OPT_PERSIST])
        self.auto.setChecked(config.setting[OPT_AUTO])
        self.diagnostics.setChecked(config.setting[OPT_DIAGNOSTICS])
        self.local_accel.setChecked(config.setting[OPT_LOCAL_ACCEL])
        self.local_concurrency.setValue(config.setting[OPT_LOCAL_CONCURRENCY])
        self.local_delay.setValue(config.setting[OPT_LOCAL_DELAY_MS])

    def save(self):
        config.setting[OPT_ENABLED] = self.enabled.isChecked()
        config.setting[OPT_MAP_ROOT] = self.map_root.text().strip()
        config.setting[OPT_PERSIST] = self.persist.isChecked()
        config.setting[OPT_AUTO] = self.auto.isChecked()
        config.setting[OPT_DIAGNOSTICS] = self.diagnostics.isChecked()
        config.setting[OPT_LOCAL_ACCEL] = self.local_accel.isChecked()
        config.setting[OPT_LOCAL_CONCURRENCY] = self.local_concurrency.value()
        config.setting[OPT_LOCAL_DELAY_MS] = self.local_delay.value()
        MAP_CACHE._set_root(config.setting[OPT_MAP_ROOT])


# Run after normal-priority metadata plugins so IDs and relationship data have
# reached their final values. The raw MusicBrainz nodes remain available as a
# fallback for both IDs.
register_track_metadata_processor(process_track, priority=PluginPriority.LOW)
register_album_metadata_processor(process_album, priority=PluginPriority.LOW)
register_options_page(ClassicalMetadataOptionsPage)
