# M0 host validation

Source: [M0 issue #2](https://github.com/Alexis-Li/StoUTextureSync/issues/2),
requirements in [#1](https://github.com/Alexis-Li/StoUTextureSync/issues/1).
Tools: [prototypes/m0](../../prototypes/m0/README.md). Measurements:
[m0-host-validation-data.json](m0-host-validation-data.json).

State: **partial host evidence, not M0 closure**. Painter 11.0 and the official
Unreal Editor 5.7.4 were exercised by scripted probes on synthetic samples. Topia
Engine 5.7.4, production samples, hand painting with a tablet and the user's
judgement of interaction stalls are still open, and `DEC-01` is a
recommendation awaiting the user's decision.

## Conclusions so far

1. **Route A closes the loop in the official editor.** Per-file atomic
   replacement (temporary file beside the target, then `os.replace`) of a source
   image that an existing Texture was imported from triggers Auto Reimport. In
   225 reimports UE always recorded the MD5 of the complete published file, kept
   every non-default texture setting (sRGB, compression, LOD group, mip
   generation), kept the material instance's texture reference and created no
   assets. Auto Reimport saves the reimported packages itself, so restarting UE
   does not lose results.
2. **Required UE configuration** (UE 5.7.4): `Monitor Content Directories` on;
   the source directory listed by absolute path *with a mount point* (an
   unmapped absolute path is refused with "not associated with mounted path");
   include wildcard `*.png`; `Auto Create Assets` and `Auto Delete Assets` off;
   `Prompt Before Action` off (default on, which waits for a click). A mount
   point is only used for creation, which stays off.
3. **Background and CPU throttling do not change reimport latency.** With
   threshold 1.0 s, three 1K files finished 1.59–1.62 s (median) after
   publication whether UE was visible in the background or minimized and with
   `Use Less CPU when in Background` on or off. Twelve files: 2.83 s at 1K,
   3.13 s at 2K. Threshold 0.3 s: three files 0.97 s, one file 0.33 s.
4. **End to end under five seconds on light content.** Scripted edits through
   the live loop prototype (500 ms event throttle, 1.0 s idle wait, threshold
   1.0 s) reached the last UE reimport in 3.03 s median for a one-Tile edit and
   3.94 s for an all-Tile edit (n=5 each, max 3.98 s). Roughly: event 0.5 s,
   idle wait 1.0 s, export 0.45–0.7 s, publication < 0.03 s, UE 1.0–1.7 s.
   Viewport visibility after the reimport callback was not measured.
5. **Painter events identify Texture Set and Tile.** `TextureStateEvent`
   carries `stack_id` and 0-based `(u, v)` `tile_indices`, which equal the
   `uvTiles` export filter coordinates; a fill restricted to one Tile reports
   only that Tile. `ADD` and `REMOVE` arrive within milliseconds, `UPDATE`
   after the cache-key throttle (default 2000 ms, settable to the 500 ms
   minimum; the setting is global and must be restored on close). `REMOVE`
   (texture became empty, for example a black mask) is also a content change.
   Hiding an already-empty layer produced no event.
6. **Export behaviour.** `uvTiles: [[u, v]]` limits export to those Tiles
   (non-existent Tiles give an empty list; UDIM numbers are rejected). The
   project preset loads by name; its outputs are Albedo (RGB base colour,
   A opacity), ARM (R AO_Mixed, G roughness, B metallic), Normal (DirectX) and
   Emissive, 8-bit PNG. `list_project_textures()` predicts outputs that the
   export does not write when a channel is absent (Emissive), so validation
   compares against the actual result and treats missing optional outputs
   separately. Repeated exports of unchanged content are byte-identical, so
   target-content dedup works; in the live loop it skipped every unchanged map.
   An unknown Texture Set raises before anything is written. Chinese staging and
   project paths worked. Messages are localized; do not parse them.
7. **Export does not freeze Painter's event loop.** A 10 ms Qt heartbeat saw
   maximum gaps of 30–60 ms while exports took 0.45–2.5 s (one 349 ms gap in a
   2K run), so Qt events are processed during export. Whether painting with a
   tablet is interrupted still needs a person.
8. **Project identity.** `project.get_uuid()` exists in Painter 11.0 and, like
   plug-in Metadata, survives save, reopen, save-as, save-as-copy, OS copy and
   a move to a Chinese path; a new project gets a new UUID. Copies therefore
   share identity: bindings need identity *plus* path conflict detection, as
   FR-06 already requires.
9. **UE offline.** With `Detect Changes On Startup` on, files changed while UE
   was closed were reimported within seconds of startup. With it off they were
   missed until a full sync republished the files; UE reimports a republished
   file only when its content differs from what UE imported (changes are
   detected by file hash), which is the recovery FR-03.7 needs.

## Recommendation for DEC-01

Route A (file publication + UE Auto Reimport) with the configuration in item 2;
POC-06 (directed reimport) not triggered because no core blocker was found.
Starting parameters: event throttle 500 ms during real-time sync, 1.0 s idle
wait, UE threshold 1.0 s (0.3–0.5 s if the budget needs it), 1K default.
Route A's limits stay as documented: the panel can report "file published",
not "UE complete"; UE association checks remain the user's.

## Open items before closure

- Topia Engine 5.7.4: not run; the user excluded that installation from
  automated testing. Needs the user or a separate test copy.
- Production clothing and body samples, real layer stacks, 9–18 outputs; SP
  manual export comparison with colour-block channels; preset reselection and
  missing-resource cases.
- Hand painting with a tablet: focus, stroke continuity, stall acceptance.
- Focus: in 4 of 38 trials UE became foreground after a reimport while the user
  was working in other applications; not attributable from these runs and must
  be re-tested with the machine idle.
- Resource baseline (RAM, commit, paging, VRAM) with both hosts open; viewport
  visibility time; baking and material-parameter events; SP exit during
  publication, disk full, no permission and cache loss in the hosts.
  Write-lock, partial publication and stale-batch logic are covered only by
  project tests.

## Baseline

- Tools: the prototype commit named in the PR that added this record;
  `ue_publish_bench.py` deduplication was corrected afterwards to collapse
  UE's double `OnAssetReimport` broadcast (two series counted it as a
  duplicate import; raw logs show identical MD5s 2 ms apart).
- Hosts: Painter 11.0.0 (Python 3.11.6); Unreal Editor 5.7.4-51494982
  official build. Windows 11 Pro, 32 GB RAM, RTX 4060; both hosts open; the
  user was using other applications during the UE runs.
- Samples: Painter's bundled test meshes under neutral names — `body_udim`
  (one Texture Set, Tiles 1001/1002/1011/1012 mapped to Face/Body/Arm/Leg) and
  `clothing_2ts` (two Texture Sets); a 38-node stack of eight starter smart
  materials for the heavy case; the installed project preset; UE source images
  generated as solid PNGs for the reimport bench.
- Run date: 2026-10-19.
