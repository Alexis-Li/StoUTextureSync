"""M0 Unreal Editor probe: record Auto Reimport results for route A.

Run inside Unreal Editor 5.7 (official build) with the Python plug-in:
    UnrealEditor.exe <StoUProbe.uproject> -ExecCmds="py <this file>"
(-ExecutePythonScript would close the editor when the script returns)
with environment variables STOU_PROBE_SOURCE (monitored source image directory),
STOU_PROBE_LOG (JSON lines) and STOU_PROBE_COMMANDS (command directory).

First run imports every *.png in the source directory into /Game/StoU, sets
non-default texture settings, creates a material and material instance that
reference the textures, and saves them. Every run then logs OnAssetReimport and
OnAssetPostImport with a wall-clock timestamp, the texture settings, the
recorded source MD5 and dirty-package state. JSON command files in the command
directory adjust settings at runtime ({"cmd": "set", ...}, "save", "report",
"quit").
"""

import json
import os
import time
import warnings

import unreal

warnings.filterwarnings("ignore", category=DeprecationWarning)

SOURCE = os.environ["STOU_PROBE_SOURCE"]
LOG = os.environ["STOU_PROBE_LOG"]
COMMANDS = os.environ["STOU_PROBE_COMMANDS"]
DEST = "/Game/StoU"

_handles = {}


def log(kind, **data):
    record = {"wall": round(time.time(), 4), "kind": kind}
    record.update(data)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def texture_info(texture):
    info = {"path": texture.get_path_name()}
    for prop in ("srgb", "compression_settings", "lod_group", "mip_gen_settings", "never_stream",
                 "filter", "flip_green_channel"):
        try:
            info[prop] = str(texture.get_editor_property(prop))
        except Exception:  # noqa: BLE001
            pass
    try:
        info["size"] = [texture.blueprint_get_size_x(), texture.blueprint_get_size_y()]
    except Exception:  # noqa: BLE001
        pass
    try:
        data = texture.get_editor_property("asset_import_data")
        info["source"] = os.path.basename(data.get_first_filename())
    except Exception as exc:  # noqa: BLE001
        info["source_error"] = repr(exc)
    try:
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        asset = registry.get_asset_by_object_path(texture.get_path_name())
        tag = asset.get_tag_value("AssetImportData")
        if tag:
            parsed = json.loads(tag)
            info["registry_md5"] = [entry.get("FileMD5") for entry in parsed]
    except Exception as exc:  # noqa: BLE001
        info["registry_error"] = repr(exc)
    return info


def dirty_packages():
    try:
        return [p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]
    except Exception as exc:  # noqa: BLE001
        return [repr(exc)]


