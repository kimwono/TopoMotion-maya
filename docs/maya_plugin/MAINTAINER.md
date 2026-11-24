# TopoMotion Maya 배포 관리

사용자 진입점은 `USER_GUIDE.html`이다. 일반 사용자에게 저장소 전체나 연구용 Python 환경을 설치하도록 안내하지 않는다.

## 현재 배포 범위

- 버전: 1.0.2, Windows x64 / Maya 2027 / Maya Python 3.13.
- 동결 모델: E12 novq seed0 ONNX + external data. E13p나 텍스트 생성 실험의 모델로 임의 교체하지 않는다.
- 추론: CPU ONNX Runtime. NumPy는 Maya 내장본을 사용하며 vendor에서 제외한다.
- 샘플: `outputs/motion_library/manifest.json`에 등록된 15개. 원본 모션 출처 표기와 vendor 라이선스를 유지한다.
- 미지원: 텍스트 생성, 교차 리그 자동 전이, 메시·스킨·컨트롤러 복원.
- 사용자 모델 설치·학습·인터넷·PyTorch 불필요.

## 소스 역할

| 경로 (저장소 기준) | 역할 |
|---|---|
| `maya_scripts/install_topomotion.py` | drag/drop 설치. 실제 Maya 사용자 폴더의 버전별 modules에 등록 |
| `maya_scripts/topomotion_plugin.py` | 명령·메뉴 등록 및 해제 |
| `maya_scripts/topomotion_panel.py` | 사용자 화면, 샘플 선택, 저장 경로, 오류·도움말·설치 진단 |
| `maya_scripts/maya_rig_export.py` | 현재 리그 구조 읽기 |
| `maya_scripts/maya_anim_export.py` | playback 구간 읽기. 행렬 이동은 미터로 기록 |
| `maya_scripts/maya_import.py` | 기존 world matrix → 새 joint/keyframe 처리 |
| `maya_scripts/topomotion_core/playback.py` | 입력 검증, anim 문서 → 기존 importer 형식, 미터 → Maya 내부 cm |
| `maya_scripts/topomotion_core/roundtrip.py` | 리그 왕복 수치 확인 |
| `maya_scripts/topomotion_core/reconstruct.py` | 32프레임 ONNX 추론·오차 보고 |
| `topomotion/package_maya.py` | 사용자 ZIP 작성. stdlib, 네트워크·학습 불필요 |
| `topomotion/v1_package.py` | 과거 연구 스모크용 조립기. 새 사용자 배포는 package_maya 사용 |
| `topomotion/model.py` 등 | 연구·학습 코드. 사용자 배포 의존성 아님 |

기존 `maya_import.py`의 로컬 OUT_DIR 변경은 작업자 설정으로 남겨 둔다.
배포 복사본은 `_copy_sanitized`가 OUT_DIR을 None으로 치환한다.
기존 `outputs/v1_package`와 연구 결과를 이동·삭제하지 않는다.

## 배포 빌드

저장소 루트에서 기존 환경으로 실행:

```powershell
uv run python -m topomotion.package_maya
```

표준 Python으로도 가능: `python -m topomotion.package_maya`.
사전 산출물은 아래 위치에 있어야 하며 누락되면 빌드를 실패시킨다.

- `outputs/e12_novq_seed0.onnx`, `outputs/e12_novq_seed0.onnx.data`
- `outputs/maya_ort_vendor/` (Windows cp313 wheel, vendor 메타데이터·라이선스 포함)
- `outputs/motion_library/` (manifest, README, 등록된 모션)

산출물: `outputs/maya_release/TopoMotion-1.0.2-Windows-Maya2027.zip` 및 `.zip.sha256`.
매번 `build-...` staging 폴더를 새로 만들므로 이전 버전의 파일이 섞이지 않는다.
같은 버전 ZIP 경로는 새 빌드로 교체되며, 이전 staging은 남는다.
ZIP에는 절대 설치 경로가 없다. installer가 수신 PC에서 `.mod`를 만든다.
`package_manifest.json`은 자기 자신을 제외한 모든 파일의 SHA-256과 크기를 기록한다.
릴리스 ZIP은 outputs 아래의 생성 산출물로 Git 추적 대상이 아니다.

