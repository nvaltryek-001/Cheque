"""The container entrypoint: how the models and reports that ship with the image reach the persistent disk.

Before: a file was copied only when it was missing, so (1) a later release could never improve the models on a disk that
already held the first ones, and (2) a copy that was cut short by a kill stayed truncated for ever.
Now: copies are atomic, a copy this entrypoint made earlier is refreshed when the release ships a newer one, and anything
that was changed on the disk (a model retrained in the app) is never overwritten."""
import json
import os
import stat
from pathlib import Path

import pytest

import docker_entrypoint as ep


@pytest.fixture()
def tree(tmp_path):
    """An image-like tree:  app/backend/models_store, app/reports/final  and a data volume."""
    app = tmp_path / "image" / "backend"
    (app / "models_store").mkdir(parents=True)
    (app.parent / "reports" / "final").mkdir(parents=True)
    (app / "models_store" / "micr_e13b.npz").write_bytes(b"MICR-v1")
    (app / "models_store" / "signature_fusion.json").write_text('{"v": 1}')
    (app.parent / "reports" / "final" / "MODEL_EVALUATION.md").write_text("report v1")
    data = tmp_path / "data"
    data.mkdir()

    class T:
        pass
    t = T()
    t.app, t.data, t.models, t.reports = app, data, data / "models", data / "reports"

    def run():
        return dict(ep.seed_all(t.models, t.reports, data_root=t.data, app=t.app))
    t.run = run
    t.ship = lambda name, content: (app / "models_store" / name).write_bytes(content)
    t.ship_report = lambda name, content: (app.parent / "reports" / "final" / name).write_text(content)
    t.manifest = lambda: json.loads((data / ep.MANIFEST_NAME).read_text())["files"]
    return t


def test_first_start_copies_everything_and_records_what_it_copied(tree):
    actions = tree.run()
    assert set(actions.values()) == {"copied"} and len(actions) == 3
    assert (tree.models / "micr_e13b.npz").read_bytes() == b"MICR-v1"
    assert (tree.reports / "final" / "MODEL_EVALUATION.md").read_text() == "report v1"
    assert set(tree.manifest()) == {"models/micr_e13b.npz", "models/signature_fusion.json", "reports/MODEL_EVALUATION.md"}
    assert not [p for p in tree.models.iterdir() if p.name.startswith(".tmp-")]
    assert stat.S_IMODE((tree.models / "micr_e13b.npz").stat().st_mode) == 0o600      # private, whatever the source mode


def test_restart_without_a_new_release_changes_nothing(tree):
    tree.run()
    before = {p: p.read_bytes() for p in tree.data.rglob("*") if p.is_file()}
    manifest_mtime = (tree.data / ep.MANIFEST_NAME).stat().st_mtime_ns
    assert set(tree.run().values()) == {"current"}
    assert {p: p.read_bytes() for p in tree.data.rglob("*") if p.is_file()} == before
    assert (tree.data / ep.MANIFEST_NAME).stat().st_mtime_ns == manifest_mtime        # not even rewritten


def test_a_new_release_improves_files_nobody_has_touched(tree):
    tree.run()
    tree.ship("micr_e13b.npz", b"MICR-v2 improved")
    tree.ship_report("MODEL_EVALUATION.md", "report v2")
    actions = tree.run()
    assert actions["models/micr_e13b.npz"] == "updated" and actions["reports/MODEL_EVALUATION.md"] == "updated"
    assert actions["models/signature_fusion.json"] == "current"
    assert (tree.models / "micr_e13b.npz").read_bytes() == b"MICR-v2 improved"
    assert (tree.reports / "final" / "MODEL_EVALUATION.md").read_text() == "report v2"
    assert tree.manifest()["models/micr_e13b.npz"] == ep._sha256(tree.models / "micr_e13b.npz")
    assert set(tree.run().values()) == {"current"}                                    # and it is stable afterwards


def test_a_model_retrained_on_the_disk_is_never_overwritten(tree):
    tree.run()
    (tree.models / "micr_e13b.npz").write_bytes(b"retrained in the app on real cheques")
    tree.ship("micr_e13b.npz", b"MICR-v2 improved")
    for _ in range(2):                                                                # on every later start, too
        actions = tree.run()
        assert actions["models/micr_e13b.npz"].startswith("kept: changed on this disk")
        assert (tree.models / "micr_e13b.npz").read_bytes() == b"retrained in the app on real cheques"
    (tree.models / "micr_e13b.npz").unlink()                                          # the documented way back to the shipped file
    assert tree.run()["models/micr_e13b.npz"] == "copied"
    assert (tree.models / "micr_e13b.npz").read_bytes() == b"MICR-v2 improved"


