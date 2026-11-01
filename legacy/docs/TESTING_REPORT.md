# Testing Report

## Previously reported targeted results

- Syndicate: five-image API regression reported 50/50 populated fields.
- Axis and ICICI: selected image tests reported successful extraction.
- One ICICI image: subsequent API diagnostic returned all ten target fields.

## Full regression
The attempted 20-image run used an incorrect directory:
new_datset\test

All 20 images were consequently unavailable to that test. This was a dataset path error, not an OCR accuracy result.

## Next test
Locate the actual test images, rerun all four bank groups through the Flask API, and compare extracted values against ground truth.
