#!/bin/bash
# Cuts a new Palimpsest release and updates the Homebrew formula to match.
#
# What it does, in order:
#   1. Tags this repo (vX.Y.Z) and pushes the tag.
#   2. Downloads that tag's tarball from GitHub and hashes it.
#   3. Edits Formula/palimpsest.rb in the homebrew-palimpsest clone to point
#      at the new tag/hash, commits, and pushes.
#   4. Refreshes Homebrew's own copy of the tap and audits the published
#      formula, so you find out immediately if something's wrong rather
#      than when a user runs `brew install`.
#
# Usage:
#   scripts/release-homebrew.sh 1.1.0
#
# Requires a clone of the formula repo at ~/homebrew-palimpsest (SSH remote -
# the tap Homebrew manages under its own Taps/ directory is HTTPS and can't
# push). One-time setup if you don't have it:
#   git clone git@github.com:eevlice/homebrew-palimpsest.git ~/homebrew-palimpsest

set -euo pipefail

VERSION="${1:?Usage: $0 <version, e.g. 1.1.0>}"
TAG="v$VERSION"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TAP_DIR="$HOME/homebrew-palimpsest"
FORMULA="$TAP_DIR/Formula/palimpsest.rb"

cd "$REPO_DIR"

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree not clean - commit or stash first." >&2
  exit 1
fi
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Tag $TAG already exists - pick a version that hasn't been released." >&2
  exit 1
fi

echo "==> Tagging $TAG on Palimpsest"
git checkout main
git pull --ff-only
git tag -a "$TAG" -m "Palimpsest $VERSION"
git push origin "$TAG"

echo "==> Hashing the release tarball"
URL="https://github.com/eevlice/Palimpsest/archive/refs/tags/$TAG.tar.gz"
TMPFILE="$(mktemp)"
curl -sL -o "$TMPFILE" "$URL"
SHA256="$(shasum -a 256 "$TMPFILE" | cut -d' ' -f1)"
rm -f "$TMPFILE"
echo "    $SHA256"

if [ ! -d "$TAP_DIR" ]; then
  echo "Expected the formula clone at $TAP_DIR - see the one-time setup note at the top of this script." >&2
  exit 1
fi

echo "==> Updating the formula"
cd "$TAP_DIR"
git checkout main
git pull --ff-only
sed -i '' "s|archive/refs/tags/v[0-9][0-9.]*\.tar\.gz|archive/refs/tags/$TAG.tar.gz|" "$FORMULA"
sed -i '' "s|sha256 \"[a-f0-9]*\"|sha256 \"$SHA256\"|" "$FORMULA"
git add Formula/palimpsest.rb
git commit -m "Update to $VERSION"
git push origin main

echo "==> Verifying the published formula"
brew update --quiet
brew audit --formula eevlice/palimpsest/palimpsest

echo
echo "Done. $TAG is live. To pick it up here: brew upgrade palimpsest"
echo "(or brew install eevlice/palimpsest/palimpsest if you'd uninstalled it)"
