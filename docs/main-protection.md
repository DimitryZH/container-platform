# Main protection: owner setup and verification

Workflow availability, passing CI and enforced merge requirements are separate facts. Repository settings are changed manually by the owner, not by this documentation change.

## Confirmed configuration: 2026-10-24

Read-only API inspection confirmed repository branch ruleset [main-protection](https://github.com/DimitryZH/container-platform/rules/25802975), ID `25802975`, with the same rules returned by the effective-rules endpoint for `main`.

| Setting | Confirmed value |
|---|---|
| Enforcement | `active` |
| Target | `~DEFAULT_BRANCH`, currently `main`; no exclusions |
| Bypass actors | Empty; current API caller's bypass permission is `never` |
| Require a pull request | Enabled |
| Required approving reviews | **0** |
| Conversation resolution | Enabled (`required_review_thread_resolution: true`) |
| Required checks | The three exact names below, from GitHub Actions |
| Branch must be up to date | Enabled (`strict_required_status_checks_policy: true`) |
| Force pushes | Blocked (`non_fast_forward`) |
| Branch deletion | Blocked (`deletion`) |
| Ordinary update restriction / branch lock | Not configured; normal PR merges remain possible |

Required checks:

- `Tests and package (Python 3.11)`
- `Tests and package (Python 3.13)`
- `Release check`

Manual runtime smoke is not required. Code-owner review and last-push approval requirements are off; allowed merge methods are merge, squash and rebase. No rules were changed by this documentation update.

The owner confirmed in the merge section of [PR #18](https://github.com/DimitryZH/container-platform/pull/18) that all three checks were labelled **Required** and successful before this documentation follow-up (revision `99e7ff4888e966f2edb92b690836da84df95dc65`). This owner UI observation and the read-only active-rule/effective-rule inspection establish configured requirements; green CI alone would not establish them. New commits still require their own successful checks.

**Verification limit:** pending-check blocking was not separately observed. The PR remains Draft; Draft blocking is not evidence of blocking by a pending required check. Live cancellation recovery also remains unverified.

The earlier read-only observation on 2026-10-24 returned `protected: false`, classic-protection HTTP 404 and empty ruleset/effective-rule lists. That pre-configuration observation is superseded by the confirmed active ruleset above. If future API access is denied or unavailable, record the state as **unverified**, rather than inferring that rules are absent.

## Maintaining the owner-managed rule

Keep `main-protection` Active with the table's settings and an empty bypass list. Do not enable ordinary-update restrictions that prevent PR merges, add manual runtime smoke as a required check, or add a merge-queue requirement (`merge_group` is unsupported by the current workflows).

Zero approvals supports the solo-maintainer process; maintainer self-review is not independent approval. Review workflow, policy, inventory and validator changes carefully: PR-authored checks are not tamper-proof against their author. An administrator's ability to edit/remove rules is separate from a configured merge bypass; do not describe the empty bypass list as protection against authorized settings changes.

See GitHub's [available rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) and [ruleset setup](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository).

## If the UI offers classic branch protection

Use an exact `main` branch protection rule with PRs required. If the approval dropdown starts at one, leave the separate **Require approvals** checkbox off to express zero approvals; keep the PR requirement enabled. Enable conversation resolution, the same three required checks and strict/up-to-date checking. Enable **Do not allow bypassing the above settings** (administrator enforcement); leave PR bypass actor lists empty. Leave **Allow force pushes**, **Allow deletions**, branch locking and update/push restrictions off. See [classic protection settings](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

The current configuration uses the confirmed ruleset above, not this classic alternative. If the owner changes models later, record the actual target/enforcement state, check names, approval count and bypass actor/mode. Do not claim a no-bypass equivalent while an administrator exemption remains enabled; avoid unexplained overlapping rules.

## Owner checklist on the documentation PR

1. Confirm the saved active rule applies to `main`; read back checks, up-to-date/conversation requirements, zero approvals, deletion/force-push restrictions and the empty bypass list. Update this observation with the actual configuration if it differs.
2. In the PR checks/merge section, confirm **each exact name above is marked required**, comes from GitHub Actions and passes on the current PR revision. Green optional checks alone do not establish enforcement.
3. Check for unresolved conversations or an out-of-date branch and verify the corresponding merge requirement, if naturally present. A Draft PR's merge restriction is a separate blocker, not evidence that branch protection works. Do not merge or mark Ready merely to perform this inspection.
4. If a pending required check is naturally observed after rule activation, record whether the merge section blocks on it. Do not manufacture failures or direct-push to `main`. If pending-check blocking was not observed, explicitly retain that limitation.
5. Record the PR URL/revision, rule state, three check outcomes and any visible bypass permission. Keep issue #17 and the milestone open until enforcement evidence and the changes are accepted.

The active rule and owner-confirmed Required labels are now recorded. Pending-check blocking and live cancellation recovery remain unverified; neither follows from green CI or Draft blocking. Issue/milestone acceptance and any merge remain owner decisions.
