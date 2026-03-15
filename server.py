"""WebDAV server for Eagle media libraries.

Scans the current directory (or a given root path) for all *.library folders
and mounts them as top-level WebDAV collections under one server.  Any
WebDAV-capable client (Windows Explorer, macOS Finder, etc.) will see each
library as a subfolder (e.g. /绘画参考/, /书/).

Usage:
    python server.py [root_dir] [--host HOST] [--port PORT]

    root_dir defaults to "." (current directory).
    HOST defaults to "0.0.0.0", PORT defaults to 48000.

Data flows
----------
PROPFIND (list directory):
    EagleProvider.get_resource_inst(path)
    → _dav_to_eagle(path) → Path("folder/name")
    → source.path_to_id lookup → folder_id_map → EagleFolderResource
    → wsgidav calls get_member_names()
        → FolderSource.files.keys() + FolderSource.subfolders.keys()
    → wsgidav calls get_member(name) for each child
        → get_resource_inst("folder/name/child") → EagleFile/FolderResource

GET (download file):
    get_resource_inst → EagleFileResource
    → get_content_length() → image.meta.size
    → get_content() → BytesIO(ImageSource.read_data())
        → reads library/images/{id}/{name}.{ext} from disk

PUT new file:
    get_resource_inst → None (file absent)
    → parent EagleFolderResource.create_empty_resource(name)
        → EaglePendingResource
    → begin_write() → _WriteBuffer
    → body written into buffer; close() calls source.new_file(path, data)
        → FolderSource.new_file(data, stem, ext)
        → creates File + ImageSource, saves to disk, generates 320px thumbnail
        → updates file_id_map, path_to_id

PUT overwrite:
    get_resource_inst → EagleFileResource
    → begin_write() → _WriteBuffer; close() calls source.write_file(path, data)
        → ImageSource.save_data(data) → disk + thumbnail regeneration
        → updates meta.size, mtime, modificationTime

MKCOL (create folder):
    parent EagleFolderResource.create_collection(name)
    → source.new_folder(parent_path / name)
        → FolderSource.new_subfolder(name)
        → Folder model created, metadata.json saved
        → folder_id_map, path_to_id updated

DELETE:
    EagleFileResource.delete()  or  EagleFolderResource.delete()
    → source.delete_node(path)
    File:   ImageSource.delete() → isDeleted=True, metadata.json + mtime.json saved
    Folder: recursively marks contained files deleted, removes from parent.subfolders,
            saves metadata.json

MOVE / RENAME:
    EagleFileResource.move_recursive(dest_dav_path)
    or EagleFolderResource.move_recursive(dest_dav_path)
    → source.rename_node(old_path, new_path)
        → updates meta.name / meta.ext, path_to_id, targets, folder.files dicts
        → ImageSource.save_meta() writes metadata.json + mtime.json
"""

import logging
import sys
from io import BytesIO
from pathlib import Path

from wsgidav import util
from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.dav_error import DAVError, HTTP_FORBIDDEN, HTTP_INTERNAL_ERROR
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider

from src.source import EagleLibrarySource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _dav_to_eagle(dav_path: str) -> Path:
    """Convert a DAV path string to an Eagle virtual Path.

    "/未分类/image.png" → Path("未分类/image.png")
    "/"                 → Path()   (root)
    """
    stripped = dav_path.strip("/")
    return Path(stripped) if stripped else Path()


# ---------------------------------------------------------------------------
# Write buffer
# ---------------------------------------------------------------------------

class _WriteBuffer:
    """Accumulates PUT body bytes; calls *callback* with the full payload on close.

    Returned by begin_write() so wsgidav can stream the request body in and we
    commit it to Eagle atomically on close.
    """

    def __init__(self, callback):
        self._buf = BytesIO()
        self._cb = callback

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def close(self) -> None:
        self._cb(self._buf.getvalue())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# File resource
# ---------------------------------------------------------------------------

