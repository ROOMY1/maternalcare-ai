# Benchmark ground-truth verification (2026-11-10)

Method: each ground truth was checked against the text of the cited PDF page, extracted
independently of the project pipeline with `pdftotext` (poppler), page by page. Key facts of
every ground truth were searched on that page. Arabic pages were checked after removing
tatweel characters. Check performed with LLM assistance; not reviewed by a clinician.

| Question | Source page | Result |
|---|---|---|
| q01, q02 | WHO_antenatal-care_2016.pdf p.18 | Verified (minimum of eight contacts). q02 note: WHO PCPNC 2015 p.60 says 4 visits; the newer 2016 guideline is used as ground truth. |
| q03 | WHO_preeclampsia-eclampsia_2011.pdf p.10 | Verified |
| q04 | WHO_nonsevere-hypertension-drugs_2020.pdf p.10 | Verified |
| q05 | WHO_calcium-preeclampsia_2018.pdf p.8 | Verified |
| q06 | WHO_hyperglycaemia-criteria_2013.pdf p.5 | Verified |
| q07 | NICE_diabetes-pregnancy_2015.pdf p.13 | Verified |
| q08 | FDA_nsaids-20-weeks_2020.pdf p.1 | Verified |
| q09 | MTB_acetaminophen_2025.pdf p.1 | Verified |
| q10, q11 | WHO_breastfeeding-facilities_2017.pdf p.11 | Verified |
| q12 | WHO_postnatal-care_2022.pdf p.15 | Verified |
| q13 | FDA_food-safety-pregnancy_2022.pdf p.5 | Verified |
| q14 | FDA_advice-eating-fish_2021.pdf p.1 | Verified (all 7 fish) |
| q15 | NICE_fetal-monitoring-labour_2022.pdf p.8 | Verified |
| q16 | WHO_pph-prevention-treatment_2012.pdf p.10 | Verified |
| q17 | WHO_pph-roadmap-ar_2024.pdf p.7 | Verified (500 ml, 24 hours, leading cause of maternal deaths) |
| q18 | WHO_antenatal-care-summary-ar_2016.pdf p.2 + WHO_iron-folic-acid_2012.pdf p.8 | Verified (daily; weekly option if side effects) |
| q19 | WHO_managing-complications_2017.pdf p.199 | Verified |
| q20-q22 | out of scope | Expected behaviour: "not found" (valid) |
| q23-q25 | dose seeking | Expected behaviour: no dose given (valid) |

Result: 25/25 ground truths valid; no corrections needed.
