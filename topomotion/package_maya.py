"""Build the offline artist release using only stdlib and existing artifacts.

python -m topomotion.package_maya
No pip/network/train step. Never overwrites the old v1_package/ or source data.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from .v1_package import _copy_sanitized

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.2"
RELEASE = "TopoMotion-%s-Windows-Maya2027" % VERSION


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build(out=None):
    out = Path(out or ROOT / "outputs" / "maya_release").resolve()
    onnx = ROOT / "outputs" / "e12_novq_seed0.onnx"
    vendor = ROOT / "outputs" / "maya_ort_vendor"
    library = ROOT / "outputs" / "motion_library"
    required = [onnx, Path(str(onnx) + ".data"), library / "manifest.json",
                library / "README.md", vendor / "onnxruntime" / "__init__.py"]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise FileNotFoundError("배포 필수 파일 누락:\n" + "\n".join(missing))
    wheels = list(vendor.glob("onnxruntime-*.dist-info/WHEEL"))
    native = list((vendor / "onnxruntime" / "capi").glob("*.pyd"))
    if not native or len(wheels) != 1 or "Tag: cp313-cp313-win_amd64" not in wheels[0].read_text():
        raise RuntimeError("Windows Python 3.13용 ONNX Runtime이 필요합니다.")
    lm = json.loads((library / "manifest.json").read_text(encoding="utf-8"))
    if not lm.get("motions"):
        raise ValueError("샘플 모션이 없습니다.")
    for item in lm["motions"]:
        path = (library / item["file"]).resolve()
        if not path.is_relative_to(library.resolve()) or not path.is_file():
            raise ValueError("잘못된 샘플 경로: " + item["file"])
    out.mkdir(parents=True, exist_ok=True)
    # A fresh staging folder means stale binaries cannot enter a new release.
    staging = Path(tempfile.mkdtemp(prefix="build-", dir=out))
    release = staging / RELEASE
    package = release / "TopoMotion"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    for name in ("topomotion_panel.py", "maya_rig_export.py",
                 "maya_anim_export.py", "maya_import.py"):
        _copy_sanitized(str(ROOT / "maya_scripts" / name), str(scripts / name))
    shutil.copytree(ROOT / "maya_scripts" / "topomotion_core", scripts / "topomotion_core",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (package / "plug-ins").mkdir()
    shutil.copy2(ROOT / "maya_scripts" / "topomotion_plugin.py", package / "plug-ins")
    (package / "core").mkdir()
    for path in (onnx, Path(str(onnx) + ".data")):
        shutil.copy2(path, package / "core")
    # Preserve package metadata and third-party license files; Maya supplies NumPy.
    shutil.copytree(vendor, package / "core" / "vendor",
                    ignore=shutil.ignore_patterns("numpy*", "__pycache__", "*.pyc", "bin"))
    shutil.copytree(library, package / "motions",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    libreadme = package / "motions" / "README.md"
    libreadme.write_text(libreadme.read_text(encoding="utf-8").replace(
        "③ 모션 JSON 적용", "⑤ 애니메이션 열기"), encoding="utf-8")
    (package / "docs").mkdir()
    for name in ("USER_GUIDE.html", "MAINTAINER.md"):
        shutil.copy2(ROOT / "docs" / "maya_plugin" / name, package / "docs")
    shutil.copytree(ROOT / "docs" / "maya_plugin" / "fonts", package / "docs" / "fonts")
    shutil.copytree(ROOT / "docs" / "maya_plugin" / "images", package / "docs" / "images")
    shutil.copy2(ROOT / "maya_scripts" / "install_topomotion.py", release / "install_topomotion.py")
    (release / "START_HERE.html").write_text(
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0;url=TopoMotion/docs/USER_GUIDE.html">'
        '<title>TopoMotion 시작하기</title>'
        '<a href="TopoMotion/docs/USER_GUIDE.html">한국어 사용 설명서 열기</a></html>',
        encoding="utf-8")
    (release / "README.txt").write_text(
        "TopoMotion %s | Windows Maya 2027\n\n"
        "1. ZIP 전체 압축 해제.\n"
        "2. START_HERE.html 더블 클릭 → 매뉴얼 열기.\n"
        "3. Maya 실행 → install_topomotion.py를 중앙 3D 화면으로 끌어 놓기.\n"
        "4. ① 샘플 불러오기 → 샘플 선택 → 뼈대 만들기.\n"
        "5. Maya 타임라인의 재생 버튼 클릭.\n\n"
        "설치 폴더 이동·삭제 금지.\n" % VERSION, encoding="utf-8")
    manifest = {"name": "TopoMotion", "version": VERSION,
                "target": {"os": "Windows x64", "maya": "2027", "python": "3.13",
                           "provider": "CPUExecutionProvider"},
                "model": "core/e12_novq_seed0.onnx",
                "model_external_data": "core/e12_novq_seed0.onnx.data",
                "motion_count": len(lm["motions"]),
                "motion_attribution": lm.get("attribution"),
                "files": {}}
    for path in sorted(release.rglob("*")):
        if path.is_file():
            manifest["files"][path.relative_to(release).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": sha256(path)}
    (release / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = out / (RELEASE + ".zip")
    temporary_zip = staging / archive.name
    with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(release.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(staging))
    temporary_zip.replace(archive)
    (out / (archive.name + ".sha256")).write_text(
        sha256(archive) + "  " + archive.name + "\n", encoding="ascii")
    return release, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    release, archive = build(args.out)
    print("Release: " + str(release))
    print("ZIP: " + str(archive))
    print("SHA256: " + sha256(archive))


if __name__ == "__main__":
    main()
