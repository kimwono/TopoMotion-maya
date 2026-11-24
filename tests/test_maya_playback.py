"""Regression tests for export/import contracts without a Maya dependency."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "playback", Path(__file__).resolve().parents[1] /
    "maya_scripts/topomotion_core/playback.py")
playback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(playback)


def document():
    matrix = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 1.2, 2.5, -3., 1.]
    return {"format": "topomotion_anim/1", "num_joints": 2, "num_frames": 1,
            "fps": 50, "rig": {"names": ["root", "tip"], "parents": [-1, 0]},
            "world_matrices": [[matrix[:], matrix[:]]]}


def test_animation_roundtrip_converts_metres_to_internal_cm_without_mutation():
    doc = document()
    before = copy.deepcopy(doc)
    out = playback.prepare(doc)
    assert out["world_matrices"][0][0][12:15] == pytest.approx([120, 250, -300])
    assert out["fps"] == 50
    assert out["names"] == ["root", "tip"]
    assert doc == before


def test_library_display_coordinates_stay_unchanged():
    doc = document()
    doc.update(doc.pop("rig"))
    doc.pop("format")
    assert playback.prepare(doc)["world_matrices"] == doc["world_matrices"]


def test_namespaces_import_without_source_namespace_and_without_name_collision():
    doc = document()
    doc["rig"]["names"] = ["character:root", "character_root"]
    assert playback.prepare(doc)["names"] == ["character_root", "character_root_2"]


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(format="topomotion_rig/1"),
    lambda d: d.update(num_frames=0),
    lambda d: d["rig"].update(parents=[1, 0]),
    lambda d: d["rig"].update(parents=[-1, 3]),
    lambda d: d["rig"].update(parents=[-1, -2]),
    lambda d: d["world_matrices"][0][0].__setitem__(12, float("nan")),
    lambda d: d["world_matrices"][0][0].pop(),
    lambda d: d.update(fps=-1),
])
def test_invalid_documents_rejected_before_maya(mutation):
    doc = document()
    mutation(doc)
    with pytest.raises(ValueError):
        playback.prepare(doc)
