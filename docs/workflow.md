# Development workflow

This is the lifecycle of StoUTextureSync work, from requirement to archive. It
needs only this repository and `gh` run from its clone. Normative constraints
live in [AGENTS.md](../AGENTS.md); this document says where each kind of
information goes and when a task is finished.

## Owners

| Information | Owner |
| --- | --- |
| Requirements not yet delivered, phase assignment, product decisions | Product issue [#1](https://github.com/Alexis-Li/StoUTextureSync/issues/1) |
| Milestone scope, acceptance criteria, exclusions, dependencies | Milestone issue (M0 [#2](https://github.com/Alexis-Li/StoUTextureSync/issues/2), M1 [#3](https://github.com/Alexis-Li/StoUTextureSync/issues/3), later ones created the same way) |
| Diff, code review, findings and local checks | Pull request |
| Host acceptance evidence | Host acceptance report on the milestone issue |
| Current behavior, installation, limits, recovery | `README.md` / `README_CN.md` |
| Vocabulary and architecture decisions | [CONTEXT.md](../CONTEXT.md), [docs/adr/](adr/) |
| Released and unreleased changes | [CHANGELOG.md](../CHANGELOG.md) |
| Selected completed decisions and essential evidence | [docs/history/](history/README.md) |
| Original requirement drafts | [docs/history/](history/README.md), read-only |

Link to the owner instead of copying its content.

## Requirement IDs

Issue #1 defines the product IDs: `FR-xx` functional requirements, `SP-xx` /
`UE-xx` / `REPO-xx` / `QA-xx` tasks, `POC-xx` validations and `DEC-xx`
decisions, with P0/P1/P2 priority. A milestone issue lists the product IDs it
covers and states its own observable `AC-01` style criteria. Review findings
use `R-001` style IDs, allocated across one issue after reading existing ones.
IDs are never renumbered or reused; a dropped item keeps its ID and says why.

## 1. From requirement to milestone task

1. **Agree the requirement.** New or changed product scope is agreed in an
   issue comment or by the user, then written into issue #1 in place. Issue #1
   keeps only the current agreed requirement; GitHub's edit history and the
   drafts keep earlier wording.
2. **Open the milestone.** Create one issue per milestone from the
   *Milestone task* template. Start with `Part of #1`, add `Blocked by: #<n>`
   for unfinished prerequisites, list the covered product IDs, and write
   acceptance as observable `AC-xx` items with the hosts that must show them.
   Separate suggested implementation from agreed requirements.
3. **Split when useful.** A milestone may get child issues for independent
   pieces (one PoC, one host component). Each child starts with
   `Part of #<milestone>` and owns its own acceptance; the milestone links them.
4. **Decide before building.** A `DEC-xx` decision (for example the M0 route
   choice) is recorded as a user-confirmed comment on its milestone, then
   moved to an ADR and reflected in issue #1 before dependent work starts.

M0 is a validation milestone: its deliverable is evidence and the `DEC-01`
route decision, not a releasable plug-in. Prototype code that must be kept
goes through a PR and stays outside `plugins/` unless it becomes product code;
one-off harnesses stay in scratch.

## 2. Delivery and review

- **Branch.** Work on `codex/<topic>` from `main` and open a PR back to `main`.
  Use one PR per reviewable piece; a milestone usually has several.
- **Describe the delivery.** The PR template records the issue and AC/finding
  IDs covered, the full base and head SHAs, the checks actually run and what
  remains. Use `Refs #<issue>`, never a closing keyword: merging into `main`
  would close the issue before host acceptance.
- **Review the actual change.** Read the issue, then the complete diff at the
  named SHAs, and check acceptance, failure paths, lifecycle (`close_plugin()`,
  reload) and unrelated changes. Each finding gets an `R-xxx` ID with trigger,
  impact, evidence and completion condition; severity and blocking are separate
  judgments. A claimed fix stays *fixed, pending re-review* until reviewed.
  When the author's own account publishes the review, it is a PR comment with
  baseline, scope, conclusion and evidence, not a GitHub approval.
- **Re-review after change.** Approval covers the stated SHA. Later commits or
  `main` drift that touch the reviewed scope need an incremental review and the
  affected checks before merge.
- **Merge.** Merge with `gh pr merge --match-head-commit <reviewed-head>` and
  note the resulting `main` SHA on the PR. Merge follows the user's
  authorization.
- **Update the issue.** A short comment: PR link, AC covered, state reached
  (implemented, reviewed, merged, host-verified) and the remaining gap.

Ordinary checks (`tools/validate_project.py`, compileall, unit tests, CI) are
local evidence only. They never establish host compatibility.

## 3. Host acceptance

Events, export, UI, file publication timing, UE reimport and lifecycle need
evidence in the declared hosts: Painter 11.0 and the official Unreal Editor
5.7.4. Record evidence as a host acceptance report on the
milestone (or child) issue, using the report format below with kind
`acceptance`. Each report states:

- tested commit (full SHA) or package name and version;
- exact host builds, OS and the relevant machine facts (for example 32GB RAM
  and whether the other host was open);
- sample identity by a neutral name (`clothing sample A`, `body UDIM sample`),
  preset name, Texture Set / Tile count and output sizes; never private asset
  contents or local paths;
- reproducible steps, measured values with units, sample counts, median / P95
  where the issue asks for them, and the pass or fail result per AC or PoC;
- untested hosts and scenarios, listed as still unverified.

A host that was not tested stays unverified even when another host passed.
Measurements that support a conclusion and would otherwise be lost (timings,
memory peaks, visual owner acceptance) go into a history record at closure.

## 4. Closure

Close a milestone issue explicitly, with a comment that links the final review,
the integration SHA on `main` and the host acceptance reports, when:

- every AC is met, or dropped or moved with the user's recorded decision;
- no blocking `R-xxx` finding is open;
- the delivery is on `main` and the host evidence matches that code.

Validation milestones close against their evidence and decision. Closing a
milestone does not release it: packaging, the `v<version>` tag and the GitHub
release follow the release rules in AGENTS.md as a separate step, and the
collection updates its submodule pin only after the release commit is published.

## 5. Keeping the requirements current

Issue #1 owns the requirements while they are undelivered. Once a milestone
closes, its delivered requirements are owned by the maintained product:

| Delivered content | New owner |
| --- | --- |
| User-visible behavior, configuration, limits, recovery | README pair |
| Terms, architecture and route decisions | CONTEXT.md, ADRs |
| Detailed contracts (naming, mapping, scheduling, publication) | Code and project tests; an ADR where the rule is a decision |
| Measured performance and host evidence | `docs/history/` record |

In the same closeout, edit issue #1: replace each delivered requirement's body
with one line naming the milestone, the closing comment and its new owner, and
keep undelivered P1/P2 items, open product choices and the milestone index in
full. This keeps #1 a short list of what is still to be built.

After that, a change to delivered behavior is a new feature or bug issue that
cites the owning document, not an edit of #1. Issue #1 closes when every phase
is delivered, dropped by decision, or moved to its own issue. No second
requirement baseline is maintained in the repository; `docs/history/` drafts
are the original source record only.

## 6. Archival

Most closed work needs no extra document: its evidence stays in the PR and
issue. At milestone closure, use [docs/history/](history/README.md) for what a
future decision or regression check will need: the route decision rationale,
host measurements, owner visual acceptance, hard-to-reproduce failures. Extend
an existing topic record before creating one.

Deleting or substantially rewriting issue comments is a separate, requested
closeout. Git does not keep remote comments, so before removing any, save the
originals (ID, author, timestamps, URL, full body) in a JSON archive under
`docs/history/`, publish it, compare it with the remote, and only then delete
superseded agent reports. Human discussion and user decisions are never
deleted. Remove task-owned scratch after the evidence it held is preserved.

## Issue report format

Formal agent reports on issues (development without a PR, review without a PR,
host acceptance, current handoff) use this structure so they can be found and
updated. There is no checker; verify it by reading the draft before publishing.

```markdown
<!-- issue-report:v1 kind=acceptance issue=2 key=acc-01 -->
## 宿主验收

- 状态：通过 / 不通过 / 部分通过 / 证据不足
- 基线：<full SHA or package version>
- 范围：<AC/POC IDs covered>
- 依据：<requirement and PR links>
- 阻塞项：R-001, R-002 / 无
- 本次验证：本轮执行 / 历史复核 / 混合 / 未执行
- 更新日期：YYYY-MM-DD

### 环境与样本
### 步骤与结果
### 未验证与限制
### 下一步
```

| Kind / title | Status values | Sections |
| --- | --- | --- |
| `development` / `## 开发交付` | 待审核 / 部分完成 / 受阻 | 交付结果, 验证与证据边界, 剩余事项, 下一步 |
| `review` / `## 审核结论` | 通过 / 需修改 / 证据不足 | 结论, 审核问题, 验证与证据边界, 后续处理 |
| `acceptance` / `## 宿主验收` | 通过 / 不通过 / 部分通过 / 证据不足 | 环境与样本, 步骤与结果, 未验证与限制, 下一步 |
| `summary` / `## 当前结论` | 进行中 / 待审核 / 待验证 / 受阻 / 可关闭 / 已关闭 | 未闭环事项, 已验证成果与边界, 验证依据, 接手入口, 记录索引 |

- Keys: `dev-01`, `review-01`, `acc-01`; the one summary per issue uses
  `current`, created only when several deliveries or rounds make the next
  action hard to find. A new baseline or a different conclusion gets a new
  key; minor corrections are edited in place.
- Write `无` in an empty section, never leave placeholders. Use
  repository-relative paths or placeholders such as `<Painter11>` or
  `<scratch>`, never drive letters or user directories.
- Before publishing, read all comments (`gh issue view <n> --comments`) and
  find an existing report with the same kind and key. Create with
  `gh issue comment <n> --body-file <draft.md>`; edit an existing one by its
  numeric comment ID through `gh api --method PATCH
  repos/{owner}/{repo}/issues/comments/<id> --input <payload.json>`, after
  re-reading it. Never use `--edit-last`.
