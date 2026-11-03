#!/bin/sh
# Keep the application and its workspace in the upstream repository.
set -eu
cd "$(dirname "$0")/.."
rev=$(cat upstream.rev)
repo=.build/upstream
if [ ! -d "$repo/.git" ]; then
    mkdir -p "$repo"
    git init -q "$repo"
    git -C "$repo" remote add origin https://github.com/kissqy/Microduck-kissqy.git
    git -C "$repo" sparse-checkout set microduck-hd1910/firmware
fi
if [ "$(git -C "$repo" rev-parse --verify HEAD 2>/dev/null || :)" != "$rev" ]; then
    git -C "$repo" fetch --depth=1 --filter=blob:none origin "$rev"
    git -C "$repo" checkout --detach "$rev"
fi
test "$(git -C "$repo" rev-parse HEAD)" = "$rev"
test -z "$(git -C "$repo" status --porcelain --untracked-files=all)" || {
    echo 'Pinned upstream checkout contains changes' >&2; exit 1;
}
