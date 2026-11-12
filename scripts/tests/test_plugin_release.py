#!/usr/bin/env python3
"""Tests for scripts/plugin_release.py (stdlib unittest, no external deps).

Run from the repo root:  python -m unittest discover -s scripts/tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import plugin_release as release  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class PluginReleaseTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="plugin-release-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        manifest = json.loads((REPO / release.MANIFEST).read_text(encoding="utf-8"))
        for plugin in manifest["plugins"]:
            target = self.root / plugin["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / plugin["file"], target)
        (self.root / release.MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def manifest(self):
        return json.loads((self.root / release.MANIFEST).read_text(encoding="utf-8"))

    def write_manifest(self, manifest):
        (self.root / release.MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def plugin_file(self, index=0):
        return self.root / self.manifest()["plugins"][index]["file"]

    def assert_check_fails(self, fragment):
        errors = release.check(self.root)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_check_passes_on_repo(self):
        self.assertEqual(release.check(REPO), [])

    def test_check_rejects_stale_hash(self):
        with self.plugin_file().open("a", encoding="utf-8") as f:
            f.write("\n# edited\n")
        self.assert_check_fails("sha256")

    def test_check_rejects_version_mismatch(self):
        manifest = self.manifest()
        manifest["plugins"][0]["version"] = "9.9.9"
        self.write_manifest(manifest)
        self.assert_check_fails("version")

    def test_check_rejects_unknown_status(self):
        manifest = self.manifest()
        manifest["plugins"][1]["platforms"]["windows-x64"] = "ok"
        self.write_manifest(manifest)
        self.assert_check_fails("windows-x64")

    def test_check_rejects_v1_reference(self):
        path = self.plugin_file()
        path.write_text(path.read_text(encoding="utf-8") + "\nx = orca.host.v1.presets\n", encoding="utf-8")
        release.update_hashes(self.root)
        self.assert_check_fails("orca.host.v1")
        path.write_text(path.read_text(encoding="utf-8").replace("orca.host.v1.presets", "host.api_info()"),
                        encoding="utf-8")
        release.update_hashes(self.root)
        self.assert_check_fails("api_info")

    def test_check_rejects_bad_commit_sha(self):
        manifest = self.manifest()
        manifest["host"]["commit"] = "40ecc66f"
        self.write_manifest(manifest)
        self.assert_check_fails("commit")

    def test_check_rejects_missing_fields(self):
        manifest = self.manifest()
        del manifest["plugins"][0]["min_host"]
        del manifest["host"]["required_api"]
        self.write_manifest(manifest)
        self.assert_check_fails("min_host")
        self.assert_check_fails("required_api")

    def test_build_is_deterministic(self):
        first, second = self.root / "dist1", self.root / "dist2"
        release.build(self.root, first)
        release.build(self.root, second)
        names = sorted(p.name for p in first.iterdir())
        self.assertEqual(names, sorted(["SHA256SUMS", "compatibility.json", "orca_ai_assist.py",
                                        "orca_filament_qr.py"]))
        for name in names:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
        sums = (first / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        self.assertEqual([line.split("  ")[1] for line in sums], sorted(n for n in names if n != "SHA256SUMS"))

    def test_build_refuses_invalid_manifest(self):
        manifest = self.manifest()
        manifest["plugins"][0]["sha256"] = "0" * 64
        self.write_manifest(manifest)
        with self.assertRaises(release.ReleaseError):
            release.build(self.root, self.root / "dist")
        self.assertFalse((self.root / "dist").exists())

    def test_not_tested_is_default_for_new_platform(self):
        manifest = self.manifest()
        del manifest["plugins"][0]["platforms"]["macos-arm64"]
        self.write_manifest(manifest)
        self.assert_check_fails("macos-arm64")
        release.update_hashes(self.root)
        self.assertEqual(self.manifest()["plugins"][0]["platforms"]["macos-arm64"], "not-tested")
        self.assertEqual(release.check(self.root), [])

    def test_main_exit_codes(self):
        self.assertEqual(release.main(["check", "--root", str(self.root)]), 0)
        with self.plugin_file().open("a", encoding="utf-8") as f:
            f.write("\n")
        self.assertEqual(release.main(["check", "--root", str(self.root)]), 1)


if __name__ == "__main__":
    unittest.main()
