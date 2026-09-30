# Repository workflow

Forgejo is the writable source repository for Traviorel:
`https://forgejo.barking-hake.ts.net/lesecuritae/Traviorel`.
GitHub (`https://github.com/lesecuritae/Traviorel`) is the public mirror.

Push commits to Forgejo `main` first. Forgejo then mirrors `main` to GitHub
when a new commit arrives. Do not merge pull requests directly on GitHub:
fetch the proposed branch, review and merge it into Forgejo `main`, and let
the mirror publish that resulting commit to GitHub. This keeps both sites on
the same history and avoids losing changes during a mirror update.
