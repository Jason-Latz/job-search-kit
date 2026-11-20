#!/bin/bash
# Daily local run. Invoked by launchd; safe to run by hand any time.
#
# Harvest and record are deterministic Python. Claude is invoked once, for the only part
# that needs judgment: ranking the shortlist and writing it up.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# launchd does not inherit an interactive shell's environment, so a token exported in
# .zshrc is invisible to the scheduled run. Source it from a file instead. Kept outside
# the repo so it can never be committed.
ENV_FILE="${JOBDIGEST_ENV:-$HOME/.jobdigest.env}"
# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] && . "$ENV_FILE"

DATE="$(date +%Y-%m-%d)"
DIGEST="$REPO/digests/$DATE.html"
mkdir -p "$REPO/digests" "$REPO/out" "$REPO/logs"

notify() {  # $1 title, $2 message, $3 optional file to open on click
  /usr/bin/osascript -e "display notification \"${2//\"/}\" with title \"${1//\"/}\" sound name \"Ping\"" >/dev/null 2>&1
}

# A hung step must not still be running when tomorrow's run is due. Every network and
# model step runs under scripts/runctl.py, which honours this deadline. It is wall-clock
# and so counts time asleep: 20 hours, because a run that sleeps under a closed lid
# has to be able to finish when the lid opens that afternoon.
MAX_RUN="${JOBDIGEST_MAX_RUN_SECONDS:-72000}"
export JOBDIGEST_DEADLINE=$(( $(date +%s) + MAX_RUN ))

fail() {  # $1 exit code, $2 step, $3 reason. Leaves a record the next digest reports.
  python3 scripts/runctl.py fail --step "$2" --rc "$1" --reason "$3" \
    --outcome out/outcome.json --keep "out/$2.log" --keep out/verify.log 2>&1 \
    || echo "could not write state/failures.jsonl"
  echo "=== $(date -Iseconds) FAILED at $2 (exit $1): $3 ==="
  exit "$1"
}

timed() {  # $1 step name, then the command. One duration line per step in run.log.
  local name="$1" t0=$SECONDS rc; shift
  "$@"; rc=$?
  echo "[step] $name total: $((SECONDS - t0))s rc=$rc"
  return $rc
}

# launchd fires this job inside a DarkWake when the lid is closed, and the Mac went back
# to sleep 11 seconds into the 2026-10-08 run (pmset -g log). Hold an idle-sleep
# assertion for as long as this script lives. JOBDIGEST_NO_CAFFEINATE=1 turns it off.
if [ -z "${JOBDIGEST_NO_CAFFEINATE:-}" ] && command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -w $$ >/dev/null 2>&1 &
fi

echo "=== $(date -Iseconds) starting run ==="
rm -f out/outcome.json

# Cheap and offline. Catches a broken edit before it can produce a wrong digest.
run_tests() { ./bin/test.sh > out/test.log 2>&1; }
if ! timed tests run_tests; then
  notify "Job Digest: tests failing" "Pipeline tests failed; no digest sent. See out/test.log"
  echo "test suite failed, aborting"; tail -20 out/test.log
  # runctl.py may be the broken file, so this record is best effort.
  fail 4 test "pipeline tests failed"
fi

# Exit 2 (no board reachable) is retried with backoff: on 2026-10-08 the run started
# as the Mac woke and gave up before the network was back.
python3 scripts/runctl.py run --step harvest --log out/harvest.log --outcome out/outcome.json \
  -- python3 scripts/jobdigest.py harvest --window 7
HARVEST=$?
if [ "$HARVEST" -eq 2 ]; then
  notify "Job Digest failed" "No job board was reachable after retries. Network or DNS problem."
  echo "harvest reported egress failure"; fail 2 harvest "no job board was reachable"
elif [ "$HARVEST" -ne 0 ]; then
  notify "Job Digest failed" "harvest exited $HARVEST. See logs/run.log"
  echo "harvest failed with $HARVEST"; fail "$HARVEST" harvest "harvest exited $HARVEST"
fi

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ]; then
  # The interactive keychain session expires and cannot refresh unattended. Without a
  # token here the run would fail every day at 09:00 with nobody watching.
  notify "Job Digest: no credentials" "Add CLAUDE_CODE_OAUTH_TOKEN to ~/.jobdigest.env"
  echo "no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in $ENV_FILE"
  fail 3 credentials "no model credentials in $ENV_FILE"
