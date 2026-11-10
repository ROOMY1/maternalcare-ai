Query mode: {'translate': True, 'normalize': False}

### Retrieval quality (top-4, gold = source + page, no LLM)

| Subset | n | page hit@1 | page hit@4 | page MRR | document hit@4 |
|---|---|---|---|---|---|
| all | 22 | 0.318 | 0.545 | 0.394 | 0.818 |
| en | 13 | 0.385 | 0.615 | 0.449 | 0.846 |
| ar | 9 | 0.222 | 0.444 | 0.315 | 0.778 |

Page hit = the exact gold page (or a listed alternative) is retrieved; document hit = any page of the gold document is retrieved (the same recommendation is often repeated on summary pages).

| id | lang | gold page rank | gold doc rank | top-1 retrieved | top-1 similarity |
|---|---|---|---|---|---|
| q01 | en | miss | 1 | WHO_antenatal-care_2016.pdf p.3 | 0.790 |
| q02 | ar | miss | miss | WHO_essential-practice-pcpnc_2015.pdf p.60 | 0.649 |
| q03 | en | 1 | 1 | WHO_preeclampsia-eclampsia_2011.pdf p.10 | 0.721 |
| q04 | ar | miss | 2 | WHO_managing-complications_2017.pdf p.211 | 0.731 |
| q05 | en | 1 | 1 | WHO_calcium-preeclampsia_2018.pdf p.8 | 0.762 |
| q06 | en | 4 | 2 | WHO_antenatal-care_2016.pdf p.66 | 0.710 |
| q07 | ar | 1 | 1 | NICE_diabetes-pregnancy_2015.pdf p.13 | 0.678 |
| q08 | en | 4 | 3 | MTB_ibuprofen_2025.pdf p.1 | 0.734 |
| q09 | ar | miss | miss | WHO_diabetes-pregnancy-care_2025.pdf p.1 | 0.635 |
| q10 | en | miss | 4 | WHO_essential-practice-pcpnc_2015.pdf p.143 | 0.731 |
| q11 | ar | miss | 3 | WHO_postnatal-care_2022.pdf p.15 | 0.702 |
| q12 | en | 3 | 1 | WHO_postnatal-care_2022.pdf p.169 | 0.799 |
| q13 | en | 1 | 1 | FDA_food-safety-pregnancy_2022.pdf p.5 | 0.641 |
| q14 | ar | 2 | 1 | FDA_advice-eating-fish_2021.pdf p.2 | 0.617 |
| q15 | en | 1 | 1 | NICE_fetal-monitoring-labour_2022.pdf p.7 | 0.710 |
| q16 | en | miss | 1 | WHO_pph-prevention-treatment_2012.pdf p.1 | 0.780 |
| q17 | ar | 3 | 3 | WHO_managing-complications_2017.pdf p.173 | 0.690 |
| q18 | ar | miss | 1 | WHO_iron-folic-acid_2012.pdf p.1 | 0.823 |
| q19 | en | 1 | 1 | WHO_managing-complications_2017.pdf p.199 | 0.716 |
| q23 | en | miss | miss | WHO_postnatal-care_2022.pdf p.41 | 0.672 |
| q24 | ar | 1 | 1 | MTB_ibuprofen_2025.pdf p.1 | 0.707 |
| q25 | en | miss | miss | WHO_diabetes-pregnancy-care_2025.pdf p.43 | 0.721 |

### Threshold calibration

- In-scope top-1 similarity: min 0.617, values [0.617, 0.635, 0.641, 0.649, 0.672, 0.678, 0.69, 0.702, 0.707, 0.71, 0.71, 0.716, 0.721, 0.721, 0.731, 0.731, 0.734, 0.762, 0.78, 0.79, 0.799, 0.823]
- Out-of-scope top-1 similarity: max 0.533, values [0.384, 0.4, 0.404, 0.407, 0.415, 0.421, 0.424, 0.424, 0.45, 0.466, 0.472, 0.48, 0.48, 0.482, 0.482, 0.498, 0.528, 0.533]
- Best balanced accuracy 1.0 for thresholds 0.54–0.61; chosen (middle) **0.57**
- At the chosen threshold: in-scope kept 22/22, out-of-scope blocked 18/18
