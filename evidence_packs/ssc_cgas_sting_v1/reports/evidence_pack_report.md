# SSc – cGAS–STING Evidence Pack v1 — Report

- **pack_id**: `ssc_cgas_sting_v1`  
- **protocol_hash**: `24ad37a634b094cc82dc54dd7f69e376a39f777a79b1c658b3f510f4dec5a7a0`  
- **queries_hash**: `15c1955805a70c7e198c168c7d6b1a55cd877304c9e58f97880c4f7cc5fddb78`  
- **pack_hash**: `9df9ac40181cb25b9018448154f8b4502845337ee678d1dd39abf0cdd26676f5`  
- **cutoff_date**: 2026-08-01  
- **paid LLM calls**: **0**

> This pack **collects and grades evidence only**. It states no final scientific conclusion,
> and it is not a systematic review.

## Pipeline counts

| Stage | Count |
|---|---:|
| discovery rows (raw) | 746 |
| unique candidates | 530 |
| exactly verified (SSc-relevant batch) | 135 |
| duplicates recorded | 217 |
| exclusions logged | 476 |
| manual review queue | 12 |
| **EvidenceCards** | **41** |

Verification status breakdown: `{'mismatch': 7, 'verified': 135, 'manual_needed': 2}`

## Distribution

- disease scope: `{'non_ssc_mechanistic_transfer': 7, 'review_navigation': 30, 'systemic_sclerosis_direct': 4}`
- relevance tier: `{'S1': 2, 'S2': 1, 'S5': 5, 'S6': 1, 'S7': 30, 'S8': 1, 'unknown': 1}`
- causal tier: `{'C1': 2, 'C3': 4, 'C4': 5, 'not_applicable': 30}`
- content level: `{'abstract': 41}`
- preprints: 1 · retractions excluded: 0
- cards carrying a contradiction/negative excerpt: 11

## SSc-direct evidence (4)

Direct = the study is *about* SSc (disease named in the title). Abstract-only mentions were
routed to human review rather than counted here.

| Identifier | Year | Relevance | Causal | Species | Status | Title |
|---|---|---|---|---|---|---|
| PMID 40374521 | 2025 | S2 | C4 | human_and_animal | published | Cyclic GMP-AMP synthase expression is enhanced in systemic sclerosis-a |
| PMID 39827047 | 2025 | S1 | C3 | human | published | Aberrant fumarate metabolism links interferon release in diffuse syste |
| DOI 10.1101/2024.03.08.583925 | 2024 | S8 | C3 | human | preprint | Chloride intracellular channel 4 (CLIC4) is a global regulator of type |
| PMID 36400785 | 2022 | S1 | C1 | human | published | Centromere defects, chromosome instability, and cGAS-STING activation  |

## Indirect mechanistic transfer (7)

Non-SSc sources. These can **never** support an SSc-direct causal claim; each card carries
`does_not_support: ssc_specific_claims`.

| Identifier | Year | Relevance | Causal | Species | Status | Title |
|---|---|---|---|---|---|---|
| PMID 41975489 | 2026 | S5 | C4 | human_and_animal | published | STING signaling in vestibular macrophages underlies Ménière's disease  |
| PMID 41888792 | 2026 | S5 | C4 | human_and_animal | published | MSC-derived exosomes ameliorate systemic lupus erythematosus by reprog |
| PMID 41529066 | 2026 | S6 | C1 | cell_line_or_in_vitro | published | HIV-1 derived oligonucleotides induce a type I IFN/STING dependent imm |
| PMID 41337545 | 2025 | unknown | C3 | human | published | Augmentation of DNA exonuclease TREX1 in macrophages as a therapy for  |
| PMID 38671352 | 2024 | S5 | C4 | human_and_animal | published | Mitochondrial (mt)DNA-cyclic GMP-AMP synthase (cGAS)-stimulator of int |
| PMID 38474239 | 2024 | S5 | C3 | animal | published | Early Pulmonary Fibrosis-like Changes in the Setting of Heat Exposure: |
| PMID 38026177 | 2023 | S5 | C4 | human_and_animal | published | Th1/17 polarization and potential treatment by an anti-interferon-γ DN |

## Review / navigation (30)

Reviews were retrieved for terminology expansion and citation-chain navigation only. They carry
`causal_tier = not_applicable` and never substitute for a primary study.

## Evidence gaps (not claims of absence)

- SSc-direct records at causal tier **C5 (human interventional)**: **0**
- SSc-direct records with longitudinal evidence: **0**
- No C5 (human interventional) SSc-direct record was found by the frozen queries. This is an evidence gap, NOT proof that such evidence does not exist.

## Does the pack contain evidence that cGAS–STING *directly causes* sustained fibroblast
activation in SSc?

**Not at the level of direct human causal evidence.** Within this frozen pack the SSc-direct
records reach at most tier C3–C4 (perturbation / multi-model), with no C5 human interventional
record and no longitudinal SSc-direct record. Association and mechanistic-support evidence is
present. Whether that suffices for a causal statement is a scientific judgement that is
deliberately **not** made here — that is the job of the downstream verifier, not of this pack.

## Citation verification

- every card carries at least one T1 source (`pubmed` and/or `crossref`)
- network calls: 294 · failures: 0 · retries: 0
- source failures logged: 0
- network faults are recorded as `source_error`/`manual_needed`, never as `not_found`

## Manual review queue (12)

Items the deterministic rules could **not** decide. None of these are EvidenceCards.

| Identifier | Why it needs a human |
|---|---|
| 41453074 | metadata conflict between T1 sources |
| 41649246 | SSc appears only in the abstract, not the title; cannot decide automatically whether the study is ab |
| 39291025 | SSc appears only in the abstract, not the title; cannot decide automatically whether the study is ab |
| 35686918 | metadata conflict between T1 sources |
| epmc:PMC13294092 | no T1 source could confirm |
| 10.64898/2026.06.29.26356321 | SSc appears only in the abstract, not the title; cannot decide automatically whether the study is ab |
| 10.1101/2025.08.13.670134 | SSc appears only in the abstract, not the title; cannot decide automatically whether the study is ab |
| epmc:PMC13058739 | no T1 source could confirm |
| 10.1101/2021.04.02.438201 | SSc appears only in the abstract, not the title; cannot decide automatically whether the study is ab |
| 39665319 | metadata conflict between T1 sources |
| 40134549 | metadata conflict between T1 sources |
| 41090983 | metadata conflict between T1 sources |

## Reproducibility

- protocol and queries were frozen and committed **before** any search ran
- all artifact hashes are listed in `manifests/hashes.sha256`
- `pack_hash` is deterministic and excludes `created_at`
- EvidenceCard `content_hash` excludes `retrieved_at`
- offline tests re-verify every hash, so a later edit cannot pass silently

## Known limitations

- Abstract-level evidence only; open-access full texts were not parsed in v1.
- Exact citation verification was run on the SSc-relevant layers (A/B/C/P). Layer D/E hits remain
  discovery candidates and were **not** promoted to EvidenceCards in this version.
- Grading is deterministic and keyword-based; it is deliberately conservative and sends
  ambiguous records to human review rather than guessing.
- `zero_hits` and gaps mean only that the frozen queries returned nothing — never that no such
  research exists.
