# Genuine signature collection and enrollment

Status: the workflow below is implemented and tested with **synthetic images only**. No real signer has been
enrolled through it, and no accuracy figure for it exists. A reference set that satisfies the policy makes a
`MATCH` *allowed*; it does not make a `MATCH` *reliable* – that needs the evaluation described in
`docs/SIGNATURE_EVALUATION_PROTOCOL.md` on real data.

## 1. Why this exists

A signature verifier is only as good as the reference signatures it compares against. The data used so far
holds **one** genuine signature per signer, cropped from that signer's own cheque. One sample cannot show how a
person's signature varies from one sitting to the next, and verifying the cheque it was cut from is not an
independent test. So enrollment now records *when and how* signatures were written, counts only independent
specimens, and refuses to issue a `MATCH` for a signer whose reference set is too thin.

## 2. Concepts

| Term | Meaning |
|---|---|
| **Signing session** | One sitting in which a signer wrote signatures: the day (`YYYY-MM-DD`, not in the future), how it was captured (paper scan, paper photo, tablet, other), the instrument, a **consent reference** and notes. Recorded by an administrator. |
| **Specimen** | One enrolled signature image. It belongs to a session (or to none – see below), may carry a `source_ref`, and has an origin (`upload` or `dataset_import`). |
| **Independent specimen** | An active specimen that belongs to a session. Specimens that share the same non-empty `source_ref` (cut from the same document) count **once**. |
| **Independent day** | A distinct session date among the independent specimens. Two sessions on one date are one day. |
| **Ready** | At least `enrollment.min_specimens` (default 5) independent specimens **and** at least `enrollment.min_sessions` (default 2) independent days. |
| **Cap** | While the reference set is not ready, a `MATCH` is lowered to `REVIEW` (`enrollment.cap_match_when_not_ready`, default on). |

Specimens with **no session** (old uploads, dataset imports) stay usable as references but do not count: their
provenance is unknown. A `MISMATCH` – including a detected copy-and-alter – is never softened by the cap; the
result only gains a note that few or related specimens cannot capture natural variation. Wrongly *accepting* a
forgery is the costly error, so only the accepting direction is capped.

## 3. Collection protocol (for the people collecting signatures)

**Before anything is written**
* Obtain informed, written consent for biometric data, stating purpose, storage, who can see it and how long
  it is kept. Record the consent form's **reference** in the session – never the form or personal details.
* Collect only what the project needs: a signer ID (e.g. `P017`), not a name, unless the bank record requires it.

**Per signer (targets for a first real collection)**
* At least **10 genuine signatures over at least 2 sessions on different days** (5+ per session; 3 days is better).
  The system's minimum is 5 independent specimens on 2 days; collect more, because some images will be refused or
  turn out poor.
* Natural writing: the signer's usual pen, speed and posture, in a box about the size of a cheque's signature
  field. No tracing, no copying earlier samples, no "neat" signing for the camera.
* Do not use signatures cut from cheques that will later be used to test the system.
* Signers collected to **evaluate** the system (rather than to be verified by it) need **at least 3 signing days**: the references
  come from the first two days and the genuine test signatures from later ones. They must never be used to train or tune
  anything, and each evaluation signer also needs forgeries by other people – see `docs/SIGNATURE_EVALUATION_PROTOCOL.md` §4.

**Capture**
* Flatbed scan at 300 dpi (grey or colour) or a flat, evenly lit photo without shadows or glare; a tablet export is fine.
* **One image per signature**, tightly cropped with a small white margin. If several signatures are written on one
  collection sheet, each box is cropped separately and never twice (a second crop of the same signature is refused).
* File names carry no personal data: `<signer_code>_<date>_<nn>.png`.

**Storage and handling**
* Images and manifests live **outside the git repository** on an encrypted volume. See section 8.
* Keep the paper originals and consent forms under the organisation's own retention rules.

## 4. Enrolling

### 4.1 In the application (administrators)

1. *Signature enrollment* → **New signer** (signer ID, bank).
2. Open the signer → **Record signing session** (day, capture method, instrument, consent reference).
3. In that session's row → **Add signatures** and choose up to 20 images. Each file is judged on its own; rejected
   files are listed with the reason (poor quality, duplicate, same signing as an enrolled specimen, unreadable).
4. Repeat for the next day. The **Reference set** panel shows `independent specimens x / 5` and `signing days y / 2`
   and what is still missing.

### 4.2 In bulk (`manage.py import-collection`)

```bash
python manage.py import-collection --manifest /secure/collection/manifest.csv --images /secure/collection/images --dry-run
python manage.py import-collection --manifest /secure/collection/manifest.csv --images /secure/collection/images --create-signers
```

The manifest (template: `docs/templates/signature_collection_manifest.csv`) has a header row and one line per image:

