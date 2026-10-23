#!/bin/bash
set -euo pipefail

LOG_DIR=/logs/verifier
REWARD_FILE="$LOG_DIR/reward.txt"
STATUS_FILE="$LOG_DIR/verification-status.txt"
TEST_LOG="$LOG_DIR/test-stdout.txt"
REPORT="$LOG_DIR/pytest-results.xml"
TMP_ROOT=/tmp/ly001-verifier
completed=0

mkdir -p "$LOG_DIR" "$TMP_ROOT"
chmod 0777 "$TMP_ROOT"

write_reward() {
    chmod 0644 "$REWARD_FILE" 2>/dev/null || true
    printf '%s\n' "$1" > "$REWARD_FILE"
    chmod 0444 "$REWARD_FILE"
}

write_status() {
    chmod 0644 "$STATUS_FILE" 2>/dev/null || true
    printf '%s\n' "$1" > "$STATUS_FILE"
    chmod 0444 "$STATUS_FILE"
}

cleanup() {
    rm -rf "$TMP_ROOT"
}

on_exit() {
    cleanup
    if [ "$completed" -ne 1 ]; then
        write_status "INCOMPLETE: verifier exited before pytest completion"
        write_reward 0.000
    fi
}

on_signal() {
    cleanup
    write_status "INCOMPLETE: verifier interrupted before pytest completion"
    write_reward 0.000
    completed=1
    exit 0
}

trap on_exit EXIT
trap on_signal INT TERM HUP
write_reward 0.000
write_status "INCOMPLETE: pytest has not completed"
rm -f "$REPORT" "$TEST_LOG" "$LOG_DIR/scoring-report.json"

set +e
/usr/bin/timeout --signal=TERM --kill-after=10s 150s \
    /usr/bin/env -i \
    PATH=/usr/local/bin:/usr/bin:/bin \
    HOME=/tmp \
    TMPDIR="$TMP_ROOT" \
    TASK_VERIFIER_TMP="$TMP_ROOT" \
    TASK_APP=/tests/trusted_candidate.py \
    TASK_CANDIDATE=/app/secondary_heat_audit.py \
    PYTHONNOUSERSITE=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/usr/local/lib/python3.13/site-packages:/tests \
    /usr/local/bin/python3 -S -s -P -B -m pytest -v -s -p no:cacheprovider \
    --junitxml="$REPORT" /tests/test_outputs.py > "$TEST_LOG" 2>&1
RC=$?
set -e

if [ "$RC" -ne 0 ] && [ "$RC" -ne 1 ]; then
    write_status "INCOMPLETE: pytest did not complete; runner exited with code $RC"
    write_reward 0.000
elif [ ! -s "$REPORT" ]; then
    write_status "INCOMPLETE: pytest completion report missing"
    write_reward 0.000
elif ! /usr/local/bin/python3 /tests/emit_case_results.py \
    --junit "$REPORT" \
    --case-map /tests/case-map.json >> "$TEST_LOG"; then
    write_status "INCOMPLETE: behavior case map could not be emitted"
    write_reward 0.000
elif ! /usr/local/bin/python3 /tests/score.py \
    --config /tests/scoring.json \
    --junit "$REPORT" \
    --report-out "$LOG_DIR/scoring-report.json" \
    --reward-out "$REWARD_FILE"; then
    write_status "INCOMPLETE: weighted scorer rejected the report"
    write_reward 0.000
else
    REWARD=$(cat "$REWARD_FILE")
    case "$REWARD" in
        0.[0-9][0-9][0-9]|1.000) ;;
        *)
            write_status "INCOMPLETE: weighted scorer produced invalid reward"
            write_reward 0.000
            completed=1
            exit 0
            ;;
    esac
    chmod 0444 "$REWARD_FILE"
    write_status "COMPLETED: weighted scoring finished with reward=$REWARD"
fi

cat "$TEST_LOG" 2>/dev/null || true
completed=1
exit 0
