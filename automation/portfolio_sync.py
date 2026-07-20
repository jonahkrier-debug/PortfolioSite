#!/usr/bin/env python3
"""Synchronize a darktable tag selection into the static portfolio site.

The darktable catalog and source files are always opened read-only. Site changes
are staged, validated, and committed only after the catalog and source snapshot
is rechecked.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import datetime as dt
import hashlib
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
EXPORTER_REVISION = 1
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
RESERVED_PAGE_SLUGS = {"index", "info", "404", "project"}
WINDOWS_DEVICE_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
AVIF_BRANDS = {b"avif", b"avis"}


class SyncError(RuntimeError):
    exit_code = 2


class ConfigError(SyncError):
    pass


class ValidationError(SyncError):
    pass


class DeferredError(SyncError):
    """A safe, transient condition. A scheduled run may retry later."""

    exit_code = 5


class StaleSnapshotError(DeferredError):
    exit_code = 4


class ExportError(SyncError):
    exit_code = 3


class AlreadyRunning(DeferredError):
    exit_code = 6


@dataclasses.dataclass(frozen=True)
class CatalogImage:
    image_id: int
    version: int
    folder: str
    filename: str
    datetime_taken: int
    change_timestamp: int
    write_timestamp: int
    history_hash: str
    tag_position: int
    description: str

    @property
    def source_path(self) -> Path:
        return Path(self.folder) / self.filename

    @property
    def sidecar_path(self) -> Path:
        source = self.source_path
        if self.version <= 0:
            return Path(f"{source}.xmp")
        return source.with_name(
            f"{source.stem}_{self.version:02d}{source.suffix}.xmp"
        )

    @property
    def key(self) -> str:
        return f"{self.image_id}:v{self.version}"


@dataclasses.dataclass(frozen=True)
class CatalogSnapshot:
    namespace_tags: tuple[str, ...]
    home: tuple[CatalogImage, ...]
    info: tuple[CatalogImage, ...]
    projects: Mapping[str, tuple[CatalogImage, ...]]
    project_tags: Mapping[str, str]
    digest: str

    def all_images(self) -> tuple[CatalogImage, ...]:
        by_key: dict[str, CatalogImage] = {}
        for image in self.home + self.info:
            by_key[image.key] = image
        for images in self.projects.values():
            for image in images:
                by_key[image.key] = image
        return tuple(by_key[key] for key in sorted(by_key))


@dataclasses.dataclass(frozen=True)
class SourceState:
    source_signature: str
    source_size: int
    source_mtime_ns: int
    sidecar_size: int
    sidecar_mtime_ns: int
    sidecar_sha256: str


@dataclasses.dataclass(frozen=True)
class AssetRequest:
    key: str
    role: str
    variant_name: str
    output_dir: str
    image: CatalogImage
    alt: str


@dataclasses.dataclass
class PreparedAsset:
    request: AssetRequest
    relative_path: str
    sha256: str
    source_signature: str
    variant_signature: str
    staged_path: Path | None

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "key": self.request.key,
            "path": self.relative_path,
            "sha256": self.sha256,
            "source_signature": self.source_signature,
            "variant_signature": self.variant_signature,
            "image_id": self.request.image.image_id,
            "version": self.request.image.version,
            "role": self.request.role,
        }


@dataclasses.dataclass(frozen=True)
class ProjectDefinition:
    slug: str
    title: str
    page: str
    description: str


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_relpath(path: str | Path) -> str:
    return Path(path).as_posix().lstrip("/")


def relative_path_key(path: str | Path) -> str:
    normalized = normalize_relpath(path)
    return normalized.casefold() if os.name == "nt" else normalized


def require_canonical_relpath(path: str) -> str:
    normalized = normalize_relpath(path)
    if path != normalized:
        raise ValidationError(f"Manifest path is not canonical: {path!r}")
    return normalized


def resolve_config_path(value: str, *, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute():
        expanded = base / expanded
    return expanded.resolve(strict=False)


def safe_site_path(site_root: Path, relative: str | Path) -> Path:
    normalized = normalize_relpath(relative)
    if not normalized or Path(normalized).is_absolute() or ".." in Path(normalized).parts:
        raise ConfigError(f"Unsafe site-relative path: {relative!s}")
    candidate = site_root / Path(normalized)
    try:
        candidate.relative_to(site_root)
    except ValueError as exc:
        raise ConfigError(f"Path escapes site root: {relative!s}") from exc
    current = site_root
    for part in Path(normalized).parts:
        current = current / part
        if not current.exists():
            continue
        is_junction = getattr(current, "is_junction", lambda: False)()
        if current.is_symlink() or is_junction:
            raise ValidationError(
                f"Refusing a managed path containing a symlink/junction: {relative!s}"
            )
    return candidate


def load_config(config_path: Path) -> dict[str, Any]:
    try:
        config_bytes = config_path.read_bytes()
        config = json.loads(config_bytes.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {config_path}: {exc}") from exc

    if config.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported config schema_version {config.get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}"
        )

    base = config_path.parent.resolve()
    config["_config_path"] = config_path.resolve()
    config["_config_sha256"] = sha256_bytes(config_bytes)
    config["_site_root"] = resolve_config_path(config.get("site_root", ".."), base=base)
    config["_state_dir"] = resolve_config_path(
        config.get("state_dir", "../.portfolio-sync"), base=base
    )

    darktable = config.get("darktable")
    if not isinstance(darktable, dict):
        raise ConfigError("darktable must be an object")
    for key in ("cli", "library_db", "data_db"):
        if not isinstance(darktable.get(key), str) or not darktable[key]:
            raise ConfigError(f"darktable.{key} must be a path string")
        darktable[f"_{key}"] = resolve_config_path(darktable[key], base=base)

    output = config.get("output")
    if not isinstance(output, dict):
        raise ConfigError("output must be an object")
    for key in ("image_root", "data_file", "manifest_file", "project_template"):
        if not isinstance(output.get(key), str) or not output[key]:
            raise ConfigError(f"output.{key} must be a path string")
    output["_image_root"] = safe_site_path(config["_site_root"], output["image_root"])
    output["_data_file"] = safe_site_path(config["_site_root"], output["data_file"])
    output["_manifest_file"] = safe_site_path(config["_site_root"], output["manifest_file"])
    output["_project_template"] = resolve_config_path(output["project_template"], base=base)

    validate_config(config)
    return config


def validate_config(config: Mapping[str, Any]) -> None:
    tags = config.get("tags")
    if not isinstance(tags, dict):
        raise ConfigError("tags must be an object")
    for key in ("namespace", "home", "info", "project_prefix"):
        if not isinstance(tags.get(key), str) or not tags[key]:
            raise ConfigError(f"tags.{key} must be a non-empty string")
    if not tags["project_prefix"].startswith(tags["namespace"] + "|"):
        raise ConfigError("tags.project_prefix must be inside tags.namespace")
    if not tags["project_prefix"].endswith("|"):
        raise ConfigError("tags.project_prefix must end with '|'")

    variants = config.get("variants")
    required_variants = {"home", "info", "project_main", "project_grid", "project_lightbox"}
    if not isinstance(variants, dict) or not required_variants.issubset(variants):
        raise ConfigError(f"variants must define: {', '.join(sorted(required_variants))}")
    for name, variant in variants.items():
        if not isinstance(variant, dict):
            raise ConfigError(f"variants.{name} must be an object")
        for dimension in ("width", "height"):
            if not isinstance(variant.get(dimension), int) or variant[dimension] < 0:
                raise ConfigError(f"variants.{name}.{dimension} must be a non-negative integer")
        quality = variant.get("quality")
        if not isinstance(quality, int) or not 0 <= quality <= 100:
            raise ConfigError(f"variants.{name}.quality must be between 0 and 100")

    legacy_pages = config["output"].get("legacy_project_pages", [])
    if not isinstance(legacy_pages, list) or not all(
        isinstance(page, str) for page in legacy_pages
    ):
        raise ConfigError("output.legacy_project_pages must be an array of filenames")
    for page in legacy_pages:
        path = Path(normalize_relpath(page))
        if (
            len(path.parts) != 1
            or path.suffix.casefold() != ".html"
            or not SLUG_RE.fullmatch(path.stem)
            or path.stem in RESERVED_PAGE_SLUGS
        ):
            raise ConfigError(f"Invalid legacy project page: {page!r}")
    retention_days = config["output"].get("stale_retention_days", 14)
    if not isinstance(retention_days, int) or retention_days < 0:
        raise ConfigError("output.stale_retention_days must be a non-negative integer")

    project_overrides = config.get("projects", {})
    if not isinstance(project_overrides, dict):
        raise ConfigError("projects must be an object")
    seen_pages: set[str] = set()
    for slug, project in project_overrides.items():
        validate_slug(slug)
        if not isinstance(project, dict):
            raise ConfigError(f"projects.{slug} must be an object")
        page = project.get("page", f"{slug}.html")
        validate_project_page(slug, page)
        normalized = normalize_relpath(page).casefold()
        if normalized in seen_pages:
            raise ConfigError(f"Duplicate project page: {page}")
        seen_pages.add(normalized)

    required = config.get("required_projects", [])
    if not isinstance(required, list):
        raise ConfigError("required_projects must be an array")
    for slug in required:
        validate_slug(slug)


def validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise ConfigError(
            f"Invalid project slug {slug!r}; use lowercase kebab-case (for example, 'new-work')"
        )
    if slug in RESERVED_PAGE_SLUGS:
        raise ConfigError(f"Reserved project slug: {slug}")
    if slug in WINDOWS_DEVICE_NAMES:
        raise ConfigError(f"Project slug is a reserved Windows device name: {slug}")


def validate_project_page(slug: str, page: str) -> None:
    normalized = normalize_relpath(page)
    path = Path(normalized)
    if len(path.parts) != 1 or path.suffix.casefold() != ".html":
        raise ConfigError(f"Project {slug!r} page must be a root-level .html filename")
    if not SLUG_RE.fullmatch(path.stem):
        raise ConfigError(
            f"Project {slug!r} page must use a lowercase kebab-case filename"
        )
    if path.stem.casefold() in RESERVED_PAGE_SLUGS:
        raise ConfigError(f"Project {slug!r} uses reserved page {page!r}")
    if path.stem.casefold() in WINDOWS_DEVICE_NAMES:
        raise ConfigError(f"Project {slug!r} uses a reserved Windows device filename")


def setup_logging(config: Mapping[str, Any], verbose: bool) -> logging.Logger:
    logger = logging.getLogger("portfolio-sync")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    state_dir: Path = config["_state_dir"]
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "portfolio-sync.log"
    file_handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


@contextlib.contextmanager
def singleton_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise AlreadyRunning("Another portfolio sync is already running") from exc
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


@contextlib.contextmanager
def open_catalog(config: Mapping[str, Any]) -> Iterator[sqlite3.Connection]:
    darktable = config["darktable"]
    library_db: Path = darktable["_library_db"]
    data_db: Path = darktable["_data_db"]
    for path in (library_db, data_db):
        if not path.is_file():
            raise DeferredError(f"darktable database is unavailable: {path}")

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            sqlite_uri(library_db), uri=True, timeout=5, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("ATTACH DATABASE ? AS data", (sqlite_uri(data_db),))
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    except sqlite3.OperationalError as exc:
        detail = str(exc)
        if "locked" in detail.casefold() or "busy" in detail.casefold():
            raise DeferredError(f"darktable catalog is locked or busy: {detail}") from exc
        raise DeferredError(f"Unable to read darktable catalog: {detail}") from exc
    finally:
        if connection is not None:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            with contextlib.suppress(sqlite3.Error):
                connection.close()


def read_catalog_snapshot(config: Mapping[str, Any]) -> CatalogSnapshot:
    darktable = config["darktable"]
    retries = int(darktable.get("read_retries", 5))
    retry_delay = float(darktable.get("read_retry_seconds", 0.5))
    last_error: DeferredError | None = None
    for attempt in range(retries):
        try:
            return _read_catalog_snapshot_once(config)
        except DeferredError as exc:
            if "locked or busy" not in str(exc):
                raise
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(retry_delay * (attempt + 1))
    raise DeferredError(f"darktable catalog stayed locked after {retries} attempts: {last_error}")


def _read_catalog_snapshot_once(config: Mapping[str, Any]) -> CatalogSnapshot:
    tags_config = config["tags"]
    namespace = tags_config["namespace"]
    home_tag = tags_config["home"]
    info_tag = tags_config["info"]
    project_prefix = tags_config["project_prefix"]

    with open_catalog(config) as connection:
        tag_rows = connection.execute(
            """
            SELECT id, name
              FROM data.tags
             WHERE name = ? OR name LIKE ?
             ORDER BY name, id
            """,
            (namespace, namespace + "|%"),
        ).fetchall()
        namespace_tags = tuple(str(row["name"]) for row in tag_rows)
        if not namespace_tags:
            raise ValidationError(
                f"Portfolio tags are not initialized. In darktable, tag images with "
                f"'{home_tag}', '{info_tag}', and '{project_prefix}<slug>' first. "
                "The existing site was left untouched."
            )

        tags_by_name = {str(row["name"]): int(row["id"]) for row in tag_rows}
        if home_tag not in tags_by_name:
            raise ValidationError(f"Required darktable tag does not exist: {home_tag}")
        if info_tag not in tags_by_name:
            raise ValidationError(f"Required darktable tag does not exist: {info_tag}")

        project_tags: dict[str, tuple[int, str]] = {}
        malformed: list[str] = []
        for name, tag_id in tags_by_name.items():
            if not name.startswith(project_prefix):
                continue
            suffix = name[len(project_prefix) :]
            if not SLUG_RE.fullmatch(suffix) or suffix in RESERVED_PAGE_SLUGS:
                malformed.append(name)
                continue
            if suffix in project_tags:
                malformed.append(name)
                continue
            project_tags[suffix] = (tag_id, name)
        if malformed:
            raise ValidationError(
                "Malformed portfolio project tag(s): " + ", ".join(sorted(malformed))
            )

        home = tuple(read_tagged_images(connection, tags_by_name[home_tag]))
        info = tuple(read_tagged_images(connection, tags_by_name[info_tag]))
        projects: dict[str, tuple[CatalogImage, ...]] = {
            slug: tuple(read_tagged_images(connection, tag_id))
            for slug, (tag_id, _name) in sorted(project_tags.items())
        }

    validate_snapshot(config, home, info, projects, project_tags)
    digest_payload = {
        "namespace_tags": namespace_tags,
        "home": [catalog_image_digest(image) for image in home],
        "info": [catalog_image_digest(image) for image in info],
        "projects": {
            slug: [catalog_image_digest(image) for image in images]
            for slug, images in sorted(projects.items())
        },
        "project_tags": {slug: value[1] for slug, value in sorted(project_tags.items())},
    }
    return CatalogSnapshot(
        namespace_tags=namespace_tags,
        home=home,
        info=info,
        projects=projects,
        project_tags={slug: value[1] for slug, value in project_tags.items()},
        digest=sha256_bytes(canonical_json(digest_payload).encode("utf-8")),
    )


def read_tagged_images(connection: sqlite3.Connection, tag_id: int) -> list[CatalogImage]:
    rows = connection.execute(
        """
        SELECT i.id AS image_id,
               i.version AS version,
               f.folder AS folder,
               i.filename AS filename,
               i.datetime_taken AS datetime_taken,
               i.change_timestamp AS change_timestamp,
               i.write_timestamp AS write_timestamp,
               COALESCE(hex(hh.current_hash), '') AS history_hash,
               ti.position AS tag_position,
               COALESCE((
                 SELECT md.value
                   FROM meta_data md
                  WHERE md.id = i.id AND md.key = 3
                  ORDER BY md.rowid
                  LIMIT 1
               ), '') AS description
          FROM tagged_images ti
          JOIN images i ON i.id = ti.imgid
          JOIN film_rolls f ON f.id = i.film_id
          LEFT JOIN history_hash hh ON hh.imgid = i.id
         WHERE ti.tagid = ?
         ORDER BY ti.position, i.id
        """,
        (tag_id,),
    ).fetchall()
    return [
        CatalogImage(
            image_id=int(row["image_id"]),
            version=int(row["version"] or 0),
            folder=str(row["folder"]),
            filename=str(row["filename"]),
            datetime_taken=int(row["datetime_taken"] or -1),
            change_timestamp=int(row["change_timestamp"] or -1),
            write_timestamp=int(row["write_timestamp"] or -1),
            history_hash=str(row["history_hash"] or ""),
            tag_position=int(row["tag_position"] or 0),
            description=str(row["description"] or "").strip(),
        )
        for row in rows
    ]


def catalog_image_digest(image: CatalogImage) -> dict[str, Any]:
    return {
        "id": image.image_id,
        "version": image.version,
        "folder": image.folder,
        "filename": image.filename,
        "datetime_taken": image.datetime_taken,
        "change_timestamp": image.change_timestamp,
        "write_timestamp": image.write_timestamp,
        "history_hash": image.history_hash,
        "tag_position": image.tag_position,
        "description": image.description,
    }


def catalog_image_source_digest(image: CatalogImage) -> dict[str, Any]:
    """Return catalog state that can affect a rendered image, not its placement."""
    payload = catalog_image_digest(image)
    del payload["tag_position"]
    return payload


def validate_snapshot(
    config: Mapping[str, Any],
    home: Sequence[CatalogImage],
    info: Sequence[CatalogImage],
    projects: Mapping[str, Sequence[CatalogImage]],
    project_tags: Mapping[str, tuple[int, str]],
) -> None:
    minimum_home = int(config.get("minimum_home_images", 1))
    if len(home) < minimum_home:
        raise ValidationError(
            f"'{config['tags']['home']}' has {len(home)} image(s); at least {minimum_home} required"
        )
    if len({image.image_id for image in info}) != 1 or len(info) != 1:
        raise ValidationError(
            f"'{config['tags']['info']}' must identify exactly one Info/About image; "
            f"found {len(info)}. The existing site was left untouched."
        )

    required_projects = set(config.get("required_projects", []))
    missing_tags = required_projects - set(project_tags)
    if missing_tags:
        prefix = config["tags"]["project_prefix"]
        names = ", ".join(prefix + slug for slug in sorted(missing_tags))
        raise ValidationError(f"Required project tag(s) do not exist: {names}")
    empty_required = [slug for slug in sorted(required_projects) if not projects.get(slug)]
    if empty_required:
        raise ValidationError(
            "Required project tag(s) have no images: " + ", ".join(empty_required)
        )


def get_windows_volume_identity(root: Path) -> tuple[str, str]:
    if os.name != "nt":
        raise ConfigError("Windows volume identity checks require Windows")
    drive = root.drive
    if not drive:
        raise ConfigError(f"Archive guard root has no drive: {root}")
    mount = drive + "\\"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    label = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_ulong()
    max_component = ctypes.c_ulong()
    flags = ctypes.c_ulong()
    fs_name = ctypes.create_unicode_buffer(261)
    ok = kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(mount),
        label,
        len(label),
        ctypes.byref(serial),
        ctypes.byref(max_component),
        ctypes.byref(flags),
        fs_name,
        len(fs_name),
    )
    if not ok:
        raise DeferredError(
            f"Cannot read archive volume identity for {mount} "
            f"(Windows error {ctypes.get_last_error()})"
        )
    guid = ctypes.create_unicode_buffer(261)
    ok = kernel32.GetVolumeNameForVolumeMountPointW(
        ctypes.c_wchar_p(mount), guid, len(guid)
    )
    if not ok:
        raise DeferredError(
            f"Cannot read archive volume GUID for {mount} "
            f"(Windows error {ctypes.get_last_error()})"
        )
    return label.value, guid.value


def check_archive_guard(config: Mapping[str, Any]) -> None:
    guard = config.get("archive_guard")
    if not isinstance(guard, dict):
        raise ConfigError("archive_guard must be an object")
    root_value = guard.get("root")
    if not isinstance(root_value, str) or not root_value:
        raise ConfigError("archive_guard.root must be a path string")
    root = resolve_config_path(root_value, base=config["_config_path"].parent)
    if not root.is_dir():
        raise DeferredError(
            f"Archive is disconnected or unavailable: {root}. Site changes were deferred."
        )
    for relative in guard.get("required_paths", []):
        required = root / Path(normalize_relpath(relative))
        if not required.exists():
            raise DeferredError(
                f"Archive sentinel is missing: {required}. Site changes were deferred."
            )

    expected_label = guard.get("volume_label")
    expected_guid = guard.get("volume_guid")
    if expected_label or expected_guid:
        label, guid = get_windows_volume_identity(root)
        if expected_label and label.casefold() != str(expected_label).casefold():
            raise DeferredError(
                f"Wrong volume mounted at {root.drive}: expected label {expected_label!r}, "
                f"found {label!r}. Site changes were deferred."
            )
        if expected_guid and guid.rstrip("\\").casefold() != str(expected_guid).rstrip("\\").casefold():
            raise DeferredError(
                f"Wrong volume mounted at {root.drive}: volume identity does not match. "
                "Site changes were deferred."
            )


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def collect_source_states(
    config: Mapping[str, Any], images: Iterable[CatalogImage]
) -> dict[str, SourceState]:
    guard_root = resolve_config_path(
        config["archive_guard"]["root"], base=config["_config_path"].parent
    )
    require_archive_sources = bool(config["archive_guard"].get("require_selected_sources", True))
    states: dict[str, SourceState] = {}
    for image in images:
        source = image.source_path
        sidecar = image.sidecar_path
        if require_archive_sources and not path_is_within(source, guard_root):
            raise ValidationError(
                f"Tagged image {image.key} is outside the guarded archive root: {source}"
            )
        if not source.is_file():
            raise DeferredError(
                f"Tagged source is unavailable: {source}. No site files were changed."
            )
        if not sidecar.is_file():
            raise DeferredError(
                f"darktable sidecar is unavailable: {sidecar}. No site files were changed."
            )
        source_stat = source.stat()
        sidecar_stat = sidecar.stat()
        sidecar_hash = sha256_file(sidecar)
        signature_payload = {
            "catalog": catalog_image_source_digest(image),
            "source_size": source_stat.st_size,
            "source_mtime_ns": source_stat.st_mtime_ns,
            "sidecar_size": sidecar_stat.st_size,
            "sidecar_mtime_ns": sidecar_stat.st_mtime_ns,
            "sidecar_sha256": sidecar_hash,
        }
        states[image.key] = SourceState(
            source_signature=sha256_bytes(canonical_json(signature_payload).encode("utf-8")),
            source_size=source_stat.st_size,
            source_mtime_ns=source_stat.st_mtime_ns,
            sidecar_size=sidecar_stat.st_size,
            sidecar_mtime_ns=sidecar_stat.st_mtime_ns,
            sidecar_sha256=sidecar_hash,
        )
    return states


def capture_site_input_state(
    config: Mapping[str, Any], definitions: Mapping[str, ProjectDefinition]
) -> dict[Path, str | None]:
    site_root: Path = config["_site_root"]
    paths = {
        config["_config_path"],
        config["output"]["_project_template"],
        safe_site_path(site_root, "index.html"),
        safe_site_path(site_root, "info.html"),
        config["output"]["_data_file"],
        config["output"]["_manifest_file"],
        *(safe_site_path(site_root, definition.page) for definition in definitions.values()),
    }
    state: dict[Path, str | None] = {}
    for path in paths:
        if not path.exists():
            state[path] = None
            continue
        if not path.is_file():
            raise ValidationError(f"Expected a regular site input file: {path}")
        state[path] = sha256_file(path)
    state[config["_config_path"]] = config.get(
        "_config_sha256", state.get(config["_config_path"])
    )
    return state


def verify_site_input_state(expected: Mapping[Path, str | None]) -> None:
    for path, expected_hash in expected.items():
        if expected_hash is None:
            if path.exists():
                raise StaleSnapshotError(
                    f"Site input appeared during export: {path}. Staged output was discarded."
                )
            continue
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise StaleSnapshotError(
                f"Site input changed during export: {path}. Staged output was discarded."
            )


def project_definition(config: Mapping[str, Any], slug: str) -> ProjectDefinition:
    override = config.get("projects", {}).get(slug, {})
    title = override.get("title") or " ".join(part.capitalize() for part in slug.split("-"))
    page = override.get("page") or f"{slug}.html"
    description = override.get("description") or f"{title} photography by Jonah Krier."
    validate_project_page(slug, page)
    return ProjectDefinition(slug=slug, title=title, page=page, description=description)


def ordered_project_slugs(
    config: Mapping[str, Any], projects: Mapping[str, Sequence[CatalogImage]]
) -> list[str]:
    configured = [slug for slug in config.get("projects", {}) if slug in projects]
    dynamic = sorted(slug for slug in projects if slug not in configured)
    return configured + dynamic


def image_alt(image: CatalogImage, fallback: str) -> str:
    return " ".join(image.description.split()) if image.description else fallback


def build_asset_requests(
    config: Mapping[str, Any], snapshot: CatalogSnapshot
) -> tuple[list[AssetRequest], dict[str, ProjectDefinition]]:
    image_root = normalize_relpath(config["output"]["image_root"])
    requests: list[AssetRequest] = []

    for index, image in enumerate(snapshot.home, 1):
        requests.append(
            AssetRequest(
                key=f"home:{image.key}",
                role="home",
                variant_name="home",
                output_dir=f"{image_root}/Home",
                image=image,
                alt=image_alt(image, f"Photograph {index}"),
            )
        )

    info_image = snapshot.info[0]
    requests.append(
        AssetRequest(
            key=f"info:{info_image.key}",
            role="info",
            variant_name="info",
            output_dir=f"{image_root}/Info",
            image=info_image,
            alt=image_alt(info_image, "Photograph by Jonah Krier"),
        )
    )

    definitions: dict[str, ProjectDefinition] = {}
    for slug in ordered_project_slugs(config, snapshot.projects):
        images = snapshot.projects[slug]
        if not images:
            continue
        definition = project_definition(config, slug)
        definitions[slug] = definition
        for index, image in enumerate(images, 1):
            alt = image_alt(image, f"Photograph {index}")
            for role_suffix, variant_name, folder in (
                ("main", "project_main", "Carousel"),
                ("grid", "project_grid", "Grid"),
                ("lightbox", "project_lightbox", "Lightbox"),
            ):
                requests.append(
                    AssetRequest(
                        key=f"project:{slug}:{role_suffix}:{image.key}",
                        role=f"project:{slug}:{role_suffix}",
                        variant_name=variant_name,
                        output_dir=f"{image_root}/Projects/{slug}/{folder}",
                        image=image,
                        alt=alt,
                    )
                )
    pages: dict[str, str] = {}
    for slug, definition in definitions.items():
        page_key = normalize_relpath(definition.page).casefold()
        if page_key in pages:
            raise ValidationError(
                f"Projects {pages[page_key]!r} and {slug!r} both target {definition.page!r}"
            )
        pages[page_key] = slug
    return requests, definitions


def validate_project_page_targets(
    config: Mapping[str, Any],
    definitions: Mapping[str, ProjectDefinition],
    previous_manifest: Mapping[str, Any] | None,
) -> None:
    site_root: Path = config["_site_root"]
    previously_generated = {
        normalize_relpath(page)
        for page in (previous_manifest.get("generated_pages", []) if previous_manifest else [])
    }
    if previous_manifest:
        previously_generated.update(
            normalize_relpath(entry["path"])
            for entry in previous_manifest.get("retained_files", [])
            if entry.get("kind") == "page"
        )
    legacy_pages = {
        normalize_relpath(page)
        for page in config["output"].get("legacy_project_pages", [])
    }
    for slug, definition in definitions.items():
        relative = normalize_relpath(definition.page)
        target = safe_site_path(site_root, relative)
        if not target.exists() or relative in previously_generated:
            continue
        if not target.is_file():
            raise ValidationError(f"Project page target is not a regular file: {target}")
        if relative not in legacy_pages:
            raise ValidationError(
                f"Refusing to overwrite existing non-generated project page: {relative}. "
                "Choose a different slug/page or explicitly configure a legacy adoption."
            )
        current = target.read_text(encoding="utf-8")
        expected_body = f'data-project="{slug}"'
        if expected_body not in current:
            raise ValidationError(
                f"Legacy project page {relative} does not match project slug {slug!r}"
            )


def load_manifest(path: Path, config: Mapping[str, Any]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"Existing portfolio manifest is unreadable; refusing cleanup: {path}: {exc}"
        ) from exc
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValidationError(
            f"Existing portfolio manifest has unsupported schema_version: "
            f"{manifest.get('schema_version')!r}"
        )
    if not isinstance(manifest.get("assets", []), list) or not isinstance(
        manifest.get("owned_files", []), list
    ):
        raise ValidationError("Existing portfolio manifest has invalid assets/owned_files")
    generated_pages = manifest.get("generated_pages", [])
    if not isinstance(generated_pages, list) or not all(
        isinstance(page, str) for page in generated_pages
    ):
        raise ValidationError("Existing portfolio manifest has invalid generated_pages")
    text_sha256 = manifest.get("text_sha256")
    if not isinstance(text_sha256, dict) or not all(
        isinstance(key, str)
        and isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value)
        for key, value in text_sha256.items()
    ):
        raise ValidationError("Existing portfolio manifest has invalid text_sha256")
    canonical_text_hashes: dict[str, str] = {}
    for text_path, checksum in text_sha256.items():
        canonical_text_path = require_canonical_relpath(text_path)
        key = relative_path_key(canonical_text_path)
        if key in {relative_path_key(path) for path in canonical_text_hashes}:
            raise ValidationError("Existing portfolio manifest repeats a text hash path")
        canonical_text_hashes[canonical_text_path] = checksum
    manifest["text_sha256"] = canonical_text_hashes
    asset_paths: list[str] = []
    seen_keys: set[str] = set()
    for entry in manifest["assets"]:
        if not isinstance(entry, dict):
            raise ValidationError("Existing portfolio manifest contains an invalid asset")
        for field in ("key", "path", "sha256", "source_signature", "variant_signature"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValidationError(f"Existing portfolio manifest asset is missing {field}")
        if entry["key"] in seen_keys:
            raise ValidationError(f"Existing portfolio manifest repeats asset key {entry['key']!r}")
        seen_keys.add(entry["key"])
        canonical_asset = require_canonical_relpath(entry["path"])
        validate_owned_path(config, canonical_asset)
        entry["path"] = canonical_asset
        asset_paths.append(relative_path_key(canonical_asset))
    for page in generated_pages:
        canonical_page = require_canonical_relpath(page)
        validate_owned_path(config, canonical_page)
    manifest["generated_pages"] = [require_canonical_relpath(page) for page in generated_pages]
    expected_owned = set(asset_paths).union(
        relative_path_key(page) for page in manifest["generated_pages"]
    )
    if not all(isinstance(path, str) for path in manifest["owned_files"]):
        raise ValidationError("Existing portfolio manifest has a non-string owned path")
    manifest["owned_files"] = [
        require_canonical_relpath(path) for path in manifest["owned_files"]
    ]
    actual_owned = {relative_path_key(path) for path in manifest["owned_files"]}
    if len(actual_owned) != len(manifest["owned_files"]) or actual_owned != expected_owned:
        raise ValidationError(
            "Existing portfolio manifest ownership is inconsistent; refusing cleanup"
        )
    retained = manifest.get("retained_files", [])
    if not isinstance(retained, list):
        raise ValidationError("Existing portfolio manifest has invalid retained_files")
    retained_paths: set[str] = set()
    for entry in retained:
        if not isinstance(entry, dict):
            raise ValidationError("Existing portfolio manifest contains an invalid retained file")
        relative = entry.get("path")
        kind = entry.get("kind")
        checksum = entry.get("sha256")
        delete_after = entry.get("delete_after")
        if (
            not isinstance(relative, str)
            or kind not in {"asset", "page", "legacy"}
            or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
            or not isinstance(delete_after, str)
        ):
            raise ValidationError("Existing portfolio manifest has malformed retained metadata")
        try:
            dt.datetime.fromisoformat(delete_after)
        except ValueError as exc:
            raise ValidationError("Existing portfolio manifest has invalid retention timestamp") from exc
        canonical_retained = require_canonical_relpath(relative)
        entry["path"] = canonical_retained
        validate_owned_path(config, canonical_retained, allow_legacy=kind == "legacy")
        normalized_retained = relative_path_key(canonical_retained)
        if normalized_retained in retained_paths or normalized_retained in actual_owned:
            raise ValidationError("Existing portfolio manifest repeats an owned/retained path")
        retained_paths.add(normalized_retained)
    return manifest


def manifest_asset_cache(manifest: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not manifest:
        return {}
    cache: dict[str, Mapping[str, Any]] = {}
    for entry in manifest.get("assets", []):
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            cache[entry["key"]] = entry
    return cache


def variant_signature(config: Mapping[str, Any], request: AssetRequest) -> str:
    variant = config["variants"][request.variant_name]
    cli: Path = config["darktable"]["_cli"]
    if not cli.is_file():
        raise ConfigError(f"darktable-cli was not found: {cli}")
    cli_stat = cli.stat()
    relevant = {
        "exporter_revision": EXPORTER_REVISION,
        "darktable_cli_size": cli_stat.st_size,
        "darktable_cli_mtime_ns": cli_stat.st_mtime_ns,
        "output_dir": normalize_relpath(request.output_dir),
        "format": "avif",
        "width": variant["width"],
        "height": variant["height"],
        "quality": variant["quality"],
        "bpp": variant.get("bpp", 10),
        "color_mode": bool(variant.get("color_mode", False)),
        "subsample": int(variant.get("subsample", 0)),
        "compression_type": variant.get("compression_type", 1),
        "tiling": bool(variant.get("tiling", True)),
        "hq": bool(variant.get("hq", True)),
        "upscale": False,
        "apply_custom_presets": False,
    }
    return sha256_bytes(canonical_json(relevant).encode("utf-8"))


def safe_filename_stem(filename: str) -> str:
    stem = Path(filename).stem
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")
    return safe[:80] or "image"


def darktable_cli_path(path: Path) -> str:
    """darktable's Windows option parser treats backslashes as escapes."""
    return path.resolve(strict=False).as_posix()