| Column | Required | Notes |
|---|---|---|
| `signer_code` | yes | 3–40 letters, digits, `_` `-` `.` |
| `session_date` | yes | `YYYY-MM-DD`, the day the signature was written |
| `file` | yes | relative to `--images`; paths outside that folder (including symlinks) are refused |
| `bank` | for new signers | one of the banks the app knows |
| `method`, `instrument` | no | default `paper_scan` |
| `consent_ref` | **yes**, unless `--allow-missing-consent` (test data only) | |
| `source_ref` | no | images cut from the same document share one value |
| `full_name`, `account_number` | no | only used when the signer is created |

Rows with the same signer, day, method, instrument and consent reference become one session. The whole manifest
is validated first; `--dry-run` writes nothing. Every image then goes through the same checks as a web upload.
The report lists problems by line number and signer ID and prints each signer's readiness – never names or images.
The exit status is 1 if any row was invalid or refused.

### 4.3 Through the API
`POST /api/signers/{id}/sessions`, `POST /api/signers/{id}/specimens/batch` (field `files`, `session_id` required),
`GET /api/signers/{id}/enrollment`, `GET /api/enrollment/overview` – see `docs/API.md`.

### 4.4 Existing specimens
Old uploads and dataset imports have no session. If – and only if – it is really known on which day a specimen
was written, record that session and link the specimen (**Session** button on the specimen, or
`PATCH /api/specimens/{id} {"session_id": …}`; audited). Otherwise leave it: it keeps working as a reference but
never counts toward readiness. Specimens imported from the signature dataset are marked `dataset_import` and
are cut from cheque scans, so those signers will not become ready from that data alone.

## 5. What the software checks – and what it cannot

| Check | Effect |
|---|---|
| Image format, size, pixel limit, quality gate | refused (poor quality can be accepted explicitly; audited) |
| Byte-identical or perceptually near-identical image (same signer or another signer) | refused (`duplicate_specimen`, `duplicate_other_signer`) |
| **Same signing**: after alignment ≥ 98 % of the ink agrees with an active specimen of the same signer (a re-scan, re-crop or slightly rotated copy) | refused (`same_signing`); also checked when a specimen is reactivated |
| Session belongs to the signer; date valid and not in the future | refused (`validation_error`) |
| Consent reference present (bulk import) | refused unless explicitly allowed |

| It cannot | So |
|---|---|
| prove on which day someone signed | the session date is the collector's attestation; the audit log records when each session was entered |
| recognise two photocopies of one signing that differ more than 2 % | record sessions and sources honestly; `source_ref` merges known repeats |
| tell a genuine signature from a skilled forgery of it | enrollment relies on trusted staff and consent; forgery handling belongs to evaluation (`docs/SIGNATURE_EVALUATION_PROTOCOL.md`) |

## 6. Effect on verification

Each verification stores `details.reference_set` (`ready`, independent specimens and days, what is missing, the
policy). When the set is not ready and the similarity score alone would have been `MATCH`, the stored decision is
`REVIEW`, `details.capped_by_policy` is true, `details.uncapped_decision` is `MATCH`, the first reason explains why
and the audit entry for the verification says so. The signature screen shows the same in a banner.

## 7. Settings (administrators, *Settings → Signature*)

| Key | Default | Meaning |
|---|---|---|
| `enrollment.min_specimens` | 5 | independent specimens needed |
| `enrollment.min_sessions` | 2 | different signing days needed |
| `enrollment.cap_match_when_not_ready` | true | cap `MATCH` at `REVIEW` when not ready; switching it off is audited and lets a single specimen produce a `MATCH` |
| `signature.max_specimens_per_signer` | 20 (new installs; an existing installation keeps its stored value) | active specimens per signer |

## 8. Data-protection checklist

* Signature images are biometric data: keep the storage volume encrypted (the app only sets file mode 600),
  limit administrator accounts, and review the audit log (every specimen view is recorded).
* Never commit images, manifests, consent forms or databases. `.gitignore` excludes the dataset folders and
  `backend/instance/`; before any push run `python scripts/prepush_audit.py --terms <private terms file>`.
* `retention.days` is a setting, **not** an automated purge: deletion of signers and specimens is manual
  (`DELETE /api/signers/{id}` removes the images too).
* Consent withdrawal = delete the signer. Verifications that reference the signer keep their decision but lose the link.

## 9. What this does not give you

* No skilled forgeries are collected here, so enrollment data alone cannot measure false acceptance; the evaluation protocol
  (`docs/SIGNATURE_EVALUATION_PROTOCOL.md`) defines what extra data that needs.
* "Ready" is a policy threshold chosen as a conservative default, not an optimum derived from data.
* The independence of days is attested, not proven.
* No real collection has been run; every number in the tests comes from generated signatures.