def test_a_disk_from_before_the_manifest_existed_is_adopted_without_losing_anything(tree):
    tree.models.mkdir(parents=True)
    (tree.models / "micr_e13b.npz").write_bytes(b"MICR-v1")                           # identical to what is shipped
    (tree.models / "signature_fusion.json").write_text('{"v": "trained here, or an older release - unknowable"}')
    actions = tree.run()
    assert actions["models/micr_e13b.npz"] == "current"
    assert actions["models/signature_fusion.json"].startswith("kept: differs from the shipped file and its origin is unknown")
    assert (tree.models / "signature_fusion.json").read_text().startswith('{"v": "trained here')
    tree.ship("micr_e13b.npz", b"MICR-v2")                                            # now that it is recorded, it can be refreshed
    assert tree.run()["models/micr_e13b.npz"] == "updated"


def test_a_damaged_manifest_makes_the_entrypoint_careful_not_broken(tree):
    tree.run()
    (tree.data / ep.MANIFEST_NAME).write_text("{ this is not json")
    tree.ship("micr_e13b.npz", b"MICR-v2")
    actions = tree.run()
    assert actions["models/micr_e13b.npz"].startswith("kept")                         # cannot prove it is an untouched copy
    assert (tree.models / "micr_e13b.npz").read_bytes() == b"MICR-v1"
    assert json.loads((tree.data / ep.MANIFEST_NAME).read_text())["version"] == 1     # the manifest is whole again ...
    assert "models/micr_e13b.npz" not in tree.manifest()                              # ... without vouching for the kept file
    assert "models/signature_fusion.json" in tree.manifest()                          # but recording what is verifiably shipped


def test_a_kill_during_the_copy_leaves_the_old_file_and_the_next_start_finishes_the_job(tree, monkeypatch):
    tree.run()
    tree.ship("micr_e13b.npz", b"MICR-v2 " * 100_000)
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("killed before the rename")))
    with pytest.raises(OSError):
        tree.run()
    monkeypatch.setattr(os, "replace", real_replace)
    assert (tree.models / "micr_e13b.npz").read_bytes() == b"MICR-v1"                 # never a truncated model
    assert not [p for p in tree.models.iterdir() if p.name.startswith(".tmp-")]
    assert tree.run()["models/micr_e13b.npz"] == "updated"
    assert (tree.models / "micr_e13b.npz").read_bytes() == b"MICR-v2 " * 100_000


def test_a_first_copy_cut_short_is_never_left_behind(tree, monkeypatch):
    """The old code copied straight to the final name; a kill half way left a truncated model that was then 'present'."""
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("killed before the rename")))
    with pytest.raises(OSError):
        tree.run()
    monkeypatch.undo()
    assert not (tree.models / "micr_e13b.npz").exists()
    assert tree.run()["models/micr_e13b.npz"] == "copied"


def test_odd_things_in_the_volume_are_left_alone(tree, tmp_path):
    tree.models.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch")
    (tree.models / "micr_e13b.npz").symlink_to(victim)                                # a link where a file should be
    (tree.models / "signature_fusion.json").mkdir()                                   # a folder where a file should be
    actions = tree.run()
    assert actions["models/micr_e13b.npz"] == "kept: not a regular file"
    assert actions["models/signature_fusion.json"] == "kept: not a regular file"
    assert victim.read_text() == "do not touch"
    (tree.models / "micr_e13b.npz").unlink()
    (tree.models / "micr_e13b.npz").symlink_to(tmp_path / "does-not-exist")           # a dangling link
    assert tree.run()["models/micr_e13b.npz"] == "kept: not a regular file"
    assert not (tmp_path / "does-not-exist").exists()                                 # nothing was written through the link


def test_nothing_shipped_is_not_an_error(tree):
    for p in (tree.app / "models_store").iterdir():
        p.unlink()
    (tree.app.parent / "reports" / "final" / "MODEL_EVALUATION.md").unlink()
    assert tree.run() == {}
    assert not (tree.data / ep.MANIFEST_NAME).exists()


def test_main_hands_the_volume_over_before_it_writes_anything():
    """Root must do only the chown; folders and copies are made as the app user (a link planted in the volume by a
    compromised server process must not let root write elsewhere). Checked on the source order."""
    src = Path(ep.__file__).read_text()
    main = src[src.index("def main()"):]
    assert main.index("drop_privileges()") < main.index("instance.mkdir") < main.index("seed_all(")
    drop = src[src.index("def drop_privileges"):src.index("def main()")]
    assert "lchown" in drop and "os.chown(" not in drop and "followlinks=True" not in drop
