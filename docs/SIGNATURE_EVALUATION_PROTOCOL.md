# Signature model evaluation protocol

Status: the protocol is implemented (`backend/app/signature/protocol.py`, `python manage.py eval-signatures`) and
tested with **synthetic images only**. It has **not** been run on real data, because no real data that can satisfy it
exists yet. For the signature data delivered with the project it reports, correctly, that genuine acceptance and
skilled-forgery rejection are **not measurable**
(`reports/final/SIGNATURE_EVALUATION_STATUS.md`).

## 1. What question this answers

> If a real signer's reference set is built the way the application builds it (≥ 5 independent specimens from
> ≥ 2 days), how often is (a) a genuine signature written on a later day matched, rejected or sent to review, and
> (b) a forgery matched – for people the model has never seen?

It does **not** answer how well the product works on whole cheques, on other scanners, or in production, and it
says nothing about the OCR half of the system.

## 2. What the earlier evaluation measured instead

| Earlier figure | What it actually tested | Why it is not the answer to §1 |
|---|---|---|
| "positive" pairs | the reference image against a simulated re-capture **of the same image** (rotation, blur, noise, JPEG) | it is the same signing; natural variation between two signings is never seen |
| "altered" / "forgery" | the reference against a script-generated stroke edit **of the same image** | an edit of a specimen is not an attempt by another person to imitate it |
| "other signer" | the reference against another signer's re-captured image | a valid zero-effort ("random") forgery test, but with one reference per signer |
| test split | 20 signers, scored while the model was being developed | repeated use of one test set makes its numbers optimistic |

These figures stay in `reports/final/MODEL_EVALUATION.md` as a **baseline of the image-processing pipeline**.
Reports and screens now call the edits *edited copies*; no number in them is a forgery-detection rate
(`app/signature/training.py: SCOPE`, the *Model training* page).

## 3. Rules the protocol enforces

Each rule is a named check in the report (`validity.checks`). A **run-level** check that fails makes the whole run
invalid and every metric "not measurable"; a **metric-level** check that fails only removes the metrics it names.

| # | Rule | Check | Level |
|---|---|---|---|
| R1 | People are split, not images: every signer is in exactly one of train / validation / test. Splits come from the manifest or are assigned reproducibly from a hash of `seed\|signer`. | `signer_level_split` | run |
| R2 | No image is listed for two different signers (byte-identical files). | `no_duplicate_images_across_people` | run |
| R3 | The model has not seen the evaluated signers. New models store a hash of every signer that fitted the weights (train) or chose the thresholds (validation) in `meta.fitted_signer_hashes`; an overlap stops the run. A model without that record gets a **warning** (it predates the protocol), not a pass. | `model_has_not_seen_these_signers` | run |
| R4 | A signer is evaluated only with **independent** genuine signatures: references are the thinnest set the deployed policy accepts as ready (default 5 specimens spread over the 2 earliest days); queries are genuine specimens written on days **strictly after every reference day**. Specimens that share a sitting with the references are not used; specimens with the same non-empty `source_ref` (or identical bytes) count once. At least 5 qualifying signers are needed to measure anything; fewer than 30 is reported as underpowered. | `independent_genuine_signers`, `queries_are_independent_of_references` | run |
| R5 | A genuine query that is, in fact, a second capture of a reference (perceptual hash distance ≤ 6, or ≥ 98 % ink agreement after alignment) is removed and counted. | `no_leakage_between_references_and_queries` | warning |
| R6 | **Skilled forgeries** are real attempts by other people: ≥ 3 per signer, by ≥ 2 different forgers, and the forger is not the signer. Without them every skilled-forgery metric is "not measurable". | `skilled_forgeries` | metric |
| R7 | **Synthetic alterations** are listed so they can be counted, but never reach a forgery metric. They are reported only as edited-copy detection. | `synthetic_alterations_kept_apart` | – |
| R8 | **Random forgeries** are other signers' genuine queries (5 per signer by default) – a zero-effort test, always reported separately from R6. | – | – |
| R9 | Thresholds are chosen on the **validation** split (`suggested_thresholds` in a validation report) and frozen for the test run (`--thresholds`). If only random forgeries exist the suggestion says it is **not** a skilled-forgery operating point. | – | – |
| R10 | The **test split is used once**: it can only be scored with `--final`, and every final run is written to a ledger (digest of the test set + model version + thresholds). A second model evaluated on the same test set is flagged, because choosing between models on test results makes them optimistic. A run that was refused does not use up the test set. | `test_set_used_once` | run (when used without `--final`) |
| R11 | Uncertainty is shown. Rates carry a 95 % interval from resampling **signers** (the signatures of one person are not independent trials), only when at least 5 signers contribute; a rate with no events also gets the exact upper bound for the share of signers with an event. | – | – |
| R12 | A metric the data cannot support is reported as **not measurable** with the reason. No number is produced. | – | – |

The ledger is a local JSON file (`instance/signature_eval_ledger.json` by default). It is advisory – anybody can
delete it – so it supports honest practice, not enforcement.

