# E1.26 — Branch-Scoped Vercel Preview Suppression

**Scope:** `LuckyCharmNoRAGRATS/trading-sandbox`, branch `feature/e116-congress-readiness`. The congressional disclosure pipeline remains research-only and all 147 archived source records remain on HOLD. This milestone makes no Tracefour/House filing request, no Supabase write, no order, no schedule, and no deployment invocation.

## Rationale

E1.25 source/test/documentation commits generated two unwanted READY Vercel previews despite commit messages containing `[skip ci]`. Those previews are distinct from a production promotion and do not establish passing Python tests. GitHub-side commit messages are not a reliable deployment block.

Vercel's documented `git.deploymentEnabled` branch map excludes named branches from automatic Git deployments while unspecified branches retain the default `true` behavior: https://vercel.com/docs/project-configuration/git-configuration

## Persistent branch-only policy

The existing root `vercel.json` keeps `version=2`, `cleanUrls=true`, and the exact dashboard rewrite `/(.*) -> /index.html`. Only the following rule is added:

```json
"git": {
  "deploymentEnabled": {
    "feature/e116-congress-readiness": false
  }
}
```

The rule does **not** explicitly suppress `main`, `feature/quant-data` or any other branch. This is a Git automatic-deployment guard, **not** authorization for manual deployments, merging, database ingestion, or trading. A separate Vercel project or Git integration may have its own settings; this milestone changes only the named project configuration and this repository branch.

## Execution and verification

1. Identified connected Vercel project `trading-sandbox`, project ID `prj_rOWHGx7hE4aG5sxfVH3fmB1II9et`.
2. Applied temporary project-wide preview suppression **before making GitHub commits**, to protect against the first branch-rule commit itself producing a preview. This temporary project setting must be returned to its previous enabled state after all edits.
3. Committed the branch rule, five regression tests and registration with the existing no-network GitHub Actions suite on the isolated branch.
4. Before this document commit, direct read-only deployment lookups returned **zero deployments** for each of the three E1.26 commit SHAs `e67ba33d470b18b7f7cbe5552b12b86de589ac0e`, `7bb611a3cf2ba5718f83a5f7719d20a80b398868`, and `08f0d372b39aceffed65bce68a6d5eb8722313af`.
5. A local isolated test run of `tests/test_e126_vercel_branch_guard.py` with `python -m pytest -q -p no:cacheprovider` produced **5 passed, 0 failed** (JUnit retained locally).
6. After this document commit, verify no E1.26 preview deployments and restore the temporary project-wide preview setting to `false` (meaning preview deployment creation is again allowed for other branches). The permanent branch-only `vercel.json` rule remains.

These are the independently run E1.26 tests; the previous E1.25 larger test count is not re-reported as a fresh full-suite execution. GitHub Actions pytest outcome and future Vercel behavior after restoration are separate verification points.

## Safety and release decision

The static policy and offline regression suite block accidental *automatic Vercel previews from this research branch* by configuration, without disturbing the dashboard route and other branches. No original House filings were authenticated; E1.25's 17 candidate documents, 21 missing-link review tasks, 12 suspected duplicate groups, and 144 blank reviewer slots remain pending. No extracted row is eligible for Supabase ingestion or trading.

**NO-GO for disclosure ingestion, execution and deployment.** Manual Vercel deploys and approvals remain out of scope.
