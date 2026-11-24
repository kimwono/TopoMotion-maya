"""Package must fail closed and carry all offline dependencies."""
import json
from pathlib import Path
import zipfile

import pytest

from topomotion import package_maya


def fake_artifacts(root):
    outputs = root / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "e12_novq_seed0.onnx").write_bytes(b"model")
    (outputs / "e12_novq_seed0.onnx.data").write_bytes(b"weights")
    vendor = outputs / "maya_ort_vendor"
    (vendor / "onnxruntime/capi").mkdir(parents=True)
    (vendor / "onnxruntime/__init__.py").write_text("")
    (vendor / "onnxruntime/capi/ort.pyd").write_bytes(b"native")
    (vendor / "onnxruntime/LICENSE").write_text("vendor license")
    (vendor / "onnxruntime-1.0.dist-info").mkdir()
    (vendor / "onnxruntime-1.0.dist-info/WHEEL").write_text("Tag: cp313-cp313-win_amd64\n")
    (vendor / "numpy").mkdir()
    (vendor / "numpy/__init__.py").write_text("must not ship")
    library = outputs / "motion_library"
    library.mkdir()
    (library / "manifest.json").write_text(json.dumps({"motions": [{"file": "motion.json"}]}))
    (library / "motion.json").write_text("{}")
    (library / "README.md").write_text("motion attribution")
    scripts = root / "maya_scripts"
    (scripts / "topomotion_core").mkdir(parents=True)
    (scripts / "topomotion_core/__init__.py").write_text("")
    for name in ("topomotion_panel", "maya_rig_export", "maya_anim_export",
                 "topomotion_plugin", "install_topomotion"):
        (scripts / (name + ".py")).write_text("# source")
    (scripts / "maya_import.py").write_text('OUT_DIR = r"C:\\Users\\example\\outputs"\n')
    docs = root / "docs/maya_plugin"
    docs.mkdir(parents=True)
    for name in ("USER_GUIDE.html", "MAINTAINER.md"):
        (docs / name).write_text("guide")
    (docs / "fonts").mkdir()
    (docs / "fonts/PretendardVariable.woff2").write_bytes(b"wOF2fixture")
    (docs / "fonts/Pretendard-LICENSE.txt").write_text("font license")
    (docs / "images").mkdir()
    (docs / "images/panel-guide-1.0.2.png").write_bytes(b"guide image fixture")
    return outputs


def test_offline_zip_integrity_and_no_vendor_numpy(tmp_path, monkeypatch):
    fake_artifacts(tmp_path)
    monkeypatch.setattr(package_maya, "ROOT", tmp_path)
    release, archive = package_maya.build()
    man = json.loads((release / "package_manifest.json").read_text(encoding="utf-8"))
    for name, info in man["files"].items():
        assert package_maya.sha256(release / name) == info["sha256"]
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        assert not any("/numpy/" in n for n in names)
        assert any(n.endswith("onnxruntime/LICENSE") for n in names)
        assert any(n.endswith("docs/fonts/PretendardVariable.woff2") for n in names)
        assert any(n.endswith("docs/fonts/Pretendard-LICENSE.txt") for n in names)
        assert z.read(package_maya.RELEASE + "/TopoMotion/docs/images/panel-guide-1.0.2.png") == b"guide image fixture"
        assert not any(n.endswith(".mod") for n in names)
        source = z.read(package_maya.RELEASE + "/TopoMotion/scripts/maya_import.py")
        assert b"C:\\Users" not in source
        assert b"OUT_DIR = None" in source
    # A fresh build does not copy arbitrary files left in an earlier staging dir.
    (release / "private.txt").write_text("do not ship")
    _, archive = package_maya.build()
    with zipfile.ZipFile(archive) as z:
        assert not any(n.endswith("private.txt") for n in z.namelist())


def test_missing_weights_aborts_release(tmp_path, monkeypatch):
    outputs = fake_artifacts(tmp_path)
    (outputs / "e12_novq_seed0.onnx.data").unlink()
    monkeypatch.setattr(package_maya, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        package_maya.build()
    assert not (outputs / "maya_release").exists()


def test_wrong_python_abi_aborts_release(tmp_path, monkeypatch):
    outputs = fake_artifacts(tmp_path)
    (outputs / "maya_ort_vendor/onnxruntime-1.0.dist-info/WHEEL").write_text(
        "Tag: cp311-cp311-win_amd64\n")
    monkeypatch.setattr(package_maya, "ROOT", tmp_path)
    with pytest.raises(RuntimeError):
        package_maya.build()
