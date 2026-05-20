#!/bin/bash
# Dispatch one submodule synth in a detached `screen` session named luna-<module>,
# then rename its docker container to the same name so both views match.
#
# Usage:
#   ./dispatch.sh <module_name>
#
# Why screen?
#   - Synth survives if the calling shell / SSH session dies.
#   - The post-synth GDS render in run.sh still fires when the container exits
#     (which would otherwise be skipped if the wrapper bash got SIGHUP'd).
#   - Re-attach any time:  screen -r luna-<module>
#   - List:                screen -ls
#
# Why rename the docker container too?
#   - OpenLane assigns UUID names to its containers. Renaming to luna-<module>
#     makes `docker ps` and screen sessions match by name — no ambiguity about
#     which container belongs to which synth.

set -e

MODULE="${1:?usage: $0 <module_name>}"
SESSION="luna-$MODULE"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Already running? Refuse to start a second one — explicit teardown is
# the user's call, since killing in-flight synth or its docker container
# loses work. To clean up cleanly:
#   docker kill $SESSION      # if a docker container is still alive
#   screen -S $SESSION -X quit
if screen -ls 2>/dev/null | grep -q "\.$SESSION\b"; then
    echo "ERROR: screen session '$SESSION' already exists."
    echo "  Attach:  screen -r $SESSION"
    echo "  Or, to tear down before re-dispatching:"
    echo "    sg docker -c 'docker kill $SESSION'   # if container still alive"
    echo "    screen -S $SESSION -X quit"
    exit 1
fi
# Same for a leftover docker container without a screen (the zombie case
# we hit before). Don't kill it — surface it to the user.
if sg docker -c "docker ps --filter name=$SESSION --format '{{.ID}}'" 2>/dev/null | grep -q .; then
    echo "ERROR: docker container '$SESSION' is still running (without a screen)."
    echo "  Inspect: docker ps --filter name=$SESSION"
    echo "  Kill:    sg docker -c 'docker kill $SESSION'"
    exit 1
fi

# Dev-iteration skips. STAMidPNR-1 is the multi-corner STA after CTS (~10
# min, redundant with the final-corner STA we already get). RepairAntennas
# is ~5 min of antenna-diode insertion — warnings, not blockers, OK to skip
# until tape-out signoff. IRDropReport requires PDN connectivity which we
# only achieve once macro placement matches chip-level PDN; skip for dev.
DEV_SKIPS="--skip OpenROAD.STAMidPNR-1 --skip OpenROAD.RepairAntennas --skip OpenROAD.IRDropReport"

# Start the synth in a detached screen session.
cd "$SCRIPT_DIR"
screen -dmS "$SESSION" bash -c "EXTRA_OPENLANE_ARGS='$DEV_SKIPS' sg docker -c './run.sh $MODULE'; echo '--- run.sh exited; press enter to close ---'; read"

# Poll for the openlane-spawned docker container and rename it.
# We look for one whose WorkingDir ends in /<module> and isn't already named luna-*.
(
    for _ in $(seq 1 60); do
        sleep 2
        for c in $(sg docker -c "docker ps --format '{{.ID}}'" 2>/dev/null); do
            WD=$(sg docker -c "docker inspect $c --format '{{.Config.WorkingDir}}'" 2>/dev/null)
            NM=$(sg docker -c "docker inspect $c --format '{{.Name}}'" 2>/dev/null)
            if [[ "$WD" == */"$MODULE" && "$NM" != /luna-* ]]; then
                sg docker -c "docker rename $c $SESSION" >/dev/null 2>&1
                exit 0
            fi
        done
    done
) &

echo "Dispatched $MODULE in screen session '$SESSION'."
echo "Attach:    screen -r $SESSION"
echo "Detach:    Ctrl-A then D (inside screen)"
echo "List:      screen -ls"
echo "Container: docker ps --filter name=$SESSION"
