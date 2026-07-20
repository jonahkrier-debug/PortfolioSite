from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from automation import portfolio_sync as portfolio_sync  # noqa: E402


def valid_avif(marker: bytes) -> bytes:
    """Return enough of an AVIF-like ISO-BMFF header for validate_avif()."""

    return b"\x00\x00\x00\x20ftypavif\x00\x00\x00\x00avifmif1" + marker * 16


class PortfolioSyncTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.site_root = self.root / "site"
        self.state_dir = self.root / "state"
        self.archive_root = self.root / "archive"
        self.library_db = self.root / "library.db"
        self.data_db = self.root / "data.db"
        self.site_root.mkdir()
        (self.archive_root / "Captures").mkdir(parents=True)
        (self.archive_root / "darktable").mkdir()
        (self.root / "darktable-cli").write_bytes(b"deterministic fake darktable-cli")
        self._create_catalogs()

        self.config = {
            "_config_path": self.root / "portfolio-sync.json",
            "_site_root": self.site_root,
            "_state_dir": self.state_dir,
            "darktable": {
                "_cli": self.root / "darktable-cli",
                "_library_db": self.library_db,
                "_data_db": self.data_db,
                "read_retries": 1,
                "read_retry_seconds": 0,
            },
            "archive_guard": {
                "root": str(self.archive_root),
                "required_paths": ["Captures", "darktable"],
                "require_selected_sources": True,
            },
            "tags": {
                "namespace": "portfolio",
                "home": "portfolio|home",
                "info": "portfolio|info",
                "project_prefix": "portfolio|project|",
            },
            "minimum_home_images": 1,
            "required_projects": [],
            "projects": {},
            "variants": {
                name: {"width": 100, "height": 100, "quality": 50}
                for name in (
                    "home",
                    "info",
                    "project_main",
                    "project_grid",
                    "project_lightbox",
                )
            },
            "output": {
                "image_root": "Images/Generated",
                "data_file": "data/projects.js",
                "manifest_file": "data/portfolio-manifest.json",
                "project_template": "project.html.template",
                "legacy_image_roots": [
                    "Images/Home",
                    "Images/Info",
                    "Images/SelectedWorks",
                ],
                "_image_root": self.site_root / "Images" / "Generated",
                "_data_file": self.site_root / "data" / "projects.js",
                "_manifest_file": self.site_root / "data" / "portfolio-manifest.json",
                "_project_template": self.root / "project.html.template",
            },
        }
        self.logger = logging.getLogger(f"portfolio-sync-test-{id(self)}")
        self.logger.handlers[:] = [logging.NullHandler()]
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_catalogs(self) -> None:
        with contextlib.closing(sqlite3.connect(self.library_db)) as connection:
            connection.executescript(
                """
                CREATE TABLE film_rolls (
                    id INTEGER PRIMARY KEY,
                    folder TEXT NOT NULL
                );
                CREATE TABLE images (
                    id INTEGER PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT 0,
                    film_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    datetime_taken INTEGER,
                    change_timestamp INTEGER,
                    write_timestamp INTEGER
                );
                CREATE TABLE tagged_images (
                    imgid INTEGER NOT NULL,
                    tagid INTEGER NOT NULL,
                    position INTEGER NOT NULL
                );
                CREATE TABLE history_hash (
                    imgid INTEGER PRIMARY KEY,
                    current_hash BLOB
                );
                CREATE TABLE meta_data (
                    id INTEGER NOT NULL,
                    key INTEGER NOT NULL,
                    value TEXT
                );
                """
            )
            connection.commit()
        with contextlib.closing(sqlite3.connect(self.data_db)) as connection:
            connection.execute(
                "CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            )
            connection.commit()

    def add_tag(self, tag_id: int, name: str) -> None:
        with contextlib.closing(sqlite3.connect(self.data_db)) as connection:
            connection.execute("INSERT INTO tags(id, name) VALUES (?, ?)", (tag_id, name))
            connection.commit()

    def seed_base_tags(self) -> None:
        self.add_tag(1, "portfolio")
        self.add_tag(2, "portfolio|home")
        self.add_tag(3, "portfolio|info")

    def add_image(
        self,
        image_id: int,
        *,
        tags: list[tuple[int, int]],
        filename: str | None = None,
        version: int = 0,
        description: str = "",
    ) -> Path:
        filename = filename or f"image-{image_id}.nef"
        folder = self.archive_root / "Captures"
        source = folder / filename
        source.write_bytes(f"raw-{image_id}".encode("ascii"))
        if version == 0:
            sidecar = Path(f"{source}.xmp")
        else:
            sidecar = source.with_name(f"{source.stem}_{version:02d}{source.suffix}.xmp")
        sidecar.write_bytes(b"sidecar")

        with contextlib.closing(sqlite3.connect(self.library_db)) as connection:
            connection.execute(
                "INSERT INTO film_rolls(id, folder) VALUES (?, ?)",
                (image_id, str(folder)),
            )
            connection.execute(
                """
                INSERT INTO images(
                    id, version, film_id, filename, datetime_taken,
                    change_timestamp, write_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (image_id, version, image_id, filename, 1000 + image_id, 2000, 3000),
            )
            connection.execute(
                "INSERT INTO history_hash(imgid, current_hash) VALUES (?, ?)",
                (image_id, bytes([image_id % 256]) * 8),
            )
            if description:
                connection.execute(
                    "INSERT INTO meta_data(id, key, value) VALUES (?, 3, ?)",
                    (image_id, description),
                )
            connection.executemany(
                "INSERT INTO tagged_images(imgid, tagid, position) VALUES (?, ?, ?)",
                [(image_id, tag_id, position) for tag_id, position in tags],
            )
            connection.commit()
        return source

    def seed_valid_selection(self) -> tuple[Path, Path]:
        self.seed_base_tags()
        home_source = self.add_image(1, tags=[(2, 10)])
        info_source = self.add_image(2, tags=[(3, 10)])
        return home_source, info_source

    def seed_last_good_site(self) -> None:
        (self.site_root / "data").mkdir(exist_ok=True)
        (self.site_root / "Images" / "Generated").mkdir(parents=True, exist_ok=True)
        (self.site_root / "index.html").write_bytes(b"last-good-index")
        (self.site_root / "info.html").write_bytes(b"last-good-info")
        (self.site_root / "data" / "projects.js").write_bytes(b"last-good-data")
        (self.site_root / "data" / "portfolio-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "assets": [],
                    "owned_files": [],
                }
            ),
            encoding="utf-8",
        )

    def site_snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.site_root).as_posix(): path.read_bytes()
            for path in sorted(self.site_root.rglob("*"))
            if path.is_file()
        }


class CatalogSelectionTests(PortfolioSyncTestCase):
    def test_uninitialized_namespace_is_not_treated_as_an_empty_site(self) -> None:
        self.seed_last_good_site()
        before = self.site_snapshot()

        with self.assertRaisesRegex(portfolio_sync.ValidationError, "not initialized"):
            portfolio_sync.sync(self.config, dry_run=False, logger=self.logger)

        self.assertEqual(before, self.site_snapshot())

    def test_info_tag_with_no_image_preserves_the_last_good_site(self) -> None:
        self.seed_base_tags()
        self.add_image(1, tags=[(2, 10)])
        self.seed_last_good_site()
        before = self.site_snapshot()

        with self.assertRaisesRegex(
            portfolio_sync.ValidationError, "exactly one Info/About image; found 0"
        ):
            portfolio_sync.sync(self.config, dry_run=False, logger=self.logger)

        self.assertEqual(before, self.site_snapshot())

    def test_info_tag_with_multiple_images_preserves_the_last_good_site(self) -> None:
        self.seed_base_tags()
        self.add_image(1, tags=[(2, 10)])
        self.add_image(2, tags=[(3, 10)])
        self.add_image(3, tags=[(3, 20)])
        self.seed_last_good_site()
        before = self.site_snapshot()

        with self.assertRaisesRegex(
            portfolio_sync.ValidationError, "exactly one Info/About image; found 2"
        ):
            portfolio_sync.sync(self.config, dry_run=False, logger=self.logger)

        self.assertEqual(before, self.site_snapshot())

    def test_future_project_tags_are_discovered_and_keep_independent_order(self) -> None:
        self.seed_base_tags()
        # Insert in reverse lexical order to prove discovery is deterministic.
        self.add_tag(40, "portfolio|project|zulu-stories")
        self.add_tag(30, "portfolio|project|future-work")
        self.add_image(1, tags=[(2, 10)])
        self.add_image(2, tags=[(3, 10)])
        self.add_image(10, tags=[(30, 20)], description="Later frame")
        self.add_image(11, tags=[(30, 10)], description="Earlier frame")
        self.add_image(12, tags=[(40, 5)])

        snapshot = portfolio_sync.read_catalog_snapshot(self.config)

        self.assertEqual(["future-work", "zulu-stories"], list(snapshot.projects))
        self.assertEqual([11, 10], [image.image_id for image in snapshot.projects["future-work"]])
        self.assertEqual([12], [image.image_id for image in snapshot.projects["zulu-stories"]])
        self.assertEqual(
            {
                "future-work": "portfolio|project|future-work",
                "zulu-stories": "portfolio|project|zulu-stories",
            },
            snapshot.project_tags,
        )

        requests, definitions = portfolio_sync.build_asset_requests(self.config, snapshot)
        self.assertEqual(["future-work", "zulu-stories"], list(definitions))
        self.assertEqual("Future Work", definitions["future-work"].title)
        self.assertEqual("future-work.html", definitions["future-work"].page)
        future_image_order = [
            request.image.image_id
            for request in requests
            if request.role.startswith("project:future-work:")
        ]
        self.assertEqual([11, 11, 11, 10, 10, 10], future_image_order)


class ArchiveAndOwnershipSafetyTests(PortfolioSyncTestCase):
    def test_disconnected_archive_defers_without_mutating_the_site(self) -> None:
        self.seed_last_good_site()
        self.config["archive_guard"]["root"] = str(self.root / "disconnected")
        before = self.site_snapshot()

        with self.assertRaisesRegex(portfolio_sync.DeferredError, "Archive is disconnected"):
            portfolio_sync.sync(self.config, dry_run=False, logger=self.logger)

        self.assertEqual(before, self.site_snapshot())

    def test_wrong_archive_identity_defers_without_reading_catalog_or_mutating_site(self) -> None:
        self.seed_last_good_site()
        self.config["archive_guard"]["volume_label"] = "DAS"
        before = self.site_snapshot()

        with mock.patch.object(
            portfolio_sync,
            "get_windows_volume_identity",
            return_value=("IMPOSTER", "\\\\?\\Volume{wrong}\\"),
        ), mock.patch.object(
            portfolio_sync,
            "read_catalog_snapshot",
            side_effect=AssertionError("catalog must not be read for the wrong archive"),
        ):
            with self.assertRaisesRegex(portfolio_sync.DeferredError, "Wrong volume mounted"):
                portfolio_sync.sync(self.config, dry_run=False, logger=self.logger)

        self.assertEqual(before, self.site_snapshot())

    def test_unsafe_manifest_cleanup_is_rejected_before_any_publication(self) -> None:
        self.seed_last_good_site()
        before = self.site_snapshot()
        previous_manifest = {
            "schema_version": 1,
            "assets": [],
            # Core pages are updated, but never owned/deleted as stale generated pages.
            "owned_files": ["index.html"],
        }
        desired_manifest = {
            "schema_version": 1,
            "assets": [],
            "generated_pages": [],
            "owned_files": [],
        }
        staging_dir = self.root / "staging"
        staging_dir.mkdir()

        with self.assertRaisesRegex(portfolio_sync.ValidationError, "unsafe owned path"):
            portfolio_sync.publish_transaction(
                self.config,
                assets=[],
                text_files={"data/projects.js": b"must-not-be-published"},
                manifest=desired_manifest,
                previous_manifest=previous_manifest,
                bootstrap_legacy=set(),
                staging_dir=staging_dir,
                logger=self.logger,
            )

        self.assertEqual(before, self.site_snapshot())

    def test_manifest_cleanup_rejects_paths_outside_explicit_owned_roots(self) -> None:
        unsafe_paths = (
            "../outside.avif",
            "data/projects.js",
            "Images/not-generated/photo.avif",
            "info.html",
        )
        for relative in unsafe_paths:
            with self.subTest(relative=relative):
                with self.assertRaises(portfolio_sync.SyncError):
                    portfolio_sync.validate_owned_path(self.config, relative)

    def test_positive_retention_keeps_retired_asset_and_records_metadata(self) -> None:
        self.config["output"]["stale_retention_days"] = 14
        asset_bytes = valid_avif(b"retained")
        asset_hash = portfolio_sync.sha256_bytes(asset_bytes)
        relative = (
            "Images/Generated/Home/"
            f"image-1-dt1v0-{asset_hash[:12]}.avif"
        )
        asset_path = self.site_root / relative
        asset_path.parent.mkdir(parents=True)
        asset_path.write_bytes(asset_bytes)
        previous_manifest = {
            "schema_version": 1,
            "assets": [{"path": relative, "sha256": asset_hash}],
            "generated_pages": [],
            "owned_files": [relative],
            "text_sha256": {},
            "retained_files": [],
        }
        desired_manifest = {
            "schema_version": 1,
            "assets": [],
            "generated_pages": [],
            "owned_files": [],
            "text_sha256": {},
            "retained_files": [],
        }

        portfolio_sync.apply_stale_retention(
            self.config, desired_manifest, previous_manifest, set()
        )

        self.assertEqual(1, len(desired_manifest["retained_files"]))
        retained = desired_manifest["retained_files"][0]
        self.assertEqual(relative, retained["path"])
        self.assertEqual(asset_hash, retained["sha256"])
        self.assertEqual("asset", retained["kind"])
        self.assertIn("delete_after", retained)

        staging_dir = self.root / "retention-staging"
        staging_dir.mkdir()
        portfolio_sync.publish_transaction(
            self.config,
            assets=[],
            text_files={},
            manifest=desired_manifest,
            previous_manifest=previous_manifest,
            bootstrap_legacy=set(),
            staging_dir=staging_dir,
            logger=self.logger,
        )

        self.assertEqual(asset_bytes, asset_path.read_bytes())
        published = json.loads(
            self.config["output"]["_manifest_file"].read_text(encoding="utf-8")
        )
        self.assertEqual([retained], published["retained_files"])


class PublicationSafetyTests(PortfolioSyncTestCase):
    def make_prepared_asset(
        self, staging_dir: Path, content: bytes
    ) -> tuple[portfolio_sync.PreparedAsset, Path, dict[str, object]]:
        self.seed_valid_selection()
        snapshot = portfolio_sync.read_catalog_snapshot(self.config)
        requests, _definitions = portfolio_sync.build_asset_requests(
            self.config, snapshot
        )
        request = requests[0]
        staged_path = staging_dir / "exports" / "00000.avif"
        staged_path.parent.mkdir(parents=True)
        staged_path.write_bytes(content)
        rendered_hash = portfolio_sync.sha256_bytes(content)
        relative = (
            "Images/Generated/Home/"
            f"permission-test-{rendered_hash[:12]}.avif"
        )
        asset = portfolio_sync.PreparedAsset(
            request=request,
            relative_path=relative,
            sha256=rendered_hash,
            source_signature="source-signature",
            variant_signature="variant-signature",
            staged_path=staged_path,
        )
        manifest: dict[str, object] = {
            "schema_version": 1,
            "assets": [asset.manifest_entry()],
            "generated_pages": [],
            "owned_files": [relative],
            "text_sha256": {},
            "retained_files": [],
        }
        return asset, self.site_root / relative, manifest

    def test_staged_asset_is_republished_through_a_target_parent_temp(self) -> None:
        staging_dir = self.root / "permission-staging"
        staging_dir.mkdir()
        content = valid_avif(b"permission-copy")
        asset, destination, manifest = self.make_prepared_asset(
            staging_dir, content
        )
        destination.parent.mkdir(parents=True)
        destination.write_bytes(content)
        if os.name != "nt":
            destination.chmod(0o600)
            asset.staged_path.chmod(0o600)
        expected_public_mode = stat.S_IMODE(destination.parent.stat().st_mode) & 0o666

        real_replace = os.replace
        installed_temps: list[tuple[Path, bytes]] = []

        def capture_replace(
            source: str | os.PathLike[str], target: str | os.PathLike[str]
        ) -> None:
            source_path = Path(source)
            if Path(target) == destination:
                installed_temps.append((source_path, source_path.read_bytes()))
            real_replace(source, target)

        with mock.patch.object(
            portfolio_sync.os, "replace", side_effect=capture_replace
        ):
            portfolio_sync.publish_transaction(
                self.config,
                assets=[asset],
                text_files={},
                manifest=manifest,
                previous_manifest=None,
                bootstrap_legacy=set(),
                staging_dir=staging_dir,
                logger=self.logger,
            )

        self.assertEqual(1, len(installed_temps))
        installed_temp, installed_bytes = installed_temps[0]
        self.assertEqual(destination.parent, installed_temp.parent)
        self.assertNotEqual(asset.staged_path, installed_temp)
        self.assertEqual(content, installed_bytes)
        self.assertEqual(content, destination.read_bytes())
        self.assertTrue(asset.staged_path.is_file())
        self.assertTrue(os.access(destination, os.R_OK))
        if os.name != "nt":
            self.assertEqual(
                expected_public_mode,
                stat.S_IMODE(destination.stat().st_mode) & 0o666,
            )

    def test_changed_staged_asset_is_rejected_without_publication(self) -> None:
        staging_dir = self.root / "changed-staging"
        staging_dir.mkdir()
        asset, destination, manifest = self.make_prepared_asset(
            staging_dir, valid_avif(b"expected")
        )
        asset.staged_path.write_bytes(valid_avif(b"changed"))

        with self.assertRaisesRegex(
            portfolio_sync.ValidationError, "changed before publication"
        ):
            portfolio_sync.publish_transaction(
                self.config,
                assets=[asset],
                text_files={},
                manifest=manifest,
                previous_manifest=None,
                bootstrap_legacy=set(),
                staging_dir=staging_dir,
                logger=self.logger,
            )

        self.assertFalse(destination.exists())
        self.assertEqual([], list(destination.parent.glob(f".{destination.name}.*.tmp")))

    def test_late_publish_failure_removes_newly_copied_asset(self) -> None:
        self.seed_last_good_site()
        before = self.site_snapshot()
        staging_dir = self.root / "rollback-staging"
        staging_dir.mkdir()
        asset, destination, manifest = self.make_prepared_asset(
            staging_dir, valid_avif(b"rollback")
        )
        previous_manifest = json.loads(
            self.config["output"]["_manifest_file"].read_text(encoding="utf-8")
        )
        manifest_path = self.config["output"]["_manifest_file"]
        real_replace = os.replace
        manifest_failure_raised = False

        def fail_first_manifest_replace(
            source: str | os.PathLike[str], target: str | os.PathLike[str]
        ) -> None:
            nonlocal manifest_failure_raised
            if Path(target) == manifest_path and not manifest_failure_raised:
                manifest_failure_raised = True
                raise OSError("simulated manifest publication failure")
            real_replace(source, target)

        with mock.patch.object(
            portfolio_sync.os, "replace", side_effect=fail_first_manifest_replace
        ):
            with self.assertRaisesRegex(OSError, "simulated manifest"):
                portfolio_sync.publish_transaction(
                    self.config,
                    assets=[asset],
                    text_files={"data/projects.js": b"new-data"},
                    manifest=manifest,
                    previous_manifest=previous_manifest,
                    bootstrap_legacy=set(),
                    staging_dir=staging_dir,
                    logger=self.logger,
                )

        self.assertTrue(manifest_failure_raised)
        self.assertEqual(before, self.site_snapshot())
        self.assertFalse(destination.exists())
        self.assertTrue(asset.staged_path.is_file())


class StaleStateAndConcurrencyTests(PortfolioSyncTestCase):
    def test_position_only_reorder_reuses_cached_assets_but_changes_snapshot(self) -> None:
        self.seed_base_tags()
        self.add_tag(30, "portfolio|project|future-work")
        # Images 1 and 3 deliberately have both Home and project roles, with
        # independent ordering positions in each exact-tag collection.
        self.add_image(1, tags=[(2, 10), (30, 30)])
        self.add_image(2, tags=[(3, 10)])
        self.add_image(3, tags=[(2, 20), (30, 10)])

        initial = portfolio_sync.read_catalog_snapshot(self.config)
        initial_sources = portfolio_sync.collect_source_states(
            self.config, initial.all_images()
        )
        initial_requests, _definitions = portfolio_sync.build_asset_requests(
            self.config, initial
        )
        self.assertEqual([1, 3], [image.image_id for image in initial.home])
        self.assertEqual(
            [3, 1],
            [image.image_id for image in initial.projects["future-work"]],
        )

        cached_paths: dict[str, str] = {}
        cached_assets: list[dict[str, str]] = []
        for index, request in enumerate(initial_requests, 1):
            relative = f"{request.output_dir}/cached-{index}.avif"
            content = valid_avif(bytes([index]))
            target = self.site_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            cached_paths[request.key] = relative
            cached_assets.append(
                {
                    "key": request.key,
                    "path": relative,
                    "sha256": portfolio_sync.sha256_bytes(content),
                    "source_signature": initial_sources[
                        request.image.key
                    ].source_signature,
                    "variant_signature": portfolio_sync.variant_signature(
                        self.config, request
                    ),
                }
            )

        with contextlib.closing(sqlite3.connect(self.library_db)) as connection:
            connection.executemany(
                "UPDATE tagged_images SET position = ? WHERE imgid = ? AND tagid = ?",
                (
                    (40, 1, 2),
                    (5, 3, 2),
                    (5, 1, 30),
                    (40, 3, 30),
                ),
            )
            connection.commit()

        reordered = portfolio_sync.read_catalog_snapshot(self.config)
        reordered_sources = portfolio_sync.collect_source_states(
            self.config, reordered.all_images()
        )
        reordered_requests, _definitions = portfolio_sync.build_asset_requests(
            self.config, reordered
        )

        self.assertEqual([3, 1], [image.image_id for image in reordered.home])
        self.assertEqual(
            [1, 3],
            [image.image_id for image in reordered.projects["future-work"]],
        )
        self.assertNotEqual(initial.digest, reordered.digest)
        self.assertEqual(initial_sources, reordered_sources)

        staging_dir = self.root / "reorder-staging"
        staging_dir.mkdir()
        with mock.patch.object(
            portfolio_sync,
            "export_with_darktable",
            side_effect=AssertionError("position-only changes must reuse cached assets"),
        ) as export_mock:
            prepared = portfolio_sync.prepare_assets(
                self.config,
                reordered_requests,
                reordered_sources,
                {"assets": cached_assets},
                staging_dir,
                self.logger,
            )

        export_mock.assert_not_called()
        self.assertTrue(all(asset.staged_path is None for asset in prepared))
        self.assertEqual(
            cached_paths,
            {asset.request.key: asset.relative_path for asset in prepared},
        )

        with self.assertRaisesRegex(
            portfolio_sync.StaleSnapshotError,
            "tags, ordering, or edit state changed during export",
        ):
            portfolio_sync.verify_snapshot_unchanged(
                self.config, initial, initial_sources
            )

    def test_catalog_reorder_between_snapshot_and_publish_is_rejected(self) -> None:
        self.seed_valid_selection()
        self.seed_last_good_site()
        initial = portfolio_sync.read_catalog_snapshot(self.config)
        source_states = portfolio_sync.collect_source_states(
            self.config, initial.all_images()
        )
        before = self.site_snapshot()

        with contextlib.closing(sqlite3.connect(self.library_db)) as connection:
            connection.execute(
                "UPDATE tagged_images SET position = 99 WHERE imgid = 1 AND tagid = 2"
            )
            connection.commit()

        with self.assertRaisesRegex(
            portfolio_sync.StaleSnapshotError,
            "tags, ordering, or edit state changed during export",
        ):
            portfolio_sync.verify_snapshot_unchanged(
                self.config, initial, source_states
            )

        self.assertEqual(before, self.site_snapshot())

    def test_same_size_same_mtime_sidecar_edit_is_still_rejected(self) -> None:
        self.seed_valid_selection()
        self.seed_last_good_site()
        initial = portfolio_sync.read_catalog_snapshot(self.config)
        source_states = portfolio_sync.collect_source_states(
            self.config, initial.all_images()
        )
        selected = initial.home[0]
        sidecar = selected.sidecar_path
        original_state = source_states[selected.key]
        old_stat = sidecar.stat()
        before = self.site_snapshot()

        # Preserve cheap filesystem metadata so this specifically exercises the XMP hash.
        sidecar.write_bytes(b"changed")
        os.utime(sidecar, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        changed_state = portfolio_sync.collect_source_states(
            self.config, initial.all_images()
        )[selected.key]
        self.assertEqual(original_state.sidecar_size, changed_state.sidecar_size)
        self.assertEqual(original_state.sidecar_mtime_ns, changed_state.sidecar_mtime_ns)
        self.assertNotEqual(original_state.sidecar_sha256, changed_state.sidecar_sha256)

        with self.assertRaisesRegex(
            portfolio_sync.StaleSnapshotError,
            "selected RAW or XMP changed during export",
        ):
            portfolio_sync.verify_snapshot_unchanged(
                self.config, initial, source_states
            )

        self.assertEqual(before, self.site_snapshot())

    def test_changed_source_signature_cannot_reuse_a_cached_asset(self) -> None:
        self.seed_valid_selection()
        snapshot = portfolio_sync.read_catalog_snapshot(self.config)
        requests, _definitions = portfolio_sync.build_asset_requests(self.config, snapshot)
        request = requests[0]
        initial_states = portfolio_sync.collect_source_states(
            self.config, snapshot.all_images()
        )
        old_relative = "Images/Generated/Home/old.avif"
        old_asset = self.site_root / old_relative
        old_asset.parent.mkdir(parents=True)
        old_bytes = valid_avif(b"o")
        old_asset.write_bytes(old_bytes)
        previous_manifest = {
            "schema_version": 1,
            "owned_files": [old_relative],
            "assets": [
                {
                    "key": request.key,
                    "path": old_relative,
                    "sha256": portfolio_sync.sha256_bytes(old_bytes),
                    "source_signature": initial_states[request.image.key].source_signature,
                    "variant_signature": portfolio_sync.variant_signature(
                        self.config, request
                    ),
                }
            ],
        }

        request.image.sidecar_path.write_bytes(b"changed")
        current_states = portfolio_sync.collect_source_states(
            self.config, snapshot.all_images()
        )
        new_bytes = valid_avif(b"n")

        def fake_export(_config, _request, destination, _logger) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(new_bytes)

        staging_dir = self.root / "staging"
        staging_dir.mkdir()
        with mock.patch.object(
            portfolio_sync, "export_with_darktable", side_effect=fake_export
        ) as export_mock:
            prepared = portfolio_sync.prepare_assets(
                self.config,
                [request],
                current_states,
                previous_manifest,
                staging_dir,
                self.logger,
            )

        export_mock.assert_called_once()
        self.assertIsNotNone(prepared[0].staged_path)
        self.assertNotEqual(old_relative, prepared[0].relative_path)
        self.assertIn(
            portfolio_sync.sha256_bytes(new_bytes)[:12], prepared[0].relative_path
        )

    def test_singleton_lock_rejects_overlap_and_is_reusable_after_release(self) -> None:
        lock_path = self.state_dir / "portfolio-sync.lock"

        with portfolio_sync.singleton_lock(lock_path):
            with self.assertRaises(portfolio_sync.AlreadyRunning):
                with portfolio_sync.singleton_lock(lock_path):
                    self.fail("the second lock acquisition unexpectedly succeeded")

        with portfolio_sync.singleton_lock(lock_path):
            pass


class EndToEndSyncTests(PortfolioSyncTestCase):
    def test_successful_publish_noop_and_project_untag_lifecycle(self) -> None:
        self.config["output"]["stale_retention_days"] = 0
        self.seed_base_tags()
        self.add_tag(30, "portfolio|project|future-work")
        self.add_image(1, tags=[(2, 10)], description="Home photograph")
        self.add_image(2, tags=[(3, 10)], description="About photograph")
        self.add_image(3, tags=[(30, 10)], description="Future photograph")

        (self.site_root / "index.html").write_text(
            """<!doctype html>
<html><head><meta property="og:image" content="Images/old-home.avif" /></head>
<body><img class="carousel-image is-active" src="Images/old-home.avif" alt="Old home" /></body></html>
""",
            encoding="utf-8",
        )
        (self.site_root / "info.html").write_text(
            """<!doctype html>
<html><head><meta property="og:image" content="Images/old-info.avif" /></head>
<body><figure class="info-image"><img src="Images/old-info.avif" alt="Old info" /></figure></body></html>
""",
            encoding="utf-8",
        )
        self.config["output"]["_project_template"].write_text(
            """<!doctype html>
<!-- Generated by the portfolio automation pipeline -->
<html><head><title>{title}</title><meta name="description" content="{description}" /></head>
<body data-project="{slug}" data-count="{count}">
<img class="project-main" src="{first_image}" alt="{first_alt}" />
</body></html>
""",
            encoding="utf-8",
        )

        exported_bytes: dict[str, bytes] = {}

        def deterministic_export(_config, request, destination, _logger) -> None:
            content = valid_avif(request.key.encode("utf-8"))
            exported_bytes[request.key] = content
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

        with mock.patch.object(
            portfolio_sync,
            "export_with_darktable",
            side_effect=deterministic_export,
        ) as export_mock:
            changed = portfolio_sync.sync(
                self.config, dry_run=False, logger=self.logger
            )

        self.assertTrue(changed)
        self.assertEqual(5, export_mock.call_count)
        manifest_path = self.config["output"]["_manifest_file"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(["future-work.html"], manifest["generated_pages"])
        self.assertEqual(
            {"future-work": "portfolio|project|future-work"},
            manifest["tags"]["projects"],
        )
        assets_by_role = {entry["role"]: entry for entry in manifest["assets"]}
        self.assertEqual(
            {
                "home",
                "info",
                "project:future-work:main",
                "project:future-work:grid",
                "project:future-work:lightbox",
            },
            set(assets_by_role),
        )
        for entry in manifest["assets"]:
            with self.subTest(asset=entry["key"]):
                asset_path = self.site_root / entry["path"]
                self.assertTrue(asset_path.is_file())
                self.assertEqual(entry["sha256"], portfolio_sync.sha256_file(asset_path))
                self.assertTrue(
                    asset_path.name.endswith(f"-{entry['sha256'][:12]}.avif")
                )
                self.assertEqual(
                    portfolio_sync.sha256_bytes(exported_bytes[entry["key"]]),
                    entry["sha256"],
                )

        data_text = self.config["output"]["_data_file"].read_text(encoding="utf-8")
        data_payload = data_text.split("window.PORTFOLIO_DATA = ", 1)[1].strip()
        site_data = json.loads(data_payload.removesuffix(";"))
        self.assertEqual(["future-work"], [item["slug"] for item in site_data["projects"]])
        future_data = site_data["projects"][0]
        self.assertEqual("future-work.html", future_data["page"])
        self.assertEqual(
            assets_by_role["project:future-work:main"]["path"],
            future_data["images"][0]["main"],
        )
        home_path = assets_by_role["home"]["path"]
        info_path = assets_by_role["info"]["path"]
        index_text = (self.site_root / "index.html").read_text(encoding="utf-8")
        info_text = (self.site_root / "info.html").read_text(encoding="utf-8")
        project_text = (self.site_root / "future-work.html").read_text(encoding="utf-8")
        self.assertEqual(2, index_text.count(home_path))
        self.assertEqual(2, info_text.count(info_path))
        self.assertIn("Future Work", project_text)
        self.assertIn(assets_by_role["project:future-work:main"]["path"], project_text)
        self.assertEqual(
            set(manifest["owned_files"]),
            {entry["path"] for entry in manifest["assets"]} | {"future-work.html"},
        )

        # An identical catalog/source/site state must not export or rewrite anything.
        published_snapshot = self.site_snapshot()
        with mock.patch.object(
            portfolio_sync,
            "export_with_darktable",
            side_effect=AssertionError("an unchanged sync must reuse valid cached assets"),
        ):
            changed = portfolio_sync.sync(
                self.config, dry_run=False, logger=self.logger
            )
        self.assertFalse(changed)
        self.assertEqual(published_snapshot, self.site_snapshot())

        project_asset_paths = [
            self.site_root / entry["path"]
            for role, entry in assets_by_role.items()
            if role.startswith("project:")
        ]
        unmanaged_file = (
            self.site_root
            / "Images"
            / "Generated"
            / "Projects"
            / "future-work"
            / "manual-note.txt"
        )
        unmanaged_file.parent.mkdir(parents=True, exist_ok=True)
        unmanaged_file.write_bytes(b"not manifest-owned")
        with contextlib.closing(sqlite3.connect(self.library_db)) as connection:
            connection.execute(
                "DELETE FROM tagged_images WHERE imgid = 3 AND tagid = 30"
            )
            connection.commit()

        # Home and Info remain cache hits; only the managed future-project files retire.
        with mock.patch.object(
            portfolio_sync,
            "export_with_darktable",
            side_effect=AssertionError("untagging a project must not re-export unchanged roles"),
        ):
            changed = portfolio_sync.sync(
                self.config, dry_run=False, logger=self.logger
            )
        self.assertTrue(changed)

        updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual([], updated_manifest["generated_pages"])
        self.assertEqual({"home", "info"}, {entry["role"] for entry in updated_manifest["assets"]})
        self.assertEqual(
            {home_path, info_path},
            {entry["path"] for entry in updated_manifest["assets"]},
        )
        self.assertFalse((self.site_root / "future-work.html").exists())
        self.assertTrue(all(not path.exists() for path in project_asset_paths))
        self.assertEqual(b"not manifest-owned", unmanaged_file.read_bytes())
        updated_data_text = self.config["output"]["_data_file"].read_text(encoding="utf-8")
        updated_payload = updated_data_text.split("window.PORTFOLIO_DATA = ", 1)[1].strip()
        self.assertEqual([], json.loads(updated_payload.removesuffix(";"))["projects"])
        self.assertTrue((self.site_root / home_path).is_file())
        self.assertTrue((self.site_root / info_path).is_file())


if __name__ == "__main__":
    unittest.main()
