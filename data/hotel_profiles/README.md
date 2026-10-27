# D7 synthetic test data

60 hotels share the canonical D3/S4 catalogue. There are 24 authored template
reviews per hotel (1440 total), not genuine guest reviews. No travel service
reviews were copied or scraped. The templates were authored in Codex; no live
LLM generation/extraction API run is claimed. The profiles use the auditable
lexical-fixture-baseline-v1 extractor and keep literal review evidence/counts.

Every record is marked is_synthetic. These texts and profiles say nothing about
actual conditions at the real OSM hotels. See docs/hotel-profiles.md and run
scripts/build_hotel_profiles.py --check for read-only verification.
