# API and backend test results

Command: `cd backend && python -m pytest -v` (run 2026-10-31, Python 3.13.18, pytest 9.1.1, Linux).
Result: **54 passed, 0 failed** in ≈ 20 s. Tests use a temporary database, storage and model copy, and
the real datasets where present (dataset-dependent tests are skipped automatically if a dataset is missing).

Coverage against the directive's list:

| Required test | Test(s) |
|---|---|
| Application startup, health | `test_startup_and_health` |
| Existing/OCR endpoints, upload, CRUD, report | `test_cheque_ocr_crud_and_report`, `test_batch_processing` |
| Signature blueprint registered once | `test_blueprints_registered_once` |
| Invalid file / spoofed extension / large file / corrupted / missing | `test_invalid_file_type`, `test_extension_spoofing_rejected`, `test_large_file_rejected`, `test_corrupted_image`, `test_missing_file` |
| Preprocessing | `test_preprocess_*` (shape, aspect ratio, empty, tiny, grayscale/RGBA, uneven background), `test_preprocess_preview` |
| Enrollment, duplicate specimen, authorization | `test_enrollment_duplicates_and_authorization`, `test_signer_crud_and_validation`, `test_auditor_cannot_upload` |
| Authentication / authorization | `test_auth_required_everywhere`, `test_login_logout_and_token_revocation`, `test_bad_credentials_and_lockout`, `test_invalid_token`, `test_user_crud_and_roles`, `test_cannot_delete_self`, `test_audit_log_access`, `test_settings_validation` |
| Comparison, missing descriptors, KNN edge cases | `test_verification_flow_and_review`, `test_missing_keypoints_do_not_crash`, `test_ratio_matches_handles_empty_and_short_lists`, `test_decide_*`, `test_features_in_range_and_identity_high` |
| No enrolled reference | `test_verification_requires_reference`, `test_decide_without_references_is_review` |
| Model unavailable | `test_model_status_and_unavailable_model` |
| Dataset readiness, import, path traversal | `test_dataset_audits_and_readiness`, `test_dataset_import_enrollment`, `test_dataset_image_path_traversal` |
| Report generation | PDF + CSV in `test_cheque_ocr_crud_and_report`, `test_verification_flow_and_review` |
| Training job | `test_micr_training_job_sync` |
| Amount parsing / validation | `test_words_to_number[*]`, `test_number_words_roundtrip`, `test_indian_format_and_figures`, `test_rupee_symbol_misread_is_corrected_by_words`, `test_validation_checks` |
| Regression: browser form without file | `test_verify_from_cheque_with_form_fields_only` (bug found by the E2E run, fixed) |

## End-to-end (browser) test
A Playwright script drove Chromium against the running server (`run.py --prod`, fresh database):
**19 / 19 checks passed** – login error state, dashboard shows zeros on an empty system, dataset import
(120 signers), cheque upload with cheque number extracted, signer suggested from account number,
correction + mark reviewed, signature verification rendered a decision, approve review, history,
all 12 navigation screens load, MICR training job completed from the UI, mobile width (390 px) has
no horizontal scroll, sign out. The only console error was the intentional 401 of the wrong-password step.
Additionally checked via the live API: signature training job (background thread) completed, CSV export
(account masked), PDF report rendered.

