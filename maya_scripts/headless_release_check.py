"""Verify an extracted artist ZIP under an isolated Maya profile (mayapy)."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import zipfile

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, type=Path)
    args = ap.parse_args()
    archive = args.zip.resolve()
    work = Path(tempfile.mkdtemp(prefix="relocated test ", dir=archive.parent))
    os.environ["MAYA_APP_DIR"] = str(work / "maya_profile")
    os.environ["MAYA_SKIP_USERSETUP_PY"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    report = {"zip": str(archive), "relocated_to": str(work), "checks": {},
              "gui_verified": False}
    initialized = False

    def check(name, condition, **details):
        report["checks"][name] = {"ok": bool(condition), **details}
        print(("PASS " if condition else "FAIL ") + name)
        if not condition:
            raise AssertionError(name)

    try:
        with zipfile.ZipFile(archive) as z:
            z.extractall(work)
        release = next(p for p in work.iterdir() if p.name.startswith("TopoMotion-"))
        package = release / "TopoMotion"
        manifest = json.loads((release / "package_manifest.json").read_text(encoding="utf-8"))
        mismatches = [name for name, meta in manifest["files"].items()
                      if hashlib.sha256((release / name).read_bytes()).hexdigest() != meta["sha256"]]
        check("zip_hashes", not mismatches, count=len(manifest["files"]))
        check("no_developer_paths", all(
            "C:\\Users\\" not in p.read_text(encoding="utf-8") and
            "C:/Users/" not in p.read_text(encoding="utf-8")
            for p in (package / "scripts").rglob("*.py")))
        import maya.standalone
        maya.standalone.initialize(name="python")
        initialized = True
        import maya.cmds as cmds
        report["maya"] = cmds.about(version=True)
        report["python"] = sys.version
        installer_spec = importlib.util.spec_from_file_location(
            "release_installer", release / "install_topomotion.py")
        installer = importlib.util.module_from_spec(installer_spec)
        installer_spec.loader.exec_module(installer)
        mod_file = Path(installer.install(show=False))
        check("isolated_install", mod_file.is_relative_to(work) and mod_file.is_file(),
              mod=str(mod_file))
        check("plugin_command", "topoMotionPanel" in
              cmds.pluginInfo("topomotion_plugin", query=True, command=True))
        check("idempotent_install", installer.install(show=False) == str(mod_file))
        import topomotion_panel as panel
        panel._core_on_path()
        from topomotion_core.playback import prepare
        entries = panel.library_entries()
        for _, path in entries:
            prepare(json.loads(Path(path).read_text(encoding="utf-8")))
        check("all_library_documents", len(entries) == 15, count=len(entries))
        grp = panel.do_library_motion(index=0)
        joints = cmds.ls(grp, dag=True, type="joint", long=True)
        check("sample_playback", len(joints) > 1 and all(
            cmds.keyframe(j, query=True, keyframeCount=True) for j in joints),
              joints=len(joints))
        root = min(joints, key=lambda j: j.count("|"))
        cmds.select(root)
        rows = panel.do_roundtrip(interactive=False)
        check("rig_roundtrip", bool(rows) and all(r["ok"] for r in rows))
        report_text = panel.installation_report(interactive=False)
        check("offline_model_load", "모델 로드: 정상" in report_text, report=report_text)
        rows = panel.do_reconstruct(interactive=False)
        check("reconstruct_button", bool(rows) and all(r["ok"] for r in rows),
              mean_deg=rows[0].get("mean_deg"), frames=rows[0].get("frames_used"))
        check("torch_free", "torch" not in sys.modules)
        mae = panel.mod("maya_anim_export")
        exports = work / "my_exports"
        # Export a nonzero-start range so rebasing to frame zero is checked too.
        doc = mae.extract_anim(root, start=5, end=36)
        path = mae.export_anims(str(exports), roots=[root], start=5, end=36)[0]
        imported = panel.do_apply_motion(path)
        imported2 = panel.do_apply_motion(path)
        check("repeat_preserves_existing", imported != imported2 and
              cmds.objExists(imported) and cmds.objExists(imported2) and cmds.objExists(grp))
        import numpy as np

        def compare_group(group):
            mre = panel.mod("maya_rig_export")
            imported_joints = cmds.ls(group, dag=True, type="joint", long=True)
            imported_root = min(imported_joints, key=lambda j: j.count("|"))
            imported_doc = mae.extract_anim(imported_root, start=0, end=31)
            error = float(np.max(np.abs(np.asarray(doc["world_matrices"]) -
                                        np.asarray(imported_doc["world_matrices"]))))
            return error

        error_cm = compare_group(imported)
        check("export_import_pose_cm", error_cm < 1e-5, max_matrix_difference=error_cm)
        cmds.currentUnit(linear="m")
        imported_m = panel.do_apply_motion(path)
        error_m = compare_group(imported_m)
        check("export_import_pose_m", error_m < 1e-5, max_matrix_difference=error_m)
        cmds.currentUnit(linear="cm")
        # Rejection must happen before group creation.
        before = set(cmds.ls(assemblies=True))
        try:
            panel._play_document({"format": "topomotion_rig/1"}, "INVALID")
        except ValueError:
            pass
        else:
            raise AssertionError("rig document accepted")
        check("invalid_file_no_scene_change", set(cmds.ls(assemblies=True)) == before)
        # Namespaced production rigs export on Windows and reopen without that
        # namespace. Also cover 50 FPS, which the legacy importer did not map.
        cmds.namespace(add="artist")
        cmds.select(clear=True)
        nr = cmds.joint(name="artist:root")
        nc = cmds.joint(name="artist:tip", position=(0, 10, 0))
        cmds.currentUnit(time="palf")
        cmds.setKeyframe(nc, attribute="rotateZ", time=1, value=0)
        cmds.setKeyframe(nc, attribute="rotateZ", time=32, value=35)
        cmds.select(nr)
        rig_paths = panel.mod("maya_rig_export").export_rigs(str(exports), roots=[nr])
        anim_paths = mae.export_anims(str(exports), roots=[nr], start=1, end=32)
        check("namespaced_export", all(Path(p).is_file() and ":" not in Path(p).name
                                       for p in rig_paths + anim_paths))
        ns_group = panel.do_apply_motion(anim_paths[0])
        check("namespaced_import_50fps", cmds.objExists(ns_group) and
              cmds.currentUnit(query=True, time=True) == "palf",
              playback_end=cmds.playbackOptions(query=True, maxTime=True))
        cmds.unloadPlugin("topomotion_plugin")
        check("plugin_unload", not cmds.pluginInfo("topomotion_plugin", query=True, loaded=True))
        # Fresh process discovers the installed .mod without sys.path setup.
        code = (
            "import maya.standalone; maya.standalone.initialize(name='python'); "
            "import maya.cmds as c; c.loadPlugin('topomotion_plugin.py'); "
            "import topomotion_panel as p; "
            "assert len(p.library_entries()) == 15; "
            "assert c.pluginInfo('topomotion_plugin',q=True,version=True)==%r; "
            "print('RESTART_DISCOVERY_OK'); maya.standalone.uninitialize()")
        code = code % manifest["version"]
        child = subprocess.run([sys.executable, "-c", code], env=os.environ.copy(),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
        check("restart_module_discovery", child.returncode == 0 and
              "RESTART_DISCOVERY_OK" in child.stdout,
              exit_code=child.returncode, stderr=child.stderr[-2000:])
    except Exception:
        report["error"] = traceback.format_exc()
        traceback.print_exc()
    finally:
        if initialized:
            maya.standalone.uninitialize()
        report["all_pass"] = "error" not in report and bool(report["checks"]) and all(
            row["ok"] for row in report["checks"].values())
        output = archive.parent / "release_check.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Report: " + str(output))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
