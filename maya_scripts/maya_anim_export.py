"""
maya_anim_export.py — **Maya 애니메이션 → 모션 문서(JSON)** 추출 (인제스천 2조각).

`maya_rig_export.py` 가 리그 구조(정지)를 뽑는다면, 이 파일은 프레임별
로컬 회전까지 뽑는다. 최종 목표의 입력 경로에서 "사용자가 이미 가진
애니메이션을 모델 입력(SkeletonSpec + rot6d 윈도우)으로 바꾸는" 절반.
uv 쪽 로더·검증은 `topomotion/maya_anim.py`.

사용법 (Maya Script Editor, Python 탭):
    import runpy; runpy.run_path(r"<저장소>/maya_scripts/maya_anim_export.py")
  → 선택한 조인트(없으면 씬의 모든 루트)를 playback 구간으로 추출.
    저장 위치: <저장소>/outputs/maya_rig/anim_<루트이름>.json

**씬을 수정하지 않는다** — currentTime 조차 바꾸지 않는다.
프레임별 평가는 전부 `getAttr(attr, time=t)` 로 한다 (시간 지정 질의는
현재 프레임을 움직이지 않는다). 읽기 전용 보장은 rig_export 와 동일.

기록 규약 (maya_rig_export 와 동일 원칙):
  - 행렬 속성(.matrix/.worldMatrix)은 씬 UI 단위와 무관하게 **항상 내부
    단위(cm)** 다 → 길이는 전부 ×MATRIX_UNIT_TO_M(0.01) 로 미터화 (프로젝트 규약
    함정 항목). 회전 블록은 column-vector 규약(v'=R@v)으로 저장.
  - 우리 모델은 offset 상수를 가정한다. 비루트 관절의 로컬 이동이 프레임마다
    변하면(stretchy rig) 여기서는 **기록만** 하고 판정은 uv 쪽 validate 가 한다.
"""
import importlib.util
import json
import os

import maya.cmds as cmds

# 시간 단위 → fps (Maya currentUnit time 코드)
TIME_TO_FPS = {"game": 15, "film": 24, "pal": 25, "ntsc": 30,
               "show": 48, "palf": 50, "ntscf": 60}


def _load_rig_export():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        raise RuntimeError(
            "붙여넣기 실행이라 __file__ 이 없습니다. 파일로 실행하세요: "
            "import runpy; runpy.run_path(r'.../maya_anim_export.py')")
    spec = importlib.util.spec_from_file_location(
        "maya_rig_export", os.path.join(here, "maya_rig_export.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_mre = _load_rig_export()
MATRIX_UNIT_TO_M = _mre.MATRIX_UNIT_TO_M


def _mat_at(attr, t):
    """matrix 속성을 시간 t 에서 평가 → float 16개 (현재 프레임 불변)."""
    v = cmds.getAttr(attr, time=t)
    while isinstance(v, (list, tuple)) and len(v) == 1:
        v = v[0]
    v = list(v)
    if len(v) != 16:
        raise RuntimeError("matrix 길이가 16 이 아님: %s@%s -> %d" % (attr, t, len(v)))
    return [float(x) for x in v]


def extract_anim(root, name=None, start=None, end=None):
    """루트 조인트 하나의 애니메이션 → 모션 문서 dict. 씬을 수정하지 않는다.

    start/end: 정수 프레임 (기본 = playback 구간). 프레임 간격 1 고정 —
    리샘플·보간은 하지 않는다 (t=0 바인드 포즈 키 같은 데이터 함정 판단은
    uv 쪽에서 원본 그대로 보고 해야 한다).
    """
    rig = _mre.extract_rig(root, name)
    joints = rig["long_names"]
    n = rig["num_joints"]
    s = MATRIX_UNIT_TO_M

    if start is None:
        start = int(cmds.playbackOptions(query=True, min=True))
    if end is None:
        end = int(cmds.playbackOptions(query=True, max=True))
    frames = list(range(int(start), int(end) + 1))

    tcode = cmds.currentUnit(query=True, time=True)
    fps = TIME_TO_FPS.get(tcode)
    warnings = list(rig["warnings"])
    if fps is None:
        fps, note = 30, "시간 단위 %r 미인식 — fps=30 으로 기록" % tcode
        warnings.append(note)

    local_rot, local_pos, world = [], [], []
    for t in frames:
        lr, lp, wm = [], [], []
        for j in joints:
            L = _mat_at(j + ".matrix", t)
            W = _mat_at(j + ".worldMatrix[0]", t)
            lr.append(_mre._rot_cols(L))
            lp.append([L[12] * s, L[13] * s, L[14] * s])
            Wm = list(W)
            Wm[12] *= s
            Wm[13] *= s
            Wm[14] *= s
            wm.append(Wm)
        local_rot.append(lr)
        local_pos.append(lp)
        world.append(wm)

    # offset 상수 가정 점검 재료: 비루트 로컬 이동의 rest 대비 최대 편차 (m)
    drift = 0.0
    for t in range(len(frames)):
        for i in range(1, n):
            o = rig["offsets"][i]
            p = local_pos[t][i]
            d = max(abs(p[0] - o[0]), abs(p[1] - o[1]), abs(p[2] - o[2]))
            drift = max(drift, d)
    if drift > 1e-4:
        warnings.append("비루트 로컬 이동이 애니메이션 중 변함 (최대 %.4f m) — "
                        "offset 상수 가정 위반 후보 (stretchy rig?)" % drift)

    return {
        "format": "topomotion_anim/1",
        "rig": rig,
        "fps": fps,
        "time_unit": tcode,
        "frame_start": int(start),
        "frame_end": int(end),
        "num_frames": len(frames),
        "num_joints": n,
        "local_rot": local_rot,     # [T][N][9] column-vector 규약 row-major
        "local_pos": local_pos,     # [T][N][3] m (offset 드리프트 검증용)
        "world_matrices": world,    # [T][N][16] Maya row-major, 평행이동만 m
        "offset_drift_max_m": drift,
        "warnings": warnings,
    }


def export_anims(out_dir=None, roots=None, selection=None, start=None, end=None):
    """루트별로 모션 문서 저장. 반환 = 저장 경로 목록."""
    if out_dir is None:
        here = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(here, "..", "outputs", "maya_rig")
    out_dir = os.path.normpath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    roots = roots or _mre.find_roots(selection)
    if not roots:
        print("[maya_anim_export] 조인트를 찾지 못했습니다 (선택 또는 씬 확인).")
        return []
    paths = []
    for r in roots:
        doc = extract_anim(r, start=start, end=end)
        p = os.path.join(out_dir, "anim_%s.json" % _mre.safe_file_name(doc["rig"]["rig_name"]))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        paths.append(p)
        print("[maya_anim_export] %s: joints=%d frames=%d (%d..%d) fps=%s -> %s"
              % (doc["rig"]["rig_name"], doc["num_joints"], doc["num_frames"],
                 doc["frame_start"], doc["frame_end"], doc["fps"], p))
        for w in doc["warnings"]:
            print("   경고: %s" % w)
    print("[maya_anim_export] %d개 저장 완료. 검증: "
          "uv run python -m topomotion.maya_anim <파일>" % len(paths))
    return paths


if __name__ in ("__main__", "<run_path>"):
    export_anims()
