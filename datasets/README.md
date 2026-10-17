# EquityVoice test payroll datasets (all synthetic - no real people)
Upload any CSV to POST /data/upload. Ground truth for every file is in _ANSWER_KEY.json.
Regenerate/tweak: python tools/gen_payroll.py <outdir>  (edit SCENARIOS in the script)

| File | Rows | Tests |
|---|---|---|
| 01_baseline_fair_control | 5,000 | No bias. Should score ~100 (false-positive check) |
| 02_gender_gap | 5,000 | Women lower in Engineering (-8%) & Finance (-6%); non-binary -4% |
| 02b_gender_gap_partial_fix | 5,000 | Same people as 02 after 70% of the gap is closed (before/after demo) |
| 03_racial_gap | 5,000 | Black staff in Sales (-7%), Hispanic in Operations (-5%), Native American (-6%) |
| 04_disability_gap | 5,000 | Disability -4% overall, extra -6% in Engineering |
| 05_intersectional | 8,000 | Stacked penalties; Black women and women with disabilities hit hardest |
| 06_glass_ceiling_pipeline | 5,000 | Equal pay per level but unequal promotion: big raw gap, ~0 adjusted gap |
| 07_enterprise_large | 50,000 | Mixed biases, performance test |
| 08_small_startup | 60 | Tiny groups: tests privacy suppression (groups < 5 hidden) |
| 09_messy_realistic | 5,050 | Label variants, $ strings, blanks, duplicates, outliers, negative tenure |
