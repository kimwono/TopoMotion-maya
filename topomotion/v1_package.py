"""
v1_package.py — Maya 플러그인 v1.0.0 **설치 가능 패키지 조립**.

기존 부품을 하나의 `.mod` 모듈로 묶는다. **새 로직을 만들지 않고 배치만 한다**
(docs/v1_architecture.md 2절 레이아웃).

  <OUT>/
  ├── TopoMotion.mod          ← Maya modules 폴더에 복사(또는 경로 등록)
  └── TopoMotion/
      ├── plug-ins/topomotion_plugin.py     Plug-in Manager 진입점
      ├── scripts/                          패널·리그/모션 입출력·numpy 코어
      ├── core/e12_novq_seed0.onnx(+.data)   동결 체크포인트
      ├── core/vendor/                      onnxruntime (numpy 제외 — Maya 내장)
      ├── motions/                          기본 모션 라이브러리 + manifest
      └── README.md

**학습 경로 코드 불가침** — topomotion/ 의 학습·평가 모듈은 읽지도 고치지도
않는다 (이 파일은 파일 복사와 vendor 설치만 한다).

실행:
  uv run python -m topomotion.v1_package                    # 조립
  uv run python -m topomotion.v1_package --vendor           # + ort 재설치
  uv run python -m topomotion.v1_package --install          # + modules 에 .mod 배치
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

NAME = "TopoMotion"
VERSION = "1.0.0"
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     ".."))
OUT = os.path.join(ROOT, "outputs", "v1_package")
MAYAPY = r"C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe"

SCRIPTS = ("topomotion_panel.py", "maya_rig_export.py", "maya_anim_export.py",
           "maya_import.py")
CORE_PKG = "topomotion_core"
PLUGIN = "topomotion_plugin.py"
ONNX_SRC = os.path.join(ROOT, "outputs", "e12_novq_seed0.onnx")
ONNX_DST = None   # 원본 파일명 유지 (external data 참조가 파일명 상대)
LIB_SRC = os.path.join(ROOT, "outputs", "motion_library")
VENDOR_SRC = os.path.join(ROOT, "outputs", "maya_ort_vendor")

# .mod 는 모듈 루트를 가리킨다. scripts/·plug-ins/ 는 Maya 가 자동 인식하지만
# 명시해 두면 레이아웃 변경 시 조용히 깨지지 않는다.
MOD_TEMPLATE = """+ {name} {version} {path}
scripts: scripts
plug-ins: plug-ins
PYTHONPATH+:=scripts
TOPOMOTION_ROOT:={path}
"""


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def _copy_sanitized(src, dst):
    """복사하면서 **개발 기계의 절대 경로를 제거**한다.

    maya_import.OUT_DIR 은 로컬 작업본에서 하드코딩된 경로로 두는 관행이 있다
    (미커밋 관행). 그 상태로 배포되면 남의 기계에서 존재하지 않는
    경로를 가리키므로, 패키지에는 항상 `None`(=__file__ 기준 자동 결정)으로
    싣는다. 작업본 파일 자체는 건드리지 않는다.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, encoding="utf-8") as f:
        txt = f.read()
    new, n = re.subn(r'^OUT_DIR\s*=\s*r?["\'][^"\']*["\']\s*$',
                     'OUT_DIR = None   # 패키지: __file__ 기준 자동 결정',
                     txt, flags=re.M)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(new)
    return dst, n


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(path) for f in fs)