class EagleFileResource(DAVNonCollection):
    """WebDAV non-collection resource backed by an Eagle ImageSource.

    One instance is created per request; the heavy data stays in the shared
    EagleLibrarySource caches.
    """

    def __init__(self, path: str, environ: dict, source: EagleLibrarySource, eagle_path: Path, dav_prefix: str = ""):
        super().__init__(path, environ)
        self._src = source
        self._ep = eagle_path
        self._dav_prefix = dav_prefix

    @property
    def _image(self):
        """Look up the live ImageSource from the source caches."""
        try:
            return self._src.file_id_map[self._src.path_to_id[self._ep]]
        except KeyError:
            raise DAVError(HTTP_INTERNAL_ERROR, f"Image not found in cache: {self._ep}")

    # ---- Required DAVNonCollection interface ----

    def get_content_length(self) -> int:
        return self._image.meta.size

    def get_content_type(self) -> str:
        return util.guess_mime_type(self.path) or "application/octet-stream"

    def get_last_modified(self) -> float:
        """Return mtime in seconds (wsgidav expects a Unix timestamp float)."""
        return self._image.meta.mtime / 1000.0

    def get_creation_date(self) -> float:
        return self._image.meta.btime / 1000.0

    def get_etag(self) -> str:
        return str(self._image.meta.modificationTime)

    def support_etag(self) -> bool:
        return True

    def get_content(self) -> BytesIO:
        return BytesIO(self._image.read_data())

    # ---- Write support ----

    def begin_write(self, _content_type=None) -> _WriteBuffer:
        """Return a buffer that commits to Eagle on close (overwrite existing file)."""
        ep = self._ep

        def _on_close(data: bytes):
            if not self._src.write_file(ep, data):
                raise DAVError(HTTP_INTERNAL_ERROR, f"write_file failed for {ep}")

        return _WriteBuffer(_on_close)

    # ---- Delete / Move ----

    def delete(self) -> None:
        if not self._src.delete_node(self._ep):
            raise DAVError(HTTP_INTERNAL_ERROR, f"delete_node failed for {self._ep}")

    def support_recursive_move(self, _dest_path: str) -> bool:
        return True

    def move_recursive(self, dest_path: str) -> None:
        inner = dest_path[len(self._dav_prefix):]
        dest_ep = _dav_to_eagle(inner)
        if not self._src.rename_node(self._ep, dest_ep):
            raise DAVError(HTTP_INTERNAL_ERROR, f"rename_node failed: {self._ep} → {dest_ep}")


# ---------------------------------------------------------------------------
# Pending resource (new-file placeholder for PUT)
# ---------------------------------------------------------------------------

class EaglePendingResource(DAVNonCollection):
    """Placeholder returned by create_empty_resource() when a PUT targets a new path.

    wsgidav calls begin_write() on this object to stream the body; on close we
    call source.new_file() to actually create the Eagle asset.
    """

    def __init__(self, path: str, environ: dict, source: EagleLibrarySource, eagle_path: Path):
        super().__init__(path, environ)
        self._src = source
        self._ep = eagle_path

    def get_content_length(self) -> int:
        return 0

    def get_content_type(self) -> str:
        return util.guess_mime_type(self.path) or "application/octet-stream"

    def get_content(self) -> BytesIO:
        return BytesIO(b"")

    def get_etag(self):
        return None

    def support_etag(self) -> bool:
        return False

    def begin_write(self, _content_type=None) -> _WriteBuffer:
        ep = self._ep

        def _on_close(data: bytes):
            if not self._src.new_file(ep, data):
                raise DAVError(HTTP_INTERNAL_ERROR, f"new_file failed for {ep}")

        return _WriteBuffer(_on_close)


# ---------------------------------------------------------------------------
# Folder resource
# ---------------------------------------------------------------------------

