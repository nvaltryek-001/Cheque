# Signature Verification Design

## Objective
Compare a signature extracted from a cheque with an authorized specimen signature.

## Planned stages
1. Detect signature region.
2. Crop and normalize signature.
3. Enroll authorized specimen securely.
4. Extract signature embeddings.
5. Compare embeddings.
6. Calibrate similarity thresholds.
7. Return match, mismatch or review-required.
8. Log the result securely.

## Candidate model
Siamese neural network with contrastive or triplet learning.

## Evaluation
Measure false acceptance rate, false rejection rate, ROC-AUC and equal error rate.

Similarity must not be treated as conclusive proof of authenticity.
