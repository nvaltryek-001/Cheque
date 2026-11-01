"""Signature evaluation protocol (app/signature/protocol.py, manage.py eval-signatures).

The data here is synthetic: pseudo-signatures drawn by conftest.synthetic_signature. For most tests the system under
test is replaced by a scripted decider that recognises each image by content, so the tests check the PROTOCOL (who is
compared with whom, what is refused, what is reported) and not the quality of the comparator. One test runs the real
comparator end to end.
"""
import argparse
import csv
import hashlib
import json
import random
import sys

import cv2
import numpy as np
import pytest

from app.signature import protocol
from app.signature.compare import FEATURES, FusionModel
from app.signature.preprocess import preprocess
from app.signature.training import PAIR_CONFIG, SCOPE, evaluate_decisions, train_and_evaluate
from conftest import BACKEND, synthetic_signature
from test_enrollment import crooked, rescan

sys.path.insert(0, str(BACKEND))

HEADER = ["file", "signer_id", "kind", "session_date", "forger_id", "source_ref", "derived_from", "split"]
DAYS = ("2026-01-10", "2026-01-20", "2026-02-10")
SIGNERS = [f"ZQX-SIGNER-{n:02d}" for n in range(1, 7)]


def fingerprint(proc) -> str:
    return hashlib.sha1(proc.gray.tobytes()).hexdigest()


class World:
    """Writes images and an evaluation manifest, and remembers which image is whose."""

    def __init__(self, root):
        self.images = root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.manifest_path = root / "manifest.csv"
        self.rows, self.who, self.seed = [], {}, 4000

    def add(self, signer, kind, name, day="", forger="", source="", derived="", split="test", img=None):
        self.seed += 1
        img = synthetic_signature(self.seed) if img is None else img
        cv2.imwrite(str(self.images / name), img)
        self.rows.append(dict(file=name, signer_id=signer, kind=kind, session_date=day, forger_id=forger, source_ref=source,
                              derived_from=derived, split=split))
        proc = preprocess(cv2.imread(str(self.images / name), cv2.IMREAD_COLOR))
        self.who[fingerprint(proc)] = (signer, kind, name)
        return name

    def write(self):
        with open(self.manifest_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, HEADER)
            w.writeheader()
            w.writerows(self.rows)
        return self.manifest_path

    def load(self):
        items, problems = protocol.load_manifest(self.write(), self.images)
        assert not problems, problems
        return items

    def decider(self, policy=None, calls=None):
        policy = policy or default_policy

        def run(query, refs):
            q, r = self.who[fingerprint(query)], self.who[fingerprint(refs[0])]
            if calls is not None:
                calls.append((q, r))
            return policy(q, r)
        return run


def default_policy(q, r):
    """Genuine signatures of the enrolled signer match; everything else is rejected."""
    if q[1] == "genuine" and q[0] == r[0]:
        return {"decision": "MATCH", "score": 0.9}
    if q[1] == "skilled_forgery":
        return {"decision": "MISMATCH", "score": 0.3}
    return {"decision": "MISMATCH", "score": 0.2}


def populate(world, signers, split="test", skilled=False, forgers=("F-ONE", "F-TWO", "F-ONE"), synthetic=False):
    """Per signer: 3 + 3 + 4 genuine signatures on three days (files <signer>-g-<day><letter>), optional forgeries and edits."""
    for s in signers:
        for d, count in enumerate((3, 3, 4)):
            for letter in "abcd"[:count]:
                world.add(s, "genuine", f"{s}-g-{d}{letter}.png", day=DAYS[d], split=split)
        if skilled:
            for n, f in enumerate(forgers):
                world.add(s, "skilled_forgery", f"{s}-sf-{n}.png", forger=f, split=split)
        if synthetic:
            for n in range(2):
                world.add(s, "synthetic_alteration", f"{s}-syn-{n}.png", derived=f"{s}-g-0a.png", split=split)
    return world


def standard_world(root, signers=SIGNERS, **kw):
    return populate(World(root), signers, **kw)


def run(world, split="test", final=True, policy=None, calls=None, ledger="auto", version="model-1", hashes=None, cfg=None):
    if ledger == "auto":                                   # a final run keeps a ledger unless a test says otherwise
        ledger = world.manifest_path.parent / "ledger.json"
    return protocol.evaluate(world.load(), split=split, final=final, decider=world.decider(policy, calls), model_version=version,
                             ledger_path=ledger, model_signer_hashes=hashes, cfg={"bootstrap": 200, **(cfg or {})})


def check(report, cid):
    return {c["id"]: c for c in report["validity"]["checks"]}[cid]


def fatal(report):
    return [c["id"] for c in report["validity"]["checks"] if c["status"] == "fail" and c["scope"] == "run"]


def measurable(report):
    return {k for k, v in report["metrics"].items() if v.get("measurable")}


def item(signer, day, name, sha=None, source="", kind="genuine", split=""):
    return protocol.Item(0, name, None, signer, kind, sha or name, day, "", source, "", split)


# ------------------------------------------------------------------ manifest
@pytest.fixture()
def folder(tmp_path):
    imgs = tmp_path / "images"
    imgs.mkdir()
    for n in ("ok.png", "other.png"):
        cv2.imwrite(str(imgs / n), synthetic_signature(1))
    (imgs / "notes.txt").write_text("x")
    cv2.imwrite(str(tmp_path / "outside.png"), synthetic_signature(2))
    return tmp_path, imgs


def write_manifest(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in HEADER})


GOOD = {"file": "ok.png", "signer_id": "P1", "kind": "genuine", "session_date": "2026-01-10"}


