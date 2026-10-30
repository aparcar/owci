"""Which firmware to test: official builds from downloads.openwrt.org (as
openwrt-tests' scripts/healthcheck.sh picks them) or a custom image."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import requests
import yaml

OPENWRT_GIT = "https://git.openwrt.org/openwrt/openwrt.git"
DOWNLOADS = "https://downloads.openwrt.org"
GITHUB_COMMITS = "https://api.github.com/repos/openwrt/openwrt/commits"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "owci"


class JobError(Exception):
    """A job cannot be created (missing image, API or download failure)."""


# --- releases --------------------------------------------------------------------


def is_snapshot(release: str) -> bool:
    return release == "SNAPSHOT" or release.endswith("-SNAPSHOT")


def downloads_dir(release: str) -> str:
    """SNAPSHOT: main branch, 25.12-SNAPSHOT: branch snapshot, 25.12.5: release."""
    if release == "SNAPSHOT":
        return f"{DOWNLOADS}/snapshots"
    return f"{DOWNLOADS}/releases/{release}"


def release_branch(release: str) -> str:
    if release == "SNAPSHOT":
        return "main"
    return "openwrt-" + ".".join(release.removesuffix("-SNAPSHOT").split(".")[:2])


def current_releases() -> list[str]:
    """Main snapshot, the stable release and its branch snapshot, like the
    daily workflow of openwrt-tests: SNAPSHOT, 25.12.5, 25.12-SNAPSHOT."""
    res = requests.get(f"{DOWNLOADS}/.versions.json", timeout=30)
    res.raise_for_status()
    stable = res.json()["stable_version"]
    branch = ".".join(stable.split(".")[:2])
    return ["SNAPSHOT", stable, f"{branch}-SNAPSHOT"]


# --- target files ------------------------------------------------------------------


class TargetLoader(yaml.SafeLoader):
    """Safe loader that accepts labgrid's `!template $VAR` values (as plain
    strings); only the `openwrt:` metadata of a target file is used here."""


TargetLoader.add_constructor("!template", lambda loader, node: loader.construct_scalar(node))


def load_target(tests: str, device: str) -> dict:
    """targets/<device>.yaml from an openwrt-tests checkout or raw URL."""
    if tests.startswith(("http://", "https://")):
        url = f"{tests.rstrip('/')}/targets/{device}.yaml"
        res = requests.get(url, timeout=30)
        if res.status_code == 404:
            raise JobError(f"no target file {url}")
        res.raise_for_status()
        text = res.text
    else:
        path = Path(tests).expanduser() / "targets" / f"{device}.yaml"
        if not path.is_file():
            raise JobError(f"no target file {path}")
        text = path.read_text()
    return yaml.load(text, Loader=TargetLoader)


def device_info(tests: str, device: str) -> dict:
    """Name, target, profile and features of a device (its target file)."""
    data = load_target(tests, device)
    meta = data.get("openwrt") or {}
    if not meta.get("target") or not meta.get("profile"):
        raise JobError(f"targets/{device}.yaml has no openwrt: target/profile")
    return {
        "name": meta.get("name") or device,
        "target": meta["target"].replace("-", "/", 1),
        "profile": meta["profile"],
        "image": meta.get("image") or {},
        "features": ((data.get("targets") or {}).get("main") or {}).get("features") or [],
    }


# --- official builds -------------------------------------------------------------------

_profiles_cache: dict = {}


def profiles_json(release: str, target: str) -> dict | None:
    """profiles.json of a release target (cached per run); None if the
    release does not build the target."""
    url = f"{downloads_dir(release)}/targets/{target}/profiles.json"
    if url not in _profiles_cache:
        res = requests.get(url, timeout=30)
        if res.status_code == 404:
            _profiles_cache[url] = None
        else:
            res.raise_for_status()
            _profiles_cache[url] = res.json()
    return _profiles_cache[url]


def resolve_official(tests: str, release: str, device: str) -> dict:
    """The device's image in a release (same choice as healthcheck.sh)."""
    info = device_info(tests, device)
    image_type = info["image"].get("type", "kernel")
    image_fs = info["image"].get("filesystem")
    profiles = profiles_json(release, info["target"])
    if profiles is None:
        raise JobError(f"{release} has no target {info['target']}")
    profile = profiles["profiles"].get(info["profile"])
    if profile is None:
        raise JobError(f"{release} {info['target']} has no profile {info['profile']}")
    for image in profile["images"]:
        if image["type"] == image_type and (not image_fs or image["filesystem"] == image_fs):
            break
    else:
        raise JobError(f"no {image_type}/{image_fs} image for {device} in {release}")
    return {
        **info,
        "release": release,
        "arch_packages": profiles["arch_packages"],
        "version_code": profiles["version_code"],
        "image": image,
        "url": f"{downloads_dir(release)}/targets/{info['target']}/{image['name']}",
    }


