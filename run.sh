#!/usr/bin/env bash
# Launcher so you don't have to remember the venv path. Sibling of run.ps1;
# same interface, POSIX spelling.
#
#   ./run.sh                                  -> chat panel, blank page
#   ./run.sh --start google.com               -> chat panel, opens Google
#   ./run.sh --start news.ycombinator.com --shots
#   ./run.sh --task 'list the top 5 stories'
#   ./run.sh --task 'find plans under $500' --allow pivothealth.com
#
# Note: single-quote any task containing a $ -- the shell expands $ inside
# double quotes, so "under $500" would silently become "under 00".

set -uo pipefail          # deliberately NOT -e: the agent writes its progress
                          # log to stderr and exits non-zero on a failed task,
                          # and we want to surface that, not abort early.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$root/.venv/bin/python"

if [ ! -x "$py" ]; then
    printf '\033[33mNo virtualenv found. Creating one...\033[0m\n'
    "${PYTHON:-python3}" -m venv "$root/.venv" || exit 1
    "$py" -m pip install --quiet --disable-pip-version-check -r "$root/requirements.txt" || exit 1
    printf '\033[32mDone.\033[0m\n'
fi

task="" ; start="" ; allow="" ; shots=0 ; headless=0
while [ $# -gt 0 ]; do
    case "$1" in
        --task|-Task)         task="${2-}"  ; shift 2 ;;
        --start|-Start)       start="${2-}" ; shift 2 ;;
        --allow|-Allow)       allow="${2-}" ; shift 2 ;;
        --shots|-Shots)       shots=1       ; shift ;;
        --headless|-Headless) headless=1    ; shift ;;
        -h|--help)            sed -n '2,12p' "$0" ; exit 0 ;;
        --) shift ; task="${task:-$*}" ; break ;;
        # A bare argument is the task, so `./run.sh 'list the top 5 stories'`
        # works the same as spelling out --task.
        *)  task="$1" ; shift ;;
    esac
done

cli=()
[ -n "$start" ] && cli+=(--start "$start")
[ -n "$allow" ] && cli+=(--allow "$allow")
[ "$shots" = 1 ]    && cli+=(--shots)
[ "$headless" = 1 ] && cli+=(--headless)

if [ -n "$task" ]; then
    exec "$py" "$root/run_task.py" ${cli[@]+"${cli[@]}"} "$task"
else
    printf '\033[36mStarting Cuaexp. The chat panel appears in the Chrome window.\033[0m\n'
    printf '\033[90mPress Ctrl+C here to stop.\033[0m\n\n'
    exec "$py" "$root/daemon.py" ${cli[@]+"${cli[@]}"}
fi
