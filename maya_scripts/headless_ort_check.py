"""
headless_ort_check.py — **Maya 안에서 torch 없이** 모델 추론이 도는지 실증
(v1.0.0 아키텍처 결정 재료 — docs/v1_architecture.md).

경로: 씬의 리그 → maya_anim_export 로 추출 → **numpy 만으로** 모델 입력
피처화(x_static 7채널·rot6d·edge_index) → vendor 디렉토리의 onnxruntime 으로
ONNX(E12 novq 동결본) 추론 → 입력·출력을 npz 로 저장.

수치 파리티(같은 문서를 uv/torch 로 처리한 값과의 대조)는 uv 쪽에서:
  uv run python -m topomotion.maya_ort_parity

사전 조건:
  1. outputs/e12_novq_seed0.onnx (uv run --with onnx --with onnxruntime
     --with onnxscript python -m topomotion.onnx_check 로 생성)
  2. vendor 설치: mayapy -m pip install onnxruntime --target outputs/maya_ort_vendor

실행:
  & "C:\\Program Files\\Autodesk\\Maya2027\\bin\\mayapy.exe" maya_scripts/headless_ort_check.py
"""

# 콘솔 인코딩 고정 — 한국어 Windows 기본 콘솔(cp949)에서 한글·em-dash 출력이
# UnicodeEncodeError 를 내고, 그것이 **게이트 실패로 오보고**되는 것을 막는다
# (headless_v1_smoke 에서 2026-08-24 실측: 왕복 오차 2.78e-16 인데 S4 가 FAIL).
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                          # noqa: BLE001
        pass

import importlib.util
import json
import os
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
VENDOR = os.path.join(ROOT, "outputs", "maya_ort_vendor")
ONNX_PATH = os.path.join(ROOT, "outputs", "e12_novq_seed0.onnx")
OUT_NPZ = os.path.join(ROOT, "outputs", "maya_rig", "ort_maya_run.npz")
DCC_SAMPLE = os.path.join(ROOT, "outputs", "mobja_dcc", "mobja_aquatic_003430_gt.json")
W = 32   # Mobjaverse 트랙 동결 윈도우

sys.path.insert(0, VENDOR)
import numpy as np              # noqa: E402 (Maya 내장 numpy)
import onnxruntime as ort       # noqa: E402 (vendor)

import maya.standalone          # noqa: E402
maya.standalone.initialize(name="python")
import maya.cmds as cmds        # noqa: E402


