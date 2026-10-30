"""owci: schedule OpenWrt firmware tests on the OpenWrt CI labs.

  owci schedule --device openwrt_one                        # main snapshot
  owci schedule --device openwrt_one --release 25.12.6 --lab aparcar
  owci schedule --device openwrt_one --firmware ./openwrt-...-initramfs.itb \\
      --title "mt76: fix 160 MHz" --url <pull request> --commit <sha> --wait
  owci status <job id>

Settings and tokens come from kci's kernelci.toml (see settings.py).
"""

from __future__ import annotations

import sys
import time

import click

from . import firmware, settings as settings_mod
from .firmware import JobError, NothingToTest
from .jobs import Backend, existing_job, schedule as create_jobs, trigger

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
@click.option("--repo", help="custom image: source repository URL")
@click.option("--branch", help="custom image: source branch")
@click.option("--commit", help="custom image: full source commit hash")
@click.option("--title", help="why: what the run tests, e.g. the change (shown in the web UI)")
@click.option("--url", help="a link for it, e.g. the pull request (default on GitHub Actions: the run)")
@click.option("--lab", help="only this lab may run it (default: any lab with the device)")
@click.option("--tests", default="", help="pytest selection, e.g. test_base.py (default: all)")
@click.option("--skip-existing", is_flag=True,
              help="do nothing if you already have a job for this device and build")
@click.option("--dry-run", is_flag=True, help="only show what would be scheduled")
@click.option("--wait", is_flag=True, help="wait for the result; exit 1 unless it passes")
@click.pass_obj
def schedule_cmd(settings_path, device, release, image, repo, branch, commit, title, url, lab,
                 tests, skip_existing, dry_run, wait):
    """Schedule a test run of one device.

    The last line says what happened: "created <job id>", "exists <job id>"
    or "would create". Exit 0 then, 2 if there is nothing to test (no image
    in that release, device only tested on snapshots), 1 on errors.
    """
    s = settings_mod.load(settings_path)
    backend = Backend(s)
    try:
        if image:
            fw = firmware.resolve_custom(s.tests, device, image)
            rev = firmware.custom_revision(fw, repo, branch, commit)
            extra = {"source": "custom"}
        else:
            if any((repo, branch, commit)):
                raise click.UsageError("--repo/--branch/--commit describe a custom --firmware")
            fw = firmware.resolve_official(s.tests, release, device)
            # snapshot commits: Maestro first (GitHub allows 60 lookups/hour)
            known = backend.find_one(kind="checkout",
                                     **{"data.kernel_revision.describe": fw["version_code"]})
            rev = firmware.official_revision(fw, known and known["data"]["kernel_revision"]["commit"])
            extra = {}
        trig = trigger(title, url)
        click.echo(f"owci: {fw['name']} ({device}) · {rev['describe']}"
                   + (f" · {title}" if title else ""))
        if skip_existing and (job := existing_job(backend, device, rev["commit"])):
            click.echo(f"exists {job['id']} ({job['state']})")
            return
        if dry_run:
            click.echo(f"would create ({fw['version_code']})")
            return
        job = create_jobs(backend, fw, rev, device, runtime=lab or s.pool, tests=tests,
                          extra=extra, trig=trig)
    except NothingToTest as e:
        click.echo(f"nothing to test: {e}")
        sys.exit(2)
    except JobError as e:
        sys.exit(f"owci: {e}")
    click.echo(f"  {result_url(s, job['id'])}")
    click.echo(f"created {job['id']} (waiting for {'lab ' + lab if lab else 'a lab with the device'})")
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
