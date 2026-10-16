# StoUTextureSync

[中文说明](README_CN.md)

StoUTextureSync is a Substance 3D Painter plug-in project for updating existing
Unreal Engine Texture assets from locally exported images.

## Availability

The project is in prototyping. Version `0.0.0` contains empty Painter entry
points; synchronization and the configuration panel are not implemented.
There is no installable release yet.

Target environment: Windows, Painter 11.0, Unreal Editor 5.7.4 and Topia Engine
5.7.4. Compatibility and performance remain unverified in those hosts.

## Planned workflow

1. Save the Painter project and select an installed export preset.
2. Configure the source-image directory, UE project and intended UE content
   folder; preview Texture Set bindings, filenames and output dimensions.
3. Manually import the images into UE and connect the existing materials.
4. Configure UE Auto Reimport for those source images, then check a manual sync.
5. Enable real-time sync to export after drawing becomes idle. Immediate sync
   handles pending changes; full sync republishes all enabled bindings.

Output defaults to 1K, with explicit 2K exceptions per Texture Set or body part.
Painter body Tiles map to named parts exported as ordinary images for separate
UE Texture assets. Clothing uses independent Texture Set naming. Automatic and
manual sync use the same export quality and the selected Painter preset.

The design targets updates within five seconds after the last effective edit;
actual delay and drawing interruptions require prototype measurement. Images
may update one at a time. Source-path changes require checking UE associations.
Reopening or switching projects restores configuration with real-time sync off.

## Development

Clone this repository on its own or initialize its submodule in the collection.
Use a branch in this repository for changes; it owns its Issues, PRs and releases.
No parent checkout is required. Read [AGENTS.md](AGENTS.md) before editing.

With Python 3.10 or newer, run the scaffold checks:

```powershell
python tools/validate_project.py
python -m compileall -q plugins
```

These checks do not establish host compatibility. Implementation and host tests
belong to the [M0 validation](https://github.com/Alexis-Li/StoUTextureSync/issues/2)
and [M1 delivery](https://github.com/Alexis-Li/StoUTextureSync/issues/3) tasks.
See the [product requirements](https://github.com/Alexis-Li/StoUTextureSync/issues/1),
[domain terms](CONTEXT.md) and [integration decision](docs/adr/0001-file-based-sync.md).

Licensed under [GPL-3.0-only](LICENSE).
