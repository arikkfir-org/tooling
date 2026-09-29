#!/bin/sh
# Builds dist/ from the committed tree:
#   dist/bundles/<sha256>.tar.gz   the bundle: committed files under claude/ only (uncommitted files never ship)
#   dist/setup.sh                  the setup script, pinned to that bundle
set -eu

cd "$(dirname "$0")/.."
rm -rf dist
mkdir -p dist/bundles

git archive --format=tar.gz --output=dist/bundle.tar.gz HEAD -- claude
sha="$(sha256sum dist/bundle.tar.gz | cut -d' ' -f1)"
mv dist/bundle.tar.gz "dist/bundles/${sha}.tar.gz"
sed "s/@BUNDLE_SHA256@/${sha}/" setup/setup.sh > dist/setup.sh
chmod 0755 dist/setup.sh

echo "Built bundle ${sha}"