## 4. The evaluation data

Kept **outside git** (private, biometric): a folder of images and a manifest CSV. Template with invented rows:
`docs/templates/signature_evaluation_manifest.csv`.

| Column | Meaning |
|---|---|
| `file` | path relative to the images folder (no `..`, no symlinks leaving the folder) |
| `signer_id` | the person the signature belongs to or imitates (a code, not a name) |
| `kind` | `genuine`, `skilled_forgery` or `synthetic_alteration` |
| `session_date` | `YYYY-MM-DD`, the day a **genuine** signature was written (required) |
| `forger_id` | who made a **skilled forgery** (required; must differ from `signer_id`) |
| `source_ref` | the same non-empty value means "cut from the same document / the same signing" |
| `derived_from` | for a synthetic alteration: the genuine file it was made from |
| `split` | optional `train` / `val` / `test`; otherwise assigned per signer |

Minimum to get *any* number: 5 signers, each with ≥ 5 independent genuine specimens on ≥ 2 days **plus** ≥ 3
genuine specimens on later days (so **at least 3 signing days** per signer). To support a claim worth publishing:
**≥ 30 signers in the test split**, ≥ 10 genuine specimens over ≥ 3 days each, and for forgery claims ≥ 3 skilled
forgeries per signer by ≥ 2 forgers who have seen the genuine signature (ideally forgers practise, as real
attackers do). Collect with the enrollment workflow (`docs/SIGNATURE_ENROLLMENT.md`); the people in the test split
must not be used to train or tune anything.

## 5. Running it

```bash
cd backend
# 1. What can the delivered data measure?  (reads the two manifests only)
python manage.py eval-signatures --dataset                     # -> reports/final/SIGNATURE_EVALUATION_STATUS.{md,json}

# 2. Development: score the validation split, as often as you like
python manage.py eval-signatures --manifest M.csv --images DIR --split val --out-dir OUT

# 3. Freeze the model and the thresholds in OUT/signature_eval_val.json, then once:
python manage.py eval-signatures --manifest M.csv --images DIR --split test --final \
        --thresholds OUT/signature_eval_val.json --out-dir OUT
```

Options: `--model` (default: the deployed fusion model), `--ledger`, `--ref-specimens` / `--ref-days` (reference
policy if you changed it in *Settings*), `--out-dir`. The command exits with status 1 when the run is not valid.
Reports contain counts, rates and check messages – never file names, signer IDs or images – so they can be shared
once you have checked that nothing in your own `--out-dir` is private.

## 6. Reading a report

| Metric | Meaning |
|---|---|
| `genuine_match_rate` | share of genuine later-day signatures decided MATCH (the genuine acceptance rate at the operating point used) |
| `genuine_review_rate` | share sent to a human |
| `genuine_mismatch_rate` | share rejected outright – the false-rejection rate. Includes rejections made by the copy-and-alter check |
| `skilled_forgery_*_rate` | the same decisions for real forgeries; `skilled_forgery_match_rate` is the false-acceptance rate that matters |
| `random_forgery_*_rate` | the same for other signers' genuine signatures |
| `*_auc`, `*_eer` | threshold-free separation of genuine scores from the negative class; EER is where false acceptance equals false rejection |

`claims.allowed` and `claims.not_allowed` are generated from the report itself (a statement is allowed only if the run
is valid and the metric measurable), so a summary cannot say more than the data supports. A validation or exploratory run
never yields a "final" claim.

## 7. Limits of the protocol

* It evaluates the **signature comparator** only, on signatures supplied as images. Cropping signatures from real cheques
  (scanner, background, stamps, bank forms) adds its own errors that this does not measure.
* The thresholds are tuned on validation people; a different population may need different ones.
* Forger skill, motivation and information vary. A forgery set made by volunteers is a **lower bound** on a determined attacker.
* The copy-and-alter rule was tuned on synthetic edits. Its false-rejection rate on real re-signed signatures is unknown;
  `genuine_mismatch_rate` is where it will show up – look at it before looking at the match rate.
* The independence of days is attested by the collector, not proven; the content checks in R5 catch re-captures
  but not every one (re-scans that agree less than 98 % after alignment can pass).
* The ledger cannot stop someone from running the test split again; it makes doing so visible in the report.
* Subgroups (bank, pen type, scan quality, age of signature) are not analysed; with < 30 signers there is no power to.

## 8. Current status

| Question | Can the delivered data answer it? |
|---|---|
| Genuine acceptance / false rejection | **No** – one specimen per signer, cut from the signer's cheque, no signing dates |
| Skilled-forgery acceptance | **No** – none exist; the 240 "forged" images are script-generated edits |
| Random-forgery rejection | only as a single-reference baseline with simulated re-captures |
| Edited-copy detection | yes, with caveats (≈ 10 % of the edits contain no visible change) |

Until real data satisfying §4 is collected, the application must not be described as detecting forged signatures, and
`MATCH` on a signature remains decision support that requires a human where the reference set is thin
(`docs/SIGNATURE_ENROLLMENT.md`).