@pytest.mark.parametrize("change,message", [
    ({"signer_id": "bad id!"}, "signer_id"),
    ({"signer_id": ""}, "signer_id"),
    ({"kind": "forged"}, "kind must be one of"),
    ({"session_date": ""}, "needs session_date"),
    ({"session_date": "10/01/2026"}, "needs session_date"),
    ({"session_date": "2026-13-45"}, "needs session_date"),
    ({"kind": "skilled_forgery", "session_date": ""}, "needs forger_id"),
    ({"kind": "skilled_forgery", "forger_id": "P1"}, "not a forgery"),
    ({"kind": "synthetic_alteration"}, "needs derived_from"),
    ({"split": "dev"}, "split must be"),
    ({"file": "missing.png"}, "file not found"),
    ({"file": "../outside.png"}, "outside the images folder"),
    ({"file": "notes.txt"}, "unsupported file type"),
    ({"file": ""}, "file is empty"),
])
def test_invalid_manifest_rows_are_reported_and_the_rest_is_kept(folder, change, message):
    tmp, imgs = folder
    write_manifest(tmp / "m.csv", [{**GOOD, "file": "other.png", "signer_id": "P0"}, {**GOOD, **change}])
    items, problems = protocol.load_manifest(tmp / "m.csv", imgs)
    assert [i.signer_id for i in items] == ["P0"]
    assert len(problems) == 1 and problems[0]["line"] == 3 and message in problems[0]["message"]


def test_listing_a_file_twice_or_deriving_from_a_missing_genuine_file_is_a_problem(folder):
    tmp, imgs = folder
    write_manifest(tmp / "m.csv", [GOOD, GOOD, {"file": "other.png", "signer_id": "P1", "kind": "synthetic_alteration",
                                                "derived_from": "nowhere.png"}])
    items, problems = protocol.load_manifest(tmp / "m.csv", imgs)
    assert len(items) == 1
    assert [p["message"] for p in problems] == ["the same file is listed twice",
                                                "derived_from is not a genuine file of this signer"]


def test_a_symlink_pointing_outside_the_images_folder_is_refused(folder):
    tmp, imgs = folder
    try:
        (imgs / "link.png").symlink_to(tmp / "outside.png")
    except OSError:
        pytest.skip("symlinks not available")
    write_manifest(tmp / "m.csv", [{**GOOD, "file": "link.png"}])
    items, problems = protocol.load_manifest(tmp / "m.csv", imgs)
    assert not items and "outside the images folder" in problems[0]["message"]


@pytest.mark.parametrize("content", ["", "file,signer_id\nok.png,P1\n"])
def test_a_manifest_without_the_required_columns_is_an_error(folder, content):
    tmp, imgs = folder
    (tmp / "m.csv").write_text(content)
    with pytest.raises(ValueError, match="needs the column"):
        protocol.load_manifest(tmp / "m.csv", imgs)


def test_missing_manifest_or_folder(folder):
    tmp, imgs = folder
    with pytest.raises(ValueError, match="cannot read the manifest"):
        protocol.load_manifest(tmp / "nope.csv", imgs)
    write_manifest(tmp / "m.csv", [GOOD])
    with pytest.raises(ValueError, match="images folder"):
        protocol.load_manifest(tmp / "m.csv", tmp / "no-such-folder")


# ------------------------------------------------------------------ splits
def test_splits_are_assigned_per_signer_reproducibly_and_in_proportion():
    items = [item(f"P{n:02d}", DAYS[0], f"f{n}-{k}") for n in range(20) for k in range(3)]
    split, problems = protocol.assign_splits(items)
    assert not problems and set(split) == {f"P{n:02d}" for n in range(20)}
    counts = {sp: sum(1 for v in split.values() if v == sp) for sp in protocol.SPLITS}
    assert counts == {"train": 12, "val": 4, "test": 4}
    shuffled = items[:]
    random.Random(3).shuffle(shuffled)
    assert protocol.assign_splits(shuffled)[0] == split                      # does not depend on file order
    assert protocol.assign_splits(items, {"seed": 7})[0] != split            # but does depend on the seed


def test_declared_splits_are_honoured_and_a_signer_in_two_splits_is_reported():
    items = [item("A", DAYS[0], "a1", split="test"), item("A", DAYS[1], "a2"), item("B", DAYS[0], "b1", split="val"),
             item("C", DAYS[0], "c1", split="val"), item("C", DAYS[1], "c2", split="test"), item("D", DAYS[0], "d1")]
    split, problems = protocol.assign_splits(items)
    assert split["A"] == "test" and split["B"] == "val" and split["D"] in protocol.SPLITS
    assert len(problems) == 1 and "more than one split" in problems[0]


def test_a_signer_in_two_splits_stops_the_evaluation(tmp_path):
    w = standard_world(tmp_path)
    w.rows[0]["split"] = "val"                                              # one signer's first row says val, the rest test
    calls = []
    report = run(w, calls=calls)
    assert not report["validity"]["valid"] and "signer_level_split" in fatal(report)
    assert calls == []


# ------------------------------------------------------------------ reference and query plan
def specimens(signer, schedule):
    return [item(signer, day, f"{signer}-{day}-{n}") for day, count in schedule for n in range(count)]


def test_references_are_the_thinnest_ready_set_and_queries_come_strictly_later():
    plan = protocol.plan_signer(specimens("A", [(DAYS[0], 4), (DAYS[1], 4), (DAYS[2], 4)]), protocol.DEFAULT_CFG)
    assert protocol.eligible(plan)
    assert len(plan["references"]) == 5
    assert {r.session_date for r in plan["references"]} == {DAYS[0], DAYS[1]}
    assert [q.session_date for q in plan["queries"]] == [DAYS[2]] * 4
    assert max(r.session_date for r in plan["references"]) < min(q.session_date for q in plan["queries"])
    assert plan["unused"] == 12 - 5 - 4                                       # the rest shared a sitting with the references


