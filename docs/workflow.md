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
| Issue progress, reviews without a PR, host acceptance results | Comments on the issue |
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
   choice) is confirmed by the user, written into the milestone's status
   comment, an ADR and issue #1 before dependent work starts.
5. **Let decisions change.** Requirements and decisions can become outdated as
   development shows more. A changed decision is agreed with the user and then
   rewritten in place wherever it lives (#1, milestone, ADR, issue comments);
   the old wording is replaced, not kept beside the new one.

M0 is a validation milestone: its deliverable is evidence and the `DEC-01`
route decision, not a releasable plug-in. Prototype code that must be kept
goes through a PR and stays outside `plugins/` unless it becomes product code;
one-off harnesses stay in scratch.

## 2. Delivery and review

- **Small direct changes.** Wording, typo, link and formatting fixes, and
  small edits to rules, the workflow, templates or `CHANGELOG.md`, are
  committed to the branch currently being worked on, without a separate branch
  or PR, when they change no code, tests, tools, CI, packaging, version, ADR or
  product decision. They reach `main` with that branch; only when no
  development branch is in progress is the current branch `main` itself. Run
  `tools/validate_project.py` first, push a normal commit (never rewrite shared
  history), and use a PR whenever the size or impact is unclear.
- **Branch.** Everything else goes through a PR: development tasks, code
  changes and significant changes. Work on `feature/<topic>` for new
  capability, tooling or documentation, or `fix/<topic>` for corrections,
  branched from `main`, and open a PR back to `main`. Use a short kebab-case
  topic and one PR per reviewable piece; a milestone usually has several.
  GitHub deletes the head branch when the PR is merged.
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
- **Merge.** Merge only after the review of the head SHA is published; the
  user's merge authorization does not replace it. Merge with
  `gh pr merge --match-head-commit <reviewed-head>`. Merge follows the user's
  authorization.
- **Update the issue.** Comment on the issue: PR and commits, the resulting
  `main` SHA, AC covered, state reached (implemented, reviewed, merged,
  host-verified) and what remains.

Ordinary checks (`tools/validate_project.py`, compileall, unit tests, CI) are
local evidence only. They never establish host compatibility.

## 3. Host acceptance

Events, export, UI, file publication timing, UE reimport and lifecycle need
evidence in the declared hosts: Painter 11.0 and the official Unreal Editor
5.7.4. Record the results as an acceptance comment on the milestone (or child)
issue. It states:

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

Close a milestone issue explicitly when the conditions below hold. Closing is
also a tidy: the status comment (see Archival) is rewritten to its final state,
`已关闭` with the final review, the integration SHA on `main` and the host
acceptance results, and the other comments are deleted. Close when:

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

Issue comments are working material, not a permanent record: progress, review
and acceptance comments are added as work goes on. A new comment carries new
facts only; a mistake or outdated statement in one's own comment is corrected
by editing that comment, never by a follow-up correction. When the user asks to
tidy an issue's comments, and when an issue closes, merge what still matters
into one status comment (format below) or its maintained owner (ADR, #1,
README, `docs/history/`) and delete the rest; outdated content is simply
dropped. Git does not keep remote comments, so anything a future decision or
regression check needs must reach a repository owner first. Remove task-owned
scratch after the evidence it held is preserved.

## Issue status comment

Tidying an issue's comments on the user's request, or closing it, produces one
status comment in this format; on an open issue new comments then accumulate
again until the next tidy. Like
every comment, it states the current fact: no change logs, "updated per
request" notes or labels for what was removed.

```markdown
<!-- issue-status:v1 issue=2 -->
## 状态

- 状态：进行中 / 待审核 / 待验证 / 受阻 / 可关闭 / 已关闭
- 基线：<main SHA or package version>
- 更新日期：YYYY-MM-DD

### 简介
### 相关提交
### 已完成
### 未完成
### 开发边界
### 决定
### 宿主验收
### 下一步
```

- 简介: what the issue delivers and where it stands, in a few sentences.
- 相关提交: PRs and commits with full SHAs and a one-line summary each.
- 已完成 / 未完成: by AC, PoC or requirement ID, with evidence links.
- 开发边界: what is in and out of scope, and items moved to another issue.
- 决定: decisions in force, linking their ADR or #1 section.
- 宿主验收: the content listed in Host acceptance, or `无`.
- 下一步: concrete next actions and who takes them.

Write `无` in an empty section, never leave placeholders. Use
repository-relative paths or placeholders such as `<Painter11>` or `<scratch>`,
never drive letters or user directories. Before editing, re-read the issue and
all its comments (`gh issue view <n> --comments`). Create the comment with
`gh issue comment <n> --body-file <draft.md>`; edit it by its numeric ID through
`gh api --method PATCH repos/{owner}/{repo}/issues/comments/<id> --input
<payload.json>`. Never use `--edit-last`.
