"""
topomotion_core — 플러그인 v1.0.0 의 **numpy 전용 코어**.

Maya 파이썬 안에서 torch 없이 도는 부분만 모은다 (docs/v1_architecture.md A안).
maya.cmds 에도 의존하지 않으므로 uv 쪽에서 그대로 임포트해 대조할 수 있다.

  featurize.py  모델 입력 피처화 (x_static 7채널 / edge_index / rot6d)
  roundtrip.py  리그 문서 규약 왕복 검증 (parents+offsets+rest_local_rot → world)

**학습 경로(topomotion/) 코드는 건드리지 않는다.** 피처화는 정본
(topomotion.data)의 의도적 복제이며, 정의가 어긋나면
`uv run python -m topomotion.maya_ort_parity` 가 빨간불을 낸다.
"""
__version__ = "1.0.0"
