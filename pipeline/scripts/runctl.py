#!/usr/bin/env python3
"""Run control for the daily digest: bounded retries, time ceilings, failure records.

Two runs in the first eight were lost to failures a second attempt would probably have
cleared (2026-10-08: no board reachable at the moment the Mac woke; 2026-10-09: the
model CLI exited 1), and nothing said a day was missing. This module is the policy
`bin/run-local.sh` applies to its network and model steps:

- a transient failure is retried a fixed number of times with a growing pause;
- a deterministic one (expired login, a crash, a usage limit) is not retried at all;
- every attempt has a timeout and the whole run has a deadline, so a hung step can
  neither loop forever nor still be running when the next day's run is due;
- a run that still fails appends one line to `state/failures.jsonl`, and the next
  digest names every day since the last one that produced nothing.

Stdlib only. The clock, the sleep and the command runner are injectable so the policy
is tested offline.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE, LOGS = ROOT / "state", ROOT / "logs"

TIMEOUT_RC = 124          # same convention as coreutils `timeout`
OK, TRANSIENT, DETERMINISTIC = "ok", "transient", "deterministic"

# An expired session cannot refresh unattended and a spent quota does not come back in
# ten minutes: retrying either only delays the notification that names the fix.
AUTH = re.compile(r"authenticat|oauth|\blog[ -]?in\b|invalid api key|unauthori[sz]ed|\b401\b", re.I)
LIMIT = re.compile(r"usage limit|credit balance|quota exceeded|billing", re.I)

# step -> (attempts, pauses between them in seconds, timeout per attempt in seconds).
# Harvest's pauses cover about twelve minutes without a network, which is a Mac waking
# on a slow Wi-Fi join; its timeout is above the worst harvest seen (29 minutes, when
# three quarters of the boards timed out). Other steps get one bounded attempt.
POLICY = {
    "harvest": (4, (30, 120, 600), 40 * 60),
    "claude": (3, (120, 600), 40 * 60),
}
DEFAULT_POLICY = (1, (), 10 * 60)


def classify(step: str, rc: int, log_text: str = "", picks: bool = False) -> tuple[str, str]:
    """-> (verdict, reason). The reason is what the failure record and the digest say."""
    if step == "claude":
        # Picks on disk are the product, whatever the exit code; and a clean exit with
        # no picks is a normal empty day, which run-local.sh turns into [].
        if picks or rc == 0:
            return OK, ""
        if AUTH.search(log_text):
            return DETERMINISTIC, "model CLI is not authenticated (run: claude setup-token)"
        if LIMIT.search(log_text):
            return DETERMINISTIC, "model usage limit or billing error"
        if rc == TIMEOUT_RC:
            return TRANSIENT, "model step timed out"
        return TRANSIENT, f"model CLI exited {rc} without writing picks"
    if rc == 0:
        return OK, ""
    if rc == TIMEOUT_RC:
        return TRANSIENT, f"{step} timed out"
    if step == "harvest" and rc == 2:
        return TRANSIENT, "no job board was reachable (network or DNS)"
    # Any other exit is a traceback or a failed invariant: the same input fails again.
    return DETERMINISTIC, f"{step} exited {rc}"


def kill_group(proc) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def attempt(cmd: list[str], log_path, timeout: float, append: bool = False,
            deadline: float | None = None) -> int:
    """Run cmd with its output in log_path. A timeout kills the whole process group
    (the model CLI starts children of its own) and returns TIMEOUT_RC.

    `timeout` is time awake: time.monotonic() stops while a Mac sleeps, and a step
    suspended under a closed lid is not hung. Counting the sleep would spend every
    attempt before the lid opens (the 2026-10-10 run slept 55 minutes and still
    delivered). `deadline` is the run's wall-clock ceiling, which does count sleep.
    """
    with open(log_path, "ab" if append else "wb") as fh:
        try:
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            fh.write(f"could not start {cmd[0]}: {exc}\n".encode())
            return 127
        end = time.monotonic() + max(timeout, 0.01)
        try:
            while True:
                left = end - time.monotonic()
                if deadline is not None:
                    left = min(left, deadline - time.time())
                if left <= 0:
                    break
                try:
                    return proc.wait(timeout=min(5.0, left))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            # The child has its own session, so nothing else stops it: without this a
            # Ctrl-C or a SIGTERM to runctl left the model CLI running detached.
            if proc.poll() is None:
                kill_group(proc)
        fh.write(f"\n[runctl] killed after {int(timeout)}s\n".encode())
        return TIMEOUT_RC


def retry(step: str, run_once, *, attempts: int, backoff=(), timeout: float,
          deadline: float | None = None, clock=time.time, sleep=time.sleep,
          log=print) -> dict:
    """Call run_once(n, timeout) -> (rc, log_text, picks) until it succeeds, fails
    deterministically, runs out of attempts, or the run's deadline leaves no room.

    Never loops unboundedly: `attempts` is a hard count, and with a deadline the pause
    before a retry must fit inside it. -> {step, result, rc, reason, attempts, seconds}
    """
    started = clock()
    rc, reason, verdict, n = 1, f"{step} did not run", TRANSIENT, 0
    for n in range(1, max(1, attempts) + 1):
        budget = timeout
        if deadline is not None:
            budget = min(timeout, deadline - clock())
            if budget <= 0:
                n -= 1
                verdict, reason = "deadline", f"run time ceiling reached before {step} could run"
                break
        t0 = clock()
        rc, text, picks = run_once(n, budget)
        verdict, reason = classify(step, rc, text, picks)
        log(f"[step] {step} attempt {n}/{attempts}: {int(clock() - t0)}s rc={rc} {verdict}"
            + (f" ({reason})" if reason else ""))
        if verdict != TRANSIENT or n >= attempts:
            break
        pause = backoff[min(n - 1, len(backoff) - 1)] if backoff else 0
        if deadline is not None and clock() + pause >= deadline:
            verdict = "deadline"
            reason += "; run time ceiling leaves no room for another attempt"
            break
        log(f"[step] {step} retrying in {pause}s")
        sleep(pause)
    result = OK if verdict == OK else ("failed" if verdict == TRANSIENT else verdict)
    out = {"step": step, "result": result, "rc": rc, "reason": reason, "attempts": n,
           "seconds": int(clock() - started)}
    log(f"[step] {step} total: {out['seconds']}s attempts={n} result={result}")
    return out


def exit_code(step: str, outcome: dict) -> int:
    """What run-local.sh sees. 0 ok; 3 login expired; otherwise the step's own code,
    never 0 for a failure."""
    if outcome["result"] == OK:
        return 0
    if step == "claude" and "authenticated" in outcome["reason"]:
        return 3
    if step == "claude":
        return 1
    return outcome["rc"] or 1


# ---------------------------------------------------------------- failure record

def record_failure(step: str, rc: int, reason: str, *, attempts: int = 1, state=None,
                   when: datetime | None = None) -> dict:
    """Append one line to state/failures.jsonl. The file is committed with the next
    successful run (`git add -A state/`), so a lost day stays in history."""
    when = when or datetime.now().astimezone()
    row = {"date": when.date().isoformat(), "at": when.isoformat(timespec="seconds"),
           "step": step, "rc": rc, "reason": reason, "attempts": attempts}
    state = pathlib.Path(state or STATE)
    state.mkdir(parents=True, exist_ok=True)
    with (state / "failures.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def read_jsonl(path) -> list[dict]:
    rows = []
    path = pathlib.Path(path)
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue          # a torn last line must not hide every earlier day
        if isinstance(row, dict):
            rows.append(row)
    return rows


def local_date(iso: str, tz=None) -> date | None:
    """runs.jsonl is in UTC and a 22:46 Central run is already tomorrow there."""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(tz).date()
    except ValueError:
        return None


def missed_days(runs: list[dict], failures: list[dict], today: date, *, tz=None,
                limit: int = 14) -> list[dict]:
    """Days after the last delivered digest and before today that delivered nothing.

    Counted from the gap in runs.jsonl rather than from failures.jsonl, so a day the
    Mac was off (no run, so no failure record either) is still reported. The failure
    record supplies the reason when there is one.
    """
    done = {d for d in (local_date(r.get("run_at"), tz) for r in runs) if d and d < today}
    if not done:
        return []
    why = {}
    for f in failures:
        why[f.get("date")] = f           # the last record of a day wins
    out, day = [], max(done) + timedelta(days=1)
    while day < today:
        f = why.get(day.isoformat())
        out.append({"date": day.isoformat(),
                    "step": f.get("step") if f else None,
                    "reason": (f.get("reason") or f"{f.get('step')} failed") if f
                    else "no run was recorded (Mac off, or the run never started)"})
        day += timedelta(days=1)
    return out[-limit:]


def missed_lines(state=None, today: date | None = None, tz=None) -> list[str]:
    state = pathlib.Path(state or STATE)
    today = today or datetime.now().astimezone(tz).date()
    rows = missed_days(read_jsonl(state / "runs.jsonl"), read_jsonl(state / "failures.jsonl"),
                       today, tz=tz)
    return [f"No digest on {r['date']}: {r['reason']}" for r in rows]


# ---------------------------------------------------------------- command line

def cmd_run(args) -> int:
    attempts, backoff, timeout = POLICY.get(args.step, DEFAULT_POLICY)
    attempts = args.attempts or attempts
    timeout = args.timeout or timeout
    if args.backoff is not None:
        backoff = tuple(int(x) for x in args.backoff.split(",") if x.strip())
    deadline = float(os.environ["JOBDIGEST_DEADLINE"]) if os.environ.get("JOBDIGEST_DEADLINE") else None
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    picks = pathlib.Path(args.picks) if args.picks else None
    log = lambda line: print(line, flush=True)       # noqa: E731
    # Raise through attempt() so its finally clause stops the child (launchctl stop, kill).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    def run_once(n: int, budget: float):
        if picks:
            picks.unlink(missing_ok=True)            # a stale file must not pass for a result
        if n > 1:
            # Keep the failed attempt's output: it is the only evidence of why.
            with open(args.log, "ab") as fh:
                fh.write(f"\n[runctl] --- attempt {n} ---\n".encode())
        rc = attempt(cmd, args.log, budget, append=n > 1, deadline=deadline)
        text = pathlib.Path(args.log).read_text(errors="replace")[-20000:]
        return rc, text, bool(picks and picks.exists() and picks.stat().st_size > 0)

    outcome = retry(args.step, run_once, attempts=attempts, backoff=backoff, timeout=timeout,
                    deadline=deadline, log=log)
    if args.outcome:
        pathlib.Path(args.outcome).write_text(json.dumps(outcome))
    return exit_code(args.step, outcome)


def cmd_fail(args) -> int:
    reason, attempts = args.reason, 1
    if args.outcome and pathlib.Path(args.outcome).exists():
        try:
            o = json.loads(pathlib.Path(args.outcome).read_text())
            if o.get("step") == args.step:
                reason, attempts = o.get("reason") or reason, o.get("attempts", 1)
        except ValueError:
            pass
    row = record_failure(args.step, args.rc, reason, attempts=attempts)
    # out/ is overwritten by the next run; keep what this one left behind.
    for name in args.keep or []:
        src = pathlib.Path(name)
        if src.exists() and src.stat().st_size:
            LOGS.mkdir(exist_ok=True)
            shutil.copyfile(src, LOGS / f"fail-{row['date']}-{src.name}")
    print(f"[fail] {json.dumps(row)}", flush=True)
    return 0


def cmd_missed(args) -> int:
    for line in missed_lines():
        print(line)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runctl")
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    r = sub.add_parser("run", help="run one step under its retry policy and time limits")
    r.add_argument("--step", required=True)
    r.add_argument("--log", required=True)
    r.add_argument("--picks", help="file whose presence means the step produced a result")
    r.add_argument("--outcome", help="write the outcome as JSON here")
    r.add_argument("--attempts", type=int)
    r.add_argument("--timeout", type=int)
    r.add_argument("--backoff", help="comma-separated pauses in seconds")
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    r.set_defaults(fn=cmd_run)
    f = sub.add_parser("fail", help="append a failure record")
    f.add_argument("--step", required=True)
    f.add_argument("--rc", type=int, required=True)
    f.add_argument("--reason", required=True)
    f.add_argument("--outcome")
    f.add_argument("--keep", action="append", help="log to copy into logs/ (repeatable)")
    f.set_defaults(fn=cmd_fail)
    m = sub.add_parser("missed", help="print one line per day without a digest")
    m.set_defaults(fn=cmd_missed)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
