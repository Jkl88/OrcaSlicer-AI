#!/usr/bin/env python3
"""Validate and package the standalone OrcaSlicer plugins listed in plugins/compatibility.json.

Usage (from the repo root):
  python3 scripts/plugin_release.py check
  python3 scripts/plugin_release.py update-hashes
  python3 scripts/plugin_release.py build --out dist

check fails on a malformed manifest, a stale sha256, a header version that differs from the manifest,
an unknown platform status, or a plugin that still uses the removed fork API (orca.host.v1, api_info).
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

MANIFEST = "plugins/compatibility.json"
PLATFORMS = ("windows-x64", "linux-x64", "macos-arm64")
STATUSES = ("passed", "failed", "not-tested")
FORBIDDEN = (("orca.host.v1", re.compile(r"\bhost\.v1\b")), ("api_info", re.compile(r"\bapi_info\b")))

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_HEADER = re.compile(r"^# /// script$(.*?)^# ///$", re.M | re.S)
_HEADER_VERSION = re.compile(r'^# version = "([^"]+)"$', re.M)


class ReleaseError(Exception):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def header_version(text):
    block = _HEADER.search(text)
    match = _HEADER_VERSION.search(block.group(1)) if block else None
    return match.group(1) if match else None


def load_manifest(root):
    return json.loads((Path(root) / MANIFEST).read_text(encoding="utf-8"))


def write_manifest(root, manifest):
    (Path(root) / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _check_host(host, errors):
    if not isinstance(host, dict):
        return errors.append("host: must be an object")
    for key in ("product", "tag", "commit", "required_api"):
        if key not in host:
            errors.append(f"host: missing {key}")
    if "commit" in host and not _SHA1.match(str(host["commit"])):
        errors.append("host: commit must be a full 40-character SHA")
    api = host.get("required_api", [])
    if not isinstance(api, list) or not api or not all(isinstance(a, str) and a for a in api):
        errors.append("host: required_api must be a non-empty list of names")


def _check_plugin(root, plugin, errors):
    name = plugin.get("id", "?") if isinstance(plugin, dict) else "?"
    if not isinstance(plugin, dict):
        return errors.append("plugins: every entry must be an object")
    for key in ("id", "file", "version", "min_host", "sha256", "platforms"):
        if key not in plugin:
            errors.append(f"{name}: missing {key}")
    for key in ("version", "min_host"):
        if key in plugin and not _VERSION.match(str(plugin[key])):
            errors.append(f"{name}: {key} must look like 1.2.3")
    platforms = plugin.get("platforms")
    if isinstance(platforms, dict):
        for platform in PLATFORMS:
            if platforms.get(platform) not in STATUSES:
                errors.append(f"{name}: platforms.{platform} must be one of {', '.join(STATUSES)}")
        for platform in set(platforms) - set(PLATFORMS):
            errors.append(f"{name}: unknown platform {platform}")
    elif "platforms" in plugin:
        errors.append(f"{name}: platforms must be an object")
    path = Path(root) / str(plugin.get("file", ""))
    if "file" not in plugin:
        return
    if not path.is_file():
        return errors.append(f"{name}: file {plugin['file']} not found")
    text = path.read_text(encoding="utf-8")
    if header_version(text) != plugin.get("version"):
        errors.append(f"{name}: header version {header_version(text)} differs from manifest version "
                      f"{plugin.get('version')}")
    if "sha256" in plugin and (not _SHA256.match(str(plugin["sha256"])) or plugin["sha256"] != sha256(path)):
        errors.append(f"{name}: sha256 is stale; run update-hashes")
    for label, pattern in FORBIDDEN:
        if pattern.search(text):
            errors.append(f"{name}: uses the removed fork API {label}")


def check(root):
    """Return a list of human-readable problems; empty means the manifest is releasable."""
    try:
        manifest = load_manifest(root)
    except (OSError, ValueError) as exc:
        return [f"{MANIFEST}: {exc}"]
    errors = []
    if manifest.get("manifest_version") != 1:
        errors.append("manifest_version must be 1")
    _check_host(manifest.get("host"), errors)
    plugins = manifest.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        return errors + ["plugins: must be a non-empty list"]
    ids = [p.get("id") for p in plugins if isinstance(p, dict)]
    if len(ids) != len(set(ids)):
        errors.append("plugins: duplicate id")
    names = [Path(str(p.get("file", ""))).name for p in plugins if isinstance(p, dict)]
    if len(names) != len(set(names)):
        errors.append("plugins: file names must be unique")
    for plugin in plugins:
        _check_plugin(root, plugin, errors)
    return errors


def update_hashes(root):
    """Rewrite sha256 fields and add missing platforms as not-tested; nothing else changes."""
    manifest = load_manifest(root)
    for plugin in manifest["plugins"]:
        plugin["sha256"] = sha256(Path(root) / plugin["file"])
        platforms = plugin.setdefault("platforms", {})
        for platform in PLATFORMS:
            platforms.setdefault(platform, "not-tested")
    write_manifest(root, manifest)


def build(root, out):
    errors = check(root)
    if errors:
        raise ReleaseError("\n".join(errors))
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = [root / p["file"] for p in load_manifest(root)["plugins"]] + [root / MANIFEST]
    for path in files:
        shutil.copyfile(path, out / path.name)
    sums = sorted((path.name, sha256(out / path.name)) for path in files)
    (out / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sums), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "update-hashes", "build"))
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--out", default="dist")
    args = parser.parse_args(argv)
    if args.command == "update-hashes":
        update_hashes(args.root)
    elif args.command == "build":
        try:
            build(args.root, args.out)
        except ReleaseError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    errors = check(args.root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
