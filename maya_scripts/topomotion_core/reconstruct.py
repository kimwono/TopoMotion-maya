"""
reconstruct.py — 모션 압축·복원 (numpy + onnxruntime 전용, torch 비의존).

패널의 [모션 압축·복원 테스트] 버튼과 헤드리스 게이트 S6 이 **같은 정의**를
쓰도록 여기에 모은다 (featurize.py / roundtrip.py 와 같은 자리).

경로: 씬 리그의 애니메이션 → featurize → 동결 ONNX(E12 novq) 추론 →
      복원된 rot6d 를 입력 rot6d 와 비교 → 측지 각도(도)로 오차 보고.

측지각을 쓰는 이유: 6D MSE 는 스케일 감이 없어 오판한다 (프로젝트 규약).
여기서는 오차가 항등원 근처가 아니라 실제 크기를 갖는 영역이므로 측지각의
√증폭 함정(왕복 검증 쪽 이슈)은 적용되지 않는다 — 왕복(roundtrip.py)은 행렬
요소 차이로 판정하고, 이쪽은 측지각으로 판정한다. 목적이 다르다.
"""
import os

import numpy as np

SEQ_LEN = 32            # 모델 동결 윈도우 W (Mobjaverse 트랙)
ONNX_NAME = "e12_novq_seed0.onnx"

# 입력 각속도 해석용 눈금 — **새 기준이 아니라 등록 지표 P 의 동결 임계**다
# (train GT p99.9 °/frame, e15/e16 eval_val.json 의 p_threshold, 정의는
# gen_metrics.train_speed_threshold). 이 값을 넘는 입력은 모델의 학습 각속도
# 범위 밖이라 복원 오차를 그대로 읽으면 안 된다 — 그래서 오차 옆에 병기한다.
SPEED_THRESHOLD_DEG = 43.77364440407884


# ------------------------------------------------------------------ 경로 탐색
def find_onnx(script_dir):
    """동결 ONNX 위치. script_dir 은 **스크립트 폴더**(패널이 있는 곳)다.

    설치본:   <모듈루트>/scripts/../core/<onnx>
    개발본:   <저장소>/maya_scripts/../outputs/<onnx>
    둘 다 한 단계 위다 — library_dir() 과 같은 규약.
    """
    for cand in (os.path.join(script_dir, "..", "core", ONNX_NAME),
                 os.path.join(script_dir, "..", "outputs", ONNX_NAME)):
        p = os.path.normpath(cand)
        if os.path.exists(p):
            return p
    return None


def find_vendor(script_dir):
    """onnxruntime vendor 디렉토리 (설치본 우선). 규약은 find_onnx 와 같다."""
    for cand in (os.path.join(script_dir, "..", "core", "vendor"),
                 os.path.join(script_dir, "..", "outputs", "maya_ort_vendor")):
        p = os.path.normpath(cand)
        if os.path.isdir(p):
            return p
    return None


# ------------------------------------------------------------------ 회전 변환
def rot6d_to_matrix(r6):
    """[...,6] → [...,3,3]. 6D = R 의 **앞 두 행** (featurize 와 같은 규약).

    Gram-Schmidt: r1 = norm(a1), r2 = norm(a2 − (r1·a2)r1), r3 = r1 × r2.
    """
    a1 = np.asarray(r6[..., 0:3], dtype=np.float64)
    a2 = np.asarray(r6[..., 3:6], dtype=np.float64)
    r1 = a1 / np.maximum(np.linalg.norm(a1, axis=-1, keepdims=True), 1e-12)
    a2 = a2 - (r1 * a2).sum(-1, keepdims=True) * r1
    r2 = a2 / np.maximum(np.linalg.norm(a2, axis=-1, keepdims=True), 1e-12)
    r3 = np.cross(r1, r2)
    return np.stack([r1, r2, r3], axis=-2)


def geodesic_deg(Ra, Rb):
    """[...,3,3] 두 회전 사이 측지 각도(도). θ = arccos((tr(Raᵀ Rb) − 1)/2)."""
    tr = np.einsum("...ij,...ij->...", Ra, Rb)      # tr(Ra^T Rb)
    c = np.clip((tr - 1.0) / 2.0, -1.0, 1.0)
    return np.degrees(np.arccos(c))


