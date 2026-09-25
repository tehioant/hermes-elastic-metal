---
name: create-pull-request
description: Create a GitHub pull request in this repo with the gh CLI, following team conventions - conventional commit title, written description, 1 to 3 labels, a reviewer, and a conventional branch name when a branch must be created. Use this skill whenever the user asks to open, create, submit, raise or push a PR / pull request / merge request, or says things like "ship this", "open a PR for my changes", or "push and create the PR", even if they don't mention the conventions.
---

# Create Pull Request

Pull requests in this repo follow strict conventions so history stays readable and reviews get routed. Use the `gh` CLI for every GitHub interaction.

## 1. Inspect the current state

```bash
git status --short
git --no-pager branch --show-current
git --no-pager log --oneline origin/main..HEAD
git --no-pager diff origin/main...HEAD --stat
```

Understand what the change does from the diff and commits — the title, labels and description all derive from it. If there are uncommitted changes, ask the user whether to commit them before continuing.

## 2. Branch (only if needed)

Create a branch when you are on `main` (or the default branch) or the user asks for a new one. Never open a PR from `main`.

Branch names follow Conventional Branch: `<type>/<short-kebab-description>`.
- Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `perf`, `build`, `hotfix`, `release`
- Lowercase, hyphens only, short and descriptive.
- Examples: `feat/add-elastic-autoscaling`, `fix/terraform-state-lock`, `chore/bump-provider-versions`

```bash
git switch -c <type>/<description>
```

If already on a well-named feature branch, keep it.

## 3. Title — Conventional Commits

Format: `<type>(<optional scope>): <imperative summary>`
- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`
- Add `!` after type/scope for breaking changes: `feat(api)!: drop v1 endpoints`
- Lowercase summary, imperative mood, no trailing period, under ~72 chars.
- The type should match the branch type when a branch exists.

Examples: `feat(terraform): add hetzner deploy module`, `fix(ci): correct plan job working directory`

## 4. Description

Always write one — reviewers rely on it. Use this template:

```markdown
## Summary
<1-3 sentences: what changed and why>

## Changes
- <key change>
- <key change>

## Testing
<how it was validated, or "Not tested" with reason>

## Notes
<breaking changes, follow-ups, risks — omit section if none>

Co-Authored-By: Warp <agent@warp.dev>
```

Write the body to a temp file and pass it with `--body-file` to avoid shell quoting problems.

## 5. Labels — minimum 1, maximum 3

Pick only from labels that exist in the repo:

```bash
gh label list
```

Choose the 1-3 most relevant (e.g. `feat` → `enhancement`, `fix` → `bug`, `docs` → `documentation`). Do not create new labels unless the user asks. If nothing fits, ask the user rather than skipping — a PR without a label violates the convention.

## 6. Reviewer

Default reviewer: `tehioant`. GitHub rejects requesting a review from the PR author, so check first:

```bash
gh api user --jq .login
```

If the author is `tehioant`, ask the user who should review instead. If the user names a reviewer, use that.

## 7. Push and create

```bash
git push -u origin HEAD
gh pr create \
  --base main \
  --title "<conventional title>" \
  --body-file /tmp/pr-body.md \
  --label "<label1>" --label "<label2>" \
  --reviewer "<reviewer>"
```

PRs are ready for review (not drafts) unless the user asks otherwise.

## 8. Verify and report

```bash
gh pr view --json url,title,labels,reviewRequests
```

Confirm the title, 1-3 labels and reviewer are present; fix with `gh pr edit` if not. Report the PR URL to the user.

## Error handling

- `gh auth status` fails → tell the user to run `gh auth login`; stop.
- Push rejected → report the error; do not force-push without explicit permission.
- A PR already exists for the branch → show it with `gh pr view` and offer to update it with `gh pr edit` instead.
