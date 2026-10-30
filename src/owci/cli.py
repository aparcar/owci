"""owci: schedule OpenWrt firmware tests on the OpenWrt CI labs.

  owci schedule --device openwrt_one                        # main snapshot
  owci schedule --device openwrt_one --release 25.12.6 --lab aparcar
  owci schedule --device openwrt_one --firmware ./openwrt-...-initramfs.itb \\
      --title "mt76: fix 160 MHz" --commit <sha> --wait
  owci status <job id>

Settings and tokens come from kci's kernelci.toml (see settings.py).
"""

from __future__ import annotations

import sys
import time

import click

from . import firmware, settings as settings_mod
from .firmware import JobError
from .jobs import Backend, schedule as create_jobs

POLL_S = 30


@click.group(help=__doc__.split("\n\n")[0])
@click.option("--settings", "settings_path", type=click.Path(dir_okay=False, exists=True),
              help="kernelci.toml (default: ./kernelci.toml, ~/.config/kernelci/kernelci.toml, "
                   "/etc/kernelci/kernelci.toml)")
@click.pass_context
def cli(ctx, settings_path):
    ctx.obj = settings_path


@cli.command(name="schedule")
@click.option("--device", required=True, help="targets/<device>.yaml in openwrt-tests (the platform)")
@click.option("--release", default="SNAPSHOT", show_default=True,
              help="official build: SNAPSHOT (main), 25.12-SNAPSHOT (branch) or 25.12.6 (release)")
@click.option("--firmware", "image", help="custom image instead: a local file or URL (uploaded)")
@click.option("--title", help="custom image: what it is, e.g. the change it tests")
@click.option("--repo", help="custom image: source repository URL")
@click.option("--branch", help="custom image: source branch")
@click.option("--commit", help="custom image: full source commit hash")
@click.option("--lab", help="only this lab may run it (default: any lab with the device)")
@click.option("--tests", default="", help="pytest selection, e.g. test_base.py (default: all)")
@click.option("--wait", is_flag=True, help="wait for the result; exit 1 unless it passes")
@click.pass_obj
def schedule_cmd(settings_path, device, release, image, title, repo, branch, commit, lab,
                 tests, wait):
    """Schedule a test run of one device."""
    s = settings_mod.load(settings_path)
    backend = Backend(s)
    try:
        if image:
            fw = firmware.resolve_custom(s.tests, device, image)
            rev = firmware.custom_revision(fw, repo, branch, commit)
            extra = {"source": "custom", **({"title": title} if title else {})}
        else:
            if any((title, repo, branch, commit)):
                raise click.UsageError("--title/--repo/--branch/--commit describe a custom --firmware")
            fw = firmware.resolve_official(s.tests, release, device)
            rev = firmware.official_revision(fw)
            extra = {}
        click.echo(f"owci: {fw['name']} ({device}) · {rev['describe']}"
                   + (f" · {title}" if title else ""))
        job = create_jobs(backend, fw, rev, device, runtime=lab or s.pool, tests=tests, extra=extra)
    except JobError as e:
        sys.exit(f"owci: {e}")
    click.echo(f"  job      {job['id']} (waiting for {'lab ' + lab if lab else 'a lab with the device'})")
    click.echo(f"\n  {result_url(s, job['id'])}")
    if wait:
        sys.exit(wait_for(backend, s, job["id"]))


@cli.command()
@click.argument("job_id")
@click.option("--wait", is_flag=True, help="wait until the run is done")
@click.pass_obj
def status(settings_path, job_id, wait):
    """Show the state of a run; exit 0 if it passed."""
    s = settings_mod.load(settings_path)
    backend = Backend(s)
    if wait:
        sys.exit(wait_for(backend, s, job_id))
    sys.exit(report(s, backend.get(job_id)))


def wait_for(backend: Backend, s, job_id: str) -> int:
    last = None
    while True:
        job = backend.get(job_id)
        data = job.get("data") or {}
        state = job["state"] if job["state"] == "done" else ("running" if data.get("job_id") else "waiting")
        if state != last:
            where = f" on {data.get('runtime')}" if state == "running" else ""
            click.echo(f"  {time.strftime('%H:%M:%S')} {state}{where}")
            last = state
        if job["state"] == "done":
            return report(s, job)
        time.sleep(POLL_S)


def report(s, job: dict) -> int:
    data = job.get("data") or {}
    summary = data.get("summary") or {}
    result = job.get("result") or ("pending" if job["state"] != "done" else "incomplete")
    counts = (f" · {summary.get('passed', 0)}/{summary.get('total', 0)} tests passed"
              if summary.get("total") else "")
    click.echo(f"owci: {result}{counts}"
               + (f" · {data['error_msg']}" if data.get("error_msg") else ""))
    click.echo(f"  {result_url(s, job['id'])}")
    return 0 if result == "pass" else 1


def result_url(s, job_id: str) -> str:
    if s.web_url:
        return f"{s.web_url.rstrip('/')}/#/run/{job_id}"
    return f"{s.api_url.removesuffix('/latest')}/viewer?node_id={job_id}"


def main():
    cli()