def download(fw: dict) -> Path:
    """The image in the local cache, checked against its sha256."""
    # Per build: main snapshot images have the same name for every build
    cache = CACHE / fw["version_code"]
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / fw["image"]["name"]
    if not path.exists():
        print(f"  downloading {fw['url']}")
        fetch(fw["url"], path)
    if sha256(path) != fw["image"]["sha256"]:
        path.unlink()
        raise JobError(f"sha256 mismatch for {path.name}")
    return path


def snapshot_commit(version_code: str) -> str:
    """r36868-c459c71c4a -> full commit hash (KCIDB wants all 40 digits)."""
    short = version_code.rsplit("-", 1)[-1]
    headers = {}
    if os.environ.get("GITHUB_TOKEN"):  # 60 requests/hour without
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    res = requests.get(f"{GITHUB_COMMITS}/{short}", headers=headers, timeout=30)
    if not res.ok:
        raise JobError(f"cannot resolve commit {short} ({version_code}): "
                       f"{res.status_code} {res.text[:200]}")
    return res.json()["sha"]


def release_commit(release: str) -> str:
    out = subprocess.run(
        ["git", "ls-remote", OPENWRT_GIT, f"refs/tags/v{release}^{{}}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if not out:
        raise JobError(f"tag v{release} not found in {OPENWRT_GIT}")
    return out[0]


def official_revision(fw: dict) -> dict:
    release = fw["release"]
    commit = snapshot_commit(fw["version_code"]) if is_snapshot(release) else release_commit(release)
    if is_snapshot(release):
        describe, tags = fw["version_code"], []
    else:
        describe, tags = f"v{release}", [f"v{release}"]
    return {"tree": "openwrt", "url": OPENWRT_GIT, "branch": release_branch(release),
            "commit": commit, "describe": describe, "commit_tags": tags}


# --- custom firmware ----------------------------------------------------------------------


def resolve_custom(tests: str, device: str, firmware: str) -> dict:
    """A developer's or CI's image (local file or URL) for a device. The
    package architecture comes from the main snapshot of the device's target."""
    info = device_info(tests, device)
    if firmware.startswith(("http://", "https://")):
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / "custom" / firmware.rstrip("/").rsplit("/", 1)[-1]
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {firmware}")
        fetch(firmware, path)
    else:
        path = Path(firmware).expanduser()
        if not path.is_file():
            raise JobError(f"no such file: {path}")
    profiles = profiles_json("SNAPSHOT", info["target"]) or {}
    digest = sha256(path)
    return {
        **info,
        "release": "custom",
        "arch_packages": profiles.get("arch_packages", "unknown"),
        "version_code": f"custom-{digest[:10]}",
        "image": {"name": path.name, "sha256": digest},
        "path": path,
    }


def custom_revision(fw: dict, repo: str | None, branch: str | None, commit: str | None) -> dict:
    """KCIDB needs a 40-digit commit: without --commit the image's sha1
    stands in (the build is then identified by its content)."""
    if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise JobError("--commit must be a full 40-digit commit hash")
    commit = commit or hashlib.sha1(fw["path"].read_bytes()).hexdigest()
    return {"tree": "openwrt", "url": repo or OPENWRT_GIT, "branch": branch or "custom",
            "commit": commit, "describe": f"custom-{commit[:10]}", "commit_tags": []}


# --- helpers ----------------------------------------------------------------------------------


def fetch(url: str, path: Path) -> None:
    with requests.get(url, stream=True, timeout=60) as res:
        res.raise_for_status()
        with path.open("wb") as f:
            for chunk in res.iter_content(1 << 20):
                f.write(chunk)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
