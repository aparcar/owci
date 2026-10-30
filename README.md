# owci

Schedule OpenWrt firmware tests on the OpenWrt CI labs: official releases and
snapshots, or your own images, on a device of your choice. Results show up in
the OpenWrt CI web UI. Built on KernelCI's own API and storage clients
(kernelci-core, `kci`).

```shell
uv tool install git+https://github.com/aparcar/owci    # or: uv run owci ... in a checkout

owci schedule --device openwrt_one                     # main snapshot, any lab with the device
owci schedule --device openwrt_one --release 25.12.6 --lab aparcar-lab --tests test_base.py
owci schedule --device openwrt_one --firmware ./openwrt-...-initramfs.itb \
    --title "mt76: fix 160 MHz" --commit <sha> --wait  # your own image; exit 0 if it passed
owci status <job id> [--wait]
```

The web UI shows the matching command on each device page ("Schedule a run")
and run page ("Run again").

## Settings

`kernelci.toml`, the file `kci` reads: `--settings <file>`, `./kernelci.toml`,
`~/.config/kernelci/kernelci.toml` or `/etc/kernelci/kernelci.toml`.

```toml
[owci]
api_url = "https://api.openwrt-kci.aparcar.org"
storage_url = "https://storage.openwrt-kci.aparcar.org"
web_url = "https://openwrtci.aparcar.org"       # links to results
pool = "openwrt-labs"                          # labs' shared queue
storage_prefix = "custom/<name>"               # your uploads go below this
# tests = "<openwrt-tests checkout or raw URL>" # target files (default: GitHub)

[owci.secrets]
api_token = "..."      # Maestro token
storage_token = "..."  # kernelci-storage JWT
```

Ask the OpenWrt CI admins for an account: they add you to
`openwrtci_submitters` and hand you this file (the openwrtci repository's
`ansible/secrets/<server>/submitters/<name>.toml`). Your storage token may only
upload below `custom/<name>/`.

In CI, use environment variables instead: `OWCI_API_URL`, `OWCI_STORAGE_URL`,
`OWCI_API_TOKEN`, `OWCI_STORAGE_TOKEN`, `OWCI_STORAGE_PREFIX`.

## What it creates

The same node chain the server's nightly schedule creates (checkout → kbuild →
job in Maestro, firmware and job definition in kernelci-storage). Device
details come from openwrt-tests' target files. Which fields go where:
`deploy/DATA.md` in the openwrtci repository.

Custom images: `--firmware` takes a file or URL. Without `--commit`, the
image's sha1 stands in for the source commit (KCIDB requires a full hash).
