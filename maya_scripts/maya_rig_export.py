"""
maya_rig_export.py — **Maya 리그 → SkeletonSpec 리그 문서(JSON)** 추출.

방향에 주의: `maya_import.py` 는 우리 결과를 Maya 로 **내보내는** 쪽이고,
이 파일은 그 반대 — 사용자가 이미 보유한 Maya 리그를 **읽어 들이는** 쪽이다.
최종 목표("애니메이터가 자기 리그를 넣으면 모션이 나온다")의 입력 경로.

사용법 (Maya Script Editor, Python 탭):
    import runpy; runpy.run_path(r"<저장소>/maya_scripts/maya_rig_export.py")
  → 선택한 조인트(없으면 씬의 모든 루트 조인트)를 리그 문서로 저장.
    기본 저장 위치: <저장소>/outputs/maya_rig/rig_<루트이름>.json

모듈로 쓸 때:
    extract_rig(root_long)          -> dict (리그 문서)
    export_rigs(out_dir, roots)     -> [저장 경로]

**씬을 수정하지 않는다** (읽기 전용). 아티스트 작업 파일에서 그대로 돌려도
안전하도록 setAttr/parent/currentTime 등 상태 변경을 일절 하지 않는다.

기록 규약 (topomotion 쪽과 일치시켜야 하는 부분):
  - offsets  : 부모 로컬 좌표에서의 평행이동, **미터**.
  - rest_local_rot : 각 관절의 rest(=추출 시점 포즈) 로컬 회전 3x3을
    **column-vector 규약(v' = R@v)** 으로 row-major 9개. Maya 의 `.matrix` 는
    row-vector 규약이라 3x3 블록의 행이 R 의 '열'이다 (export.py
    `_world_matrices_maya` 의 역변환과 같은 규칙).
  - jointOrient 는 `.matrix` 에 이미 포함되므로 따로 합성하지 않는다.
    (분리해서 곱하려 들면 Maya 의 곱 순서 규칙에 걸려 틀리기 쉽다 —
     프로젝트 규약에 기록한 Euler 순서 사고와 같은 계열의 함정.)
  - up 축·단위는 **자동 교정하지 않고 메타데이터로 남긴다**. DCC 스모크에서
    up 축 자동 추정이 8개 중 3개 틀렸던 실측이 있어, 조용한 교정은 금지.
"""
import json
import hashlib
import os
import re

import maya.cmds as cmds

# **행렬 속성(.matrix/.worldMatrix)은 씬 UI 단위와 무관하게 항상 Maya 내부
# 단위(cm)로 나온다.** `.translate` 같은 일반 속성만 currentUnit 에 맞춰
# 변환된다 — 이 둘을 같은 규칙으로 다루면 씬이 m 단위일 때 100배가 틀린다
# (headless_rig_export_check.py case A vs C 실측: offsets 최대 차 89.1 m).
# 따라서 행렬에서 뽑은 길이는 항상 이 상수로 환산한다.
MATRIX_UNIT_TO_M = 0.01

# 씬 UI 단위 → 미터 (메타데이터·보고용. 행렬 환산에는 쓰지 않는다).
UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
             "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344}

# 3x3 블록의 행 노름이 1 에서 이만큼 벗어나면 scale/shear 로 보고 경고한다.
ORTHO_TOL = 1e-4


def _mat(attr):
    """matrix 속성 → float 16개 평탄 리스트 (getAttr 반환 형태가 버전마다 다름)."""
    v = cmds.getAttr(attr)
    while isinstance(v, (list, tuple)) and len(v) == 1:
        v = v[0]
    v = list(v)
    if len(v) != 16:
        raise RuntimeError("matrix 길이가 16 이 아님: %s -> %d" % (attr, len(v)))
    return [float(x) for x in v]


def _rot_cols(m16):
    """Maya row-major(row-vector) 4x4 → column-vector 규약 R 의 row-major 9개.
    Maya 행렬의 행 k = R 의 열 k 이므로 R[i][k] = m16[k*4+i]."""
    return [m16[k * 4 + i] for i in range(3) for k in range(3)]


def _row_norms(m16):
    out = []
    for k in range(3):
        r = m16[k * 4:k * 4 + 3]
        out.append((r[0] * r[0] + r[1] * r[1] + r[2] * r[2]) ** 0.5)
    return out


def find_roots(selection=None):
    """루트 조인트(부모가 조인트가 아닌 조인트)의 long name 목록."""
    if selection:
        sel = cmds.ls(selection, type="joint", long=True) or []
    else:
        sel = cmds.ls(selection=True, type="joint", long=True, dag=True) or []
    if sel:
        # 선택 안에서도 최상위만 남긴다 (자식까지 선택돼 있어도 리그 1개로 취급)
        roots = []
        for j in sel:
            p = cmds.listRelatives(j, parent=True, type="joint", fullPath=True)
            if not p or p[0] not in sel:
                roots.append(j)
        return roots
    return [j for j in (cmds.ls(type="joint", long=True) or [])
            if not cmds.listRelatives(j, parent=True, type="joint", fullPath=True)]


def _walk(root):
    """BFS 순서 조인트 목록 + parents. BFS 라 parents[i] < i 가 보장된다
    (numpy_fk / maya_import 의 전제)."""
    joints, parents = [root], [-1]
    head = 0
    while head < len(joints):
        j = joints[head]
        for c in (cmds.listRelatives(j, children=True, type="joint",
                                     fullPath=True) or []):
            joints.append(c)
            parents.append(head)
        head += 1
    return joints, parents


