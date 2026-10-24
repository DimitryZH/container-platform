# Main protection: owner setup and verification

Workflow availability, passing CI and enforced merge requirements are separate facts. Repository settings are changed manually by the owner, not by this documentation change.

## Observed configuration: 2026-10-24

Authenticated read-only API queries were available and returned:

| Query | Observation |
|---|---|
| `GET /repos/DimitryZH/container-platform/branches/main` | `protected: false` |
| `GET /repos/DimitryZH/container-platform/branches/main/protection` | HTTP 404, `Branch not protected` |
| `GET /repos/DimitryZH/container-platform/rulesets?includes_parents=true` | HTTP 200, empty list |
| `GET /repos/DimitryZH/container-platform/rules/branches/main` | HTTP 200, empty list |

These results establish that no classic protection or applicable ruleset was returned for `main` at observation time. The required checks are **not currently confirmed as enforced**. No ruleset bypass entries exist in the returned configuration; with no active rule this does not restrict direct branch updates. Administrative settings-edit permission was available, but no setting was changed. The ability to edit/remove rules is separate from a configured merge bypass.

Recheck after owner configuration. If a future query is denied or unavailable, record protection as **unverified**, rather than inferring that rules are absent.

## Preferred owner configuration

In repository **Settings → Rules → Rulesets**, create one branch ruleset, for example `main-pr-checks`, with enforcement **Active** and an exact `main` target (`refs/heads/main`, without exclusions).

| Setting | Required value |
|---|---|
| Bypass list | Empty, including no administrator/team/app bypass entry |
| Require a pull request before merging | Enabled |
| Required approving reviews | **0**, for the solo-maintainer process |
| Require conversation resolution | Enabled (may be labelled “Require all conversations to be resolved”) |
| Require status checks to pass | All three exact check names below |
| Require branches to be up to date before merging | Enabled |
| Block force pushes | Enabled |
| Restrict deletions | Enabled |
| Restrict updates / lock branch | **Disabled**; normal PR merges must remain possible |

Required status checks, with GitHub Actions selected as the expected source where offered:

- `Tests and package (Python 3.11)`
- `Tests and package (Python 3.13)`
- `Release check`

Save the rule as Active, not Evaluate/Disabled, and confirm all three checks were actually added. Manual runtime smoke is optional and must **not** be a required check. Do not add a merge-queue requirement: the current workflows do not support `merge_group`.

Zero approvals keeps a solo-maintainer PR record and CI gate without requiring an impossible self-approval. Maintainer self-review is not independent approval. Leave requirements for code-owner approval or approval from another recent-push reviewer off unless an independent reviewer/process is introduced explicitly. Review workflows, policy, inventory and validator changes carefully; PR-authored checks are not tamper-proof against their author.

See GitHub's [available rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) and [ruleset setup](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository). An administrator can still edit rules; do not describe the absence of bypass entries as protection against authorized settings changes.

## If the UI offers classic branch protection

Use an exact `main` branch protection rule with PRs required. If the approval dropdown starts at one, leave the separate **Require approvals** checkbox off to express zero approvals; keep the PR requirement enabled. Enable conversation resolution, the same three required checks and strict/up-to-date checking. Enable **Do not allow bypassing the above settings** (administrator enforcement); leave PR bypass actor lists empty. Leave **Allow force pushes**, **Allow deletions**, branch locking and update/push restrictions off. See [classic protection settings](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

Record whichever model was actually saved, its target/enforcement state, exact required checks, approval count and any real bypass actor/mode. Do not claim an equivalent no-bypass configuration if an administrator exemption remains enabled. Avoid overlapping rules unless their combined behavior is understood.

## Owner checklist on the documentation PR

1. Confirm the saved active rule applies to `main`; read back checks, up-to-date/conversation requirements, zero approvals, deletion/force-push restrictions and the empty bypass list. Update this observation with the actual configuration if it differs.
2. In the PR checks/merge section, confirm **each exact name above is marked required**, comes from GitHub Actions and passes on the current PR revision. Green optional checks alone do not establish enforcement.
3. Check for unresolved conversations or an out-of-date branch and verify the corresponding merge requirement, if naturally present. A Draft PR's merge restriction is a separate blocker, not evidence that branch protection works. Do not merge or mark Ready merely to perform this inspection.
4. If a pending required check is naturally observed after rule activation, record whether the merge section blocks on it. Do not manufacture failures or direct-push to `main`. If pending-check blocking was not observed, explicitly retain that limitation.
5. Record the PR URL/revision, rule state, three check outcomes and any visible bypass permission. Keep issue #17 and the milestone open until enforcement evidence and the changes are accepted.

This documentation PR can demonstrate attached checks and supply an inspection point. It cannot by itself demonstrate enforcement before the owner activates the rule. No pending-check blocking or live cancellation recovery has been established by green CI.
