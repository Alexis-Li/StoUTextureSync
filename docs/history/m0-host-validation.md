# M0 host validation

Source: [M0 issue #2](https://github.com/Alexis-Li/StoUTextureSync/issues/2),
requirements in [#1](https://github.com/Alexis-Li/StoUTextureSync/issues/1).
Tools: [prototypes/m0](../../prototypes/m0/README.md). Measurements:
[m0-host-validation-data.json](m0-host-validation-data.json), a hand-made
summary of probe logs that were not kept; statements marked "observed" below
come from those logs and cannot be recomputed from the JSON.

State: Painter 11.0 and the official Unreal Editor 5.7.4 were exercised by
scripted probes on synthetic samples. `DEC-01` selects route A
([ADR 0002](../adr/0002-route-a-auto-reimport.md)). Validation on production
samples and the plug-in itself belongs to
[M1](https://github.com/Alexis-Li/StoUTextureSync/issues/3).

## Conclusions so far

1. **Route A closes the loop in the official editor.** Per-file atomic
   replacement (temporary file beside the target, then `os.replace`) of a source
   image that an existing Texture was imported from triggers Auto Reimport. In
   the 176 atomic reimports of the series in the JSON the source MD5 recorded by
   UE equalled the MD5 of the complete published file. The end-to-end runs add
   21 published-file reimports (5 x 1 file, 5 x 3 files, 1 manual sync) for
   which the JSON holds no MD5 field. Observed in the logs: every non-default texture
   setting (sRGB, compression, LOD group, mip generation) and the material
   instance's texture reference stayed, no asset was created, and Auto Reimport
   saved the reimported packages (no dirty package before a normal quit, one run
   per setting). The per-trial counts of these observations were not kept.
2. **Required UE configuration** (UE 5.7.4): `Monitor Content Directories` on;
   the source directory listed by absolute path *with a mount point* (an
   unmapped absolute path is refused with "not associated with mounted path");
   include wildcard `*.png`; `Auto Create Assets` and `Auto Delete Assets` off;
   `Prompt Before Action` off (default on, which waits for a click). A mount
   point is only used for creation, which stays off. Observed, not in the JSON.
3. **Background and CPU throttling did not change reimport latency in these
   runs.** With threshold 1.0 s, three 1K files finished 1.59–1.62 s (median,
   n=5 per series) after publication whether UE was visible in the background
   or minimized and with `Use Less CPU when in Background` on or off; UE in the
   foreground was not a series. Twelve 1K files: 2.83 s (n=5); twelve 2K files:
   3.13 s (n=3). Threshold 0.3 s: three files 0.97 s, one file 0.33 s (n=5
   each). The sources are near-solid PNGs in an empty project, so decode and
   compression cost of real textures is not represented. A slow direct writer
   (one file, written in two halves 2.5 s apart, n=2) was imported only after
   the write finished, with the complete file's MD5.
4. **End to end under five seconds for one to three changed 1K files on light
   content.** Scripted edits through the live loop prototype (500 ms event
   throttle, 1.0 s idle wait, threshold 1.0 s) reached the last UE reimport in
   3.03 s median for a one-Tile edit (1 file published) and 3.94 s for an
   all-Tile edit (3 files published of 12 exported; n=5 each, max 3.98 s).
   Roughly: event 0.5 s, idle wait 1.0 s, export 0.45–0.7 s, publication and
   scheduling about 0.05 s, UE 1.0–1.7 s. A change of all 12 files was not run
   end to end; the 12-file UE series (2.83 s) with the same SP side (about
   2.25 s) would exceed five seconds. 2K and heavy-stack end-to-end runs were
   not done, and viewport visibility after the reimport callback was not
   measured.
5. **Painter events identify Texture Set and Tile.** `TextureStateEvent`
   carries `stack_id` and 0-based `(u, v)` `tile_indices`, which use the same
   coordinates as the `uvTiles` export filter (the two were matched by
   coordinate, not in one batch); a fill restricted to one Tile reported only
   that Tile (one edit; one run per throttle setting). `ADD` and `REMOVE` arrive within
   milliseconds, `UPDATE` after the cache-key throttle (default 2000 ms,
   settable to the 500 ms minimum; the setting is global and must be restored
   on close). `REMOVE` (texture became empty, for example a black mask) is also
   a content change. Three edits produced no event at all: hiding a layer,
   inserting an empty paint layer and showing only the roughness channel of a
   layer. Whether they changed the export was not recorded, so event coverage of
   layer and channel visibility is unverified.
6. **Export behaviour.** `uvTiles: [[u, v]]` limits export to those Tiles
   (non-existent Tiles give an empty list; UDIM numbers are rejected). The
   project preset used here planned Albedo, ARM, Normal and Emissive PNGs;
   `list_project_textures()` predicts Emissive, which the export does not write
   when the channel is absent, so validation compares against the actual result
   and treats missing optional outputs separately. Repeated exports of
   unchanged content were byte-identical (one boolean in the JSON), so
   target-content dedup works; in the live loop it skipped every unchanged map.
   An unknown Texture Set raised before anything was written (observed). Chinese
   staging paths worked. Messages are localized; do not parse them. Not
   verified from this data: bit depth, colour space, Alpha, ARM channel order
   and DirectX normal convention (the JSON holds no pixel values). The four
   Tiles of each output carry identical content in the sample (3 distinct hashes
   over 12 files), so a Tile mix-up would not show in file content.
7. **Qt timers kept running during export.** A 10 ms Qt heartbeat saw median
   gaps of 32.6–61.7 ms and maximum gaps of 37.4 / 38.6 / 349.0 / 80.2 ms for
   the heavy stack at 1K full / 1K one Tile / 2K full / 2K one Tile, while
   light-case exports took 0.38–1.95 s (single runs; the five 1K repeats had a
   median of 0.52 s) and heavy-stack medians were 0.43–2.53 s (the 2K heavy
   maximum was 4.07 s). No
   light-case stall or idle baseline was kept, and event callbacks were not
   observed directly. Whether painting with a tablet is interrupted still needs
   a person.
8. **Project identity.** `project.get_uuid()` exists in Painter 11.0 and, like
   plug-in Metadata, survives save, reopen, save-as, save-as-copy, OS copy and
   a move to a Chinese path (one run each); a new project gets a new UUID.
   Copies therefore share identity: bindings need identity *plus* path conflict
   detection, as FR-06 already requires.
9. **UE offline.** One run per setting, two files, normal editor quit (no
   crash). With `Detect Changes On Startup` on, the files changed while UE was
   closed were reimported within the fixed 8 s observation window after
   startup (the time was not recorded). With it off the changes were missed
   until a full sync republished the files; UE reimports a republished file only
   when its content differs from what UE imported (changes are detected by file
   hash), which is the recovery FR-03.7 needs.
10. **A background reimport did not take the foreground from Painter.** Retest
    on an idle machine (2026-10-21) with Painter brought to the front before
    each trial and the foreground process sampled every 50 ms from publication
    to the end of the trial: with UE visible behind Painter or minimized,
    Painter stayed in front at every sample in all 30 trials (three 1K files
    n=10 and twelve 1K files n=5 per state); UE never appeared. In the
    UE-foreground control (n=15) UE stayed in front. Latency did not depend on
    the state: three files 1.49 / 1.56 / 1.61 s and twelve files 2.79 / 2.72 /
    2.78 s (median; visible background / minimized / foreground). During one
    end-to-end live run (114.6 s, 12 batches, 33 published files and 33 distinct
    reimports, threshold 1.0 s) the foreground never left Painter. Scripted
    edits only: a reimport during a pen stroke or with a Painter dialog open was
    not exercised.

## DEC-01

Route A (file publication + UE Auto Reimport) with the configuration in item 2;
POC-06 (directed reimport) not triggered because no core blocker was found.
The focus condition of PoC-01 is met for scripted runs (item 10). Starting
parameters: event throttle 500 ms during real-time sync, 1.0 s idle wait, UE
threshold 1.0 s (0.3 s was also measured; 0.5 s was not), 1K default.
Route A's limits stay as documented: the panel can report "file published",
not "UE complete"; UE association checks remain the user's.

## Limits of this evidence

- Synthetic samples and scripted edits only: no production clothing or body
  projects, no hand painting, no comparison with a manual SP export, no
  distinguishable Tile content.
- Focus: in the first runs UE was the foreground process before or after 4 of
  38 atomic trials while the user was working in other applications, which
  could not be attributed; the idle-machine retest (item 10) replaces them.
  The retest used scripted edits and one end-to-end run; focus during tablet
  painting is part of M1 AC-07.
- Not measured: resource baseline with both hosts open, viewport visibility,
  baking and material-parameter events, host failure injection, the 12-file or
  2K end-to-end time, the real-time switch during export or publication
  (it was switched off 0.2 s after an edit, during the idle wait). Write-lock,
  partial publication and stale-batch logic are covered only by project tests.
- Duplicate imports: the bench merges UE's double `OnAssetReimport` broadcast
  by `(asset, MD5)`, which would also merge a genuine repeat of identical
  content; no per-trial duplicate counts were kept.

## Baseline

- Tools: `prototypes/m0/` first committed as
  `6288e96385ff1a343ed1dd44ba1643d82dfbb571`. The `ue_publish_bench.py` used
  for the measurements predates the de-duplication fix that collapses UE's
  double `OnAssetReimport` broadcast; two series had counted it as a duplicate
  import and their summary values were corrected by hand from raw logs (identical
  MD5s 2 ms apart) that were not kept. Later changes to the prototype split the
  foreground field into `ue_foreground_before` and `ue_became_foreground` with a
  new rule, rename `online_unsaved` to `online_content`, require `preset_name`
  and add `ue_make_sources.py`. The focus retest used the `focus` and
  `serve --focus-log` plans of `ue_publish_bench.py` at
  `eafc3dd4024f362d94448b5a1e71196300578f96`.
- Hosts: Painter 11.0.0 (Python 3.11.6); Unreal Editor 5.7.4-51494982
  official build. Windows 11 Pro, 32 GB RAM, RTX 4060; both hosts open; the
  user was using other applications during the UE runs.
- Samples: Painter's bundled test meshes under neutral names — `body_udim`
  (one Texture Set, Tiles 1001/1002/1011/1012 mapped to Face/Body/Arm/Leg) and
  `clothing_2ts` (two Texture Sets); a 38-node stack of eight starter smart
  materials for the heavy case; an installed project preset (not part of the
  repository); UE source images were near-solid generated PNGs; `ue_make_sources.py`
  reproduces a comparable set.
- Run dates: 2026-10-19; focus retest 2026-10-21 on the same hosts and machine
  with nobody using it, Painter open with an empty session (the live run
  creates its own project).
