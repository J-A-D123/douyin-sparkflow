#!/bin/sh
# Only an active scheduler on AC power prevents idle sleep; screen/lid behavior stays normal.
set -u
docker_bin="${1:-/usr/local/bin/docker}"
awake_pid=""
sleep_pid=""
release_assertion() {
    if [ -n "$awake_pid" ]; then
        kill "$awake_pid" 2>/dev/null || true
        wait "$awake_pid" 2>/dev/null || true
        awake_pid=""
    fi
}
cleanup() {
    release_assertion
    if [ -n "$sleep_pid" ]; then kill "$sleep_pid" 2>/dev/null || true; fi
}
trap 'cleanup; exit 0' INT TERM
trap cleanup EXIT
while :; do
    active=false
    case "$(/usr/bin/pmset -g batt 2>/dev/null)" in
        *"AC Power"*)
            if [ "$("$docker_bin" inspect --format '{{.State.Running}}' douyin-scheduler 2>/dev/null)" = true ]; then active=true; fi
            ;;
    esac
    if [ "$active" = true ]; then
        if [ -z "$awake_pid" ] || ! kill -0 "$awake_pid" 2>/dev/null; then
            /usr/bin/caffeinate -i &
            awake_pid=$!
            printf '%s SparkFlow active on AC power\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
        fi
    else
        release_assertion
    fi
    /bin/sleep 30 &
    sleep_pid=$!
    wait "$sleep_pid" || true
    sleep_pid=""
done
