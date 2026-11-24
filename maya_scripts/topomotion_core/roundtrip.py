"""
roundtrip.py — 리그 문서 규약 왕복 검증 (numpy 전용, Maya 안에서 실행 가능).

검증 내용: 리그 문서의 `parents` + `offsets` + `rest_local_rot` 만으로 FK 를
다시 돌려 world 행렬을 복원하고, 문서에 함께 저장된 Maya 의
`rest_world_matrices` 와 비교한다. 즉 **"우리가 읽은 규약대로 되돌리면 Maya 가
보여주던 그 포즈가 나오는가"** 를 본다.

⚠️ 판정은 **행렬 요소 차이**로 한다 (프로젝트 규약).
   측지각은 항등원 근처에서 θ ≈ √(2δ) 로 증폭되어, 요소 오차 1.7e-8 이
   0.011도로 보인다 — 정상 리그를 불일치로 오판했던 곳이다. 측지각은 참고 병기.

루트는 기준점으로 고정한다 (문서의 rest_world 를 그대로 사용). 그래야 상위
그룹 변환이 섞이지 않고 **규약 왕복만** 분리해서 잰다.
"""
import numpy as np

# 요소 차이 판정 임계 (m 및 무차원). Maya 자체 합성이 1.8e-15 수준이므로
# 1e-6 은 충분히 느슨하면서 진짜 규약 오류(1e-2 이상)를 확실히 잡는다.
TOL_ELEMENT = 1e-6


def _maya_to_Rt(m16):
    """Maya row-major 4x4 (평행이동이 마지막 **행**) → (R[3,3], t[3]).

    Maya 행렬의 행 k = column-vector 규약 R 의 열 k → R[i][k] = m16[k*4+i].
    """
    m = np.asarray(m16, dtype=np.float64)
    R = np.array([[m[k * 4 + i] for k in range(3)] for i in range(3)])
    return R, m[12:15].copy()


def verify(doc):
    """리그 문서 → 왕복 검증 결과 dict. 씬·문서를 수정하지 않는다."""
    parents = list(doc["parents"])
    n = int(doc["num_joints"])
    off = np.asarray(doc["offsets"], dtype=np.float64).reshape(n, 3)
    Rl = np.asarray(doc["rest_local_rot"], dtype=np.float64).reshape(n, 3, 3)

    Rw_ref = np.zeros((n, 3, 3))
    tw_ref = np.zeros((n, 3))
    for i, m16 in enumerate(doc["rest_world_matrices"]):
        Rw_ref[i], tw_ref[i] = _maya_to_Rt(m16)

    Rw = np.zeros_like(Rw_ref)
    tw = np.zeros_like(tw_ref)
    for i in range(n):
        p = parents[i]
        if p < 0:                      # 루트 = 기준점 (상위 그룹 변환 배제)
            Rw[i], tw[i] = Rw_ref[i], tw_ref[i]
            continue
        if p >= i:
            raise ValueError("parents[%d]=%d — BFS 순서(parents[i]<i) 위반" % (i, p))
        Rw[i] = Rw[p] @ Rl[i]
        tw[i] = tw[p] + Rw[p] @ off[i]

    d_rot = float(np.abs(Rw - Rw_ref).max())
    d_pos = float(np.abs(tw - tw_ref).max())
    worst = int(np.abs(tw - tw_ref).max(axis=1).argmax())

    # 참고 병기 (판정 기준 아님 — 상단 주석 참조)
    tr = np.clip(((Rw @ np.transpose(Rw_ref, (0, 2, 1))).trace(axis1=1, axis2=2)
                  - 1.0) / 2.0, -1.0, 1.0)
    geo = np.degrees(np.arccos(tr))

    ok = d_rot <= TOL_ELEMENT and d_pos <= TOL_ELEMENT
    return {
        "ok": bool(ok),
        "n_joints": n,
        "max_rot_element_diff": d_rot,
        "max_pos_diff_m": d_pos,
        "tolerance": TOL_ELEMENT,
        "worst_joint_index": worst,
        "worst_joint_name": (doc.get("names") or [None] * n)[worst],
        "geodesic_deg_max_reference_only": float(geo.max()),
        "note": "판정은 행렬 요소 차이. 측지각은 항등원 근처 √증폭 탓에 "
                "참고 병기만 (프로젝트 규약).",
    }


def format_report(r):
    """패널·헤드리스 공용 한 줄 요약."""
    return ("왕복 %s — 관절 %d · 회전 요소차 %.3e · 위치차 %.3e m "
            "(임계 %.0e, 최악 관절 %s) · 측지각(참고) %.4f도"
            % ("일치" if r["ok"] else "불일치", r["n_joints"],
               r["max_rot_element_diff"], r["max_pos_diff_m"], r["tolerance"],
               r["worst_joint_name"], r["geodesic_deg_max_reference_only"]))
