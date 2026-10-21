# M0 validation prototypes

Evidence tools for [M0](https://github.com/Alexis-Li/StoUTextureSync/issues/2).
They are not the product plug-in: nothing here is installed by Painter, and
`plugins/stou_texture_sync/` is unchanged. Results and conclusions are recorded
in [docs/history/m0-host-validation.md](../../docs/history/m0-host-validation.md).

| File | Runs in | Purpose |
| --- | --- | --- |
| `stou_m0/` | any Python 3.10+ | Host-independent core: preset-template naming and body-part mapping, idle-debounced serial scheduler with generations, per-file atomic publication with target-content dedup, OS write lock |
| `tests/` | any Python 3.10+ | `python -m unittest discover -s prototypes/m0/tests` |
| `painter_remote.py` | outside | Sends Python to Painter started with `--enable-remote-scripting` |
| `run_painter_scenario.py` | outside | Starts a `painter_probe` scenario and prints its JSON lines |
| `painter_probe.py` | Painter 11.0 | Scenarios `identity`, `events`, `export`, `heavy`, `live` |
| `painter_live_loop.py` | Painter 11.0 | Route A loop: events, scheduler, filtered export, naming, publication |
| `example_live_config.json` | - | Live loop configuration template |
| `ue_make_project.py` | outside | Disposable UE 5.7 content-only project with route A Auto Reimport settings |
| `ue_probe.py` | Unreal Editor 5.7 | First import, settings and material references; logs every reimport |
| `ue_publish_bench.py` | outside | Publishes source images and times UE reimport per window/CPU setting |
| `ue_make_sources.py` | outside | Writes the 12 near-solid source images the UE probes start from |
| `png_tools.py` | outside | Dependency-free PNG writer for the bench |

## Reproducing

Use scratch directories outside the repository (`<scratch>` below). The probes
use the meshes shipped in Painter's own `automated_tests` resources under
neutral names (`body_udim`: one Texture Set with four UV Tiles; `clothing_2ts`:
two Texture Sets) and an export preset installed in Painter, selected by name.
The scenarios `export` and `heavy` need `preset_name`: any installed project
export preset whose outputs are Albedo, ARM and Normal PNGs named like
`T_$textureSet_Albedo(_$udim)`; the measured runs used a private preset that
is not part of this repository, so other presets give other file counts.

```bash
"<Painter11>/Adobe Substance 3D Painter.exe" --enable-remote-scripting
python prototypes/m0/run_painter_scenario.py identity --scratch <scratch>/painter
python prototypes/m0/run_painter_scenario.py events --scratch <scratch>/painter --kw "{\"throttle_ms\": 500}" --log-name events_500
python prototypes/m0/run_painter_scenario.py export --scratch <scratch>/painter
python prototypes/m0/run_painter_scenario.py heavy --scratch <scratch>/painter
```

Pass the preset as `--kw "{\"preset_name\": \"<name>\"}"` for `export` and
`heavy`; they stop with a lookup error when it is missing.

Route A in the official Unreal Editor:

```bash
python prototypes/m0/ue_make_sources.py <scratch>/ue_src
python prototypes/m0/ue_make_project.py <scratch>/ue_project <scratch>/ue_src
python prototypes/m0/ue_publish_bench.py --plan default --source <scratch>/ue_src --log <scratch>/ue_probe.jsonl --commands <scratch>/ue_cmd --out <scratch>/bench.jsonl --uproject <scratch>/ue_project/StoUProbe.uproject --ue-exe "<UE5.7>/Engine/Binaries/Win64/UnrealEditor.exe"
```

The first editor start imports the images of `<scratch>/ue_src` into `/Game/StoU`.
The bench starts `ue_probe.py` itself, restarts the editor for each
configuration and, except in the `focus` plan, never takes keyboard focus
outside editor start.
`--plan focus` (Painter running) brings Painter to the front before each trial,
publishes with UE visible behind it, minimized, or in front as a control, and
samples the foreground process every 50 ms; it takes focus by design, so run it
on an idle machine. `--plan serve --focus-log <file>` records every foreground
change while an end-to-end run is active.
`--plan offline` runs the UE-closed scenario (changes while the editor is
closed, with Detect Changes On Startup on and off) and `--plan quick` a short
smoke series.

End to end: keep one configured editor running with `ue_publish_bench.py --plan
serve` (same arguments as above; it runs until `<commands>/stop_serve` exists),
start Painter with remote scripting, write a live configuration from
`example_live_config.json` whose `target_dir` is the monitored directory
(`<scratch>/ue_src`), then

```bash
python prototypes/m0/run_painter_scenario.py live --scratch <scratch>/painter --kw "{\"config\": \"<scratch>/live.json\"}"
```

For manual painting tests, run `painter_live_loop.start("<config>")` from
Painter's Python console and paint; each batch is a JSON line in the configured log.

## Evidence summary

`docs/history/m0-host-validation-data.json` is a hand-made summary of the probe
logs of the measured runs. The raw logs were not kept and no script rebuilds
the summary from new logs, so a new run is compared with it by hand.
