# Separate Phase 0 finding: QVault frame registry test

This existing QVault issue was investigated separately at the user's direction and remains untouched.

Command from `E:\QVault\backend`, with `PYTHONDONTWRITEBYTECODE=1` and database, storage, output, and branding paths isolated in `E:\Jana Soft\DefenceStudio\analysis\phase0-baseline`:

`python -m pytest -q -p no:cacheprovider tests/test_frame_extraction_strategies.py::TestExtractionOptions::test_all_four_strategies_are_registered`

Result: **1 failed in 0.99s**. `backend/app/services/frame_extraction_service.py` defines `STRATEGIES` with five entries, adding `all_frames`; `backend/tests/test_frame_extraction_strategies.py::TestExtractionOptions::test_all_four_strategies_are_registered` asserts exact equality with four entries. The extra registered strategy explains the failure. Neither that registry nor the test was modified. No QVault source or runtime data was modified. This is a separate QVault maintenance item.
