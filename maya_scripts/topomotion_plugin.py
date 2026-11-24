"""
topomotion_plugin.py — TopoMotion v1.0.2 Maya 플러그인 진입점.

`.mod` 로 설치하면 Plug-in Manager 에 뜨고, 로드하면 명령
`topoMotionPanel` 이 등록된다. 메뉴 항목도 함께 만든다.

  로드:   cmds.loadPlugin("topomotion_plugin.py")
  패널:   cmds.topoMotionPanel()
  언로드: cmds.unloadPlugin("topomotion_plugin")

**로직은 이 파일에 두지 않는다** — 전부 scripts/topomotion_panel.py 호출.
플러그인은 등록/해제와 메뉴만 담당한다 (신규 로직 최소화 원칙).
"""
import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

VENDOR = "TopoMotion"
VERSION = "1.0.2"
MENU = "topoMotionMenu"


def maya_useNewAPI():
    """Maya Python API 2.0 사용 선언 (내용 없음 — 존재 자체가 신호)."""


class TopoMotionPanelCmd(om.MPxCommand):
    kName = "topoMotionPanel"

    @staticmethod
    def creator():
        return TopoMotionPanelCmd()

    def doIt(self, args):
        import topomotion_panel
        topomotion_panel.show()


def _build_menu():
    if cmds.menu(MENU, exists=True):
        cmds.deleteUI(MENU)
    parent = mel.eval("$tmp = $gMainWindow")
    cmds.menu(MENU, parent=parent, label="TopoMotion", tearOff=True)
    cmds.menuItem(parent=MENU, label="패널 열기",
                  command="import maya.cmds as c; c.topoMotionPanel()")
    cmds.menuItem(parent=MENU, divider=True)
    cmds.menuItem(parent=MENU, label="Shelf에 바로가기 추가",
                  command="import topomotion_panel as p; p.install_shelf()")
    cmds.menuItem(parent=MENU, label="사용 매뉴얼",
                  command="import topomotion_panel as p; p._ui_call(p.open_manual)")


def initializePlugin(obj):
    plugin = om.MFnPlugin(obj, VENDOR, VERSION, "Any")
    plugin.registerCommand(TopoMotionPanelCmd.kName, TopoMotionPanelCmd.creator)
    if not cmds.about(batch=True):
        _build_menu()
    om.MGlobal.displayInfo("[TopoMotion] v%s 로드됨 — 명령 %s"
                           % (VERSION, TopoMotionPanelCmd.kName))


def uninitializePlugin(obj):
    plugin = om.MFnPlugin(obj)
    if not cmds.about(batch=True):
        for window in ("topomotionPanel", "topomotionLibrary"):
            if cmds.window(window, exists=True):
                cmds.deleteUI(window)
        if cmds.menu(MENU, exists=True):
            cmds.deleteUI(MENU)
    plugin.deregisterCommand(TopoMotionPanelCmd.kName)
