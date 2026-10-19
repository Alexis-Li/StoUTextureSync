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
- Follow docs/workflow.md for Issue/PR work: feature/<topic> or fix/<topic>
  branches with a PR to main, `Refs #<issue>` (never closing keywords), full
  base/head SHAs, AC and R- finding IDs, a published review of the exact head
  SHA before every merge, `--match-head-commit` merges, one status comment per
  issue and explicit closure. Small documentation and rule edits may be
  committed directly to main as docs/workflow.md defines. Merge, closure and
  comment deletion follow the user's authorization. Preserve unrelated work and
  never rewrite shared history.
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

## Verification and release

Run `python tools/validate_project.py` and `python -m compileall -q plugins`
for scaffold changes. Add meaningful project tests as functionality appears;
run focused tests first and broaden when affected behavior warrants it.
Changes to export, events, UI, reimport or lifecycle require evidence in the
declared hosts. Record unverified scenarios explicitly.

M1 owns the release packaging and version tooling. Painter archives use
StoUTextureSync-<version>-SP.zip and contain only installable runtime files;
verify an installation before publishing. Independent repository release tags
use v<version>. The collection updates its pin after the chosen product commit
is published and reviewed; it does not track the moving tip automatically.