def setup_assets():
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    existing = unreal.EditorAssetLibrary.list_assets(DEST, recursive=True)
    if existing:
        log("setup_skipped", assets=len(existing))
        return
    tasks = []
    for name in sorted(os.listdir(SOURCE)):
        if not name.lower().endswith(".png"):
            continue
        task = unreal.AssetImportTask()
        task.filename = os.path.join(SOURCE, name)
        task.destination_path = DEST
        task.automated = True
        task.save = False
        task.replace_existing = True
        tasks.append(task)
    tools.import_asset_tasks(tasks)
    textures = []
    for task in tasks:
        for path in task.imported_object_paths:
            asset = unreal.load_asset(path)
            if isinstance(asset, unreal.Texture2D):
                textures.append(asset)
                name = asset.get_name()
                # Non-default settings that a reimport must keep.
                if "_ARM" in name:
                    asset.set_editor_property("srgb", False)
                    asset.set_editor_property("compression_settings",
                                              unreal.TextureCompressionSettings.TC_MASKS)
                elif "_Normal" in name:
                    asset.set_editor_property("compression_settings",
                                              unreal.TextureCompressionSettings.TC_NORMALMAP)
                    asset.set_editor_property("srgb", False)
                else:
                    asset.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_CHARACTER)
                    asset.set_editor_property("mip_gen_settings",
                                              unreal.TextureMipGenSettings.TMGS_SHARPEN4)
    material = tools.create_asset("M_StoUProbe", DEST, unreal.Material, unreal.MaterialFactoryNew())
    mel = unreal.MaterialEditingLibrary
    sample = mel.create_material_expression(material, unreal.MaterialExpressionTextureSampleParameter2D,
                                            -400, 0)
    sample.set_editor_property("parameter_name", "Albedo")
    albedo = next((t for t in textures if "_Albedo" in t.get_name()), textures[0] if textures else None)
    if albedo:
        sample.set_editor_property("texture", albedo)
    mel.connect_material_property(sample, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    mel.recompile_material(material)
    instance = tools.create_asset("MI_StoUProbe", DEST, unreal.MaterialInstanceConstant,
                                  unreal.MaterialInstanceConstantFactoryNew())
    mel.set_material_instance_parent(instance, material)
    second = next((t for t in textures if t is not albedo), albedo)
    if second:
        mel.set_material_instance_texture_parameter_value(instance, "Albedo", second)
    unreal.EditorAssetLibrary.save_directory(DEST, only_if_is_dirty=False, recursive=True)
    log("setup_done", textures=[texture_info(t) for t in textures])


def material_refs():
    instance = unreal.load_asset(DEST + "/MI_StoUProbe")
    if not instance:
        return None
    value = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(instance, "Albedo")
    return value.get_path_name() if value else None


def on_reimport(obj):
    data = {"asset": obj.get_name(), "class": obj.get_class().get_name()}
    if isinstance(obj, unreal.Texture2D):
        data.update(texture_info(obj))
    data["dirty"] = dirty_packages()
    log("reimport", **data)


def on_post_import(factory, obj):
    log("post_import", asset=obj.get_name() if obj else None,
        factory=factory.get_class().get_name() if factory else None)


def report():
    textures = [unreal.load_asset(p) for p in unreal.EditorAssetLibrary.list_assets(DEST, recursive=True)]
    log("report", textures=[texture_info(t) for t in textures if isinstance(t, unreal.Texture2D)],
        material_instance_albedo=material_refs(), dirty=dirty_packages(),
        assets=sorted(t.get_name() for t in textures if t))


def settings_objects():
    loading = unreal.find_object(None, "/Script/UnrealEd.Default__EditorLoadingSavingSettings")
    perf = unreal.find_object(None, "/Script/UnrealEd.Default__EditorPerformanceSettings")
    return loading, perf


def apply_set(values):
    loading, perf = settings_objects()
    applied = {}
    for key, value in values.items():
        target = perf if key == "throttle_cpu_when_not_foreground" else loading
        try:
            target.set_editor_property(key, value)
            applied[key] = str(target.get_editor_property(key))
        except Exception as exc:  # noqa: BLE001
            applied[key] = "error: " + repr(exc)
    log("settings", applied=applied)


_last_poll = [0.0]


def tick(delta):
    now = time.time()
    if now - _last_poll[0] < 0.1:
        return
    _last_poll[0] = now
    try:
        names = sorted(os.listdir(COMMANDS))
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(COMMANDS, name)
        try:
            with open(path, encoding="utf-8") as handle:
                command = json.load(handle)
        except (OSError, ValueError):
            continue
        os.remove(path)
        cmd = command.get("cmd")
        log("command", cmd=cmd, id=command.get("id"))
        if cmd == "set":
            apply_set(command.get("values", {}))
        elif cmd == "save":
            unreal.EditorAssetLibrary.save_directory(DEST, only_if_is_dirty=True, recursive=True)
            log("saved", dirty=dirty_packages())
        elif cmd == "report":
            report()
        elif cmd == "quit":
            log("quitting")
            shutdown()
            unreal.SystemLibrary.quit_editor()


def shutdown():
    subsystem = unreal.get_editor_subsystem(unreal.ImportSubsystem)
    if "reimport" in _handles:
        subsystem.on_asset_reimport.remove_callable(on_reimport)
        subsystem.on_asset_post_import.remove_callable(on_post_import)
    if "tick" in _handles:
        unreal.unregister_slate_post_tick_callback(_handles.pop("tick"))
    _handles.clear()


def main():
    os.makedirs(COMMANDS, exist_ok=True)
    log("start", engine=unreal.SystemLibrary.get_engine_version(),
        project=unreal.Paths.get_project_file_path())
    setup_assets()
    subsystem = unreal.get_editor_subsystem(unreal.ImportSubsystem)
    subsystem.on_asset_reimport.add_callable(on_reimport)
    subsystem.on_asset_post_import.add_callable(on_post_import)
    _handles["reimport"] = True
    _handles["tick"] = unreal.register_slate_post_tick_callback(tick)
    loading, perf = settings_objects()
    settings = {}
    for key in ("monitor_content_directories", "auto_reimport_threshold", "auto_create_assets",
                "auto_delete_assets", "detect_changes_on_startup", "prompt_before_auto_importing"):
        try:
            settings[key] = str(loading.get_editor_property(key))
        except Exception as exc:  # noqa: BLE001
            settings[key] = repr(exc)
    try:
        settings["throttle_cpu_when_not_foreground"] = str(
            perf.get_editor_property("throttle_cpu_when_not_foreground"))
    except Exception as exc:  # noqa: BLE001
        settings["throttle_cpu_when_not_foreground"] = repr(exc)
    report()
    log("ready", settings=settings)


main()
