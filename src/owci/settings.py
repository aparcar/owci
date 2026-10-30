"""Settings of the owci command, from kci's settings file.

kernelci.toml (--settings, ./kernelci.toml, ~/.config/kernelci/kernelci.toml
or /etc/kernelci/kernelci.toml, the files `kci` reads), section [owci]:

    [owci]
    api_url = "https://api.openwrt-kci.aparcar.org"
    storage_url = "https://storage.openwrt-kci.aparcar.org"
    web_url = "https://openwrtci.aparcar.org"        # links to results
    pool = "openwrt-labs"                           # shared lab runtime
    # target files: an openwrt-tests checkout or its raw URL
    tests = "https://raw.githubusercontent.com/aparcar/openwrt-tests/main"
    # submitters: their storage token may only upload below this path
    storage_prefix = "custom/paul"

    [owci.secrets]
    api_token = "..."       # Maestro token
    storage_token = "..."   # kernelci-storage JWT

The OpenWrt CI deployment (the openwrtci repository's ansible/server.yml)
writes this file for the admin into ansible/secrets/<server>/kernelci.toml and
for each of its openwrtci_submitters into
ansible/secrets/<server>/submitters/<name>.toml. A section named [openwrtci]
(files written before the rename) is read as well. Environment variables override it
(for CI): OWCI_API_URL, OWCI_STORAGE_URL, OWCI_API_TOKEN,
OWCI_STORAGE_TOKEN, OWCI_STORAGE_PREFIX.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import kernelci.settings

DEFAULT_TESTS = "https://raw.githubusercontent.com/aparcar/openwrt-tests/main"
DEFAULT_POOL = "openwrt-labs"


@dataclass
class Settings:
    api_url: str
    storage_url: str
    api_token: str
    storage_token: str
    web_url: str | None = None
    pool: str = DEFAULT_POOL
    tests: str = DEFAULT_TESTS
    storage_prefix: str = ""
    path: str | None = None


def load(path: str | None = None) -> Settings:
    raw = kernelci.settings.Settings(path)
    # [openwrtci]: settings files written before the command was renamed
    section = raw.get_raw("owci") or raw.get_raw("openwrtci") or {}
    secrets = section.get("secrets") or {}

    def value(key, env, default=None, source=section):
        return os.environ.get(env) or source.get(key) or default

    settings = Settings(
        api_url=value("api_url", "OWCI_API_URL"),
        storage_url=value("storage_url", "OWCI_STORAGE_URL"),
        api_token=value("api_token", "OWCI_API_TOKEN", source=secrets),
        storage_token=value("storage_token", "OWCI_STORAGE_TOKEN", source=secrets),
        web_url=section.get("web_url"),
        pool=section.get("pool") or DEFAULT_POOL,
        tests=section.get("tests") or DEFAULT_TESTS,
        storage_prefix=value("storage_prefix", "OWCI_STORAGE_PREFIX", "").strip("/"),
        path=raw.path,
    )
    missing = [k for k in ("api_url", "storage_url", "api_token", "storage_token")
               if not getattr(settings, k)]
    if missing:
        where = raw.path or "no kernelci.toml found"
        raise SystemExit(f"owci: missing {', '.join(missing)} in [owci] ({where})")
    return settings