def vendor_install(dst):
    """onnxruntime 을 --no-deps 로 설치 (numpy 는 Maya 내장 사용 → ~47MB 절약).

    mayapy 가 없으면 기존 vendor 디렉토리에서 numpy 를 제외하고 복사한다.
    """
    os.makedirs(dst, exist_ok=True)
    if os.path.exists(MAYAPY):
        r = subprocess.run([MAYAPY, "-m", "pip", "install", "onnxruntime",
                            "--no-deps", "--target", dst, "--quiet"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "mayapy pip --no-deps"
        print("  경고: mayapy pip 실패 — 기존 vendor 복사로 대체\n  " + r.stderr.strip()[:300])
    if not os.path.isdir(VENDOR_SRC):
        return "없음 (vendor 미설치 — 추론 버튼 비활성)"
    skip = {"numpy", "numpy.libs", "numpy-2.3.5.dist-info"}
    for item in os.listdir(VENDOR_SRC):
        if item in skip or item.startswith("numpy"):
            continue
        s, d = os.path.join(VENDOR_SRC, item), os.path.join(dst, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    return "기존 vendor 복사 (numpy 제외)"


def build(do_vendor=False):
    pkg = os.path.join(OUT, NAME)
    if os.path.isdir(pkg):
        shutil.rmtree(pkg)
    os.makedirs(pkg)
    man = {"name": NAME, "version": VERSION, "components": {}}

    # 1) scripts/ — 패널 + 입출력 + numpy 코어
    sanitized = []
    for fn in SCRIPTS:
        _, n = _copy_sanitized(os.path.join(ROOT, "maya_scripts", fn),
                               os.path.join(pkg, "scripts", fn))
        if n:
            sanitized.append("%s (OUT_DIR %d곳)" % (fn, n))
    man["sanitized_paths"] = sanitized
    shutil.copytree(os.path.join(ROOT, "maya_scripts", CORE_PKG),
                    os.path.join(pkg, "scripts", CORE_PKG),
                    ignore=shutil.ignore_patterns("__pycache__"))
    man["components"]["scripts"] = sorted(SCRIPTS) + [CORE_PKG + "/"]

    # 2) plug-ins/ — Plug-in Manager 진입점
    _copy(os.path.join(ROOT, "maya_scripts", PLUGIN),
          os.path.join(pkg, "plug-ins", PLUGIN))
    man["components"]["plugin"] = PLUGIN

    # 3) core/ — ONNX 동결본 (+ external data 사이드카)
    #    ⚠️ 이 그래프는 가중치를 `<name>.onnx.data` 로 분리 저장한다. 참조가
    #    **파일명 상대 경로**라 .onnx 만 옮기거나 이름을 바꾸면 세션 생성이
    #    실패한다 (통합 스모크 S5 에서 발견). → 원본 파일명 그대로 둘 다 싣는다.
    if os.path.exists(ONNX_SRC):
        base = os.path.basename(ONNX_SRC)
        d = _copy(ONNX_SRC, os.path.join(pkg, "core", base))
        side = ONNX_SRC + ".data"
        has_side = os.path.exists(side)
        if has_side:
            _copy(side, os.path.join(pkg, "core", base + ".data"))
        man["components"]["onnx"] = {
            "file": "core/" + base, "bytes": _size(d), "sha256_16": _sha(d),
            "external_data": ("core/" + base + ".data") if has_side else None,
            "external_bytes": _size(side) if has_side else 0}
    else:
        man["components"]["onnx"] = None
        print("  경고: ONNX 없음 — topomotion.onnx_check 먼저 실행")

    # 4) core/vendor/ — onnxruntime
    if do_vendor:
        how = vendor_install(os.path.join(pkg, "core", "vendor"))
        man["components"]["vendor"] = {
            "how": how, "bytes": _size(os.path.join(pkg, "core", "vendor"))}
    else:
        man["components"]["vendor"] = {"how": "생략 (--vendor 로 포함)", "bytes": 0}

    # 5) motions/ — 기본 모션 라이브러리 (ODC-By 표기 동반)
    if os.path.isdir(LIB_SRC):
        shutil.copytree(LIB_SRC, os.path.join(pkg, "motions"),
                        dirs_exist_ok=True)
        with open(os.path.join(LIB_SRC, "manifest.json"), encoding="utf-8") as f:
            lm = json.load(f)
        man["components"]["motions"] = {
            "count": len(lm.get("motions", [])), "license": lm.get("license"),
            "bytes": _size(os.path.join(pkg, "motions"))}
    else:
        man["components"]["motions"] = None
        print("  경고: 모션 라이브러리 없음 — topomotion.motion_library 먼저 실행")

    # 6) .mod + README
    mod_path = os.path.join(OUT, NAME + ".mod")
    with open(mod_path, "w", encoding="utf-8") as f:
        f.write(MOD_TEMPLATE.format(name=NAME, version=VERSION,
                                    path=pkg.replace("\\", "/")))
    with open(os.path.join(pkg, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme(man))
    man["total_bytes"] = _size(pkg)
    with open(os.path.join(OUT, "package_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(man, f, indent=1, ensure_ascii=False)
    return pkg, mod_path, man


def readme(man):
    lic = ""
    p = os.path.join(LIB_SRC, "README.md")
    if os.path.exists(p):
        lic = open(p, encoding="utf-8").read()
    return """# TopoMotion v{v} — Maya 플러그인

## 설치

1. 이 폴더(`{n}`)와 `{n}.mod` 를 함께 둔 뒤, `{n}.mod` 를 Maya 의 modules
   폴더에 복사한다:
   `%USERPROFILE%/Documents/maya/<버전>/modules/`
   (또는 환경변수 `MAYA_MODULE_PATH` 에 `{n}.mod` 가 있는 폴더를 추가)
2. Maya 재시작 → **Windows > Settings/Preferences > Plug-in Manager** 에서
   `topomotion_plugin.py` 를 Loaded 체크 (Auto load 도 권장).
3. 상단 **TopoMotion** 메뉴 > 패널 열기. 또는 스크립트에서:
   `import maya.cmds as cmds; cmds.topoMotionPanel()`

`.mod` 안의 경로는 조립 시점의 절대 경로다 — 폴더를 옮기면 `.mod` 의 마지막
필드를 새 경로로 고쳐라.

## 쓰는 법 (v1.0.0 범위)

- **① 라이브러리 모션 재생** — 동봉된 기본 모션을 골라 씬에 리그를 만들고
  재생한다. 재생 구간이 모션 길이에 맞춰진다.
- **② 리그 왕복 확인** — 씬의 리그를 **읽기 전용**으로 훑어 우리 규약으로
  되돌렸을 때 Maya 의 world 행렬이 그대로 복원되는지 본다. 판정은 행렬 요소
  차이(임계 1e-6)이며 측지각은 참고 병기다.
- ③④ 리그·애니메이션 문서 저장, ⑤⑥ 저장된 문서 적용.

**교차 리그 전이(라이브러리 모션을 임의 사용자 리그에 얹기)는 v1.0.0 범위
밖이다** — 미해결 연구 항목 (docs/v1_architecture.md 3절).

## 구성

- `scripts/` 패널·리그/모션 입출력·numpy 코어 (torch 비의존)
- `plug-ins/topomotion_plugin.py` Plug-in Manager 진입점
- `core/` 동결 모델(ONNX) + onnxruntime vendor
- `motions/` 기본 모션 라이브러리

## 라이선스·출처

{lic}
""".format(v=VERSION, n=NAME, lic=lic or "(모션 라이브러리 미포함)")


def install_mod(mod_path):
    """사용자 Maya modules 폴더에 .mod 배치."""
    base = os.path.join(os.path.expanduser("~"), "Documents", "maya")
    if not os.path.isdir(base):
        return None, "maya 사용자 폴더 없음: " + base
    vers = sorted([d for d in os.listdir(base)
                   if d[:4].isdigit() and os.path.isdir(os.path.join(base, d))])
    if not vers:
        return None, "버전 폴더 없음: " + base
    dst_dir = os.path.join(base, vers[-1], "modules")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(mod_path))
    shutil.copy2(mod_path, dst)
    return dst, "Maya %s" % vers[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", action="store_true", help="onnxruntime 동봉")
    ap.add_argument("--install", action="store_true", help=".mod 를 modules 에 배치")
    a = ap.parse_args()

    print("TopoMotion v%s 패키지 조립" % VERSION)
    pkg, mod_path, man = build(do_vendor=a.vendor)
    print("\n구성:")
    for k, v in man["components"].items():
        if isinstance(v, dict):
            mb = v.get("bytes", 0) / 1024 ** 2
            print("  %-8s %s (%.2f MB)" % (k, {kk: vv for kk, vv in v.items()
                                               if kk != "bytes"}, mb))
        else:
            print("  %-8s %s" % (k, v))
    print("\n합계 %.1f MB" % (man["total_bytes"] / 1024 ** 2))
    print("패키지: " + pkg)
    print(".mod   : " + mod_path)
    if a.install:
        dst, note = install_mod(mod_path)
        print("설치: %s (%s)" % (dst or "실패", note))
    print("\n스모크: & \"%s\" maya_scripts/headless_v1_smoke.py" % MAYAPY)
    return 0


if __name__ == "__main__":
    sys.exit(main())


