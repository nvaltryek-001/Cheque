"""OCR evaluation without hand-made transcriptions.

The dataset has no text labels, so only these honest measurements are possible:
  * cheque_number exact match – label = 6-digit filename stem
  * account_number in registry – the extracted number must equal one of the account
    numbers that name the genuine signature files of the same bank (an independent
    source; it shows the value exists, not that it belongs to this exact cheque)
  * amount consistency – words and figures parsed to the same value (self-consistency)
  * date validity – 8 digits forming a real calendar date
  * fill rate – share of cheques where each field produced a value
If confirmed labels exist (human-reviewed cheques exported from the app), they are
used for exact-match field accuracy as well.
"""
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

import cv2

from . import amounts
from .engine import BaseEngine
from .fields import FIELD_ORDER, extract_cheque
from .micr import MicrModel


def cer(ref: str, hyp: str) -> float:
    ref, hyp = ref or "", hyp or ""
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / len(ref)


def evaluate(items: list[dict], engine: BaseEngine, micr: MicrModel | None,
             registry: dict[str, set[str]] | None = None, labels: dict[str, dict] | None = None) -> dict:
    registry = registry or {}
    labels = labels or {}
    per_field_filled = defaultdict(int)
    chq_total = chq_ok = 0
    chq_cer = []
    acct_total = acct_in_registry = 0
    amt_both = amt_consistent = 0
    date_valid = 0
    label_hits = defaultdict(lambda: [0, 0])
    per_bank = defaultdict(lambda: defaultdict(int))
    rows, times = [], []
    for it in items:
        img = cv2.imread(str(it["path"]), cv2.IMREAD_COLOR)
        if img is None:
            continue
        t0 = time.time()
        res = extract_cheque(img, engine, micr)
        times.append(time.time() - t0)
        f = {k: v["value"] for k, v in res["fields"].items()}
        bank = it["bank"]
        per_bank[bank]["n"] += 1
        for k in FIELD_ORDER:
            if f[k]:
                per_field_filled[k] += 1
                per_bank[bank][f"filled_{k}"] += 1
        stem = Path(it["filename"]).stem
        if stem.isdigit() and len(stem) == 6:
            chq_total += 1
            hit = f["cheque_number"] == stem
            chq_ok += hit
            per_bank[bank]["cheque_number_ok"] += hit
            per_bank[bank]["cheque_number_n"] += 1
            chq_cer.append(cer(stem, f["cheque_number"]))
        if bank in registry:
            acct_total += 1
            hit = f["account_number"] in registry[bank]
            acct_in_registry += hit
            per_bank[bank]["account_in_registry"] += hit
        n_fig = amounts.parse_figures(f["amount_figures"])
        n_words = amounts.words_to_number(f["amount_words"])
        if n_fig and n_words:
            amt_both += 1
            amt_consistent += n_fig == n_words
            per_bank[bank]["amount_consistent"] += n_fig == n_words
        date_valid += bool(f["date"])
        lab = labels.get(it["filename"])
        if lab:
            for k, v in lab.items():
                if k in FIELD_ORDER and v:
                    label_hits[k][1] += 1
                    label_hits[k][0] += (str(f[k]).strip().lower() == str(v).strip().lower())
        rows.append({"file": f'{it["split"]}/{bank}/{it["filename"]}', **f})
    n = len(rows)
    pct = lambda a, b: round(100.0 * a / b, 1) if b else None
    return {
        "images": n,
        "engine": engine.name,
        "micr_model": micr is not None,
        "fill_rate_pct": {k: pct(per_field_filled[k], n) for k in FIELD_ORDER},
        "cheque_number": {"labelled": chq_total, "exact_match": chq_ok, "accuracy_pct": pct(chq_ok, chq_total),
                          "mean_cer": round(sum(chq_cer) / len(chq_cer), 4) if chq_cer else None},
        "account_number_in_signature_registry": {"checked": acct_total, "found": acct_in_registry,
                                                 "pct": pct(acct_in_registry, acct_total)},
        "amount_words_vs_figures": {"both_parsed": amt_both, "consistent": amt_consistent,
                                    "pct_of_images": pct(amt_consistent, n), "pct_of_parsed": pct(amt_consistent, amt_both)},
        "date_valid_pct": pct(date_valid, n),
        "confirmed_label_accuracy": {k: {"n": v[1], "exact": v[0], "pct": pct(v[0], v[1])} for k, v in label_hits.items()},
        "per_bank": {b: dict(v) for b, v in per_bank.items()},
        "mean_seconds_per_image": round(sum(times) / len(times), 2) if times else None,
        "predictions": rows,
    }
