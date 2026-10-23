#!/bin/bash
set -euo pipefail

LOG_DIR=/logs/verifier
REWARD_FILE="$LOG_DIR/reward.txt"
STATUS_FILE="$LOG_DIR/verification-status.txt"
TEST_LOG="$LOG_DIR/test-stdout.txt"
mkdir -p "$LOG_DIR"
REPORT=$(mktemp "$LOG_DIR/pytest-results.XXXXXX.xml")
completed=0

write_reward() {
    local value="$1"
    chmod 0644 "$REWARD_FILE" 2>/dev/null || true
    printf '%s\n' "$value" > "$LOG_DIR/reward.txt"
    chmod 0444 "$REWARD_FILE"
}

write_status() {
    chmod 0644 "$STATUS_FILE" 2>/dev/null || true
    printf '%s\n' "$1" > "$STATUS_FILE"
    chmod 0444 "$STATUS_FILE"
}

on_exit() {
    if [ "$completed" -ne 1 ]; then
        write_status "INCOMPLETE: verifier exited before pytest completion"
        write_reward 0.000
    fi
}

on_signal() {
    write_status "INCOMPLETE: verifier interrupted before pytest completion"
    write_reward 0.000
    completed=1
    exit 0
}

write_reward 0.000
write_status "INCOMPLETE: pytest has not completed"
trap on_exit EXIT
trap on_signal INT TERM HUP
rm -f "$REPORT"

set +e
timeout --signal=TERM --kill-after=10s 150s \
    env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS \
    python3 -u -m pytest -v -s -p no:cacheprovider \
    --junitxml="$REPORT" /tests/test_outputs.py > "$TEST_LOG" 2>&1
RC=$?
set -e

if [ "$RC" -ne 0 ] && [ "$RC" -ne 1 ]; then
    case "$RC" in
        124|125|126|127|137|143)
            write_status "INCOMPLETE: pytest did not complete; runner exited with code $RC"
            ;;
        *)
            write_status "INCOMPLETE: pytest exited before a scoreable report with code $RC"
            ;;
    esac
    write_reward 0.000
elif [ ! -s "$REPORT" ]; then
    write_status "INCOMPLETE: pytest completion report missing"
    write_reward 0.000
else
    if ! python3 /tests/emit_case_results.py \
        --junit "$REPORT" \
        --case-map /tests/case-map.json >> "$TEST_LOG"; then
        write_status "INCOMPLETE: behavior case map could not be emitted"
        write_reward 0.000
        cat "$TEST_LOG" 2>/dev/null || true
        completed=1
        rm -f "$REPORT"
        exit 0
    fi
    if python3 /tests/score.py \
        --config /tests/scoring.json \
        --junit "$REPORT" \
        --report-out "$LOG_DIR/scoring-report.json" \
        --reward-out "$REWARD_FILE"; then
        REWARD=$(cat "$REWARD_FILE")
        case "$REWARD" in
            0.[0-9][0-9][0-9]|1.000) ;;
            *)
                write_status "INCOMPLETE: weighted scorer produced invalid reward"
                write_reward 0.000
                completed=1
                rm -f "$REPORT"
                exit 0
                ;;
        esac
        chmod 0444 "$REWARD_FILE"
        write_status "COMPLETED: weighted scoring finished with reward=$REWARD"
    else
        write_status "INCOMPLETE: weighted scorer rejected the report"
        write_reward 0.000
    fi
fi
cat "$TEST_LOG" 2>/dev/null || true
completed=1
rm -f "$REPORT"
exit 0
