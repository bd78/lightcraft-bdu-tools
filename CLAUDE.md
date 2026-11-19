# Rules for agents working in lightcraft-bdu-tools

Python and PowerShell helper scripts for contributing to storytold/lightcraft. See `README.md` for what each
script does.

## Always

1. **Branch before editing.** Never work, commit or push on `main`. Start every change from an up-to-date `main`:

   ```sh
   git switch main
   git pull --ff-only origin main
   git switch -c fix/<short-kebab-name>    # to fix something
   git switch -c feat/<short-kebab-name>   # for a new feature or tool
   ```

   One branch per change. If you are already on the `fix/` or `feat/` branch for this same change, keep using it.

2. **Ship through the `ship` skill** (`.claude/skills/ship/SKILL.md`). Whenever changes are to be committed, pushed
   or proposed, follow it in full: credential check, local CodeRabbit review with every finding fixed until none
   remain, script checks, commit, push to `origin`, pull request. Never commit outside it.

3. **Keep credentials on this machine.** `coderabbit review` uploads the uncommitted changes, so run
   `python check_secrets.py` before it, and never commit `.venv/`, `reports/`, `.claude/settings.local.json`,
   secrets, tokens or personal data.