@pytest.mark.parametrize("seed", range(25))
def test_no_query_is_ever_written_on_or_before_a_reference_day(seed):
    rnd = random.Random(seed)
    days = sorted(rnd.sample([f"2026-0{m}-{d:02d}" for m in range(1, 6) for d in (3, 11, 19, 27)], rnd.randint(2, 8)))
    genuine = [item("A", d, f"A-{d}-{n}") for d in days for n in range(rnd.randint(1, 6))]
    plan = protocol.plan_signer(genuine, protocol.DEFAULT_CFG)
    if plan["references"]:
        assert len(plan["references"]) == 5 and len({r.session_date for r in plan["references"]}) >= 2
    if plan["references"] and plan["queries"]:
        assert max(r.session_date for r in plan["references"]) < min(q.session_date for q in plan["queries"])
        assert not {r.file for r in plan["references"]} & {q.file for q in plan["queries"]}


def test_thin_early_days_are_topped_up_from_later_days():
    plan = protocol.plan_signer(specimens("A", [(DAYS[0], 1), (DAYS[1], 1), ("2026-02-01", 2), ("2026-02-05", 1), ("2026-03-01", 4)]),
                                protocol.DEFAULT_CFG)
    assert len(plan["references"]) == 5 and max(r.session_date for r in plan["references"]) == "2026-02-05"
    assert {q.session_date for q in plan["queries"]} == {"2026-03-01"}


@pytest.mark.parametrize("schedule,reason", [
    ([(DAYS[0], 8)], "references"),                                           # one sitting is not enough
    ([(DAYS[0], 2), (DAYS[1], 2)], "references"),                             # fewer than five specimens
    ([(DAYS[0], 5), (DAYS[1], 5)], "queries"),                                # nothing written after the reference days
    ([(DAYS[0], 3), (DAYS[1], 3), (DAYS[2], 2)], "queries"),                  # fewer than three later specimens
])
def test_signers_without_independent_references_and_queries_are_not_evaluated(schedule, reason):
    plan = protocol.plan_signer(specimens("A", schedule), protocol.DEFAULT_CFG)
    assert not protocol.eligible(plan) and plan["reason"] == reason


def test_specimens_from_one_source_and_identical_files_count_once():
    genuine = [item("A", DAYS[0], "a1", source="Cheque 7"), item("A", DAYS[1], "a2", source=" cheque   7 "),
               item("A", DAYS[0], "a3", sha="same"), item("A", DAYS[2], "a4", sha="same"), item("A", DAYS[2], "a5")]
    plan = protocol.plan_signer(genuine, protocol.DEFAULT_CFG)
    assert plan["independent"] == 3                                           # {cheque 7}, {same}, {a5}


