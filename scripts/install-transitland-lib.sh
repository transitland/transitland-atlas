#!/usr/bin/env bash

set -euo pipefail  # Fail on any error, undefined var, or pipe failure

VERSION="v1.0.0-rc3"
BINARY="transitland-linux"
INSTALL_PATH="./transitland"  # Install locally, not system-wide

echo "Downloading transitland-lib ${VERSION}..."
if ! wget -q --show-progress \
  "https://github.com/interline-io/transitland-lib/releases/download/${VERSION}/${BINARY}" \
  -O "${BINARY}"; then
    echo "ERROR: Failed to download transitland-lib"
    exit 1
fi

if [[ ! -s "${BINARY}" ]]; then
    echo "ERROR: Downloaded file is empty or missing"
    exit 1
fi

echo "Setting permissions..."
if ! chmod a+rx "${BINARY}"; then
    echo "ERROR: Failed to set executable permissions"
    exit 1
fi

echo "Installing to ${INSTALL_PATH}..."
mv "${BINARY}" "${INSTALL_PATH}"

echo "Verifying installation..."
if ! ./transitland --version; then
    echo "ERROR: Installation verification failed"
    exit 1
fi

echo "Successfully installed transitland-lib ${VERSION}"