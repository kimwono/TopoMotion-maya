"""
maya_import.py  —  Maya 2025+ 내부에서 실행하는 스크립트 (Python 3).

사용법:
  1) topomotion/export.py 등으로 outputs/ 에 JSON 을 만들어 둔다.
  2) Maya > Script Editor > Python 탭에서 이 "파일"을 실행 (__file__ 이 잡혀
     outputs/ 위치가 자동 결정된다):
       import runpy; runpy.run_path(r"<저장소>/maya_scripts/maya_import.py")
     내용을 통째로 붙여넣어 실행할 경우엔 __file__ 이 없으므로
     아래 OUT_DIR 에 outputs 절대 경로를 직접 넣을 것.
  3) 실행. 타임라인에 스켈레톤 애니메이션이 생성됨. 재생 버튼으로 확인.

동작 원리:
  export.py 가 각 관절의 world 4x4 행렬(프레임별)을 저장했다.
  여기서는 joint 계층을 만든 뒤, 프레임마다 각 joint 의 world 행렬을 xform 으로 직접 세팅.
  => Maya 의 jointOrient / rotation order 규칙에 의존하지 않음. 보이는 것 = FK 출력.

의존성 없음: maya.cmds + 표준 json 만 사용.
"""
import json
import os
import maya.cmds as cmds

# ===== 여기만 수정 =====
# 출력 JSON 폴더. None 이면 이 파일 위치 기준 ../outputs (저장소의 outputs/) 자동 사용.
# 주의: Script Editor 에 내용을 "붙여넣기"로 실행하면 __file__ 이 없어 자동 결정이
# 안 되므로, 그 경우엔 여기에 절대 경로를 직접 넣을 것.
OUT_DIR = None

if OUT_DIR is None:
    try:
        OUT_DIR = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs"))
    except NameError:
        raise RuntimeError(
            "OUT_DIR 자동 결정 실패: 붙여넣기 실행이라 __file__ 이 없습니다. "
            "스크립트 상단의 OUT_DIR 에 outputs 폴더 절대 경로를 직접 넣어 주세요.")

# (JSON 파일명, 그룹 이름) 목록. 여러 개를 한 번에 올려 나란히 비교한다.
#   gt   = FBX 원본 (파서/좌표계 검증용)
#   pred = 학습된 모델 출력
# export 시 캐릭터가 +Z(Front 뷰 카메라 쪽)를 보도록 yaw -90도 회전되고,
# 리그 간 간격은 X 축 → Front 직교 뷰에서 정면을 본 채 좌우로 나란히 선다.
IMPORTS = [
    ("gt.json",   "GT"),
    ("pred.json", "PRED"),
]

# body_types.py 의 체형 변형(body_*.json)과 topology_variants.py 의 토폴로지
# 변형(topo_*.json)은 실행부에서 자동으로 전부 추가된다.
#   예: body_00_adult_male.json -> 그룹 BODY_00_ADULT_MALE
#       topo_03_simple_spine.json -> 그룹 TOPO_03_SIMPLE_SPINE
IMPORT_BODY_VARIANTS = True
IMPORT_TOPO_VARIANTS = True
# DCC 스모크 (docs/dcc_smoke_plan.md): outputs/mobja_dcc/mobja_*_{gt,pred}.json
# 자동 로드. gt/pred 는 export 시 이미 X 축으로 나란히 배치돼 있다 (world_offset).
# 주의: 같은 폴더의 mobja_meta.json 은 리그 문서가 아니라 메타데이터이므로
# 패턴에 걸리면 안 된다 (걸리면 로드가 중간에 죽는다).
IMPORT_MOBJA_DCC = True

BONE_RADIUS = 0.03      # joint 표시 크기
# =======================


