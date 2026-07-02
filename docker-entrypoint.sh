#!/bin/sh
# Runs as root (before dropping to the app user) so it can make the /data mount
# writable. A host bind mount (./data:/data) arrives owned by the host uid, which
# the non-root app user can't write to — chown fixes that. Then drop privileges.
set -e

DATA_DIR="${PINCHIVE_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

if [ "$(id -u)" = "0" ]; then
    # Only chown the top level + known subdirs (cheap); recursive chown of a huge
    # archive every boot would be slow, so we fix the roots the app needs.
    chown pinchive:pinchive "$DATA_DIR" 2>/dev/null || true
    for d in boards cookies; do
        mkdir -p "$DATA_DIR/$d"
        chown pinchive:pinchive "$DATA_DIR/$d" 2>/dev/null || true
    done
    exec gosu pinchive "$@"
fi

exec "$@"