def _load(mod_name, fname):
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(ROOT, "maya_scripts", fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# numpy 피처화 — topomotion.data.SkeletonSpec.build_static_features 및
# matrix_to_rotation_6d 와 **같은 정의** (파리티는 uv 쪽 검사가 판정한다).
# torch 를 못 쓰는 환경이 전제라 의도적으로 복제한다 — 정의가 바뀌면
# maya_ort_parity 가 빨간불로 알려준다.
# ---------------------------------------------------------------------------
def static_features(parents, offsets):
    n = len(parents)
    offs = np.asarray(offsets, dtype=np.float32)                    # [N,3]
    bone_len = np.linalg.norm(offs, axis=-1, keepdims=True)         # [N,1]
    depth = np.zeros(n, dtype=np.float32)
    for i in range(n):
        d, p = 0, parents[i]
        while p >= 0:
            d += 1
            p = parents[p]
        depth[i] = d
    depth = (depth / max(depth.max(), 1.0)).reshape(-1, 1)
    deg = np.zeros(n, dtype=np.float32)
    for p in parents:
        if p >= 0:
            deg[p] += 1
    is_ee = (deg == 0).astype(np.float32).reshape(-1, 1)
    deg = (deg / max(deg.max(), 1.0)).reshape(-1, 1)
    return np.concatenate([offs, bone_len, depth, deg, is_ee], axis=-1)


def edge_index(parents):
    src, dst = [], []
    for child, parent in enumerate(parents):
        if parent < 0:
            continue
        src += [parent, child]
        dst += [child, parent]
    return np.asarray([src, dst], dtype=np.int64)


def rot6d_from_local(local_rot, n, t):
    """[T][N][9](row-major, column-vector 규약) → [N,T,6] (R 의 앞 두 행)."""
    R = np.asarray(local_rot, dtype=np.float32).reshape(t, n, 3, 3)
    r6 = R[:, :, :2, :].reshape(t, n, 6)
    return np.transpose(r6, (1, 0, 2)).copy()


def main():
    assert os.path.exists(ONNX_PATH), "ONNX 없음 — topomotion.onnx_check 먼저"
    mi = _load("maya_import", "maya_import.py")
    mae = _load("maya_anim_export", "maya_anim_export.py")

    # 1) 씬 준비: 우리 export JSON 을 올려 애니메이션 리그 확보 (J=10, 32프레임)
    src = mi.load_doc(DCC_SAMPLE)
    mi.build_and_animate(src, "ORTCHK")
    grp = cmds.ls("ORTCHK", long=True)[0]
    joints = cmds.ls(grp + "|*", dag=True, type="joint", long=True)
    root = min(joints, key=lambda p: p.count("|"))

    # 2) 추출 → numpy 피처화 (torch 비의존)
    t0 = time.perf_counter()
    doc = mae.extract_anim(root, name="ortchk", start=0,
                           end=src["num_frames"] - 1)
    t_extract = (time.perf_counter() - t0) * 1000
    rig = doc["rig"]
    n, t = doc["num_joints"], doc["num_frames"]
    assert t >= W, "윈도우(32)보다 짧은 클립"
    t0 = time.perf_counter()
    x_dyn = rot6d_from_local(doc["local_rot"], n, t)[:, :W, :]      # [N,32,6]
    x_static = static_features(rig["parents"], rig["offsets"])      # [N,7]
    ei = edge_index(rig["parents"])                                 # [2,E]
    batch = np.zeros(n, dtype=np.int64)
    t_feat = (time.perf_counter() - t0) * 1000

    # 3) onnxruntime 추론 (CPU)
    t0 = time.perf_counter()
    sess = ort.InferenceSession(ONNX_PATH, providers=["CPUExecutionProvider"])
    t_sess = (time.perf_counter() - t0) * 1000
    feed = {"x_dyn": x_dyn, "x_static": x_static,
            "edge_index": ei, "batch": batch}
    sess.run(["rot6d"], feed)                                       # warmup
    t0 = time.perf_counter()
    rot6d = sess.run(["rot6d"], feed)[0]
    t_infer = (time.perf_counter() - t0) * 1000

    # 4) 저장 — uv 쪽 파리티 검사 입력
    os.makedirs(os.path.dirname(OUT_NPZ), exist_ok=True)
    np.savez(OUT_NPZ, x_dyn=x_dyn, x_static=x_static, edge_index=ei,
             batch=batch, rot6d=rot6d,
             parents=np.asarray(rig["parents"], dtype=np.int64),
             offsets=np.asarray(rig["offsets"], dtype=np.float32),
             local_rot=np.asarray(doc["local_rot"], dtype=np.float32))
    meta = {"python": sys.version.split()[0], "ort": ort.__version__,
            "numpy": np.__version__, "J": n, "frames_used": W,
            "ms_extract": round(t_extract, 1), "ms_featurize": round(t_feat, 2),
            "ms_session_load": round(t_sess, 1), "ms_infer": round(t_infer, 2),
            "vendor": VENDOR, "onnx": ONNX_PATH}
    with open(OUT_NPZ.replace(".npz", "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    print("mayapy 단독 추론 완료 (torch 미사용):")
    for k, v in meta.items():
        print("  %s: %s" % (k, v))
    print("저장: %s" % OUT_NPZ)
    print("다음: uv run python -m topomotion.maya_ort_parity")


main()
maya.standalone.uninitialize()
