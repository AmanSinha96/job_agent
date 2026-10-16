#!/usr/bin/env bash
# Commit and push this run's data/jobs.db (+ small cache files), shared by
# all three sweep workflows.
#
# jobs.db is what makes cross-run dedup work — if this run's copy doesn't
# land on main, the next run re-saves and re-emails the same jobs. When the
# push is rejected because another workflow pushed first, this run's rows are
# merged into the newer jobs.db from main (merge_db.py) rather than picking
# one whole side of the binary file. The previous step used
# `git rebase -X ours`, which during a rebase keeps the UPSTREAM side — it
# silently discarded this run's updates every time two runs overlapped.
#
# Usage: scripts/commit_data.sh "<commit message>"
set -u

msg="$1"
data_files=(data/jobs.db resume_cache.json generated_resumes/index.json)

git config --local user.email "action@github.com"
git config --local user.name "Job Agent Bot"

for f in "${data_files[@]}"; do
  [ -e "$f" ] && git add "$f"
done

if git diff --cached --quiet; then
  echo "Nothing to commit."
  exit 0
fi
git commit -q -m "$msg"

for attempt in 1 2 3 4 5; do
  if git push -q; then
    echo "Pushed updated data."
    exit 0
  fi
  echo "Push rejected (attempt $attempt/5) — merging this run's jobs.db into latest main..."

  tmp="$(mktemp -d)"
  cp data/jobs.db "$tmp/ours.db"
  git show HEAD~1:data/jobs.db > "$tmp/base.db" 2>/dev/null || : > "$tmp/base.db"
  for f in resume_cache.json generated_resumes/index.json; do
    [ -e "$f" ] && mkdir -p "$tmp/$(dirname "$f")" && cp "$f" "$tmp/$f"
  done

  git fetch -q origin main
  git reset -q --hard origin/main

  python merge_db.py --ours "$tmp/ours.db" --base "$tmp/base.db" --into data/jobs.db || {
    echo "::warning::merge_db.py failed — this cycle's jobs.db updates were not persisted."
    exit 0
  }
  for f in resume_cache.json generated_resumes/index.json; do
    [ -e "$tmp/$f" ] && mkdir -p "$(dirname "$f")" && cp "$tmp/$f" "$f"
  done

  for f in "${data_files[@]}"; do
    [ -e "$f" ] && git add "$f"
  done
  if git diff --cached --quiet; then
    echo "Nothing left to commit after merge."
    exit 0
  fi
  git commit -q -m "$msg"
  sleep $((attempt * 5))
done

echo "::warning::Could not push updated data/jobs.db after 5 attempts — this cycle's dedup state was not persisted."
exit 0
