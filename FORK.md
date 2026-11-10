# This fork

`kemalp/strix` is a fork of [usestrix/strix](https://github.com/usestrix/strix) that adds
sign-in with a Claude subscription (`strix auth login claude`) so scans can run on a Claude
plan — including Mythos — instead of a metered API key. See
[docs/llm-providers/anthropic.mdx](docs/llm-providers/anthropic.mdx).

## Remotes

| Remote | Points at | Used for |
|--------|-----------|----------|
| `origin` | `kemalp/strix` | pushing our work |
| `upstream` | `usestrix/strix` | pulling their updates (push is disabled) |

`main` is a plain mirror of upstream and is never committed to. All work lives on
`claude-subscription`, which is the branch checked out by default.

## Taking upstream updates

Merge into the feature branch, never into `main`:

```bash
git fetch upstream
git merge upstream/main        # on claude-subscription
git push                       # goes to origin, never upstream
```

Upstream has no knowledge of this work, so their releases never touch it: our commits stay
in `claude-subscription` and a merge only brings their code in around them. If upstream
refactors something we patch (`strix/config/models.py`, `strix/interface/auth_cli.py`), git
reports the conflict in that file — resolve it there and keep the `claude/` route intact.

## Branch protection

`claude-subscription` is protected against force-pushes and deletion, and two checks must
report green: `lint` and `pytest (fast)`.

It deliberately does not require the full `pytest (3.12)` / `pytest (3.14)` matrix. That
matrix takes longer than the job timeout, so it is killed and never resolves — requiring it
would mean a check that can never turn green. `pytest (fast)` runs the config, routing and
auth tests in about ten seconds and is the signal that actually matters here; the full matrix
still runs for information.

Admins are not enforced, so a direct `git push` to `claude-subscription` overrides the
checks. Pushes only get the "4 of 4 required status checks are expected" warning, not a
rejection. Nothing here depends on GitHub Actions minutes being available.
