#!/usr/bin/env bash
# Safely merge upstream (cyberjunky/python-garminconnect) into the fork.
# Never resets or force-pushes: the Streamlit app stays in history.
set -euo pipefail
git diff --quiet && git diff --cached --quiet || { echo "Working tree not clean"; exit 1; }
git fetch upstream
git branch -f backup/pre-sync-$(date +%F) HEAD
git merge upstream/master -m "Merge upstream master into fork"
git push origin HEAD
git push origin --tags
