# StoUTextureSync

Painter texture edits become local source images associated with existing
Unreal Engine Texture assets.

## Language

**Source image**:
The final local image file used as an existing UE Texture's import source.
_Avoid_: UE asset, temporary export

**Texture binding**:
The correspondence between a Painter output, its final source-image path and
the expected UE Texture asset in one configured project.
_Avoid_: Material binding, automatic import

**Body part mapping**:
The assignment from a Painter UV Tile to a named body part in the exported
filename; each resulting image is an ordinary UE Texture source.
_Avoid_: UE UDIM import, virtual texture mapping

**Real-time sync**:
Automatic synchronization after drawing becomes idle, using the configured
export quality.
_Avoid_: Per-frame streaming, low-resolution preview

**Immediate sync**:
A manual request to process pending changes in enabled bindings while retaining
the real-time switch state.
_Avoid_: Full sync, automatic sync

**Full sync**:
A manual request to re-export and publish every enabled binding regardless of
content deduplication.
_Avoid_: Asset submission, first import

**Sync batch**:
The outputs for one synchronization request associated with one project and
configuration revision.
_Avoid_: Atomic transaction, UE frame

**File published**:
A complete source image has replaced its target file; this does not establish
that UE has finished importing or displaying it.
_Avoid_: UE complete, render complete

**Partial publication**:
Some images of a batch reached their final paths while others failed, leaving a
recoverable mixed result rather than a completed batch.
_Avoid_: Successful sync, normal refresh transition