def validate_avif(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 32:
        raise ExportError(f"darktable produced an empty or truncated AVIF: {path}")
    header = path.read_bytes()[:64]
    if len(header) < 16 or header[4:8] != b"ftyp":
        raise ExportError(f"darktable output is not an ISO-BMFF/AVIF file: {path}")
    brands = {header[offset : offset + 4] for offset in range(8, len(header) - 3, 4)}
    if not brands.intersection(AVIF_BRANDS):
        raise ExportError(f"darktable output has no AVIF brand: {path}")


def export_with_darktable(
    config: Mapping[str, Any], request: AssetRequest, destination: Path, logger: logging.Logger
) -> None:
    darktable = config["darktable"]
    cli: Path = darktable["_cli"]
    if not cli.is_file():
        raise ConfigError(f"darktable-cli was not found: {cli}")
    variant = config["variants"][request.variant_name]
    runtime_config = config["_state_dir"] / "darktable-cli"
    runtime_config.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    command = [
        str(cli),
        darktable_cli_path(request.image.source_path),
        darktable_cli_path(request.image.sidecar_path),
        darktable_cli_path(destination),
        "--width",
        str(variant["width"]),
        "--height",
        str(variant["height"]),
        "--hq",
        "true" if variant.get("hq", True) else "false",
        "--upscale",
        "false",
        "--apply-custom-presets",
        "false",
        "--out-ext",
        "avif",
        "--core",
        "--configdir",
        darktable_cli_path(runtime_config),
        "--conf",
        f"plugins/imageio/format/avif/quality={variant['quality']}",
        "--conf",
        f"plugins/imageio/format/avif/bpp={variant.get('bpp', 10)}",
        "--conf",
        f"plugins/imageio/format/avif/color_mode={'true' if variant.get('color_mode', False) else 'false'}",
        "--conf",
        f"plugins/imageio/format/avif/subsample={variant.get('subsample', 0)}",
        "--conf",
        f"plugins/imageio/format/avif/compression_type={variant.get('compression_type', 1)}",
        "--conf",
        f"plugins/imageio/format/avif/tiling={'true' if variant.get('tiling', True) else 'false'}",
    ]
    logger.info("Exporting %s as %s", request.image.key, request.role)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=int(darktable.get("export_timeout_seconds", 300)),
        check=False,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise ExportError(
            f"darktable-cli failed for {request.image.source_path} "
            f"(exit {completed.returncode}): {details[-2000:]}"
        )
    validate_avif(destination)


def prepare_assets(
    config: Mapping[str, Any],
    requests: Sequence[AssetRequest],
    source_states: Mapping[str, SourceState],
    previous_manifest: Mapping[str, Any] | None,
    staging_dir: Path,
    logger: logging.Logger,
) -> list[PreparedAsset]:
    site_root: Path = config["_site_root"]
    cache = manifest_asset_cache(previous_manifest)
    prepared: list[PreparedAsset] = []
    for request in requests:
        source_state = source_states[request.image.key]
        current_variant_signature = variant_signature(config, request)
        cached = cache.get(request.key)
        if (
            cached
            and cached.get("source_signature") == source_state.source_signature
            and cached.get("variant_signature") == current_variant_signature
            and isinstance(cached.get("path"), str)
            and isinstance(cached.get("sha256"), str)
            and Path(normalize_relpath(cached["path"])).parent.as_posix().casefold()
            == Path(normalize_relpath(request.output_dir)).as_posix().casefold()
        ):
            cached_path = safe_site_path(site_root, cached["path"])
            if cached_path.is_file() and sha256_file(cached_path) == cached["sha256"]:
                validate_avif(cached_path)
                prepared.append(
                    PreparedAsset(
                        request=request,
                        relative_path=normalize_relpath(cached["path"]),
                        sha256=cached["sha256"],
                        source_signature=source_state.source_signature,
                        variant_signature=current_variant_signature,
                        staged_path=None,
                    )
                )
                continue

        staged_path = staging_dir / "exports" / f"{len(prepared):05d}.avif"
        export_with_darktable(config, request, staged_path, logger)
        rendered_hash = sha256_file(staged_path)
        filename = (
            f"{safe_filename_stem(request.image.filename)}-dt{request.image.image_id}"
            f"v{request.image.version}-{rendered_hash[:12]}.avif"
        )
        relative_path = normalize_relpath(Path(request.output_dir) / filename)
        prepared.append(
            PreparedAsset(
                request=request,
                relative_path=relative_path,
                sha256=rendered_hash,
                source_signature=source_state.source_signature,
                variant_signature=current_variant_signature,
                staged_path=staged_path,
            )
        )
    return prepared


def prepared_by_key(assets: Sequence[PreparedAsset]) -> dict[str, PreparedAsset]:
    return {asset.request.key: asset for asset in assets}


def build_site_data(
    snapshot: CatalogSnapshot,
    assets: Sequence[PreparedAsset],
    definitions: Mapping[str, ProjectDefinition],
) -> dict[str, Any]:
    by_key = prepared_by_key(assets)
    home_images = []
    for image in snapshot.home:
        asset = by_key[f"home:{image.key}"]
        home_images.append({"src": asset.relative_path, "alt": asset.request.alt})

    info_image = snapshot.info[0]
    info_asset = by_key[f"info:{info_image.key}"]

    projects = []
    definition_order = list(definitions)
    for slug in definition_order:
        images = snapshot.projects[slug]
        if not images:
            continue
        definition = definitions[slug]
        project_images = []
        for image in images:
            main = by_key[f"project:{slug}:main:{image.key}"]
            grid = by_key[f"project:{slug}:grid:{image.key}"]
            lightbox = by_key[f"project:{slug}:lightbox:{image.key}"]
            project_images.append(
                {
                    "main": main.relative_path,
                    "grid": grid.relative_path,
                    "lightbox": lightbox.relative_path,
                    "alt": main.request.alt,
                }
            )
        projects.append(
            {
                "title": definition.title,
                "slug": definition.slug,
                "page": definition.page,
                "images": project_images,
            }
        )
    return {
        "home": {"images": home_images},
        "info": {"image": {"src": info_asset.relative_path, "alt": info_asset.request.alt}},
        "projects": projects,
    }


def render_data_js(site_data: Mapping[str, Any]) -> str:
    payload = json.dumps(site_data, ensure_ascii=False, indent=2)
    return (
        "/* Generated by automation/portfolio_sync.py from darktable tags.\n"
        "   Do not edit this file by hand; change tags/order in darktable instead. */\n"
        f"window.PORTFOLIO_DATA = {payload};\n"
    )


def replace_exactly_once(pattern: str, replacement: str, text: str, label: str) -> str:
    updated, count = re.subn(
        pattern, lambda _match: replacement, text, flags=re.DOTALL
    )
    if count != 1:
        raise ValidationError(f"Could not update {label}; expected one matching element")
    return updated


def render_index_html(current: str, first: Mapping[str, str]) -> str:
    escaped_src = html.escape(first["src"], quote=True)
    escaped_alt = html.escape(first["alt"], quote=True)
    current = replace_exactly_once(
        r'<meta property="og:image" content="[^"]*"\s*/>',
        f'<meta property="og:image" content="{escaped_src}" />',
        current,
        "index.html Open Graph image",
    )
    return replace_exactly_once(
        r'<img class="carousel-image is-active"\s+src="[^"]*"\s+alt="[^"]*"\s*/>',
        f'<img class="carousel-image is-active" src="{escaped_src}" alt="{escaped_alt}" />',
        current,
        "index.html fallback image",
    )


def render_info_html(current: str, info_image: Mapping[str, str]) -> str:
    escaped_src = html.escape(info_image["src"], quote=True)
    escaped_alt = html.escape(info_image["alt"], quote=True)
    current = replace_exactly_once(
        r'<meta property="og:image" content="[^"]*"\s*/>',
        f'<meta property="og:image" content="{escaped_src}" />',
        current,
        "info.html Open Graph image",
    )
    return replace_exactly_once(
        r'<figure class="info-image">\s*<img\s+src="[^"]*"\s+alt="[^"]*"\s*/>\s*</figure>',
        (
            '<figure class="info-image">\n'
            f'          <img src="{escaped_src}" alt="{escaped_alt}" />\n'
            "        </figure>"
        ),
        current,
        "info.html image",
    )


def render_project_pages(
    config: Mapping[str, Any],
    snapshot: CatalogSnapshot,
    definitions: Mapping[str, ProjectDefinition],
    site_data: Mapping[str, Any],
) -> dict[str, str]:
    template_path: Path = config["output"]["_project_template"]
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Unable to read project template {template_path}: {exc}") from exc
    data_projects = {project["slug"]: project for project in site_data["projects"]}
    rendered: dict[str, str] = {}
    for slug, definition in sorted(definitions.items()):
        project = data_projects[slug]
        first = project["images"][0]
        values = {
            "title": html.escape(definition.title, quote=True),
            "slug": html.escape(definition.slug, quote=True),
            "page": html.escape(definition.page, quote=True),
            "description": html.escape(definition.description, quote=True),
            "first_image": html.escape(first["main"], quote=True),
            "first_alt": html.escape(first["alt"], quote=True),
            "count": len(snapshot.projects[slug]),
        }
        try:
            page_text = template.format(**values)
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"Invalid project template {template_path}: {exc}") from exc
        if "{" in page_text and "}" in page_text:
            # HTML may legitimately contain braces in scripts, but the current template does not.
            pass
        rendered[normalize_relpath(definition.page)] = page_text
    return rendered


