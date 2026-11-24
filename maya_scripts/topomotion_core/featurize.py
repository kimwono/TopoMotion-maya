"""
featurize.py — 모델 입력 피처화 (numpy 전용).

`topomotion.data.SkeletonSpec.build_static_features` / `matrix_to_rotation_6d`
와 **같은 정의**의 의도적 복제. torch 를 못 쓰는 Maya 환경이 전제다.
정의가 어긋나면 `uv run python -m topomotion.maya_ort_parity` 가 판정한다.

(v1.0.0 조립 전에는 headless_ort_check.py 안에 있었다 — 플러그인과 검사
스크립트가 같은 코드를 쓰도록 분리했다. 로직은 그대로.)
"""
import numpy as np


def static_features(parents, offsets):
    """[N,7] = offset(3) · bone_len(1) · depth_norm(1) · deg_norm(1) · is_ee(1)."""
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
    """양방향 간선 [2,E] (부모→자식, 자식→부모)."""
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
