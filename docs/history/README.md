# Project history

This directory keeps knowledge that stays useful after work is complete and
cannot be recovered from the code, tests, maintained documents or tracker.
See the [development workflow](../workflow.md) for when records are written.

## Requirement drafts

The requirement draft and technical notes as last edited before the product
moved into this repository. They are the original source record and are not
edited; current requirements are owned as described in the
[workflow](../workflow.md#5-keeping-the-requirements-current).

- [requirements.md](requirements.md): product scope, functional requirements,
  validation plan and milestones.
- [optimization-notes.md](optimization-notes.md): technical basis and
  verification limits behind the requirements.

## Topic records

- [m0-host-validation.md](m0-host-validation.md): M0 (issue #2) host
  evidence for Painter 11.0 and Unreal Editor 5.7.4, with its measurements in
  [m0-host-validation-data.json](m0-host-validation-data.json).

## What belongs here

Add or extend a record only when a future decision or regression check needs
it and no maintained owner already answers it, for example:

- the rationale and evidence behind a route decision such as `DEC-01`;
- host measurements, calibration inputs and owner visual acceptance;
- hard-to-reproduce failures and their confirmed cause;
- JSON archives of issue comments saved before those comments are deleted.

Routine PR checks, repeated successful runs, debugging rounds and publication
receipts stay in the PR or issue.

Organize records by capability or milestone with purpose-based names such as
`m0-host-validation.md` or `reimport-route.md`, and name the source issues
inside. Put the conclusion, accepted scope and limits first, then the baseline
(full SHA or version), host builds, sample identity, evidence source and
reproduction entry. Keep implementation, review, integration and host
acceptance distinct, and tie each conclusion to its baseline; a later run
updates the record without presenting old evidence as new.

Small text evidence (CSV or JSON measurements) may sit beside its record, with
its purpose stated there. Do not commit `.spp` projects, private textures,
proprietary presets, screenshots of private assets or machine-specific paths.
List each record under Topic records.