def build_generated_text_files(
    config: Mapping[str, Any],
    snapshot: CatalogSnapshot,
    site_data: Mapping[str, Any],
    definitions: Mapping[str, ProjectDefinition],
) -> dict[str, bytes]:
    site_root: Path = config["_site_root"]
    index_path = safe_site_path(site_root, "index.html")
    info_path = safe_site_path(site_root, "info.html")
    try:
        index_current = index_path.read_text(encoding="utf-8")
        info_current = info_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Unable to read site HTML: {exc}") from exc

    text_files: dict[str, str] = {
        normalize_relpath(config["output"]["data_file"]): render_data_js(site_data),
        "index.html": render_index_html(index_current, site_data["home"]["images"][0]),
        "info.html": render_info_html(info_current, site_data["info"]["image"]),
    }
    text_files.update(render_project_pages(config, snapshot, definitions, site_data))
    return {path: text.encode("utf-8") for path, text in text_files.items()}


def discover_bootstrap_legacy_files(config: Mapping[str, Any]) -> set[str]:
    if not config["output"].get("bootstrap_legacy_cleanup", True):
        return set()
    site_root: Path = config["_site_root"]
    roots = [normalize_relpath(root).rstrip("/") + "/" for root in config["output"].get("legacy_image_roots", [])]
    if not roots:
        return set()
    referenced: set[str] = set()
    for relative in (config["output"]["data_file"], "index.html", "info.html"):
        path = safe_site_path(site_root, relative)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.findall(r'["\'](Images/[^"\']+?\.avif)["\']', text, flags=re.IGNORECASE):
            normalized = normalize_relpath(match)
            if any(normalized.casefold().startswith(root.casefold()) for root in roots):
                if safe_site_path(site_root, normalized).is_file():
                    referenced.add(normalized)
    return referenced