# ------------------------------------------------------------------ 본체
def run(doc, onnx_path, seq_len=SEQ_LEN, start=0, session=None):
    """모션 문서(doc) 한 개를 압축·복원하고 오차를 잰다.

    doc: maya_anim_export.extract_anim 산출 (rig / local_rot / num_joints /
         num_frames). 씬은 건드리지 않는다.
    반환 dict: ok · n_joints · frames_used · mean/max/p95 측지각(도) ·
               최악 관절 이름 · 추론 시간(ms) · 입력/출력 rot6d.
    """
    import time

    from . import featurize as fz

    rig = doc["rig"]
    n, t = int(doc["num_joints"]), int(doc["num_frames"])
    if t < start + seq_len:
        return {"ok": False,
                "error": ("프레임이 부족합니다 — %d프레임 필요, %d프레임 있음 "
                          "(시작 %d)" % (seq_len, t, start))}

    x_dyn = fz.rot6d_from_local(doc["local_rot"], n, t)
    x_dyn = np.ascontiguousarray(x_dyn[:, start:start + seq_len, :])   # [N,W,6]
    x_static = fz.static_features(rig["parents"], rig["offsets"])      # [N,7]
    ei = fz.edge_index(rig["parents"])                                 # [2,E]
    batch = np.zeros(n, dtype=np.int64)

    if session is None:
        import onnxruntime as ort
        session = ort.InferenceSession(onnx_path,
                                       providers=["CPUExecutionProvider"])
    feed = {"x_dyn": x_dyn, "x_static": x_static, "edge_index": ei,
            "batch": batch}
    session.run(["rot6d"], feed)                                       # warmup
    t0 = time.perf_counter()
    out = session.run(["rot6d"], feed)[0]                              # [N,W,6]
    ms = (time.perf_counter() - t0) * 1000.0

    if out.shape != x_dyn.shape or not np.isfinite(out).all():
        return {"ok": False, "error": "출력 형상·유한성 이상: %s" % (out.shape,)}

    # 입력 각속도 — 오차를 해석하려면 입력이 학습 범위 안인지 함께 봐야 한다
    Rin = rot6d_to_matrix(x_dyn)                                   # [N,W,3,3]
    step = geodesic_deg(Rin[:, 1:], Rin[:, :-1])                   # [N,W-1]
    over = float((step > SPEED_THRESHOLD_DEG).mean())

    err = geodesic_deg(Rin, rot6d_to_matrix(out))                  # [N,W]
    per_joint = err.mean(axis=1)
    worst = int(np.argmax(per_joint))
    names = rig.get("names") or rig.get("long_names") or []
    return {
        "ok": True, "rig_name": doc.get("rig_name") or rig.get("rig_name"),
        "n_joints": n, "frames_used": seq_len, "start": start,
        "mean_deg": float(err.mean()), "max_deg": float(err.max()),
        "p95_deg": float(np.percentile(err, 95)),
        "worst_joint_index": worst,
        "worst_joint_name": (names[worst].split("|")[-1]
                             if worst < len(names) else "?"),
        "worst_joint_mean_deg": float(per_joint[worst]),
        "ms_infer": round(ms, 2), "onnx": os.path.basename(onnx_path),
        "speed_median_deg": float(np.median(step)),
        "speed_max_deg": float(step.max()),
        "speed_threshold_deg": SPEED_THRESHOLD_DEG,
        "speed_violation_rate": over,
        "in_envelope": bool(over == 0.0),
        "x_dyn": x_dyn, "rot6d_out": out,
    }


def format_report(r):
    """패널·헤드리스 공용 한 줄 요약."""
    if not r.get("ok"):
        return "복원 실패 — %s" % r.get("error", "원인 불명")
    line = ("복원 오차 평균 %.3f도 · 최대 %.3f도 · p95 %.3f도 "
            "(관절 %d · %d프레임 · 최악 관절 %s %.3f도) · 추론 %.1f ms"
            % (r["mean_deg"], r["max_deg"], r["p95_deg"], r["n_joints"],
               r["frames_used"], r["worst_joint_name"],
               r["worst_joint_mean_deg"], r["ms_infer"]))
    line += ("\n입력 각속도 중앙값 %.2f도/프레임 · 최대 %.2f도/프레임 "
             "(학습 범위 임계 %.1f)"
             % (r["speed_median_deg"], r["speed_max_deg"],
                r["speed_threshold_deg"]))
    if not r.get("in_envelope", True):
        line += ("\n⚠️ 입력의 %.2f%% 가 학습 각속도 범위를 넘는다 — 이 구간의 "
                 "복원 오차는 모델 성능이 아니라 범위 밖 입력을 반영한다."
                 % (100.0 * r["speed_violation_rate"]))
    return line
