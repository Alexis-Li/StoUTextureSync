"""Create a disposable content-only UE 5.7 project configured for Auto Reimport probes.

Usage: python ue_make_project.py <project_dir> <source_image_dir> [--threshold 1.0]

The source image directory is monitored by absolute path and must be mapped to a
mount point (UE 5.7 refuses to watch an unmapped absolute directory),
with only *.png included; Auto Create / Auto Delete and the confirmation prompt
are disabled, matching the route A setup in Issue #1.
"""

from __future__ import annotations

import argparse
import json
import os


def write_project(project_dir: str, source_dir: str, threshold: float = 1.0,
                  detect_on_startup: bool = True, throttle_background: bool = True,
                  mount: str = "/Game/StoU/") -> str:
    os.makedirs(os.path.join(project_dir, "Config"), exist_ok=True)
    os.makedirs(os.path.join(project_dir, "Content"), exist_ok=True)
    os.makedirs(source_dir, exist_ok=True)
    uproject = os.path.join(project_dir, "StoUProbe.uproject")
    with open(uproject, "w", encoding="utf-8") as handle:
        json.dump({"FileVersion": 3, "EngineAssociation": "5.7", "Category": "", "Description": "",
                   "Plugins": [{"Name": "PythonScriptPlugin", "Enabled": True},
                               {"Name": "EditorScriptingUtilities", "Enabled": True}]},
                  handle, indent=1)
    source = os.path.abspath(source_dir).replace("\\", "/").rstrip("/") + "/"
    settings = f"""[/Script/UnrealEd.EditorLoadingSavingSettings]
bMonitorContentDirectories=True
!AutoReimportDirectorySettings=ClearArray
+AutoReimportDirectorySettings=(SourceDirectory="{source}",MountPoint="{mount}",Wildcards=((Wildcard="*.png",bInclude=True)))
AutoReimportThreshold={threshold}
bAutoCreateAssets=False
bAutoDeleteAssets=False
bDetectChangesOnStartup={detect_on_startup}
bPromptBeforeAutoImporting=False
bAutoSaveEnable=False

[/Script/UnrealEd.EditorPerformanceSettings]
bThrottleCPUWhenNotForeground={throttle_background}
"""
    with open(os.path.join(project_dir, "Config", "DefaultEditorPerProjectUserSettings.ini"),
              "w", encoding="utf-8") as handle:
        handle.write(settings)
    with open(os.path.join(project_dir, "Config", "DefaultEngine.ini"), "w", encoding="utf-8") as handle:
        handle.write("[/Script/EngineSettings.GameMapsSettings]\n"
                     "EditorStartupMap=/Engine/Maps/Templates/Template_Default.Template_Default\n")
    return uproject


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    parser.add_argument("source_dir")
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--no-detect-on-startup", action="store_true")
    parser.add_argument("--no-throttle", action="store_true")
    args = parser.parse_args()
    print(write_project(args.project_dir, args.source_dir, args.threshold,
                        not args.no_detect_on_startup, not args.no_throttle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