fi

if ! python3 scripts/jobdigest.py verify > out/verify.log 2>&1; then
  notify "Job Digest: harvest invalid" "Invariant check failed; no digest sent."
  echo "verify failed after harvest"; cat out/verify.log
  fail 5 verify "harvest failed its invariant check"
fi

# Retried when the failure looks transient (API error, timeout); an expired login or a
# usage limit is not, because waiting ten minutes fixes neither. Each attempt's output
# is kept in out/claude.log.
rm -f out/picks.json
python3 scripts/runctl.py run --step claude --log out/claude.log --picks out/picks.json \
  --outcome out/outcome.json -- \
  claude -p "$(cat prompt.md)" \
  --permission-mode acceptEdits \
  --allowedTools "Bash,Read,Write,Edit,Glob,Grep"
CLAUDE_RC=$?

if [ ! -s out/picks.json ]; then
  # A run that produced no picks is normal. A run where Claude never wrote the file is
  # not, and the two must not look alike.
  if [ "$CLAUDE_RC" -ne 0 ]; then
    # An expired CLI session is the most likely cause and the least obvious from an exit
    # code, so name it. Re-auth with `claude setup-token` for a long-lived token.
    if [ "$CLAUDE_RC" -eq 3 ]; then
      notify "Job Digest: login expired" "Run 'claude setup-token' in a terminal to reauthorize."
      echo "claude is not authenticated; run: claude setup-token"
      fail 3 claude "model CLI is not authenticated"
    fi
    notify "Job Digest incomplete" "Claude failed after retries without writing picks."
    echo "claude failed ($CLAUDE_RC) and wrote no picks"
    fail 1 claude "model CLI failed without writing picks"
  fi
  echo "[]" > out/picks.json
fi

# The model can write anything into picks.json, including a URL that was never on the
# shortlist. Nothing upstream can prevent that, so it is checked before delivery.
if ! python3 scripts/jobdigest.py verify --picks out/picks.json > out/verify.log 2>&1; then
  notify "Job Digest: picks invalid" "Model picks failed validation; no digest sent."
  echo "verify failed on picks"; cat out/verify.log
  fail 6 verify "model picks failed validation"
fi

# Days since the last digest that produced nothing. Read before `record` appends today
# to runs.jsonl; render puts the same lines at the top of the digest.
MISSED="$(python3 scripts/runctl.py missed 2>/dev/null)"
[ -n "$MISSED" ] && echo "$MISSED"

do_render() { python3 scripts/jobdigest.py render --picks out/picks.json --out "$DIGEST" > out/render.log 2>&1; }
do_record() { python3 scripts/jobdigest.py record --picks out/picks.json >> out/render.log 2>&1; }
timed render do_render
timed record do_record

COUNT=$(python3 -c "import json;print(len(json.load(open('out/picks.json'))))" 2>/dev/null || echo "?")
ln -sfn "$DATE.html" "$REPO/digests/latest.html"

MISSED_N=0
[ -n "$MISSED" ] && MISSED_N=$(printf '%s\n' "$MISSED" | wc -l | tr -d ' ')
if [ "$MISSED_N" != "0" ]; then
  # Said even on an empty day: a silent gap is the failure this exists to prevent.
  notify "Job Digest: $COUNT roles, $MISSED_N day(s) missed" "$(printf '%s' "$MISSED" | head -1)"
elif [ "$COUNT" = "0" ]; then
  echo "run complete, nothing cleared the bar"
else
  notify "Job Digest: $COUNT roles" "$COUNT new roles tracked. Run ./bin/board.sh to star them."
fi

# Commit only the pipeline's own paths: a bare commit would sweep in whatever you
# had staged elsewhere in the repo. Keep this repo PRIVATE: state/ is your tracker.
git add -A state/ data/ digests/ >/dev/null 2>&1
git commit -q -m "Digest $DATE: $COUNT roles" -- state/ data/ digests/ >/dev/null 2>&1
# Best effort: the digest is already on disk, so a failed push must not fail the run.
# It only costs the offsite copy of starred roles and application history.
if git remote get-url origin >/dev/null 2>&1 && ! python3 scripts/runctl.py run --step push --log out/push.log --timeout 180 \
     -- git push -q origin main; then
  echo "warning: git push failed; tracker history is local-only until the next run"
fi
echo "=== $(date -Iseconds) done, $COUNT roles ==="