class EagleFolderResource(DAVCollection):
    """WebDAV collection backed by an Eagle FolderSource."""

    def __init__(self, path: str, environ: dict, source: EagleLibrarySource, eagle_path: Path, dav_prefix: str = ""):
        super().__init__(path, environ)
        self._src = source
        self._ep = eagle_path
        self._dav_prefix = dav_prefix

    @property
    def _folder(self):
        try:
            return self._src.folder_id_map[self._src.path_to_id[self._ep]]
        except KeyError:
            raise DAVError(HTTP_INTERNAL_ERROR, f"Folder not found in cache: {self._ep}")

    def get_last_modified(self) -> float:
        return self._folder.meta.modificationTime / 1000.0

    def get_creation_date(self) -> float:
        return self._folder.meta.modificationTime / 1000.0

    # ---- Required DAVCollection interface ----

    def get_member_names(self) -> list[str]:
        """Return names of all direct children (files + subfolders)."""
        f = self._folder
        return list(f.files.keys()) + list(f.subfolders.keys())

    def get_member(self, name: str):
        """Delegate to the provider so path mapping stays centralised."""
        child_dav = self.path.rstrip("/") + "/" + name
        return self.provider.get_resource_inst(child_dav, self.environ)

    # ---- Write support ----

    def create_empty_resource(self, name: str) -> EaglePendingResource:
        """Called by wsgidav before a PUT to a new (non-existing) child path."""
        child_ep = self._ep / name
        child_dav = self.path.rstrip("/") + "/" + name
        return EaglePendingResource(child_dav, self.environ, self._src, child_ep)

    def create_collection(self, name: str) -> None:
        """MKCOL handler."""
        if not self._src.new_folder(self._ep / name):
            raise DAVError(HTTP_FORBIDDEN, f"Cannot create folder '{name}' here")

    # ---- Delete / Move ----

    def delete(self) -> None:
        if not self._src.delete_node(self._ep):
            raise DAVError(HTTP_INTERNAL_ERROR, f"delete_node failed for {self._ep}")

    def support_recursive_delete(self) -> bool:
        return True

    def support_recursive_move(self, _dest_path: str) -> bool:
        return True

    def move_recursive(self, dest_path: str) -> None:
        inner = dest_path[len(self._dav_prefix):]
        dest_ep = _dav_to_eagle(inner)
        if not self._src.rename_node(self._ep, dest_ep):
            raise DAVError(HTTP_INTERNAL_ERROR, f"rename_node failed: {self._ep} → {dest_ep}")


# ---------------------------------------------------------------------------
# Top-level collection listing all libraries
# ---------------------------------------------------------------------------

class LibraryListResource(DAVCollection):
    """Virtual root collection that lists all loaded Eagle libraries as subfolders."""

    def __init__(self, path: str, environ: dict, provider: "MultiLibraryProvider"):
        super().__init__(path, environ)
        self._provider = provider

    def get_member_names(self) -> list[str]:
        return list(self._provider._sources.keys())

    def get_member(self, name: str):
        child_dav = self.path.rstrip("/") + "/" + name + "/"
        return self._provider.get_resource_inst(child_dav, self.environ)

    def create_empty_resource(self, name: str):
        raise DAVError(HTTP_FORBIDDEN, "Cannot create files in root")

    def create_collection(self, name: str) -> None:
        raise DAVError(HTTP_FORBIDDEN, "Cannot create folders in root")


# ---------------------------------------------------------------------------
# Multi-library provider
# ---------------------------------------------------------------------------