def validate_owned_path(
    config: Mapping[str, Any], relative: str, *, allow_legacy: bool = False
) -> None:
    normalized = normalize_relpath(relative)
    image_root = Path(normalize_relpath(config["output"]["image_root"]))
    path = Path(normalized)
    candidate = safe_site_path(config["_site_root"], normalized)
    try:
        generated_parts = path.relative_to(image_root).parts
    except ValueError:
        generated_parts = ()
    filename_re = re.compile(r"[A-Za-z0-9_-]{1,80}-dt\d+v\d+-[0-9a-f]{12}\.avif\Z")
    valid_generated_layout = (
        len(generated_parts) == 2
        and generated_parts[0] in {"Home", "Info"}
        and filename_re.fullmatch(generated_parts[1])
    ) or (
        len(generated_parts) == 4
        and generated_parts[0] == "Projects"
        and SLUG_RE.fullmatch(generated_parts[1]) is not None
        and generated_parts[2] in {"Carousel", "Grid", "Lightbox"}
        and filename_re.fullmatch(generated_parts[3]) is not None
    )
    if valid_generated_layout:
        if candidate.exists() and not candidate.is_file():
            raise ValidationError(f"Owned generated asset is not a regular file: {relative}")
        return
    if (
        len(path.parts) == 1
        and path.suffix.casefold() == ".html"
        and SLUG_RE.fullmatch(path.stem)
        and path.stem not in RESERVED_PAGE_SLUGS
    ):
        if candidate.exists() and not candidate.is_file():
            raise ValidationError(f"Owned generated page is not a regular file: {relative}")
        return
    if allow_legacy:
        legacy_roots = [
            normalize_relpath(root).rstrip("/") + "/"
            for root in config["output"].get("legacy_image_roots", [])
        ]
        if any(normalized.casefold().startswith(root.casefold()) for root in legacy_roots):
            if path.suffix.casefold() == ".avif" and (
                not candidate.exists() or candidate.is_file()
            ):
                return
    raise ValidationError(f"Manifest contains an unsafe owned path; refusing cleanup: {relative}")


