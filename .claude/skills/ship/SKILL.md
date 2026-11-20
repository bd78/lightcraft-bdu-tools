---
name: ship
description: Commit, push and open a pull request for changes in lightcraft-bdu-tools. Checks the uncommitted changes for credentials, runs CodeRabbit locally and fixes findings until none remain, checks the scripts, commits on the fix/ or feat/ branch, pushes to origin and creates the PR with gh. Use when asked to commit, push, ship or open a PR in this repository.
---

# Ship a change: review, commit, push, pull request

Run these steps in order for the uncommitted changes of this repository. Don't skip a step.

## 1. Check the branch

- `git branch --show-current` must be a `fix/<short-kebab-name>` (bug fix) or `feat/<short-kebab-name>` (new
  feature or tool) branch for this change, e.g. `fix/gap-search-false-matches` or `feat/issue-age-filter`.
- If you are on `main`, create the branch now; the uncommitted changes come with it:

  ```sh
  git switch -c fix/<short-kebab-name>    # or feat/<short-kebab-name>
  ```

- `git status --short --untracked-files=all`: decide which files belong to this change. If nothing changed, stop
  and say so.

## 2. Check for credentials

The review sends every uncommitted change to CodeRabbit's servers, so check all of it first:

```sh
python check_secrets.py
```

It scans staged changes, unstaged changes and new files, and prints only `file:line (kind)`, never the value. Don't
print a flagged line yourself (no `git diff`, `cat` or `Read` of it) while it may hold a credential.

If it reports something:

1. Unstage the file if it is staged: `git restore --staged <file>`.
2. Take the credential out of the change: delete it, move the file outside the repository, or add the file to
   `.gitignore`.
3. Run `python check_secrets.py` again. Go on only when it reports nothing. If you can't remove a flagged item,
   stop and ask the person.

## 3. Run CodeRabbit on the uncommitted changes

```sh
coderabbit review --agent --uncommitted --include-untracked
```

- The flag is `--agent` (singular). `--include-untracked` keeps new files in the review; don't drop it to work
  around step 2, or new files would be committed without a review.
- The review counts as clean only when its last line reports `"status":"review_completed"` **and** `"findings":0`.
  A skipped or interrupted review (`review_skipped`, a timeout, no `complete` line) is not clean: run it again.
  A result with `"reused":true` after you changed files did not look at them: run it again with `--fresh`.
- A review takes 2–6 minutes and can take up to 15; stopping it early discards it. Don't pipe it through `tail`
  (that hides progress until the end). Run it in the background and do the step 5 checks meanwhile, but don't edit
  files while it runs.
- The repository gets 10 reviews per rolling hour (`coderabbit usage`). If the command fails (not installed, not
  logged in, usage limit), stop and report the exact error. Don't commit as if the review had passed.

## 4. Fix every finding, then review again

- Fix all findings of a run before reviewing again: each run takes minutes and uses quota.
- Fix what each finding describes, narrowly. Don't change unrelated code.
- Go back to step 2. Repeat until CodeRabbit reports **no findings**.
- Loop guard: if the same finding comes back after two fix rounds, or a finding looks wrong, stop. Report the
  finding, why you think it is wrong or can't be fixed, and what you tried, then ask what to do. Never hide or
  dismiss a finding to get a clean run.

## 5. Check the scripts

```sh
python -m py_compile activate_venv.py gap_report.py check_secrets.py
python activate_venv.py run python gap_report.py --help
```

When a change affects the report, also run `python activate_venv.py run python gap_report.py` and read the report
it writes. Remove `__pycache__/` if `py_compile` left one.

## 6. Commit

- Stage files by path (`git add <path> …`), not `git add -A`, and check `git status` first.
- Never commit `.venv/`, `reports/`, `.claude/settings.local.json`, secrets, tokens or personal data.
- Message: a short imperative summary line, a blank line, then what changed and why.

## 7. Push to `origin` and open a pull request

```sh
git push -u origin <branch>
gh pr create --repo bd78/lightcraft-bdu-tools --base main --head <branch> --title "<summary>" --body "<what and why>"
```

- If the branch already has an open PR (`gh pr view <branch>`), the push updates it; don't create another.
- The PR description says what changed, why, and what you ran to check it (the CodeRabbit result, the step 5
  commands).
- Give the person the PR link. Don't merge it unless they ask.
