"""Validate JSON before scene edits; adapt metre exports to Maya matrices."""
import math
import re

def prepare(doc):
    if not isinstance(doc, dict):
        raise ValueError("TopoMotion 모션 JSON 파일을 선택하세요.")
    kind = doc.get("format")
    if kind == "topomotion_rig/1":
        raise ValueError("rig_ 파일은 정지 리그 정보입니다. ④에서 저장한 anim_ 파일을 선택하세요.")
    if kind not in (None, "topomotion_anim/1"):
        raise ValueError("지원하지 않는 JSON 형식: %s" % kind)
    rig = doc.get("rig", {}) if kind == "topomotion_anim/1" else doc
    try:
        n, frames = doc["num_joints"], doc["num_frames"]
        names, parents = rig["names"], rig["parents"]
        matrices = doc["world_matrices"]
    except (KeyError, TypeError) as exc:
        raise ValueError("모션 정보가 없는 JSON입니다. anim_ 또는 motion_ 파일을 선택하세요.") from exc
    if (type(n) is not int or type(frames) is not int or n < 1 or frames < 1
            or len(names) != n or len(parents) != n or len(matrices) != frames):
        raise ValueError("관절 수 또는 프레임 수가 파일 내용과 맞지 않습니다.")
    if any(type(p) is not int or p < -1 or p >= n for p in parents):
        raise ValueError("잘못된 부모 관절 번호입니다.")
    for i, p in enumerate(parents):
        seen = {i}
        while p >= 0:
            if p in seen:
                raise ValueError("관절 계층에 순환이 있습니다.")
            seen.add(p)
            p = parents[p]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("관절 이름이 비어 있습니다.")
    factor = 1.
    if kind == "topomotion_anim/1":
        # xform(matrix=...) accepts internal centimetres, even when the UI's
        # linear unit is metres. Verified with Maya 2027, not scalar translate.
        factor = 100.
    converted = []
    for frame in matrices:
        if len(frame) != n:
            raise ValueError("프레임별 관절 수가 일정하지 않습니다.")
        row = []
        for matrix in frame:
            if len(matrix) != 16 or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in matrix):
                raise ValueError("모션 행렬은 유한한 숫자 16개여야 합니다.")
            m = list(matrix)
            m[12:15] = [v * factor for v in m[12:15]]
            row.append(m)
        converted.append(row)
    fps = doc.get("fps", 30)
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("잘못된 FPS 값입니다.")
    # New joints do not require the source scene's namespaces to exist.
    safe_names, used = [], set()
    for name in names:
        base = re.sub(r"[^A-Za-z0-9_]", "_", name)
        safe, suffix = base, 2
        while safe in used:
            safe = "%s_%d" % (base, suffix)
            suffix += 1
        used.add(safe)
        safe_names.append(safe)
    return dict(names=safe_names, parents=parents, num_joints=n, num_frames=frames,
                fps=fps, world_matrices=converted)
