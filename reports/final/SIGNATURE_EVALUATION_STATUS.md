# Signature evaluation report

Protocol version 1 · split **all** · generated 2026-11-01T04:21:54+00:00

**Validity: NOT VALID – the metrics are not measurable or not trustworthy**

## Population

* signers: 120
* genuine specimens: 120
* signers with two or more genuine specimens: 0
* skilled forgeries listed: 0
* synthetic alterations set aside: 240

## Validity checks

| Check | Status | Detail |
|---|---|---|
| signer_level_split | PASS | 120 signers, each in exactly one split (genuine specimens train/val/test: 80/20/20) |
| independent_genuine_signers | FAIL | 0 of 120 signers have two or more genuine specimens (most specimens per signer: 1); the manifest has no signing-date column and each specimen was cut from the signer's cheque, so there are no independent genuine queries: genuine acceptance and false rejection are NOT MEASURABLE |
| skilled_forgeries | FAIL | 0 skilled forgeries (the manifest records no forger); the 240 'forged' rows are script-generated stroke edits of the specimen itself: false acceptance of skilled forgeries is NOT MEASURABLE |
| synthetic_alterations_kept_apart | PASS | 240 synthetic alterations are reported only as edited-copy detection, never as forgeries |
| test_set_used_once | WARN | the test split of this dataset was used repeatedly while the signature model was developed: treat any number taken from it as exploratory |

## Metrics

| Metric | Result |
|---|---|
| genuine_match_rate, genuine_review_rate, genuine_mismatch_rate, skilled_forgery_match_rate, skilled_forgery_review_rate, skilled_forgery_mismatch_rate, skilled_forgery_auc, skilled_forgery_eer | **not measurable** – one genuine specimen per signer, cut from the signer's cheque; no signing dates; no real forgeries |
| random_forgery_match_rate, random_forgery_review_rate, random_forgery_mismatch_rate, random_forgery_auc, random_forgery_eer | measurable, with this caveat: single-reference baseline only: the genuine queries are simulated re-captures of the reference image, not independent signings |
| edited_copy_detection | measurable, with this caveat: 240 synthetic stroke edits of the specimen itself; about 10 % contain no visible edit and cannot be detected; says nothing about forgeries by another person |

## What may be claimed

* Edited copies of an enrolled specimen (synthetic stroke additions or erasures) are detected by an alignment check; the counts are in reports/final/MODEL_EVALUATION.md. This is tamper-evidence for copied images, not forgery detection.
* On a single-reference baseline with simulated re-captures, signatures of different signers are separated by the similarity score (same report).

## What may NOT be claimed

* Any genuine-acceptance or false-rejection rate for real signers.
* Any statement about resistance to skilled forgery.
* Population-level accuracy: every signer has one specimen, so no signer can be evaluated with independent signatures.
* Detection of forgeries on the strength of synthetic alterations: those are edits of a specimen and may only be cited as edited-copy detection.
* Performance on whole cheques, other scanners, or signatures captured differently from the evaluation data.
* Suitability for production use: this protocol measures one component on one data set.
