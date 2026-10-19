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
| `png_tools.py` | outside | Dependency-free PNG writer for the bench |

## Reproducing

Use scratch directories outside the repository (`<scratch>` below). The probes
use the meshes shipped in Painter's own `automated_tests` resources under
neutral names (`body_udim`: one Texture Set with four UV Tiles; `clothing_2ts`:
two Texture Sets) and an export preset installed in Painter, selected by name.

```bash
"<Painter11>/Adobe Substance 3D Painter.exe" --enable-remote-scripting
python prototypes/m0/run_painter_scenario.py identity --scratch <scratch>/painter
python prototypes/m0/run_painter_scenario.py events --scratch <scratch>/painter --kw "{\"throttle_ms\": 500}" --log-name events_500
python prototypes/m0/run_painter_scenario.py export --scratch <scratch>/painter
python prototypes/m0/run_painter_scenario.py heavy --scratch <scratch>/painter
```

The `export` and `heavy` scenarios take the preset name as `preset_name`
(`--kw "{\"preset_name\": \"<name>\"}"`).

Route A in the official Unreal Editor:

```bash
python prototypes/m0/ue_make_project.py <scratch>/ue_project <scratch>/ue_src
python prototypes/m0/ue_publish_bench.py --plan default --source <scratch>/ue_src --log <scratch>/ue_probe.jsonl --commands <scratch>/ue_cmd --out <scratch>/bench.jsonl --uproject <scratch>/ue_project/StoUProbe.uproject --ue-exe "<UE5.7>/Engine/Binaries/Win64/UnrealEditor.exe"
```

`<scratch>/ue_src` must first hold the final-named source images; the first
editor start imports them into `/Game/StoU`. The bench restarts the editor for
each configuration and never takes keyboard focus except during editor start.

End to end (UE probe running, Painter with remote scripting): write a live
configuration from `example_live_config.json` whose `target_dir` is the
monitored directory, then

```bash
python prototypes/m0/run_painter_scenario.py live --scratch <scratch>/painter --kw "{\"config\": \"<scratch>/live.json\"}"
```

For manual painting tests, run `painter_live_loop.start("<config>")` from
Painter's Python console and paint; each batch is a JSON line in the configured log.