## Full pytest output
```
platform linux -- Python 3.13.18, pytest-9.1.1, pluggy-1.6.0 -- /usr/local/bin/python3
cachedir: .pytest_cache
rootdir: /home/claude/chequeverify/backend
plugins: platformdirs-4.13.3, anyio-4.15.1
collecting ... collected 54 items

tests/test_api.py::test_startup_and_health PASSED                        [  1%]
tests/test_api.py::test_blueprints_registered_once PASSED                [  3%]
tests/test_api.py::test_auth_required_everywhere PASSED                  [  5%]
tests/test_api.py::test_login_logout_and_token_revocation PASSED         [  7%]
tests/test_api.py::test_bad_credentials_and_lockout PASSED               [  9%]
tests/test_api.py::test_invalid_token PASSED                             [ 11%]
tests/test_api.py::test_user_crud_and_roles PASSED                       [ 12%]
tests/test_api.py::test_cannot_delete_self PASSED                        [ 14%]
tests/test_api.py::test_invalid_file_type PASSED                         [ 16%]
tests/test_api.py::test_extension_spoofing_rejected PASSED               [ 18%]
tests/test_api.py::test_corrupted_image PASSED                           [ 20%]
tests/test_api.py::test_large_file_rejected PASSED                       [ 22%]
tests/test_api.py::test_missing_file PASSED                              [ 24%]
tests/test_api.py::test_auditor_cannot_upload PASSED                     [ 25%]
tests/test_api.py::test_cheque_ocr_crud_and_report PASSED                [ 27%]
tests/test_api.py::test_batch_processing PASSED                          [ 29%]
tests/test_api.py::test_signer_crud_and_validation PASSED                [ 31%]
tests/test_api.py::test_enrollment_duplicates_and_authorization PASSED   [ 33%]
tests/test_api.py::test_verification_requires_reference PASSED           [ 35%]
tests/test_api.py::test_verification_flow_and_review PASSED              [ 37%]
tests/test_api.py::test_preprocess_preview PASSED                        [ 38%]
tests/test_api.py::test_dataset_audits_and_readiness PASSED              [ 40%]
tests/test_api.py::test_dataset_image_path_traversal PASSED              [ 42%]
tests/test_api.py::test_dataset_import_enrollment PASSED                 [ 44%]
tests/test_api.py::test_model_status_and_unavailable_model PASSED        [ 46%]
tests/test_api.py::test_settings_validation PASSED                       [ 48%]
tests/test_api.py::test_audit_log_access PASSED                          [ 50%]
tests/test_api.py::test_dashboard_and_analytics PASSED                   [ 51%]
tests/test_api.py::test_unknown_api_route_json PASSED                    [ 53%]
tests/test_api.py::test_micr_training_job_sync PASSED                    [ 55%]
tests/test_api.py::test_verify_from_cheque_with_form_fields_only PASSED  [ 57%]
tests/test_units.py::test_preprocess_output_shape_and_quality PASSED     [ 59%]
tests/test_units.py::test_preprocess_preserves_aspect_ratio PASSED       [ 61%]
tests/test_units.py::test_preprocess_empty_image PASSED                  [ 62%]
tests/test_units.py::test_preprocess_tiny_and_invalid PASSED             [ 64%]
tests/test_units.py::test_preprocess_grayscale_and_rgba_inputs PASSED    [ 66%]
tests/test_units.py::test_preprocess_uneven_background PASSED            [ 68%]
tests/test_units.py::test_ratio_matches_handles_empty_and_short_lists PASSED [ 70%]
tests/test_units.py::test_features_in_range_and_identity_high PASSED     [ 72%]
tests/test_units.py::test_missing_keypoints_do_not_crash PASSED          [ 74%]
tests/test_units.py::test_decide_without_references_is_review PASSED     [ 75%]
tests/test_units.py::test_decide_identical_vs_different_with_baseline PASSED [ 77%]
tests/test_units.py::test_decide_uses_best_of_multiple_specimens PASSED  [ 79%]
tests/test_units.py::test_fusion_model_monotone_and_thresholds PASSED    [ 81%]
tests/test_units.py::test_words_to_number[Twenty Three Lakh Fifty Thousand-2350000] PASSED [ 83%]
tests/test_units.py::test_words_to_number[One Crore Forty Lakhs-14000000] PASSED [ 85%]
tests/test_units.py::test_words_to_number[Fifty-Five Thousand Four Hundred Ninety Only-55490] PASSED [ 87%]
tests/test_units.py::test_words_to_number[Thirty Three Lakhs Only-3300000] PASSED [ 88%]
tests/test_units.py::test_words_to_number[Forty Two Thousand And Twenty-42020] PASSED [ 90%]
tests/test_units.py::test_words_to_number[Twenty-Six Lakh Twelve Thousan-2612000] PASSED [ 92%]
tests/test_units.py::test_number_words_roundtrip PASSED                  [ 94%]
tests/test_units.py::test_indian_format_and_figures PASSED               [ 96%]
tests/test_units.py::test_rupee_symbol_misread_is_corrected_by_words PASSED [ 98%]
tests/test_units.py::test_validation_checks PASSED                       [100%]

============================= 54 passed in 20.25s ==============================
```
