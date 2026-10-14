#!/bin/sh
set -eu

data_dir="${SLIDEGEN_DATA_DIR:-/var/data}"
mkdir -p "$data_dir"
chown -R slidegen:slidegen "$data_dir"

exec gosu slidegen "$@"