# ------------------------------------------------------------------ statistics
def test_auc_and_eer_on_known_cases():
    assert protocol.auc([3, 4, 5], [0, 1, 2]) == 1.0
    assert protocol.auc([0, 1, 2], [3, 4, 5]) == 0.0
    assert protocol.auc([1, 1], [1, 1]) == 0.5
    assert protocol.auc([1, 2, 3], [2]) == pytest.approx(0.5)
    assert protocol.auc([], [1]) is None and protocol.auc([1], []) is None
    assert protocol.eer([3, 4, 5], [0, 1, 2]) == 0.0
    assert protocol.eer([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(0.5)
    assert protocol.eer([], [1]) is None


def test_upper_bound_for_no_events_is_the_exact_binomial_bound():
    assert protocol.upper_bound_no_events(30) == pytest.approx(1 - 0.05 ** (1 / 30))
    assert 0.09 < protocol.upper_bound_no_events(30) < 0.10
    assert protocol.upper_bound_no_events(0) is None


def test_intervals_come_from_resampling_signers_and_are_reproducible():
    cfg = {**protocol.DEFAULT_CFG, "bootstrap": 500}
    clustered = {f"s{n}": ((5, 5) if n % 2 else (0, 5)) for n in range(10)}          # half the signers always fail
    out = protocol.rate_with_ci(clustered, cfg, "t")
    assert out["value"] == 0.5 and out["events"] == 25 and out["trials"] == 50 and out["signers"] == 10
    lo, hi = out["ci95"]
    assert hi - lo > 0.4                                                       # 50 trials treated as independent would give about 0.28
    assert protocol.rate_with_ci(clustered, cfg, "t") == out


def test_rates_report_what_cannot_be_said():
    cfg = protocol.DEFAULT_CFG
    assert protocol.rate_with_ci({"a": (0, 0)}, cfg, "t") == {"measurable": False, "reason": "no trials"}
    few = protocol.rate_with_ci({"a": (1, 4), "b": (0, 4)}, cfg, "t")
    assert few["ci95"] is None                                                 # fewer than five signers: no interval is offered
    none = protocol.rate_with_ci({f"s{n}": (0, 3) for n in range(10)}, cfg, "t")
    assert none["value"] == 0.0 and none["upper95_share_of_signers_with_an_event"] == pytest.approx(1 - 0.05 ** 0.1, abs=1e-3)


# ------------------------------------------------------------------ the evaluation
def test_a_valid_run_scores_genuine_queries_from_later_days_against_the_thin_reference_set(tmp_path):
    w = standard_world(tmp_path)
    calls = []
    report = run(w, calls=calls)
    assert report["validity"]["valid"] and report["population"]["signers_scored"] == 6
    genuine = [(q, r) for q, r in calls if q[1] == "genuine" and q[0] == r[0]]
    assert len(genuine) == 6 * 4 and all(q[2].split("-g-")[1].startswith("2") for q, _ in genuine)   # day-3 files only
    m = report["metrics"]
    assert m["genuine_match_rate"]["value"] == 1.0 and m["genuine_match_rate"]["trials"] == 24
    assert m["random_forgery_match_rate"]["value"] == 0.0 and m["random_forgery_match_rate"]["trials"] == 6 * 5
    assert report["population"]["random_forgeries_scored"] == 30
    # unknown model provenance and a small set are warnings, never a silent pass
    assert set(report["validity"]["warnings"]) == {"independent_genuine_signers", "model_has_not_seen_these_signers"}


def test_every_comparison_uses_the_signers_own_earliest_references_and_zero_effort_forgeries_are_other_signers_queries(tmp_path):
    w = standard_world(tmp_path)
    calls = []
    run(w, calls=calls)
    assert {r[2] for _, r in calls} == {f"{s}-g-0a.png" for s in SIGNERS}              # refs[0] of each signer
    zero_effort = [(q, r) for q, r in calls if q[0] != r[0]]
    assert len(zero_effort) == 30 and all(q[1] == "genuine" and "-g-2" in q[2] for q, _ in zero_effort)
    plan = protocol.plan_signer([i for i in w.load() if i.signer_id == SIGNERS[0] and i.kind == "genuine"], protocol.DEFAULT_CFG)
    assert [r.file for r in plan["references"]] == [f"{SIGNERS[0]}-g-{n}.png" for n in ("0a", "1a", "0b", "1b", "0c")]


def test_outcomes_become_rates_with_signer_level_intervals(tmp_path):
    w = standard_world(tmp_path, skilled=True)

    def policy(q, r):
        if q[1] == "genuine" and q[0] == r[0]:
            review = q[0] == SIGNERS[0] and q[2][-5] in "ab"
            return {"decision": "REVIEW" if review else "MATCH", "score": 0.6 if q[0] == SIGNERS[0] else 0.9}
        if q[1] == "skilled_forgery":
            accepted = q[0] == SIGNERS[1] and q[2].endswith("0.png")
            return {"decision": "MATCH" if accepted else "MISMATCH", "score": 0.85 if q[0] == SIGNERS[1] else 0.3}
        return {"decision": "MISMATCH", "score": 0.2}

    m = run(w, policy=policy)["metrics"]
    assert (m["genuine_match_rate"]["events"], m["genuine_review_rate"]["events"], m["genuine_mismatch_rate"]["events"]) == (22, 2, 0)
    assert m["skilled_forgery_match_rate"]["events"] == 1 and m["skilled_forgery_match_rate"]["trials"] == 18
    lo, hi = m["genuine_match_rate"]["ci95"]
    assert lo <= m["genuine_match_rate"]["value"] <= hi
    assert m["skilled_forgery_auc"]["value"] == pytest.approx(420 / 432, abs=1e-4)
    assert 0 < m["skilled_forgery_eer"]["value"] <= 0.5


def test_the_test_split_cannot_be_used_while_developing(tmp_path):
    w = standard_world(tmp_path)

    def forbidden(query, refs):
        raise AssertionError("the test split must not be scored without --final")

    report = protocol.evaluate(w.load(), split="test", final=False, decider=forbidden, model_version="m")
    assert not report["validity"]["valid"] and fatal(report) == ["test_set_used_once"]
    assert not measurable(report) and all("--final" in v["reason"] for v in report["metrics"].values())
    assert any("Any final performance figure" in x for x in report["claims"]["not_allowed"])


def test_a_validation_run_is_exploratory_even_if_final_is_requested(tmp_path):
    w = standard_world(tmp_path, split="val")
    ledger = tmp_path / "ledger.json"
    report = run(w, split="val", final=True, ledger=ledger)
    assert report["validity"]["valid"] and report["final"] is False and not ledger.exists()
    assert any("not a final run" in x for x in report["claims"]["not_allowed"])


def test_signers_the_model_was_trained_on_cannot_be_evaluated(tmp_path):
    w = standard_world(tmp_path)
    calls = []
    report = run(w, calls=calls, hashes={protocol.signer_hash(SIGNERS[2]), "unrelated"})
    assert not report["validity"]["valid"] and fatal(report) == ["model_has_not_seen_these_signers"]
    assert "1 evaluated signer(s) were used to train or tune the model" in check(report, "model_has_not_seen_these_signers")["message"]
    assert calls == [] and not measurable(report)
    clean = run(w, hashes={protocol.signer_hash("somebody-else")})
    assert clean["validity"]["valid"] and check(clean, "model_has_not_seen_these_signers")["status"] == "pass"


def test_too_few_qualifying_signers_leaves_every_metric_not_measurable(tmp_path):
    w = standard_world(tmp_path, signers=SIGNERS[:4])
    calls = []
    report = run(w, calls=calls)
    assert not report["validity"]["valid"] and calls == []
    assert not measurable(report) and set(report["metrics"]) == set(protocol.METRICS)
    assert all("at least 5 are needed" in v["reason"] for v in report["metrics"].values())
    assert "Any genuine-acceptance or false-rejection rate for real signers." in report["claims"]["not_allowed"]
    assert report["claims"]["allowed"] == []


def test_signers_without_later_signatures_do_not_count_and_are_explained(tmp_path):
    w = standard_world(tmp_path)
    w.rows = [r for r in w.rows if not (r["signer_id"] in SIGNERS[:2] and r["session_date"] == DAYS[2])]
    report = run(w)
    c = check(report, "independent_genuine_signers")
    assert report["population"]["signers_qualifying"] == 4 and not report["validity"]["valid"]
    assert c["status"] == "fail" and c["ineligible"] == {"too few genuine specimens written after the reference days": 2}


def test_an_underpowered_set_is_scored_but_flagged(tmp_path):
    report = run(standard_world(tmp_path))
    c = check(report, "independent_genuine_signers")
    assert c["status"] == "warn" and "underpowered" in c["message"]
    assert any("fewer than 30 evaluated signers" in x for x in report["claims"]["not_allowed"])


def test_without_real_forgeries_the_skilled_forgery_metrics_are_not_measurable_but_the_rest_is(tmp_path):
    w = standard_world(tmp_path, synthetic=True)
    calls = []
    report = run(w, calls=calls)
    m = report["metrics"]
    assert report["validity"]["valid"] and m["genuine_match_rate"]["measurable"] and m["random_forgery_auc"]["measurable"]
    for key in ("skilled_forgery_match_rate", "skilled_forgery_review_rate", "skilled_forgery_mismatch_rate", "skilled_forgery_auc",
                "skilled_forgery_eer"):
        assert m[key]["measurable"] is False and "skilled forgeries by different people" in m[key]["reason"] and "value" not in m[key]
    assert check(report, "skilled_forgeries")["status"] == "fail"
    assert "Any statement about resistance to skilled forgery." in report["claims"]["not_allowed"]
    assert not any("skilled" in x.lower() for x in report["claims"]["allowed"])
    assert any("edited-copy detection" in x for x in report["claims"]["not_allowed"])


def test_synthetic_alterations_are_set_aside_and_never_counted_as_forgeries(tmp_path):
    w = standard_world(tmp_path, synthetic=True)
    calls = []
    report = run(w, calls=calls)
    assert report["population"]["synthetic_alterations_set_aside"] == 12
    assert report["population"]["skilled_forgeries_scored"] == 0 and report["population"]["skilled_forgeries_listed"] == 0
    assert not [q for q, _ in calls if q[1] == "synthetic_alteration"]             # they never reach the comparator
    assert "synthetic" not in json.dumps(report["metrics"]) and "edited_copy" not in json.dumps(report["metrics"])
    c = check(report, "synthetic_alterations_kept_apart")
    assert c["status"] == "pass" and "not attempts by another person" in c["message"]


def test_skilled_forgeries_need_at_least_two_different_forgers(tmp_path):
    one_forger = run(standard_world(tmp_path / "a", skilled=True, forgers=("F-ONE", "F-ONE", "F-ONE")))
    assert one_forger["metrics"]["skilled_forgery_match_rate"]["measurable"] is False
    assert one_forger["population"]["skilled_forgeries_scored"] == 0
    two = run(standard_world(tmp_path / "b", skilled=True))
    assert two["metrics"]["skilled_forgery_match_rate"]["measurable"] and two["population"]["skilled_forgeries_scored"] == 18
    assert check(two, "skilled_forgeries")["status"] == "warn"                      # measurable, but only six signers
    assert any(x.startswith("Skilled forgeries by other people were MATCHed 0.0%") for x in two["claims"]["allowed"])


def test_only_signers_with_enough_forgeries_enter_the_forgery_metrics(tmp_path):
    w = standard_world(tmp_path, skilled=True)
    w.rows = [r for r in w.rows if r["file"] != f"{SIGNERS[0]}-sf-2.png"]
    report = run(w)
    assert report["population"]["skilled_forgeries_scored"] == 15                 # the signer left with two forgeries counts for none
    assert report["metrics"]["skilled_forgery_match_rate"]["signers"] == 5


@pytest.mark.parametrize("copy_of,make", [("0b", crooked), ("0a", rescan)], ids=["crooked scan (alignment rule)", "plain re-scan (hash rule)"])
def test_a_genuine_query_that_is_a_second_capture_of_a_reference_is_removed(tmp_path, copy_of, make):
    w = standard_world(tmp_path)
    ref = cv2.imread(str(w.images / f"{SIGNERS[0]}-g-{copy_of}.png"))
    name = f"{SIGNERS[0]}-g-2a.png"
    w.rows = [r for r in w.rows if r["file"] != name]
    w.add(SIGNERS[0], "genuine", name, day=DAYS[2], img=make(ref))
    calls = []
    report = run(w, calls=calls)
    c = check(report, "no_leakage_between_references_and_queries")
    assert c["status"] == "warn" and c["message"].startswith("1 genuine image(s)")
    assert not [q for q, r in calls if q[2] == name and r[0] == SIGNERS[0]]       # never compared with the signer's own references
    assert report["population"]["genuine_queries_scored"] == 23


def test_the_same_image_for_two_signers_stops_the_evaluation(tmp_path):
    w = standard_world(tmp_path)
    shared = cv2.imread(str(w.images / f"{SIGNERS[0]}-g-2a.png"))
    w.add(SIGNERS[1], "genuine", "copy-of-another-signers-signature.png", day=DAYS[2], img=shared)
    calls = []
    report = run(w, calls=calls)
    assert fatal(report) == ["no_duplicate_images_across_people"] and calls == []


def test_unreadable_images_are_dropped_and_counted(tmp_path):
    w = standard_world(tmp_path)
    (w.images / f"{SIGNERS[0]}-g-2a.png").write_bytes(b"not an image")
    report = run(w)
    assert report["population"]["dropped"]["genuine query: unreadable or unusable image"] == 1
    assert report["population"]["genuine_queries_scored"] == 23


def test_an_image_that_declares_an_enormous_size_is_dropped_before_it_is_decoded(tmp_path):
    w = standard_world(tmp_path)
    cv2.imwrite(str(w.images / f"{SIGNERS[0]}-g-2a.png"), np.zeros((6000, 6000), np.uint8))       # 36 megapixels, ~40 KB on disk
    assert (w.images / f"{SIGNERS[0]}-g-2a.png").stat().st_size < 200_000
    report = run(w)
    assert report["population"]["dropped"]["genuine query: unreadable or unusable image"] == 1
    assert report["population"]["genuine_queries_scored"] == 23


def test_an_evaluation_with_nothing_in_it_is_simply_not_measurable(tmp_path):
    (tmp_path / "images").mkdir()
    write_manifest(tmp_path / "m.csv", [])
    items, problems = protocol.load_manifest(tmp_path / "m.csv", tmp_path / "images")
    report = protocol.evaluate(items, split="val", decider=lambda q, r: 1 / 0, model_version="m")
    assert items == [] and problems == [] and not report["validity"]["valid"] and not measurable(report)
    assert protocol.render_markdown(report).count("**not measurable**") == 1


# ------------------------------------------------------------------ the ledger and thresholds
def test_every_final_run_is_recorded_and_a_second_model_on_the_same_test_set_is_flagged(tmp_path):
    w = standard_world(tmp_path)
    ledger = tmp_path / "ledger.json"
    first = run(w, ledger=ledger, version="model-1")
    assert check(first, "test_set_used_once")["status"] == "pass" and "first final use" in check(first, "test_set_used_once")["message"]
    assert [e["model_version"] for e in json.loads(ledger.read_text())] == ["model-1"]
    again = run(w, ledger=ledger, version="model-1")
    assert "already evaluated" in check(again, "test_set_used_once")["message"]
    assert len(json.loads(ledger.read_text())) == 1
    second = run(w, ledger=ledger, version="model-2")
    c = check(second, "test_set_used_once")
    assert c["status"] == "warn" and "1 other model version" in c["message"]
    assert second["validity"]["valid"] and "test_set_used_once" in second["validity"]["warnings"]
    assert [e["model_version"] for e in json.loads(ledger.read_text())] == ["model-1", "model-2"]


def test_a_refused_run_does_not_use_up_the_test_set(tmp_path):
    ledger = tmp_path / "ledger.json"
    run(standard_world(tmp_path, signers=SIGNERS[:3]), ledger=ledger)
    assert not ledger.exists()


def test_a_final_run_without_a_ledger_says_so(tmp_path):
    c = check(run(standard_world(tmp_path), ledger=None), "test_set_used_once")
    assert c["status"] == "warn" and "no ledger" in c["message"]


def test_a_damaged_ledger_is_treated_as_empty(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{not json")
    assert protocol.read_ledger(ledger) == []
    ledger.write_text('{"a": 1}')
    assert protocol.read_ledger(ledger) == []


def test_the_test_set_digest_does_not_depend_on_where_the_files_are(tmp_path):
    a = run(standard_world(tmp_path / "x"), version="m")
    b = run(standard_world(tmp_path / "y"), version="m")
    assert a["test_set_digest"] == b["test_set_digest"]


def test_thresholds_are_suggested_from_the_validation_split_only(tmp_path):
    val = run(standard_world(tmp_path, split="val"), split="val", final=False)
    th = val["suggested_thresholds"]
    assert th["basis"].startswith("random_forgery") and "NOT a skilled-forgery operating point" in th["caveat"]
    assert 0 < th["mismatch"] <= th["match"] <= 1
    assert "suggested_thresholds" not in run(standard_world(tmp_path / "t", split="test"))


def test_thresholds_tuned_on_real_forgeries_say_so(tmp_path):
    th = run(standard_world(tmp_path, split="val", skilled=True), split="val", final=False)["suggested_thresholds"]
    assert th["basis"] == "skilled_forgery" and th["caveat"] is None and th["negative_scores"] == 18


# ------------------------------------------------------------------ what the reports contain
def test_reports_contain_counts_and_rates_but_no_names_files_or_signers(tmp_path):
    report = run(standard_world(tmp_path, skilled=True, synthetic=True))
    blob = json.dumps(report) + protocol.render_markdown(report)
    for secret in SIGNERS + ["F-ONE", "F-TWO", "-g-0a", "-sf-0", "-syn-0", ".png"]:
        assert secret not in blob
    assert "model-1" in blob


def test_markdown_lists_every_claim_and_limit(tmp_path):
    text = protocol.render_markdown(run(standard_world(tmp_path)))
    assert text.startswith("# Signature evaluation report") and "## What may NOT be claimed" in text
    assert "Detection of forgeries on the strength of synthetic alterations" in text
    assert "VALID for the metrics below" in text and "(final run)" in text
    bad = protocol.render_markdown(run(standard_world(tmp_path / "z", signers=SIGNERS[:3])))
    assert "NOT VALID" in bad and "**not measurable**" in bad


def test_the_delivered_dataset_cannot_measure_genuine_acceptance_or_skilled_forgery():
    genuine = [{"filename": f"zq{n}.png", "bank": "axis", "signer_id": f"ZQ-DS-{n}", "split": "train" if n < 6 else "test",
                "split_norm": "train" if n < 6 else "test"} for n in range(8)]
    altered = [{"filename": f"ze{n}.png", "bank": "axis", "target_signer_id": f"ZQ-DS-{n // 2}", "split": "train", "split_norm": "train"}
               for n in range(16)]
    report = protocol.describe_signature_dataset({"genuine": genuine, "altered": altered})
    m = report["metrics"]
    for key in ("genuine_match_rate", "genuine_mismatch_rate", "skilled_forgery_match_rate", "skilled_forgery_auc"):
        assert m[key]["measurable"] is False and "value" not in m[key]
    assert m["edited_copy_detection"]["measurable"] and "says nothing about forgeries by another person" in m["edited_copy_detection"]["caveat"]
    assert report["population"]["signers_with_two_or_more_genuine_specimens"] == 0
    assert report["validity"]["valid"] is False and report["validity"]["failed"] == ["independent_genuine_signers", "skilled_forgeries"]
    assert any("not forgery detection" in x for x in report["claims"]["allowed"])
    text = protocol.render_markdown(report)
    assert "ZQ-DS" not in text and "zq3.png" not in text and "16 synthetic alterations" in text


# ------------------------------------------------------------------ the training report no longer calls edits forgeries
def test_training_report_names_synthetic_edits_as_edited_copies_and_records_the_signers_it_used(tmp_path):
    rows, altered, seed = [], [], 0
    for split in ("train", "val", "test"):
        for k in range(2):
            seed += 1
            name = f"{split}{k}.png"
            cv2.imwrite(str(tmp_path / name), synthetic_signature(seed))
            rows.append({"filename": name, "bank": "axis", "signer_id": f"T-{split}-{k}", "split": split, "split_norm": split,
                         "path": tmp_path / name})
            edited = synthetic_signature(seed)
            cv2.line(edited, (30, 20), (200, 70), (30, 30, 120), 3)
            cv2.imwrite(str(tmp_path / f"e-{name}"), edited)
            altered.append({"filename": f"e-{name}", "bank": "axis", "target_signer_id": f"T-{split}-{k}", "split": split,
                            "split_norm": split, "path": tmp_path / f"e-{name}"})
    manifests = {"genuine": rows, "altered": altered}
    cfg = {"positives_per_signer": 1, "impostors_per_signer": 1, "epochs": 40}
    model, report = train_and_evaluate(manifests, cfg)
    summary = report["decisions_with_alteration_check"]["test"]["summary"]
    assert "edited_copy_as_is_accepted" in summary and "edited_copy_as_is_rejected" in summary
    assert "forgery" not in json.dumps(report["decisions_with_alteration_check"])
    assert report["scope"] == SCOPE and any("skilled forgery" in x for x in SCOPE["does_not_measure"])
    assert any("not forgeries by another person" in x for x in report["limitations"])
    fitted = set(model.meta["fitted_signer_hashes"])
    assert fitted == {protocol.signer_hash(f"T-{s}-{k}") for s in ("train", "val") for k in range(2)}
    assert all(t not in json.dumps(model.meta) for t in ("T-train", "T-val", "T-test"))      # hashes only, no IDs
    assert "edited_copy_recaptured" in evaluate_decisions(manifests, "val", model, {**PAIR_CONFIG, **cfg})


# ------------------------------------------------------------------ command line
def fusion_model(path, fitted=None):
    meta = {"version": "fusion-test"}
    if fitted is not None:
        meta["fitted_signer_hashes"] = fitted
    FusionModel({k: 0.5 for k in FEATURES}, 0.0, {k: 0.5 for k in FEATURES}, {k: 0.2 for k in FEATURES},
                {"match": 0.8, "mismatch": 0.55}, meta).save(path)
    return path


def ns(**kw):
    base = dict(dataset=False, manifest=None, images=None, split="val", final=False, model=None, thresholds=None, ledger=None,
                out_dir=None, ref_specimens=None, ref_days=None)
    return argparse.Namespace(**{**base, **kw})


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    import manage
    world = World(tmp_path / "w")
    populate(world, SIGNERS, split="val")
    populate(world, [f"{s}-T" for s in SIGNERS], split="test")
    world.write()
    monkeypatch.setattr(protocol, "default_decider", lambda model, thresholds=None: world.decider())
    return manage, world, tmp_path


def test_command_line_runs_validation_then_a_final_test_with_the_frozen_thresholds(cli, capsys):
    manage, world, tmp = cli
    model = fusion_model(tmp / "model.json", fitted=["0123456789abcdef"])
    common = dict(manifest=str(world.manifest_path), images=str(world.images), model=str(model), ledger=str(tmp / "ledger.json"),
                  out_dir=str(tmp / "out"))
    manage.cmd_eval_signatures(ns(split="val", **common))
    val = json.loads((tmp / "out" / "signature_eval_val.json").read_text())
    assert val["split"] == "val" and val["final"] is False and val["suggested_thresholds"]
    assert (tmp / "out" / "signature_eval_val.md").read_text().startswith("# Signature evaluation report")
    assert not (tmp / "ledger.json").exists()
    manage.cmd_eval_signatures(ns(split="test", final=True, thresholds=str(tmp / "out" / "signature_eval_val.json"), **common))
    final = json.loads((tmp / "out" / "signature_eval_test_final.json").read_text())
    th = val["suggested_thresholds"]
    assert final["final"] is True and f"match={th['match']}|mismatch={th['mismatch']}" in final["model_version"]
    ledger = json.loads((tmp / "ledger.json").read_text())
    assert len(ledger) == 1 and ledger[0]["model_version"] == final["model_version"]
    out = capsys.readouterr().out
    assert "What may NOT be claimed" in out and "ZQX" not in out and ".png" not in out


def test_command_line_refuses_the_test_split_without_final_and_bad_threshold_files(cli):
    manage, world, tmp = cli
    model = fusion_model(tmp / "model.json")
    common = dict(manifest=str(world.manifest_path), images=str(world.images), model=str(model), ledger=str(tmp / "ledger.json"))
    with pytest.raises(SystemExit, match="--final"):
        manage.cmd_eval_signatures(ns(split="test", **common))
    (tmp / "t.json").write_text(json.dumps({"split": "test", "suggested_thresholds": {"match": 0.8, "mismatch": 0.5}}))
    with pytest.raises(SystemExit, match="validation-split report"):
        manage.cmd_eval_signatures(ns(split="test", final=True, thresholds=str(tmp / "t.json"), **common))
    with pytest.raises(SystemExit, match="cannot read the --thresholds report"):
        manage.cmd_eval_signatures(ns(split="test", final=True, thresholds=str(tmp / "absent.json"), **common))
    assert not (tmp / "ledger.json").exists()


def test_command_line_exits_non_zero_when_the_run_is_not_valid(cli):
    manage, world, tmp = cli
    model = fusion_model(tmp / "model.json", fitted=[protocol.signer_hash(SIGNERS[0])])
    with pytest.raises(SystemExit) as exc:
        manage.cmd_eval_signatures(ns(split="val", manifest=str(world.manifest_path), images=str(world.images), model=str(model)))
    assert exc.value.code == 1


def test_command_line_needs_a_model_and_an_evaluation_set(cli):
    manage, world, tmp = cli
    with pytest.raises(SystemExit, match="--manifest and --images"):
        manage.cmd_eval_signatures(ns())
    with pytest.raises(SystemExit, match="no trained signature model"):
        manage.cmd_eval_signatures(ns(manifest=str(world.manifest_path), images=str(world.images), model=str(tmp / "absent.json")))
    with pytest.raises(SystemExit, match="cannot read the manifest"):
        manage.cmd_eval_signatures(ns(manifest=str(tmp / "absent.csv"), images=str(world.images), model=str(fusion_model(tmp / "m.json"))))


def test_command_line_describes_the_delivered_dataset_without_reading_images(tmp_path, monkeypatch, capsys):
    import manage
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "genuine_signatures.csv").write_text("filename,bank,signer_id,split\n" + "".join(f"g{n}.png,axis,ZQ-DS-{n},train\n" for n in range(4)))
    (ds / "forged_signatures.csv").write_text("filename,bank,target_signer_id,split\n" + "".join(f"f{n}.png,axis,ZQ-DS-{n % 4},train\n" for n in range(8)))
    monkeypatch.setattr(manage.Config, "SIGNATURE_DATASET_DIR", ds)
    manage.cmd_eval_signatures(ns(dataset=True, out_dir=str(tmp_path / "out")))
    data = json.loads((tmp_path / "out" / "SIGNATURE_EVALUATION_STATUS.json").read_text())
    assert data["validity"]["valid"] is False and data["population"]["synthetic_alterations_set_aside"] == 8
    assert "not measurable" in (tmp_path / "out" / "SIGNATURE_EVALUATION_STATUS.md").read_text()
    assert "ZQ-DS" not in capsys.readouterr().out
    monkeypatch.setattr(manage.Config, "SIGNATURE_DATASET_DIR", tmp_path / "nowhere")
    with pytest.raises(SystemExit, match="signature dataset not found"):
        manage.cmd_eval_signatures(ns(dataset=True))


# ------------------------------------------------------------------ with the real comparator
def same_hand(base, var, w=320, h=100, sd=1.6):
    """The same pseudo-signature written again: every vertex wobbles a little, the whole thing shifts a little."""
    rng = np.random.default_rng(base)
    img = np.full((h, w, 3), 245, np.uint8)
    pts = np.cumsum(rng.normal(0, 6, (40, 2)), axis=0)
    pts = pts - pts.min(0)
    pts = pts / max(pts.max(), 1) * [w - 40, h - 30] + [20, 15]
    wobble = np.random.default_rng(10_000 * (base + 1) + var)
    pts = pts + wobble.normal(0, sd, pts.shape) + wobble.normal(0, 3, (1, 2))
    cv2.polylines(img, [pts.astype(np.int32)], False, (30, 30, 120), 2, cv2.LINE_AA)
    cv2.line(img, (25, h - 20), (w - 30, h - 25), (30, 30, 120), 2)
    return img


def test_the_real_comparator_runs_through_the_protocol(tmp_path):
    w = World(tmp_path)
    for base, signer in enumerate(("REAL-1", "REAL-2", "REAL-3")):
        for n in range(6):
            w.add(signer, "genuine", f"{signer}-{n}.png", day=DAYS[n // 2], img=same_hand(base, n))
    cfg = {"reference_min_specimens": 3, "min_queries": 2, "min_signers_hard": 3, "random_forgeries_per_signer": 3, "bootstrap": 100}
    report = protocol.evaluate(w.load(), split="test", final=True, decider=protocol.default_decider(None), model_version="baseline",
                               cfg=cfg, ledger_path=tmp_path / "ledger.json")
    assert report["validity"]["valid"] and report["population"]["signers_scored"] == 3
    assert report["population"]["genuine_queries_scored"] == 6 and report["population"]["random_forgeries_scored"] == 9
    assert report["metrics"]["random_forgery_auc"]["value"] > 0.8                    # same hand scores above somebody else's
    assert not report["metrics"]["skilled_forgery_auc"]["measurable"]
    assert json.loads(json.dumps(report))["split"] == "test"                         # the report is plain JSON
