#!/usr/bin/env bash
#
# Install the transitland CLI to /usr/local/bin.
#
# The binary is kept in TRANSITLAND_CACHE_DIR (default ~/.cache/transitland),
# which workflows persist with actions/cache keyed on this file's hash, so a
# version bump here is also a cache bust. Whether it comes from the cache or a
# fresh download, the binary must match the SHA-256 published for the release
# before it is installed.
set -euo pipefail

VERSION="v1.3.4"
SHA256="1fe5aacc9e438404a43b31bfa36fdf8b22e6d747932fa6e43200627a444a528f"
URL="https://github.com/interline-io/transitland-lib/releases/download/${VERSION}/transitland-linux"

cache_dir="${TRANSITLAND_CACHE_DIR:-$HOME/.cache/transitland}/${VERSION}"
bin="${cache_dir}/transitland"
mkdir -p "$cache_dir"

verify() { echo "${SHA256}  $1" | sha256sum --check --status; }

if [ -f "$bin" ] && verify "$bin"; then
  echo "Using cached transitland ${VERSION}"
else
  echo "Downloading transitland ${VERSION}"
  wget -q "$URL" -O "${bin}.tmp"
  if ! verify "${bin}.tmp"; then
    echo "Error: transitland ${VERSION} download does not match the expected SHA-256" >&2
    rm -f "${bin}.tmp"
    exit 1
  fi
  mv "${bin}.tmp" "$bin"
fi

chmod a+rx "$bin"
sudo install -m 0755 "$bin" /usr/local/bin/transitland
transitland version || true
