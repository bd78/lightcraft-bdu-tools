# lightcraft-bdu-tools

Personal helper scripts for contributing to [storytold/lightcraft](https://github.com/storytold/lightcraft).

Requirements: Python 3.9+ and the [GitHub CLI](https://cli.github.com) logged in (`gh auth status`).
No third-party Python packages.

## `activate_venv.py`: the virtual environment

Creates `.venv` on first use (and installs `requirements.txt` whenever it changes). A script can't
change the shell that started it, so it offers three ways in:

```sh
python activate_venv.py                          # new shell with the venv active; `exit` to leave
python activate_venv.py run python gap_report.py # one command inside the venv
python activate_venv.py path                     # the activation script, to load it yourself:
. (python activate_venv.py path)                 #   PowerShell
source "$(python activate_venv.py path)"         #   bash
python activate_venv.py --recreate               # rebuild .venv first
```

## `check_secrets.py`: no credentials in a review or commit

Scans staged changes, unstaged changes and new files for tokens, keys and password assignments, and
prints only `file:line (kind)`, never the value. Exit code 1 when something is found. Run it before
`coderabbit review` (which uploads the changes) and before committing; see `CLAUDE.md`.

```sh
python check_secrets.py
```

## `gap_report.py`: what to work on next

Writes `reports/lightcraft-next-<date>.md` with:

- **the top 10 gaps** from the ranked table in `docs/gaps.md`, in its order, skipping each gap that has an
  open pull request (matched by keywords in PR titles) with a commit in the last 28 days;
- **the top 10 fixes**: open issues labelled `priority: critical` / `high` / `medium` / `low` and `bug`,
  `regression`, `security` or `performance`, that are not assigned, not labelled `needs info` /
  `in progress` / similar, have no open pull request closing them with a recent commit, and nobody claimed
  in a comment in the last 28 days. Sorted by priority, then 👍, then oldest first.

```sh
python activate_venv.py run python gap_report.py
python gap_report.py --top 10 --stale-days 28 -o reports/next.md
python gap_report.py --gaps-file ../lightcraft/docs/gaps.md      # a local gaps.md instead of GitHub's
python gap_report.py --terms-file my_terms.json                  # extra PR title keywords per gap
python gap_report.py --help
```

Fewer than 10 gaps are listed when fewer are free. Matching is by keywords, so a PR with an unusual title
can be missed and a broad keyword can match an unrelated one: the report lists the PRs it matched and the
search terms it used. Edit `DEFAULT_TERMS` in `gap_report.py` (or pass `--terms-file`) when `docs/gaps.md`
gains a gap the defaults don't cover. A run takes about 1–2 minutes.