def load_doc(path):
    # encoding 명시 필수: Windows 한국어 로캘의 기본 인코딩은 cp949 라
    # 한글 주석이 든 UTF-8 JSON 을 열면 UnicodeDecodeError 로 죽는다.
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_and_animate(doc, GROUP_NAME):
    names = doc["names"]
    parents = doc["parents"]
    n = doc["num_joints"]
    frames = doc["num_frames"]
    fps = doc.get("fps", 30)
    Ms = doc["world_matrices"]      # [T][N][16]

    # 씬 프레임레이트 맞추기 (mismatch 시 타이밍 왜곡 방지)
    fps_map = {24: "film", 25: "pal", 30: "ntsc", 48: "show", 60: "ntscf"}
    if fps in fps_map:
        cmds.currentUnit(time=fps_map[fps])

    # 기존 리그 정리
    if cmds.objExists(GROUP_NAME):
        cmds.delete(GROUP_NAME)
    grp = cmds.ls(cmds.group(em=True, name=GROUP_NAME), long=True)[0]

    # --- 1) joint 계층 생성 (부모부터: parents[i] < i 가정, depth 순서면 안전) ---
    # 이름 충돌 주의: 여러 리그를 한 씬에 올리면 짧은 이름이 겹친다
    # (Mobjaverse 는 관절명이 j0, j1... 이라 16개 로드 시 tm_j0 이 16개).
    # Maya 는 부모가 다르면 같은 짧은 이름을 허용하지만, 그 이름으로
    # select/xform 하면 "More than one object matches name" 로 실패한다.
    # → 생성 직후 **전체 경로(long name)** 를 받아 이후 전부 그 경로로 조작하고,
    #   root 도 처음부터 그룹 아래에 만든다 (나중에 parent 하면 경로가 바뀜).
    joints = [None] * n
    order = sorted(range(n), key=lambda i: _depth(i, parents))
    for i in order:
        cmds.select(clear=True)
        if parents[i] >= 0 and joints[parents[i]] is not None:
            cmds.select(joints[parents[i]])
        else:
            cmds.select(grp)
        cmds.joint(name=f"tm_{names[i]}", radius=BONE_RADIUS)
        j = cmds.ls(selection=True, long=True)[0]
        joints[i] = j
        # jointOrient 를 0 으로 => world 행렬 세팅이 깔끔하게 매핑됨
        cmds.setAttr(j + ".jointOrientX", 0)
        cmds.setAttr(j + ".jointOrientY", 0)
        cmds.setAttr(j + ".jointOrientZ", 0)

    # --- 2) 프레임마다 world 행렬을 xform 으로 세팅 + 키프레임 ---
    cmds.playbackOptions(min=0, max=frames - 1, ast=0, aet=frames - 1)
    for t in range(frames):
        cmds.currentTime(t)
        # 부모부터 세팅해야 자식 world 계산이 맞음
        for i in order:
            cmds.xform(joints[i], worldSpace=True, matrix=Ms[t][i])
        for i in range(n):
            cmds.setKeyframe(joints[i],
                             attribute=["translateX", "translateY", "translateZ",
                                        "rotateX", "rotateY", "rotateZ"])

    # --- 3) Euler 필터 (몸 전체가 한 바퀴 구르는 버그 수정) ---
    # xform 은 행렬을 프레임마다 독립적으로 Euler 로 분해하므로, 회전이 ±180 경계를
    # 넘거나 gimbal(Hips 는 Y≈90) 근처를 지나면 인접 키가 ~360도 점프한다.
    # world 행렬 자체는 연속이어도 rotate 채널 키가 점프하면 재생 시
    # 키 사이 보간이 먼 길로 돌아 몸통이 통째로 구른다. → 키 완성 후 연속화.
    rot_curves = []
    for j in joints:
        for ch in ("rotateX", "rotateY", "rotateZ"):
            rot_curves += cmds.listConnections(j + "." + ch, type="animCurve") or []
    if rot_curves:
        cmds.filterCurve(rot_curves, filter="euler")
    cmds.currentTime(0)
    print(f"[maya_import] rig '{GROUP_NAME}' 생성 완료: joints={n} frames={frames} fps={fps}")
    # 실제 생성된 노드의 **전체 경로** (Maya 가 '.' 등 금지 문자를 바꾸고,
    # 여러 리그를 올리면 짧은 이름이 겹치므로 names 로 재구성하면 안 된다)
    # — headless 검증 등 재사용용
    return joints


def _depth(i, parents):
    d, p = 0, parents[i]
    while p >= 0:
        d += 1
        p = parents[p]
    return d


def run_import(out_dir=None, imports=None):
    """등록된 패턴 전부(또는 imports 지정분만) 로드. headless(mayapy) 재사용용."""
    import glob
    out_dir = out_dir or OUT_DIR
    if imports is None:
        imports = list(IMPORTS)
        patterns = ((["body_*.json"] if IMPORT_BODY_VARIANTS else [])
                    + (["topo_*.json"] if IMPORT_TOPO_VARIANTS else [])
                    + ([os.path.join("mobja_dcc", "mobja_*_gt.json"),
                        os.path.join("mobja_dcc", "mobja_*_pred.json")]
                       if IMPORT_MOBJA_DCC else []))
        for pat in patterns:
            for p in sorted(glob.glob(os.path.join(out_dir, pat))):
                base = os.path.splitext(os.path.basename(p))[0]  # body_00_adult_male
                imports.append((os.path.relpath(p, out_dir), base.upper()))
    for fname, group in imports:
        path = os.path.join(out_dir, fname)
        if not os.path.exists(path):
            print(f"[maya_import] 건너뜀 (파일 없음): {path}")
            continue
        build_and_animate(load_doc(path), group)
    print("[maya_import] 완료. 타임라인 재생으로 확인하세요.")


# Maya Script Editor 실행(붙여넣기="__main__", runpy="<run_path>")에서만 자동 실행.
# mayapy 등에서 모듈로 import 하면 자동 실행 없이 함수만 노출된다.
if __name__ in ("__main__", "<run_path>"):
    run_import()