def build_manifest(
    config: Mapping[str, Any],
    snapshot: CatalogSnapshot,
    assets: Sequence[PreparedAsset],
    definitions: Mapping[str, ProjectDefinition],
    text_files: Mapping[str, bytes],
) -> dict[str, Any]:
    pages = sorted(normalize_relpath(definition.page) for definition in definitions.values())
    owned_files = sorted({asset.relative_path for asset in assets}.union(pages))
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "catalog_digest": snapshot.digest,
        "tags": {
            "home": config["tags"]["home"],
            "info": config["tags"]["info"],
            "projects": dict(sorted(snapshot.project_tags.items())),
        },
        "assets": [asset.manifest_entry() for asset in sorted(assets, key=lambda item: item.request.key)],
        "generated_pages": pages,
        "owned_files": owned_files,
        "text_sha256": {
            normalize_relpath(path): sha256_bytes(data)
            for path, data in sorted(text_files.items())
        },
        "retained_files": [],
    }


def parse_manifest_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def apply_stale_retention(
    config: Mapping[str, Any],
    manifest: dict[str, Any],
    previous_manifest: Mapping[str, Any] | None,
    bootstrap_legacy: set[str],
) -> None:
    site_root: Path = config["_site_root"]
    retention_days = int(config["output"].get("stale_retention_days", 14))
    now = dt.datetime.now(dt.timezone.utc)
    delete_after = (now + dt.timedelta(days=retention_days)).isoformat(timespec="seconds")
    desired_active = {
        relative_path_key(path): normalize_relpath(path) for path in manifest["owned_files"]
    }
    retained: dict[str, dict[str, str]] = {}

    if previous_manifest:
        for entry in previous_manifest.get("retained_files", []):
            relative = normalize_relpath(entry["path"])
            key = relative_path_key(relative)
            if key in desired_active:
                continue
            if retention_days > 0 and parse_manifest_time(entry["delete_after"]) > now:
                retained_path = safe_site_path(site_root, relative)
                if not retained_path.exists():
                    continue
                if not retained_path.is_file() or sha256_file(retained_path) != entry["sha256"]:
                    raise ValidationError(
                        f"Retained managed file was modified: {relative}. Refusing to replace/delete it."
                    )
                retained[key] = {
                    "path": relative,
                    "sha256": entry["sha256"],
                    "delete_after": entry["delete_after"],
                    "kind": entry["kind"],
                }

        prior_assets = {
            relative_path_key(entry["path"]): (normalize_relpath(entry["path"]), entry["sha256"])
            for entry in previous_manifest.get("assets", [])
        }
        prior_text = {
            relative_path_key(path): checksum
            for path, checksum in previous_manifest.get("text_sha256", {}).items()
        }
        prior_pages = {
            relative_path_key(page) for page in previous_manifest.get("generated_pages", [])
        }
        prior_owned = {
            relative_path_key(path): normalize_relpath(path)
            for path in previous_manifest.get("owned_files", [])
        }
        newly_retired = set(prior_owned) - set(desired_active)
        for key in sorted(newly_retired):
            relative = prior_owned[key]
            if key in retained or retention_days == 0:
                continue
            if key in prior_assets:
                relative, checksum = prior_assets[key]
                kind = "asset"
            elif key in prior_pages and key in prior_text:
                checksum = prior_text[key]
                kind = "page"
            else:
                raise ValidationError(
                    f"Prior manifest has no integrity hash for retired path: {relative}"
                )
            retired_path = safe_site_path(site_root, relative)
            if not retired_path.exists():
                continue
            if not retired_path.is_file() or sha256_file(retired_path) != checksum:
                raise ValidationError(
                    f"Managed file changed before retirement: {relative}. Refusing to replace/delete it."
                )
            retained[key] = {
                "path": relative,
                "sha256": checksum,
                "delete_after": delete_after,
                "kind": kind,
            }

    if retention_days > 0:
        bootstrap = {
            relative_path_key(path): normalize_relpath(path) for path in bootstrap_legacy
        }
        for key in sorted(set(bootstrap) - set(desired_active)):
            relative = bootstrap[key]
            path = safe_site_path(site_root, relative)
            if path.is_file():
                retained[key] = {
                    "path": relative,
                    "sha256": sha256_file(path),
                    "delete_after": delete_after,
                    "kind": "legacy",
                }

    manifest["retained_files"] = sorted(retained.values(), key=lambda entry: entry["path"])


