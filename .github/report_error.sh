#!/bin/bash
# Turns the end of a build log into an error annotation, which shows on the run summary.
log="$1"
[ -f "$log" ] || exit 0
summary=$(grep -n -i -E "error|exception|traceback|failed|not found|exited with code" "$log" | tail -n 25)
ending=$(tail -n 40 "$log")
for part in summary ending; do
    text="${!part}"
    text="${text//'%'/'%25'}"
    text="${text//$'\r'/}"
    text="${text//$'\n'/'%0A'}"
    echo "::error title=Build log ($part)::$text"
done
