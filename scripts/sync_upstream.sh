#!/usr/bin/env bash
# Merge the original (cyberjunky/python-garminconnect) into the app branch.
# master = pure mirror of upstream, streamlit-app = master + Streamlit app.
# Never resets or force-pushes; upstream push is disabled.
set -euo pipefail
git diff --quiet && git diff --cached --quiet || { echo "Working tree not clean"; exit 1; }
git fetch upstream
git checkout streamlit-app
git branch -f backup/pre-sync-$(date +%F) HEAD
git merge upstream/master -m "Merge upstream master into fork"
git push origin streamlit-app
