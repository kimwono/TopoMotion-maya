"""
TopoMotion 1.0.2 artist panel: library playback, rig/animation I/O and diagnostics.

Maya entry point: cmds.topoMotionPanel() after installing the release.
Development entry: runpy.run_path('<repo>/maya_scripts/topomotion_panel.py').

Export/import functions stay in their dedicated modules. The playback adapter
validates documents and converts metre exports into Maya's internal centimetres.
Up-axis choices record metadata only; they do not silently rotate the scene.
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import maya.cmds as cmds

WINDOW = "topomotionPanel"
VERSION = "1.0.2"
AXES = ("+Y", "-Y", "+Z", "-Z", "+X", "-X")


def _here():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        raise RuntimeError(
            "붙여넣기 실행이라 __file__ 이 없습니다. 파일로 실행하세요: "
            "import runpy; runpy.run_path(r'.../topomotion_panel.py')")


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_here(), name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_mods = {}


def mod(name):
    if name not in _mods:
        _mods[name] = _load(name)
    return _mods[name]


def default_out_dir():
    return os.path.normpath(os.path.join(cmds.internalVar(userAppDir=True),
                                         "TopoMotion", "exports"))


def core():
    """numpy 전용 코어 패키지 (featurize / roundtrip). 개발 저장소·설치본 공통."""
    _core_on_path()
    import topomotion_core.roundtrip as rt
    return rt


def _core_on_path():
    if _here() not in sys.path:
        sys.path.insert(0, _here())


def core_reconstruct():
    """압축·복원 코어. vendor onnxruntime 을 sys.path 에 올린 뒤 임포트한다."""
    _core_on_path()
    import topomotion_core.reconstruct as rc
    v = rc.find_vendor(_here())
    if v and v not in sys.path:
        sys.path.insert(0, v)          # Maya 내장 numpy + vendor ort
    return rc


def library_dir():
    """모션 라이브러리 위치 — 설치본(../motions) 우선, 없으면 개발 저장소.

    설치본 레이아웃은 scripts/ 와 motions/ 가 형제다 (.mod 모듈 루트 기준).
    """
    for cand in (os.path.join(_here(), "..", "motions"),
                 os.path.join(_here(), "..", "outputs", "motion_library")):
        p = os.path.normpath(cand)
        if os.path.exists(os.path.join(p, "manifest.json")):
            return p
    return None


# ---------------------------------------------------------------- up 축 추정
def estimate_up_axis(root):
    """루트-위-중심 휴리스틱 (dcc_smoke 의 부호 규칙과 동일 발상):
    rest 포즈에서 root 가 관절 중심(centroid)에서 가장 크게 벗어난 축을
    up 후보로, 부호는 root 가 위에 있다고 가정해 정한다.
    주의: 추정일 뿐이다 — 반드시 사용자 확정을 거쳐 저장한다."""
    mre = mod("maya_rig_export")
    joints, _ = mre._walk(root)
    pos = []
    for j in joints:
        W = mre._mat(j + ".worldMatrix[0]")
        pos.append((W[12], W[13], W[14]))
    n = float(len(pos))
    cx = sum(p[0] for p in pos) / n
    cy = sum(p[1] for p in pos) / n
    cz = sum(p[2] for p in pos) / n
    d = (pos[0][0] - cx, pos[0][1] - cy, pos[0][2] - cz)
    axis = max(range(3), key=lambda i: abs(d[i]))
    sign = "+" if d[axis] >= 0 else "-"
    return sign + "XYZ"[axis]


def confirm_up_axis(rig_name, auto, scene_up):
    """확정 다이얼로그. 반환 = 사용자가 고른 축 문자열 또는 None(취소)."""
    msg = ("뼈대: %s\n\n위쪽 방향: %s\n씬의 위쪽: %s\n\n"
           "캐릭터의 위쪽 방향을 확인하세요." % (rig_name, auto, scene_up.upper()))
    r = cmds.confirmDialog(title="TopoMotion — 위쪽 방향", message=msg,
                           button=["이 방향 사용 (%s)" % auto, "다른 방향 선택", "취소"],
                           defaultButton="이 방향 사용 (%s)" % auto,
                           cancelButton="취소", dismissString="취소")
    if r == "취소":
        return None
    if r.startswith("이 방향 사용"):
        return auto
    r2 = cmds.confirmDialog(title="TopoMotion — 방향 선택",
                            message="뼈대 '%s'의 위쪽 방향 선택" % rig_name,
                            button=list(AXES) + ["취소"],
                            defaultButton=auto if auto in AXES else "+Y",
                            cancelButton="취소", dismissString="취소")
    return None if r2 == "취소" else r2


def _stamp_up_axis(path, auto, user):
    """저장된 리그/모션 JSON 에 up 축 메타데이터를 기록 (문서 형식 불변 —
    필드 추가만. topomotion/maya_rig.py 는 모르는 필드를 무시한다)."""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    tgt = doc["rig"] if doc.get("format") == "topomotion_anim/1" else doc
    tgt["up_axis_auto"] = auto
    tgt["up_axis_user"] = user
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False,
                  indent=1 if doc.get("format") == "topomotion_rig/1" else None)
    return path


# ------------------------------------------------------------------- 동작들
def do_export_rigs(interactive=True):
    """선택(없으면 씬 전체) 리그 → 리그 문서 + up 축 확정."""
    mre = mod("maya_rig_export")
    out_dir = _field_out_dir()
    roots = mre.find_roots()
    if not roots:
        cmds.warning("[TopoMotion] 뼈대의 가장 위 조인트를 선택하세요.")
        return []
    scene_up = cmds.upAxis(query=True, axis=True)
    done = []
    for root in roots:
        auto = estimate_up_axis(root)
        user = confirm_up_axis(root.split("|")[-1], auto,
                               scene_up) if interactive else auto
        if user is None:
            print("[TopoMotion] 건너뜀 (사용자 취소): %s" % root)
            continue
        paths = mre.export_rigs(out_dir=out_dir, roots=[root])
        for p in paths:
            _stamp_up_axis(p, auto, user)
            done.append(p)
            print("[TopoMotion] up 축 기록: auto=%s user=%s -> %s"
                  % (auto, user, p))
    return done


def do_export_anims(interactive=True):
    """선택(없으면 씬 전체) 애니메이션 → 모션 문서 + up 축 확정."""
    mae = mod("maya_anim_export")
    mre = mod("maya_rig_export")
    out_dir = _field_out_dir()
    roots = mre.find_roots()
    if not roots:
        cmds.warning("[TopoMotion] 뼈대의 가장 위 조인트를 선택하세요.")
        return []
    scene_up = cmds.upAxis(query=True, axis=True)
    done = []
    for root in roots:
        auto = estimate_up_axis(root)
        user = confirm_up_axis(root.split("|")[-1], auto,
                               scene_up) if interactive else auto
        if user is None:
            continue
        paths = mae.export_anims(out_dir=out_dir, roots=[root])
        for p in paths:
            _stamp_up_axis(p, auto, user)
            done.append(p)
    return done


def do_apply_motion(path=None):
    """모션 JSON(우리 export 형식) 선택 → 씬에 리그 생성·재생."""
    mi = mod("maya_import")
    if path is None:
        picked = cmds.fileDialog2(fileMode=1, dir=_field_out_dir(),
                                  caption="애니메이션 파일 선택",
                                  fileFilter="애니메이션 파일 (*.json)")
        if not picked:
            return
        path = picked[0]
    base = os.path.splitext(os.path.basename(path))[0]
    return _play_document(mi.load_doc(path), "TM_" + base.upper())


def _play_document(doc, group):
    _core_on_path()
    from topomotion_core.playback import prepare
    doc = prepare(doc)
    # The legacy importer replaces existing groups. Always use a new group.
    base = re.sub(r"[^A-Za-z0-9_]", "_", group)
    group, suffix = base, 2
    while cmds.objExists(group):
        group = "%s_%d" % (base, suffix)
        suffix += 1
    cmds.undoInfo(openChunk=True, chunkName="TopoMotion playback")
    try:
        mod("maya_import").build_and_animate(doc, group)
        fps_map = {15: "game", 24: "film", 25: "pal", 30: "ntsc",
                   48: "show", 50: "palf", 60: "ntscf"}
        cmds.currentUnit(time=fps_map.get(doc["fps"], "%gfps" % doc["fps"]),
                         updateAnimation=False)
        _clear_selection()
    finally:
        cmds.undoInfo(closeChunk=True)
    print("[TopoMotion] 새 그룹: %s (%d프레임)" % (group, doc["num_frames"]))
    return group


def _clear_selection():
    """모션 생성 후 선택을 비운다.

    `maya_import.build_and_animate` 는 joint 계층을 만들 때 `cmds.select` 로
    부모를 지정하므로 **마지막에 만든 잎 조인트가 선택된 채로 남는다.** 이후
    ①②③ 버튼은 "선택 없으면 씬 전체" 계약이라, 이 잔여 선택이 있으면 잎 조인트
    하나만 리그로 잡는다 (통합 스모크 S4 에서 관절 1개로 잡혀 발견).
    구성 과정의 부산물이지 사용자의 선택 의도가 아니므로 비운다.
    """
    cmds.select(clear=True)


def library_entries():
    """manifest → [(라벨, 절대경로)] (카테고리·이름 정렬)."""
    d = library_dir()
    if not d:
        return []
    with open(os.path.join(d, "manifest.json"), "r", encoding="utf-8") as f:
        man = json.load(f)
    categories = {"aquatic": "수중형", "avian": "조류형", "biped": "두발형",
                  "quadruped": "네발형", "hexapod": "여섯발형", "octopod": "여덟발형",
                  "serpentine": "뱀형", "others": "기타"}
    out = []
    for m in sorted(man.get("motions", []), key=lambda m: (m.get("cat", ""), m.get("id", ""))):
        label = "%s · %s   |   관절 %s개 · %s프레임" % (
            categories.get(m.get("cat"), "기타"), m.get("id", "?"),
            m.get("J", "?"), m.get("frames", "?"))
        out.append((label, os.path.join(d, m["file"])))
    return out


def do_library_motion(index=None):
    """라이브러리 모션 선택 → 씬에서 재생 (v1.0.0 주 사용 흐름)."""
    items = library_entries()
    if not items:
        cmds.warning("[TopoMotion] 샘플이 없습니다. ZIP 전체를 다시 압축 해제하세요. "
                     "")
        return None
    if index is None:                       # 배치 실행이면 index 로 직접 지정
        return show_library()
    if not 0 <= index < len(items):
        cmds.warning("[TopoMotion] 범위 밖 번호: %s" % (index + 1))
        return None
    label, path = items[index]
    mi = mod("maya_import")
    grp = "LIB_" + os.path.splitext(os.path.basename(path))[0].upper()
    doc = mi.load_doc(path)
    grp = _play_document(doc, grp)
    n = doc.get("num_frames", 0)
    if n:                                   # 재생 구간을 모션 길이에 맞춘다
        cmds.playbackOptions(minTime=0, maxTime=max(n - 1, 1),
                             animationStartTime=0, animationEndTime=max(n - 1, 1))
    _clear_selection()
    print("[TopoMotion] 라이브러리 재생: %s -> 그룹 %s (%d프레임)"
          % (label, grp, n))
    return grp


def do_roundtrip(interactive=True):
    """사용자 리그 읽기 → **규약 왕복 확인** (씬 무변경, 읽기 전용).

    판정은 행렬 요소 차이. 측지각은 참고 병기 (topomotion_core.roundtrip).
    """
    mre = mod("maya_rig_export")
    rt = core()
    roots = mre.find_roots()
    if not roots:
        cmds.warning("[TopoMotion] 뼈대의 가장 위 조인트를 선택하세요.")
        return []
    rows = []
    for root in roots:
        doc = mre.extract_rig(root)
        r = rt.verify(doc)
        r["rig_name"] = doc["rig_name"]
        r["warnings"] = doc["warnings"]
        rows.append(r)
        print("[TopoMotion] %s: %s" % (doc["rig_name"], rt.format_report(r)))
        for w in doc["warnings"]:
            print("   경고: %s" % w)
    if interactive:
        msg = "\n\n".join(
            "%s\n%s · 관절 %d개%s" % (
                r["rig_name"], "뼈대 정보 일치" if r["ok"] else "뼈대 정보 불일치",
                r["n_joints"], "\n주의 사항 %d개 — Script Editor에서 확인" % len(r["warnings"])
                if r["warnings"] else "") for r in rows)
        cmds.confirmDialog(title="TopoMotion — 뼈대 확인", message=msg, button=["확인"])
    return rows


def do_reconstruct(interactive=True, start=0):
    """선택 리그의 모션을 **동결 ONNX 로 압축·복원**하고 오차를 보고한다.

    씬 무변경 (읽기 전용 추출 → 추론 → 수치 보고). torch 를 쓰지 않는다 —
    vendor onnxruntime + Maya 내장 numpy 만 쓴다.
    판정 지표는 측지 각도(도) — 6D MSE 로 판단하지 않는다 (동결 규약).
    """
    mre = mod("maya_rig_export")
    mae = mod("maya_anim_export")
    rc = core_reconstruct()
    onnx = rc.find_onnx(_here())
    if not onnx:
        cmds.warning("[TopoMotion] 복원 검사에 필요한 파일이 없습니다. ZIP 전체를 다시 압축 해제하세요. "
                     "")
        return []
    roots = mre.find_roots()
    if not roots:
        cmds.warning("[TopoMotion] 뼈대의 가장 위 조인트를 선택하세요.")
        return []
    rows = []
    for root in roots:
        doc = mae.extract_anim(root)
        r = rc.run(doc, onnx, start=start)
        r.pop("x_dyn", None)                 # 대용량 배열은 반환에서 뺀다
        r.pop("rot6d_out", None)
        r["rig_name"] = doc["rig"]["rig_name"]
        rows.append(r)
        print("[TopoMotion] %s: %s" % (r["rig_name"], rc.format_report(r)))
    if interactive:
        summaries = []
        for r in rows:
            if not r.get("ok"):
                summaries.append("%s\n%s" % (r["rig_name"], r.get("error", "검사 실패")))
                continue
            text = ("%s · %d프레임\n회전 차이: 평균 %.2f° / 최대 %.2f°\n"
                    "차이가 가장 큰 관절: %s" % (r["rig_name"], r["frames_used"],
                    r["mean_deg"], r["max_deg"], r["worst_joint_name"]))
            if not r.get("in_envelope", True):
                text += "\n빠른 회전 포함 — 다른 구간에서도 확인하세요."
            summaries.append(text)
        cmds.confirmDialog(title="TopoMotion — 복원 결과", message="\n\n".join(summaries),
                           button=["확인"])
    return rows


def do_load_outputs():
    """outputs/ 의 등록 패턴 전부 로드 (기존 run_import 그대로)."""
    mi = mod("maya_import")
    mi.run_import()


def install_shelf():
    """현재 shelf 에 패널 버튼 설치."""
    import maya.mel as mel
    top = cmds.tabLayout(mel.eval("$tmp = $gShelfTopLevel"),
                         query=True, selectTab=True)
    for child in cmds.shelfLayout(top, query=True, childArray=True) or []:
        if cmds.objectTypeUI(child) == "shelfButton" and cmds.shelfButton(
                child, query=True, annotation=True) == "TopoMotion 패널":
            return child
    cmd = ("import maya.cmds as cmds\n"
           "if not cmds.pluginInfo('topomotion_plugin', query=True, loaded=True):\n"
           "    cmds.loadPlugin('topomotion_plugin.py')\n"
           "cmds.topoMotionPanel()")
    b = cmds.shelfButton(parent=top, label="TopoMotion", annotation="TopoMotion 패널",
                         imageOverlayLabel="TopoM", image="commandButton.png",
                         command=cmd, sourceType="python")
    print("[TopoMotion] shelf 버튼 설치: %s" % b)
    return b


# ----------------------------------------------------------------------- UI
def _field_out_dir():
    if cmds.window(WINDOW, exists=True) and cmds.textField("tmOutDir", exists=True):
        v = cmds.textField("tmOutDir", query=True, text=True).strip()
        if v:
            return v
    return default_out_dir()


def _ui_call(action):
    """Display actionable errors; keep detailed traces in the Script Editor."""
    try:
        result = action()
        if isinstance(result, list) and result and isinstance(result[0], str):
            cmds.confirmDialog(title="TopoMotion — 저장 완료",
                               message="\n".join(result), button=["확인"])
        return result
    except Exception as exc:
        import traceback
        traceback.print_exc()
        cmds.confirmDialog(title="TopoMotion — 실행 확인",
                           message="%s\n\n[설치 확인]과 [사용 매뉴얼]를 확인하세요."
                                   % exc, button=["확인"])
        return None


def browse_output():
    chosen = cmds.fileDialog2(fileMode=3, caption="저장 폴더 선택",
                              startingDirectory=_field_out_dir())
    if chosen:
        cmds.textField("tmOutDir", edit=True, text=chosen[0])


def open_manual():
    candidates = [Path(_here()).parent / "docs" / "USER_GUIDE.html",
                  Path(_here()).parent / "docs" / "maya_plugin" / "USER_GUIDE.html"]
    for path in candidates:
        if path.is_file():
            import webbrowser
            webbrowser.open(path.resolve().as_uri())
            return
    raise RuntimeError("설명서가 없습니다. 전체 배포 ZIP을 다시 압축 해제하세요.")


def installation_report(interactive=True):
    rc = core_reconstruct()
    rows = ["TopoMotion " + VERSION, "Maya " + cmds.about(version=True),
            "Python " + sys.version.split()[0], "플러그인: " + _here(),
            "저장 폴더: " + _field_out_dir(),
            "라이브러리: %d개" % len(library_entries())]
    ready = False
    try:
        import numpy
        import onnxruntime as ort
        onnx = rc.find_onnx(_here())
        if not onnx:
            raise RuntimeError("모델 파일 없음")
        ort.InferenceSession(onnx, providers=["CPUExecutionProvider"])
        ready = True
        rows += ["NumPy " + numpy.__version__, "ONNX Runtime " + ort.__version__,
                 "모델 로드: 정상 (CPU)"]
    except Exception as exc:
        rows.append("모델 로드: 실패 — %s" % exc)
    report = "\n".join(rows)
    print(report)
    if interactive:
        cmds.confirmDialog(title="TopoMotion — 설치 확인",
                           message="샘플: %d개\n복원 검사: %s\n\n저장 위치\n%s" % (
                               len(library_entries()), "사용 가능" if ready else "사용 불가 — 재설치 필요",
                               _field_out_dir()),
                           button=["확인"])
    return report


def show_library():
    win = "topomotionLibrary"
    if cmds.window(win, exists=True):
        cmds.deleteUI(win)
    cmds.window(win, title="TopoMotion — 샘플 선택", widthHeight=(620, 340))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8)
    cmds.text(label="샘플 선택 → 뼈대 만들기 → ▶ 재생",
              align="left")
    items = library_entries()
    listing = cmds.textScrollList(append=[label for label, _ in items],
                                  height=250, allowMultiSelection=False,
                                  selectIndexedItem=1)

    def play(*_):
        selected = cmds.textScrollList(listing, query=True,
                                       selectIndexedItem=True) or []
        if selected:
            _ui_call(lambda: do_library_motion(index=selected[0] - 1))

    cmds.button(label="뼈대 만들기", command=play)
    cmds.showWindow(win)
    return win


def show():
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    w = cmds.window(WINDOW, title="TopoMotion v" + VERSION, widthHeight=(450, 530),
                    sizeable=True)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6, columnOffset=("both", 8))
    cmds.text(label="TopoMotion", align="left",
              font="boldLabelFont")
    cmds.text(label="① 샘플 불러오기 → 뼈대 만들기 → ▶ 재생", align="left")
    cmds.button(label="사용 매뉴얼",
                command=lambda *_: _ui_call(open_manual))
    cmds.button(label="설치 확인", command=lambda *_: _ui_call(installation_report))
    cmds.separator(height=4, style="in")
    cmds.text(label="저장 위치", align="left")
    cmds.textField("tmOutDir", text=default_out_dir())
    cmds.button(label="저장 위치 변경", command=lambda *_: _ui_call(browse_output))
    cmds.separator(height=4, style="in")
    lib = library_dir()
    cmds.button(label="① 샘플 불러오기 (%s)"
                      % ("%d개" % len(library_entries()) if lib else "없음"),
                enable=bool(lib), command=lambda *_: _ui_call(do_library_motion))
    cmds.button(label="② 뼈대 확인",
                command=lambda *_: _ui_call(do_roundtrip))
    cmds.button(label="③ 뼈대 저장",
                command=lambda *_: _ui_call(do_export_rigs))
    cmds.button(label="④ 애니메이션 저장",
                command=lambda *_: _ui_call(do_export_anims))
    cmds.button(label="⑤ 애니메이션 열기",
                command=lambda *_: _ui_call(do_apply_motion))
    cmds.button(label="⑥ 복원 결과 확인",
                command=lambda *_: _ui_call(do_reconstruct))
    cmds.separator(height=4, style="in")
    cmds.button(label="Shelf에 바로가기 추가", command=lambda *_: _ui_call(install_shelf))
    cmds.text(label="②③④⑥: 가장 위 조인트 선택 · 선택이 없으면 씬 전체",
              align="left", font="smallPlainLabelFont")
    cmds.showWindow(w)
    return w


if __name__ in ("__main__", "<run_path>"):
    show()
