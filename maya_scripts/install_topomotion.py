"""Drag this file from an extracted release into Maya's viewport."""
import os
from pathlib import Path
import sys

VERSION = "1.0.2"


def module_text(package):
    return ("+ MAYAVERSION:2027 PLATFORM:win64 TopoMotion %s %s\n"
            "scripts: scripts\nplug-ins: plug-ins\nPYTHONPATH+:=scripts\n"
            % (VERSION, Path(package).resolve().as_posix()))


def install(package=None, show=True):
    import maya.cmds as cmds
    package = Path(package or Path(__file__).resolve().parent / "TopoMotion").resolve()
    if sys.platform != "win32" or not str(cmds.about(version=True)).startswith("2027"):
        raise RuntimeError("이 배포본은 Windows Maya 2027용입니다. 사용 매뉴얼의 지원 환경을 확인하세요.")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Maya 2027에서 설치 파일을 실행하세요.")
    required = ("plug-ins/topomotion_plugin.py", "scripts/topomotion_panel.py",
                "core/e12_novq_seed0.onnx", "core/e12_novq_seed0.onnx.data",
                "motions/manifest.json", "docs/USER_GUIDE.html")
    missing = [name for name in required if not (package / name).is_file()]
    if missing:
        raise RuntimeError("ZIP 전체를 압축 해제하세요. 누락: " + ", ".join(missing))
    plugin = package / "plug-ins" / "topomotion_plugin.py"
    loaded = cmds.pluginInfo("topomotion_plugin", query=True, loaded=True)
    if loaded:
        old = Path(cmds.pluginInfo("topomotion_plugin", query=True, path=True)).resolve()
        if old != plugin:
            raise RuntimeError("다른 위치의 TopoMotion이 로드되어 있습니다. Plug-in Manager에서 "
                               "기존 Auto load를 끄고 Maya를 재시작한 뒤 다시 설치하세요.\n" + str(old))
    # Avoid mixing Python modules from an old installation even after unload.
    for name, module in tuple(sys.modules.items()):
        if name == "topomotion_panel" or name == "topomotion_core" or name.startswith("topomotion_core."):
            source = getattr(module, "__file__", None)
            if source and not Path(source).resolve().is_relative_to(package):
                raise RuntimeError("이전 TopoMotion이 실행 중입니다. Maya를 재시작하세요.")
    modules = Path(cmds.internalVar(userAppDir=True)) / "2027" / "modules"
    modules.mkdir(parents=True, exist_ok=True)
    mod_file = modules / "TopoMotion.mod"
    content = module_text(package)
    if mod_file.exists() and mod_file.read_text(encoding="utf-8") != content:
        # Backups cannot remain in modules/: Maya also reads non-.mod files there.
        backups = modules.parent / "TopoMotion_backups"
        backups.mkdir(exist_ok=True)
        import time
        (backups / ("TopoMotion-%d.mod" % time.time_ns())).write_bytes(mod_file.read_bytes())
    # Write registration only after the Python plug-in has loaded successfully.
    scripts = str(package / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    if not loaded:
        cmds.loadPlugin(str(plugin), quiet=True)
    mod_file.write_text(content, encoding="utf-8")
    cmds.pluginInfo("topomotion_plugin", edit=True, autoload=True)
    cmds.pluginInfo(savePluginPrefs=True)
    print("[TopoMotion] 설치 완료: %s" % mod_file)
    if show and not cmds.about(batch=True):
        cmds.topoMotionPanel()
        cmds.confirmDialog(title="TopoMotion — 설치 완료",
                           message="상단 TopoMotion 메뉴에서 다시 열 수 있습니다.\n"
                                   "① 샘플 불러오기부터 시작하세요.\n\n"
                                   "설치 폴더를 이동하거나 삭제하지 마세요.\n" + str(package),
                           button=["확인"])
    return str(mod_file)


def onMayaDroppedPythonFile(*args):
    try:
        install()
    except Exception as exc:
        import maya.cmds as cmds
        cmds.confirmDialog(title="TopoMotion — 설치 확인", message=str(exc),
                           button=["확인"])
        raise


if __name__ in ("__main__", "<run_path>"):
    install()
