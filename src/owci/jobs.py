"""Create OpenWrt test jobs in Maestro with KernelCI's own clients.

Which fields go where: deploy/DATA.md in the openwrtci repository (own
fields of job nodes in data.misc).

The node chain labs pick up (same as openwrtci's deploy/seed/openwrt_jobs.py):

  checkout   openwrt / openwrt-25.12 @ v25.12.5
  └─ kbuild  armsr-armv8-generic       artifacts.firmware (+ sha256 in data)
     └─ job  openwrt-tests             state=available, artifacts.job_definition
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import kernelci.api
import kernelci.config.api
import kernelci.config.storage
import kernelci.storage

from .firmware import JobError, download
from .settings import Settings


class Backend:
    """Maestro and kernelci-storage, through kernelci-core."""

    def __init__(self, settings: Settings):
        api_config = kernelci.config.api.API("owci", settings.api_url.removesuffix("/latest") + "/")
        self.api = kernelci.api.get_api(api_config, settings.api_token)
        storage_url = settings.storage_url.rstrip("/") + "/"
        storage_config = kernelci.config.storage.BackendStorage(
            api_url=storage_url, name="owci", storage_type="backend", base_url=storage_url)
        self.storage = kernelci.storage.get_storage(storage_config, f"Bearer {settings.storage_token}")
        self.settings = settings

    def upload(self, local: Path, dest_dir: str, name: str | None = None) -> str:
        """Upload below the settings' storage_prefix (submitters may only
        write there); returns the public URL."""
        if self.settings.storage_prefix:
            dest_dir = f"{self.settings.storage_prefix}/{dest_dir}"
        try:
            return self.storage.upload_single((str(local), name or local.name), dest_dir)
        except Exception as e:
            raise JobError(f"storage upload {dest_dir}/{name or local.name}: {e}") from e

    def add(self, node: dict) -> dict:
        try:
            return self.api.node.add(node)
        except Exception as e:
            raise JobError(f"API: creating {node['kind']} {node['name']}: {e}") from e

    def update(self, node: dict) -> dict:
        try:
            return self.api.node.update(node)
        except Exception as e:
            raise JobError(f"API: updating {node['id']}: {e}") from e

    def get(self, node_id: str) -> dict:
        return self.api.node.get(node_id)

    def find(self, **attributes) -> list:
        return list(self.api.node.find(attributes))


def isa_arch(arch_packages: str) -> str:
    """data.arch, like KernelCI's kernel ARCH: the instruction set only.
    aarch64_cortex-a53 -> aarch64, mipsel_24kc -> mipsel, x86_64 -> x86_64.
    (KCIDB requires ^[a-z0-9_]*$; one invalid architecture makes it reject
    send_kcidb's whole batch.)"""
    arch = "x86_64" if arch_packages.startswith("x86_64") else arch_packages.split("_", 1)[0]
    return re.sub(r"[^a-z0-9_]", "_", arch.lower())


def openwrt_meta(fw: dict) -> dict:
    return {"release": fw["release"], "version_code": fw["version_code"], "target": fw["target"],
            "arch_packages": fw["arch_packages"]}


def schedule(backend: Backend, fw: dict, rev: dict, device: str, *, runtime: str,
             tests: str = "", extra: dict | None = None) -> dict:
    """checkout -> kbuild (firmware in storage) -> available job. `extra`
    goes into data.openwrt of all three (e.g. source/title/submitter of a
    custom build)."""
    meta = {**openwrt_meta(fw), **(extra or {})}
    checkout = backend.add({
        "kind": "checkout", "name": "checkout", "path": ["checkout"],
        "state": "done", "result": "pass",
        "data": {"kernel_revision": rev, "openwrt": meta},
    })
    print(f"  checkout {checkout['id']}")

    if fw["release"] == "custom":
        local = fw["path"]
        dest = f"custom/{fw['image']['sha256'][:16]}"
    else:
        local = download(fw)
        dest = f"openwrt/{fw['release']}/{fw['version_code']}/{fw['target']}"
    firmware_url = backend.upload(local, dest, fw["image"]["name"])
    name = f"{fw['target'].replace('/', '-')}-{fw['profile']}"
    build = backend.add({
        "kind": "kbuild", "name": name, "path": ["checkout", name],
        "parent": checkout["id"], "state": "done", "result": "pass",
        "artifacts": {"firmware": firmware_url},
        "data": {
            "kernel_revision": rev,
            "arch": isa_arch(fw["arch_packages"]),
            "compiler": "custom" if fw["release"] == "custom" else "openwrt-buildbot",
            "defconfig": fw["profile"],
            "config_full": f"{fw['target']}:{fw['profile']}:{fw['arch_packages']}",
            "openwrt": {**meta, "profile": fw["profile"], "image": fw["image"]["name"],
                        "sha256": fw["image"]["sha256"]},
        },
    })
    print(f"  build    {build['id']} ({name}, {fw['version_code']})")

    job = backend.add({
        "kind": "job", "name": "openwrt-tests", "path": [*build["path"], "openwrt-tests"],
        "parent": build["id"], "state": "running",
        "data": {
            "kernel_revision": rev,
            "runtime": runtime,
            "platform": device,
            "arch": isa_arch(fw["arch_packages"]),
            # the web UI takes device names and features from the jobs; in
            # misc, see openwrtci's deploy/DATA.md
            "misc": {"openwrt": {**meta, "profile": fw["profile"], "name": fw["name"],
                                 "features": fw["features"]}},
        },
    })
    job_def = {
        "artifacts": {"firmware": firmware_url},
        "integrity": {"sha256": {"firmware": fw["image"]["sha256"]}},
        "tests": [{"id": "openwrt-tests", "type": "pytest", "parameters": tests, "timeout_s": 1800}],
        "environment": {"platform": device, "arch": fw["arch_packages"],
                        "requirements": fw["features"]},
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{job['id']}.json"
        path.write_text(json.dumps(job_def, indent=2))
        job["artifacts"] = {"job_definition": backend.upload(path, "openwrt/jobs")}
    job["state"] = "available"
    return backend.update(job)