class MultiLibraryProvider(DAVProvider):
    """wsgidav DAVProvider that exposes multiple Eagle libraries under one share.

    Each *.library folder in *root_path* is mounted as a top-level subfolder
    named after the library stem (e.g. ``/绘画参考/``).
    """

    def __init__(self, root_path: Path):
        super().__init__()
        self._sources: dict[str, EagleLibrarySource] = {}
        for lib_dir in sorted(root_path.glob("*.library")):
            name = lib_dir.stem
            logger.info("Loading library: %s", lib_dir)
            self._sources[name] = EagleLibrarySource(lib_dir)
        logger.info(
            "Loaded %d libraries, total %d folders, %d files",
            len(self._sources),
            sum(len(s.folder_id_map) for s in self._sources.values()),
            sum(len(s.file_id_map) for s in self._sources.values()),
        )

    def get_resource_inst(self, path: str, environ: dict):
        """Route requests to the appropriate EagleLibrarySource."""
        if path in ("/", ""):
            return LibraryListResource("/", environ, self)

        parts = path.strip("/").split("/", 1)
        lib_name = parts[0]
        if lib_name not in self._sources:
            return None

        src = self._sources[lib_name]
        src.update_cache()

        inner = "/" + parts[1] if len(parts) > 1 else "/"
        ep = _dav_to_eagle(inner)
        dav_prefix = "/" + lib_name

        _id = src.path_to_id.get(ep)
        if _id is None:
            return None

        if _id in src.folder_id_map:
            return EagleFolderResource(path, environ, src, ep, dav_prefix)

        if _id in src.file_id_map:
            return EagleFileResource(path, environ, src, ep, dav_prefix)

        return None


# ---------------------------------------------------------------------------
# Provider (single-library, kept for backwards compatibility)
# ---------------------------------------------------------------------------

class EagleProvider(DAVProvider):
    """wsgidav DAVProvider that exposes a single Eagle library as the share root."""

    def __init__(self, library_path: Path):
        super().__init__()
        logger.info("Loading Eagle library: %s", library_path)
        self._src = EagleLibrarySource(library_path)
        logger.info(
            "Library loaded — %d folders, %d files",
            len(self._src.folder_id_map),
            len(self._src.file_id_map),
        )

    def get_resource_inst(self, path: str, environ: dict):
        """Return an EagleFolderResource, EagleFileResource, or None.

        Called by wsgidav for every incoming request.
        """
        self._src.update_cache()
        ep = _dav_to_eagle(path)

        if ep not in self._src.path_to_id:
            return None

        _id = self._src.path_to_id[ep]

        if _id in self._src.folder_id_map:
            return EagleFolderResource(path, environ, self._src, ep)

        if _id in self._src.file_id_map:
            return EagleFileResource(path, environ, self._src, ep)

        return None


# ---------------------------------------------------------------------------
# App factory + entry point
# ---------------------------------------------------------------------------

def make_app(root_path: Path, verbose: int = 1) -> WsgiDAVApp:
    """Build and return a configured WsgiDAVApp scanning *root_path* for *.library folders."""
    config = {
        "provider_mapping": {"/": MultiLibraryProvider(root_path)},
        # No authentication — suitable for local / trusted-network use
        "http_authenticator": {"domain_controller": None},
        "simple_dc": {"user_mapping": {"*": True}},
        "verbose": verbose,
    }
    return WsgiDAVApp(config)


if __name__ == "__main__":
    import argparse
    from typing import cast
    from wsgiref.simple_server import make_server
    from wsgiref.types import WSGIApplication

    parser = argparse.ArgumentParser(description="Eagle WebDAV server — mounts all *.library folders as top-level directories")
    parser.add_argument("root", nargs="?", default=".", help="directory to scan for *.library folders (default: current directory)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=48000)
    parser.add_argument("--verbose", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    root_path = Path(args.root)
    if not root_path.is_dir():
        print(f"Directory not found: {root_path}", file=sys.stderr)
        sys.exit(1)

    libraries = sorted(root_path.glob("*.library"))
    if not libraries:
        print(f"No .library folders found in: {root_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(libraries)} librar{'y' if len(libraries) == 1 else 'ies'}:")
    for lib in libraries:
        print(f"  /{lib.stem}/")

    app = make_app(root_path, verbose=args.verbose)
    print(f"Serving on http://{args.host}:{args.port}/")
    make_server(args.host, args.port, cast(WSGIApplication, app)).serve_forever()
