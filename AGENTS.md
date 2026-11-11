# StoUTextureSync development rules

This independent repository owns the product, including future UE execution
components. It can be cloned alone or mounted as a registered submodule in a
collection. Run Git and `gh` here for product work; the parent owns only its
registration and pinned commit. Do not change sibling projects.

## Contracts and layout

- Read CONTEXT.md for terminology and docs/adr/ for architecture decisions.
  Undelivered requirements live in Issue #1; milestone scope and acceptance in
  its milestone Issues (M0 #2, M1 #3). docs/workflow.md defines the lifecycle.
- The project is a 0.0.0 scaffold. Do not describe planned functionality as
  implemented or claim host verification from ordinary Python checks.
- Painter runtime lives in plugins/stou_texture_sync/. Use lowercase snake_case
  Python modules, one consistent __version__, and start_plugin()/close_plugin().
  Add modules/ or startup/ only when used. Keep tests and tools outside runtime.
- Use Painter 11.0's Python and PySide6. Call Painter APIs on the main thread;
  callbacks only record changes and schedule work. Pure file work may use workers.
  Close reverses event, timer, thread and UI registration; reload must be safe.
- Keep scheduling, mapping, configuration and publishing testable without the
  host. Validate paths, formats, lifecycle and stale task results before writes.
- Modify user projects only as the feature documents. Metadata uses a
  plug-in-owned key. Write only to configured or owned paths; never delete
  user-created source images. Export through Painter's supported API.
- Keep code, tests, dependencies, packaging, version commands and CI usable
  without the collection repository. A future UE component belongs to this
  product and shares the product version; document separate host installation.

## Documentation and delivery

- README.md and README_CN.md are aligned current manuals. Write every document,
  issue and comment as the current state and edit it in place; omit
  development diaries, change notes and labels for what was removed.
- Keep vocabulary in CONTEXT.md, durable decisions in docs/adr/, release changes
  in CHANGELOG.md, and requirements/unresolved work in Issues. When a milestone
  closes, move its delivered requirements to their maintained owners and trim
  Issue #1 as docs/workflow.md describes; do not keep another requirement
  baseline beside the Issues. docs/history/ keeps the original drafts read-only
  and the selected completed decisions and evidence under its own rules.
- Follow docs/workflow.md for Issue/PR work. Cut `feature/<topic>` or
  `fix/<topic>` branches from the integration branch in progress (`main` when
  none is), with a PR back to it. Use `Refs #<issue>` and avoid closing
  keywords. Record full base/head SHAs and AC and R- finding IDs. Require
  independent review of the exact head SHA before every merge and use
  `--match-head-commit`. Implementers deliver as `待审核`.
  A PR branch holds only its PR's change. Small documentation and rule edits
  go directly on the integration branch under the workflow's delivery rules.
  Every issue comment ends with a `当前状态` footer for the whole issue.
  Tidy comments on the user's request and at explicit closure. Commit, push,
  PR creation, merge, tracker updates, closure and deletion follow the task's
  authorization. Preserve unrelated work and never rewrite shared history.
- Use gh from this clone. Re-read an issue and its comments before editing,
  keep decisions in force, rewrite outdated ones in place once the user agrees,
  and use UTF-8 body files for multiline text.
  Never publish machine-specific paths, credentials or private asset contents.
  Report the state reached precisely: implemented, reviewed, merged,
  host-verified or released.
- Keep scratch under the actual Git root's parent .tmp/; fall back to ignored
  .tmp/ inside this repository. Remove task-owned scratch when no longer needed.
- Do not check in caches, host build output, archives, .spp projects or proprietary
  presets. Keep the GPL license and assess any third-party reuse separately.

## Verification

Before implementing, and when acting on review findings, follow the design,
fix and verification-scope rules in docs/workflow.md.

Commands run from this repository's root. Run focused checks first and broaden
when affected behavior warrants it.

| Scope | Command |
| --- | --- |
| Documentation, rules and project files | `python tools/validate_project.py` |
| Painter scaffold and runtime | `python tools/validate_project.py` and `python -m compileall -q plugins` |
| M0 prototype logic | `python -m unittest discover -s prototypes/m0/tests` |
| Tools, versions, packaging, CI or repository layout | `python tools/validate_project.py` and `python -m unittest discover -s tests` |

Add meaningful project tests as functionality appears. Changes to export,
events, UI, reimport or lifecycle require evidence in Painter 11.0 and the
official Unreal Editor 5.7.4 for the affected behavior. Record unverified
scenarios explicitly and plan unattended acceptance during design, with a
reason for each check that needs manual acceptance.

## Versions and changelog

Only the release change sets the product version. Follow
[Release](docs/workflow.md#release) for this project's version and packaging
commands, installation acceptance and publication.

User-visible changes get an entry under `Unreleased` in CHANGELOG.md, with
their issue number. Use the panel's terms, describe the effect on users and
state any action needed when upgrading. Group entries under `Upgrade notes`,
`Added`, `Changed`, `Fixed` and `Removed` in that order, omitting empty groups.
Keep review findings and routine verification evidence with their issue or PR.
