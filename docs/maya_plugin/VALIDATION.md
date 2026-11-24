# Maya 배포 검증 기록 — 2026-10-20

대상: TopoMotion 1.0.1, Windows Maya 2027 / Python 3.13.9.
배포 ZIP: `outputs/maya_release/TopoMotion-1.0.1-Windows-Maya2027.zip` (20,252,625 bytes).
SHA-256: `b72e82c0d8b072bf05c0fbfd1dee79e090846ba7d9e33db64eafcc5f97ab4fc5`.

## 최초 배포 검사 결과

- 순수 Python 회귀 검사: **14 passed** (`tests/test_maya_playback.py`, `tests/test_maya_release.py`).
- 실제 Maya 2027 mayapy 검사: **19개 항목 통과**.
- 새 경로(공백 포함)에 ZIP을 풀어 검사. 별도 MAYA_APP_DIR을 사용해 실제 사용자 설정을 변경하지 않음.
- 샘플 15개의 문서 구조 검사, 첫 샘플의 실제 키 생성·모델 복원 검사.
- cm/m 씬 모두에서 저장→재생→재추출 행렬 최대 차이 1e-5 미만.
- namespace가 있는 리그 저장/읽기와 50fps 확인.
- 새 mayapy 프로세스에서 .mod 검색 후 플러그인 이름만으로 로드 성공.
- 사용자 매뉴얼의 목차 링크 10개 연결 및 변경 런타임 소스 문법 확인.
- 세부 결과: `outputs/maya_release/release_check.json`.

| 검사 | 결과 |
|---|---|
| zip_hashes | PASS |
| no_developer_paths | PASS |
| isolated_install | PASS |
| plugin_command | PASS |
| idempotent_install | PASS |
| all_library_documents | PASS |
| sample_playback | PASS |
| rig_roundtrip | PASS |
| offline_model_load | PASS |
| reconstruct_button | PASS |
| torch_free | PASS |
| repeat_preserves_existing | PASS |
| export_import_pose_cm | PASS |
| export_import_pose_m | PASS |
| invalid_file_no_scene_change | PASS |
| namespaced_export | PASS |
| namespaced_import_50fps | PASS |
| plugin_unload | PASS |
| restart_module_discovery | PASS |

## 검증하지 않은 범위

GUI 뷰포트 드래그 설치, 패널의 실제 시각 배치, 대화상자 클릭, Shelf 조작,
GUI 재시작 시 Auto load는 직접 조작해 검증하지 않았다.
headless에서는 Maya가 plugin preferences 저장을 생략하므로 재시작 검사는
`.mod` 검색과 명령 로드를 확인한 것이며 GUI Auto load 동작의 증거가 아니다.
매뉴얼의 브라우저 시각 점검은 도구의 localhost 연결 시간 초과로 완료하지 못했다.
다른 Windows PC 및 Maya 버전에서도 검증하지 않았다.

## 기존 사용자 작업 보존

연구/학습 코드와 기존 outputs/v1_package는 이동·삭제하지 않았다.
기존 `maya_scripts/maya_import.py`의 미커밋 OUT_DIR 설정은 그대로 두고,
배포 복사본에서만 개발 PC 경로를 제거했다. 커밋·푸시·외부 배포는 하지 않았다.

## 사용자 매뉴얼 간소화 — 2026-10-20

- 기술 구성 설명을 제거하고 설치·재생·저장 중심의 짧은 지시문으로 개정.
- 목차 8개와 내부 링크 연결 확인. ZIP 안의 매뉴얼이 소스와 일치함을 확인.
- 이전 ZIP 대비 변경 파일은 `README.txt`, `TopoMotion/docs/USER_GUIDE.html`뿐.
- 실행 코드·모델·샘플·라이브러리는 이전 검증본과 해시가 동일. Maya 검사는 재실행하지 않음.
- 새 ZIP의 전체 구성 파일 크기·SHA-256 확인.
- 위 최초 배포 ZIP의 해시는 과거 검사 대상이며, 현재 ZIP의 해시는 아래와 같음.
- 매뉴얼 간소화 시점 ZIP SHA-256: `6d8addc2acbc357100bad752e603e71d06231da988f4ae27ede7df5a5911ca2b`.
- 문서 갱신 검사 결과: `outputs/maya_release/manual_revision_check.json`.

## 1.0.2 버튼·매뉴얼 디자인 — 2026-10-20

- 버튼·대화상자·샘플 분류를 사용자 용어로 변경. 사용자 매뉴얼의 명칭 10개와 일치 확인.
- 고정 사이드 목차, 현재 절 표시, 좁은 화면의 목차 토글, hover/focus 및 펼침 표시 추가.
- Pretendard와 JetBrains Mono 및 원본 라이선스 동봉. fonts/sources.json의 공식 출처·해시 확인.
- Python 회귀 검사 14개, Maya 2027 mayapy 검사 19개 통과.
- 목차 8개 연결, JavaScript 문법, ZIP 구성 전체 해시·크기, 글꼴 동봉 확인.
- 새 결과: outputs/maya_release/release_check_1.0.2.json.
- 브라우저 도구가 file: URL을 정책상 차단하여 실제 렌더링·hover·스크롤 조작은 검증하지 못함. 우회하지 않음.
- Maya GUI의 실제 화면과 대화상자 클릭도 직접 검증하지 않음.
- ZIP: `TopoMotion-1.0.2-Windows-Maya2027.zip`
- SHA-256: `2a8c37aef410806f5ae5ade84405ee875ddd1269e509285546234b5aed03f339`

## 설치본 및 번호 이미지 가이드 — 2026-10-21

- 실제 사용자 설치를 1.0.2로 갱신. Maya 패널의 버튼 10개, 설치 확인 창의 샘플 15개·복원 검사 사용 가능 표시, 샘플 선택창을 GUI에서 확인.
- 기존 Shelf의 개발용 경로를 플러그인 명령으로 변경. 기존 씬 이름·노드·수정 상태·시간이 유지됨을 확인.
- GUI 증거: `outputs/maya_release/gui_evidence/`, 설치 정보: `outputs/maya_release/installed_gui_1.0.2.json`.
- 번호 박스 6개로 구성한 `images/panel-guide-1.0.2.png` 추가. 실제 패널 화면을 참고해 image_gen으로 재구성한 안내 그림이며, GUI 검증 증거로 사용하지 않음.
- 매뉴얼 설치 절에서 그림으로 이동 가능. 이미지 대체 텍스트, 원본 크기로 열기 링크 추가.
- 소스·배포 ZIP·현재 설치본에 동일 문서와 이미지 반영. 전체 470개 구성 파일의 크기·해시 및 설치본 일치 확인. 기존 배포본 대비 변경은 매뉴얼과 이미지뿐이며 실행 코드·모델은 동일.
- 패키지 회귀 검사 3개 통과. 검사 결과: `outputs/maya_release/panel_guide_check.json`.
- 최신 ZIP SHA-256: `cf29bf70a1465de7224ea6f66990afc9bf0c6b5e24ae448313105d1721fad54a`.
- 브라우저 시각 검증은 미완료. Firefox Windows 제어는 URL 정책 미지원으로 중단되었으며, 현재 브라우저 도구에는 Waterfox·Brave·Helium이 노출되지 않음. 열린 in-app 로컬 HTML도 URL 정책으로 차단됨. 다른 경로로 우회하지 않음.