def manifest_without_timestamp(manifest: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(manifest)
    result.pop("generated_at", None)
    return result


def verify_snapshot_unchanged(
    config: Mapping[str, Any],
    initial: CatalogSnapshot,
    initial_sources: Mapping[str, SourceState],
) -> None:
    check_archive_guard(config)
    current = read_catalog_snapshot(config)
    if current.digest != initial.digest:
        raise StaleSnapshotError(
            "darktable tags, ordering, or edit state changed during export; staged output was discarded"
        )
    current_sources = collect_source_states(config, current.all_images())
    if current_sources != initial_sources:
        raise StaleSnapshotError(
            "A selected RAW or XMP changed during export; staged output was discarded"
        )


def write_temp_sibling(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def copy_asset_to_temp_sibling(
    source: Path, destination: Path, expected_sha256: str
) -> Path:
    """Copy an asset through a destination-local temp file and verify its bytes.

    Creating the file below the public target directory is important on Windows:
    a file moved directly from the staging tree keeps the staging tree's DACL,
    while a newly created file inherits the target directory's permissions.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as source_handle, handle:
            if os.name != "nt":
                # NamedTemporaryFile starts at 0600. Mirror the destination
                # directory's normal access classes without carrying staging
                # permissions into the public tree.
                parent_mode = stat.S_IMODE(destination.parent.stat().st_mode)
                os.fchmod(handle.fileno(), parent_mode & 0o666)
            for block in iter(lambda: source_handle.read(1024 * 1024), b""):
                handle.write(block)
                digest.update(block)
            handle.flush()
            os.fsync(handle.fileno())
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValidationError(
                f"Prepared asset changed before publication: {source}"
            )
        validate_avif(temp_path)
        return temp_path
    except BaseException:
        if not handle.closed:
            handle.close()
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def restore_file(path: Path, previous: bytes | None) -> None:
    if previous is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    temp = write_temp_sibling(path, previous)
    os.replace(temp, path)


def publish_transaction(
    config: Mapping[str, Any],
    assets: Sequence[PreparedAsset],
    text_files: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    previous_manifest: Mapping[str, Any] | None,
    bootstrap_legacy: set[str],
    staging_dir: Path,
    logger: logging.Logger,
    site_input_state: Mapping[Path, str | None] | None = None,
) -> None:
    site_root: Path = config["_site_root"]
    manifest_path: Path = config["output"]["_manifest_file"]
    desired_owned = {
        relative_path_key(path): normalize_relpath(path) for path in manifest["owned_files"]
    }
    desired_retained = {
        relative_path_key(entry["path"]): entry
        for entry in manifest.get("retained_files", [])
    }
    prior_owned = {
        relative_path_key(path): normalize_relpath(path)
        for path in (previous_manifest.get("owned_files", []) if previous_manifest else [])
    }
    prior_retained = {
        relative_path_key(entry["path"]): entry
        for entry in (previous_manifest.get("retained_files", []) if previous_manifest else [])
    }
    bootstrap_paths = {
        relative_path_key(path): normalize_relpath(path) for path in bootstrap_legacy
    }
    stale_keys = sorted(
        (set(prior_owned) | set(prior_retained) | set(bootstrap_paths))
        - set(desired_owned)
        - set(desired_retained)
    )
    previous_pages = {
        relative_path_key(path)
        for path in (previous_manifest.get("generated_pages", []) if previous_manifest else [])
    }
    prior_asset_hashes = {
        relative_path_key(entry["path"]): entry["sha256"]
        for entry in (previous_manifest.get("assets", []) if previous_manifest else [])
    }
    prior_text_hashes = {
        relative_path_key(path): checksum
        for path, checksum in (previous_manifest.get("text_sha256", {}) if previous_manifest else {}).items()
    }
    stale: list[str] = []
    for key in stale_keys:
        retained_entry = prior_retained.get(key)
        relative = (
            normalize_relpath(retained_entry["path"])
            if retained_entry
            else prior_owned.get(key) or bootstrap_paths[key]
        )
        stale.append(relative)
        kind = retained_entry.get("kind") if retained_entry else None
        allow_legacy = key in bootstrap_paths or kind == "legacy"
        validate_owned_path(config, relative, allow_legacy=allow_legacy)
        candidate = safe_site_path(site_root, relative)
        if not candidate.exists():
            continue
        if not candidate.is_file():
            raise ValidationError(f"Refusing to retire a non-file path: {relative}")
        expected_hash = (
            retained_entry.get("sha256")
            if retained_entry
            else prior_asset_hashes.get(key) or prior_text_hashes.get(key)
        )
        if expected_hash is None and key in bootstrap_paths:
            expected_hash = sha256_file(candidate)
        if expected_hash is None or sha256_file(candidate) != expected_hash:
            raise ValidationError(
                f"Managed stale file was modified; refusing to delete it: {relative}"
            )
        if (key in previous_pages or kind == "page") and candidate.is_file():
            prefix = candidate.read_bytes()[:512]
            if b"Generated by the portfolio automation pipeline" not in prefix:
                raise ValidationError(
                    f"Refusing to retire a project page without the generator marker: {relative}"
                )

    new_asset_paths: list[Path] = []
    previous_text: dict[Path, bytes | None] = {}
    moved_stale: list[tuple[Path, Path]] = []
    temp_files: list[Path] = []
    trash_root = staging_dir / "trash"
    if site_input_state is not None:
        verify_site_input_state(site_input_state)
    try:
        # Content-addressed assets are installed before references point at them.
        for asset in assets:
            destination = safe_site_path(site_root, asset.relative_path)
            destination_existed = destination.exists()
            if destination_existed:
                if sha256_file(destination) != asset.sha256:
                    raise ValidationError(
                        f"Refusing to overwrite unexpected generated asset: {destination}"
                    )
                # A cache hit has no staged file and needs no publication. A
                # freshly rendered asset may have identical bytes/path after
                # an exporter-revision bump; republish it so destination-local
                # permissions are repaired as well.
                if asset.staged_path is None:
                    continue
            if asset.staged_path is None or not asset.staged_path.is_file():
                raise ValidationError(f"Prepared asset is missing: {asset.relative_path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Recheck after mkdir so a pre-existing symlink/junction component
            # cannot be introduced by the directory-creation step.
            destination = safe_site_path(site_root, asset.relative_path)
            asset_temp = copy_asset_to_temp_sibling(
                asset.staged_path, destination, asset.sha256
            )
            temp_files.append(asset_temp)
            os.replace(asset_temp, destination)
            temp_files.remove(asset_temp)
            if not destination_existed:
                new_asset_paths.append(destination)

        prepared_text: dict[Path, Path] = {}
        for relative, data in text_files.items():
            target = safe_site_path(site_root, relative)
            previous_text[target] = target.read_bytes() if target.exists() else None
            prepared_text[target] = write_temp_sibling(target, data)
            temp_files.append(prepared_text[target])
        previous_text[manifest_path] = manifest_path.read_bytes() if manifest_path.exists() else None
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest_temp = write_temp_sibling(manifest_path, manifest_bytes)
        temp_files.append(manifest_temp)

        for target, temp in prepared_text.items():
            if site_input_state is not None:
                expected_hash = site_input_state.get(target)
                if expected_hash is None:
                    if target.exists():
                        raise StaleSnapshotError(
                            f"Site target appeared before publication: {target}"
                        )
                elif not target.is_file() or sha256_file(target) != expected_hash:
                    raise StaleSnapshotError(
                        f"Site target changed before publication: {target}"
                    )
            os.replace(temp, target)
            temp_files.remove(temp)

        # Stale files are moved, not deleted, so rollback can restore them.
        for index, relative in enumerate(stale):
            source = safe_site_path(site_root, relative)
            if not source.exists():
                continue
            trash = trash_root / f"{index:05d}-{source.name}"
            trash.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, trash)
            moved_stale.append((source, trash))

        os.replace(manifest_temp, manifest_path)
        temp_files.remove(manifest_temp)
    except BaseException:
        for source, trash in reversed(moved_stale):
            if trash.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(OSError):
                    os.replace(trash, source)
        for path, previous in reversed(list(previous_text.items())):
            with contextlib.suppress(OSError):
                restore_file(path, previous)
        for path in reversed(new_asset_paths):
            with contextlib.suppress(OSError):
                path.unlink()
        raise
    finally:
        for temp in temp_files:
            with contextlib.suppress(OSError):
                temp.unlink()

    logger.info(
        "Published %d assets and %d project page(s); retired %d stale managed file(s)",
        len(assets),
        len(manifest["generated_pages"]),
        len(moved_stale),
    )


def summarize_plan(
    snapshot: CatalogSnapshot,
    requests: Sequence[AssetRequest],
    definitions: Mapping[str, ProjectDefinition],
    logger: logging.Logger,
) -> None:
    logger.info(
        "Validated tags: Home=%d, Info=%d, Projects=%s (%d rendered assets)",
        len(snapshot.home),
        len(snapshot.info),
        ", ".join(f"{slug}={len(snapshot.projects[slug])}" for slug in definitions),
        len(requests),
    )


def sync(config: Mapping[str, Any], *, dry_run: bool, logger: logging.Logger) -> bool:
    check_archive_guard(config)
    snapshot = read_catalog_snapshot(config)
    source_states = collect_source_states(config, snapshot.all_images())
    requests, definitions = build_asset_requests(config, snapshot)
    previous_manifest = load_manifest(config["output"]["_manifest_file"], config)
    validate_project_page_targets(config, definitions, previous_manifest)
    site_input_state = capture_site_input_state(config, definitions)
    summarize_plan(snapshot, requests, definitions, logger)
    if dry_run:
        logger.info("Dry run complete; no exports or site files were changed")
        return False

    bootstrap_legacy = discover_bootstrap_legacy_files(config) if previous_manifest is None else set()
    state_dir: Path = config["_state_dir"]
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stage-", dir=state_dir) as staging_value:
        staging_dir = Path(staging_value)
        assets = prepare_assets(
            config,
            requests,
            source_states,
            previous_manifest,
            staging_dir,
            logger,
        )
        site_data = build_site_data(snapshot, assets, definitions)
        text_files = build_generated_text_files(
            config, snapshot, site_data, definitions
        )
        manifest = build_manifest(config, snapshot, assets, definitions, text_files)
        apply_stale_retention(
            config, manifest, previous_manifest, bootstrap_legacy
        )

        all_text_matches = all(
            safe_site_path(config["_site_root"], relative).is_file()
            and safe_site_path(config["_site_root"], relative).read_bytes() == data
            for relative, data in text_files.items()
        )
        manifest_matches = bool(
            previous_manifest
            and manifest_without_timestamp(previous_manifest)
            == manifest_without_timestamp(manifest)
        )
        no_new_assets = all(asset.staged_path is None for asset in assets)
        if all_text_matches and manifest_matches and no_new_assets and not bootstrap_legacy:
            verify_snapshot_unchanged(config, snapshot, source_states)
            verify_site_input_state(site_input_state)
            logger.info("No portfolio changes detected")
            return False

        verify_snapshot_unchanged(config, snapshot, source_states)
        verify_site_input_state(site_input_state)
        publish_transaction(
            config,
            assets,
            text_files,
            manifest,
            previous_manifest,
            bootstrap_legacy,
            staging_dir,
            logger,
            site_input_state,
        )
        return True


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export darktable-tagged photographs into the portfolio site"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("portfolio-sync.json"),
        help="Path to the sync JSON config",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and show counts without exporting")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Treat transient deferred states (offline/locked/stale) as a successful task run",
    )
    parser.add_argument("--no-lock", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config.resolve())
        logger = setup_logging(config, args.verbose)
        lock_context = (
            contextlib.nullcontext()
            if args.no_lock
            else singleton_lock(config["_state_dir"] / "portfolio-sync.lock")
        )
        with lock_context:
            changed = sync(config, dry_run=args.dry_run, logger=logger)
        if changed:
            logger.info("Portfolio sync completed with changes")
        return 0
    except SyncError as exc:
        logger = logging.getLogger("portfolio-sync")
        if not logger.handlers:
            logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
        level = logging.WARNING if isinstance(exc, DeferredError) else logging.ERROR
        logger.log(level, "%s", exc)
        if args.scheduled and isinstance(exc, DeferredError):
            return 0
        return exc.exit_code
    except Exception:
        logger = logging.getLogger("portfolio-sync")
        if not logger.handlers:
            logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
        logger.exception("Unexpected portfolio sync failure")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
