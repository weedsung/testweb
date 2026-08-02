# Hypertransition Google Trends runner

Temporary research runner for the Obsidian theory note `초고속 전이 사회 이론`.

- First pilot: NFT search-term interest for KR, JP, TW, SG, US and GB.
- Each country is collected in two overlapping windows of five years or less.
- The overlap median ratio aligns the independently normalized windows.
- The stitched result is rescaled to 0–100 within each country.
- This is an approximation for testing the V/H pipeline, not consistently scaled official API data.
- Output commits are produced by GitHub Actions under `hypertransition/output/`.