def extract_rig(root, name=None):
    """루트 조인트 하나 → 리그 문서 dict. 씬을 수정하지 않는다."""
    joints, parents = _walk(root)
    unit = cmds.currentUnit(query=True, linear=True)
    s = MATRIX_UNIT_TO_M          # 행렬은 항상 내부 단위(cm) — 상단 주석 참조
    warnings = []
    if unit not in UNIT_TO_M:
        warnings.append("알 수 없는 씬 단위 %r (행렬 환산은 영향 없음)" % unit)

    offsets, rest_local_rot, rest_world = [], [], []
    jo, ro, rot, scl, ssc = [], [], [], [], []
    for j in joints:
        L = _mat(j + ".matrix")
        W = _mat(j + ".worldMatrix[0]")
        norms = _row_norms(L)
        if max(abs(n - 1.0) for n in norms) > ORTHO_TOL:
            warnings.append("scale/shear 있음 (로컬 행렬 행 노름 %.4f/%.4f/%.4f): %s"
                            % (norms[0], norms[1], norms[2], j))
        offsets.append([L[12] * s, L[13] * s, L[14] * s])
        rest_local_rot.append(_rot_cols(L))
        # world 행렬은 평행이동만 미터로 환산 (회전 블록은 무차원)
        Wm = list(W)
        Wm[12] *= s
        Wm[13] *= s
        Wm[14] *= s
        rest_world.append(Wm)
        jo.append([float(x) for x in cmds.getAttr(j + ".jointOrient")[0]])
        rot.append([float(x) for x in cmds.getAttr(j + ".rotate")[0]])
        scl.append([float(x) for x in cmds.getAttr(j + ".scale")[0]])
        ro.append(int(cmds.getAttr(j + ".rotateOrder")))
        ssc.append(bool(cmds.getAttr(j + ".segmentScaleCompensate"))
                   if cmds.attributeQuery("segmentScaleCompensate", node=j,
                                          exists=True) else False)

    max_rot = max((max(abs(v) for v in r) for r in rot), default=0.0)
    if max_rot > 1e-6:
        warnings.append(
            "추출 시점 포즈가 rest 가 아님 (rotate 최대 %.3f도) — 현재 포즈를 "
            "rest 로 기록했다. 바인드 포즈에서 다시 뽑으려면 Go to Bind Pose 후 실행." % max_rot)
    up = cmds.upAxis(query=True, axis=True)
    if up != "y":
        warnings.append("씬 up 축이 %s — 파이프라인 정본은 Y-up. 자동 교정하지 "
                        "않았으므로 로드 쪽에서 처리할 것." % up)

    short = [j.split("|")[-1] for j in joints]
    return {
        "format": "topomotion_rig/1",
        "source": "maya",
        "maya_version": cmds.about(version=True),
        "scene": cmds.file(query=True, sceneName=True) or "(untitled)",
        "rig_name": name or short[0],
        "unit": unit,                       # 씬 UI 단위 (기록용)
        "scale_to_m": s,                    # 실제 적용한 행렬→m 배율 (항상 0.01)
        "ui_unit_scale_to_m": UNIT_TO_M.get(unit),
        "up_axis": up,
        "num_joints": len(joints),
        "names": short,
        "long_names": joints,
        "parents": parents,
        "offsets": offsets,                 # [N,3] 미터, 부모 로컬
        "rest_local_rot": rest_local_rot,   # [N,9] column-vector 규약 row-major
        "rest_world_matrices": rest_world,  # [N,16] Maya row-major, 평행이동만 미터
        "joint_attrs": {"joint_orient": jo, "rotate": rot, "scale": scl,
                        "rotate_order": ro, "segment_scale_compensate": ssc},
        "warnings": warnings,
    }


def safe_file_name(name):
    """Keep readable rig names; encode namespaces safely on Windows."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")
    if safe != name:
        safe += "_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return safe or "rig"


def export_rigs(out_dir=None, roots=None, selection=None):
    """루트별로 리그 문서 저장. 반환 = 저장 경로 목록."""
    if out_dir is None:
        try:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "outputs", "maya_rig")
        except NameError:
            raise RuntimeError(
                "out_dir 자동 결정 실패 (붙여넣기 실행이라 __file__ 없음). "
                "export_rigs(out_dir=r'...') 로 직접 지정하세요.")
    out_dir = os.path.normpath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    roots = roots or find_roots(selection)
    if not roots:
        print("[maya_rig_export] 조인트를 찾지 못했습니다 (선택 또는 씬 확인).")
        return []
    paths = []
    for r in roots:
        doc = extract_rig(r)
        p = os.path.join(out_dir, "rig_%s.json" % safe_file_name(doc["rig_name"]))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
        paths.append(p)
        print("[maya_rig_export] %s: joints=%d unit=%s up=%s -> %s"
              % (doc["rig_name"], doc["num_joints"], doc["unit"],
                 doc["up_axis"], p))
        for w in doc["warnings"]:
            print("   경고: %s" % w)
    print("[maya_rig_export] %d개 리그 저장 완료. 검증: "
          "uv run python -m topomotion.maya_rig <파일>" % len(paths))
    return paths


if __name__ in ("__main__", "<run_path>"):
    export_rigs()
