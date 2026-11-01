#!/bin/bash
# Brings the Unraid installation to this repository's state. Run this on the PC, from the repository.
set -eu
UNRAID=${1:-unraid}
BASE=/mnt/user/appdata/home-net
cd "$(dirname "$0")/.."
ssh "$UNRAID" "find $BASE/repo -mindepth 1 -delete"
git archive HEAD | ssh "$UNRAID" "tar xf - -C $BASE/repo"
ssh "$UNRAID" "HOME_NET_DATA=$BASE/data python3 $BASE/repo/home-net apply"
echo "Fertig."