## 검증

```powershell
uv run python -m pytest tests/test_maya_playback.py tests/test_maya_release.py -q
& "C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe" maya_scripts/headless_release_check.py --zip outputs/maya_release/TopoMotion-1.0.2-Windows-Maya2027.zip
```

검증기는 새 임시 경로에 ZIP을 풀고 manifest 해시, 별도 Maya 사용자 폴더에 설치,
플러그인 로드/해제, 15개 샘플 구조, 샘플 재생, 리그 왕복, 모델 추론,
내보낸 애니메이션 다시 읽기와 단위·포즈 일치, 중복 그룹 보존을 확인한다.
사용자의 실제 Maya 설정은 수정하지 않는다.
headless 결과는 `outputs/maya_release/release_check.json`에 저장된다.

headless 검사는 GUI 조작 자체를 보증하지 않는다. 전달 전 Windows Maya GUI에서
설치 파일 끌어 놓기, 메뉴/패널 표시, 매뉴얼 열기, 샘플 선택, F/재생, 축 선택 취소,
Shelf 설치, 재시작 후 Auto load를 수동 확인한다. 실행하지 않은 항목은 검증 완료로 표기하지 않는다.

## 데이터 규약과 제한

- library/기존 world JSON: 기존 표시 좌표를 그대로 사용. 임의로 100배 변환하지 않는다.
- `topomotion_anim/1`: names/parents는 rig 하위, world matrix 이동은 미터.
  `playback.prepare`가 내부 cm로 환산한다. xform의 matrix 인자는 UI 길이 단위와 무관하게 내부 cm를 받는다 (Maya 2027 실측). 원본 JSON을 변경하지 않는다.
- `topomotion_rig/1`: 애니메이션이 아니므로 재생을 거부한다.
- 입력 크기/부모 계층 순환/NaN 등을 씬 변경 전에 검사한다.
- 새 그룹 이름은 중복 시 suffix를 붙여 이전 그룹을 보존한다.
- ①⑤는 씬 시간 단위와 재생 범위를 변경한다. 내보낸 구간은 0부터 재생한다.
- ③④는 같은 리그 이름의 파일을 덮어쓴다. namespace 등 파일명 금지 문자는 치환하고 원래 이름의 해시를 덧붙여 충돌을 피한다. 다시 여는 조인트 이름도 namespace 없는 고유 이름으로 변환한다.
- up 축 선택은 메타데이터 기록이며 포즈 교정이 아니다.
- 복잡한 scale/shear/stretch와 joint 사이 transform 계층은 제한이 있다.

## 변경 시 함께 갱신

버튼 이름·순서·기능을 바꾸면 사용자 HTML의 버튼 표와 실습을 갱신한다.
버전은 plugin, panel, installer, package_maya와 문서에서 함께 변경한다.
Maya 버전을 늘리려면 해당 Maya Python ABI에 맞는 vendor와 실제 mayapy 검증이 먼저다.
라이선스 문서는 동봉된 공급자 원본을 보존하며 프로젝트 코드 라이선스를 임의 부여하지 않는다.

## 1.0.2 화면·매뉴얼

- 패널과 사용자 매뉴얼의 버튼 이름을 동일하게 유지: 샘플 불러오기, 뼈대 확인/저장, 애니메이션 저장/열기, 복원 결과 확인.
- 사용자 대화상자는 짧은 요약, Script Editor는 상세 수치를 유지.
- 매뉴얼 목차: 넓은 화면에서 왼쪽 고정, 좁은 화면에서 오른쪽 목차 버튼으로 열기. 현재 절 표시, Esc/바깥 클릭 닫기 지원.
- 펼침 항목: hover/focus 표시, +/− 상태 표시. 모션 감소 설정 지원.
- 본문 Pretendard 400, 제목·강조 700–750, 파일명 JetBrains Mono 400. 글꼴은 docs/fonts에 동봉.
- 폰트 공식 출처 및 SHA-256: fonts/sources.json. OFL 라이선스 파일을 글꼴과 함께 보존.
