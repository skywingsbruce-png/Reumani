# SSc–cGAS–STING Canary Evidence Subset v1 — Audit Report

- source pack: `ssc_cgas_sting_v1` · `source_pack_hash` `9df9ac40181cb25b9018448154f8b450…`
- `protocol_hash` `24ad37a634b094cc82dc54dd7f69e376…`
- `subset_hash` `7430fcbd4c3d1e8f1eb5f37b11f0b3ec…` · audit date 2026-08-01
- **paid LLM calls: 0** · original v1 cards were never modified

> The subset **references** v1 cards by `evidence_id` + `content_hash` and adds an audit overlay.
> No original card was copied-and-edited or overwritten.

## 1. Per-card audit of the four SSc-direct cards

| Card | Verdict | SSc real subject | cGAS–STING measured / perturbed | Perturbation target | Fibroblast | Causal tier |
|---|---|---|---|---|---|---|
| `SSCCGAS-40374521` | **confirmed** | True | True / True | cGAS (small-molecule cGAS inhibition) | True | C4 |
| `SSCCGAS-39827047` | **downgrade** | True | True / True | STING (small-molecule STING inhibitor) + VDAC1 + mitophagy | False | C3 |
| `SSCCGAS-36400785` | **confirmed** | True | True / False | — | True | C1 |
| `SSCCGAS-bcca09cf` | **downgrade** | True | True / False | CLIC4 (NOT cGAS-STING); cGAS used only as a stimulus arm | False | C3 → C1 |

**`SSCCGAS-40374521` — confirmed**  
species: human tissue/fibroblasts/PCLS + mouse bleomycin model  
design: cross-sectional human expression + in-vitro and ex-vivo perturbation + animal model; cross-sectional=True, longitudinal=False, interventional=preclinical_only  
excerpt verbatim: True; excerpt actually supports the card fields: False; limitations sufficient: False  
- stored excerpt is a BACKGROUND sentence ('its involvement in SSc-ILD remains unknown'), not the supporting finding; the finding sentence is in Results/Conclusion
- does_not_support was EMPTY: must state that intervention is preclinical, so this does not establish human direct causality
- overlay adds `does_not_support`: ['human_direct_causality (intervention is preclinical: cultured fibroblasts, precision-cut lung slices, bleomycin mouse model)']

**`SSCCGAS-39827047` — downgrade**  
species: human (CD14+ monocytes from 12 SSc patients; whole skin biopsies)  
design: case-control human monocytes + in-vitro perturbation; cross-sectional=True, longitudinal=False, interventional=in_vitro_only  
excerpt verbatim: True; excerpt actually supports the card fields: True; limitations sufficient: False  
- readout is IFN-beta release in MONOCYTES, not fibroblast activation
- supports_claims wrongly included 'ssc_context_with_fibrosis_readout' - the study does not measure a fibrosis/fibroblast outcome
- sample size IS stated in the abstract (12 SSc patients) but the card recorded 'unknown'
- overlay adds `does_not_support`: ['fibroblast_activation_outcome (monocyte IFN readout only)', 'human_direct_causality']
- overlay removes over-claimed `supports_claims`: ['ssc_context_with_fibrosis_readout']
- corrected sample size: **12** (card said unknown)

**`SSCCGAS-36400785` — confirmed**  
species: human (lesional skin fibroblasts from SSc patients)  
design: observational / correlative; cross-sectional=True, longitudinal=False, interventional=none  
excerpt verbatim: True; excerpt actually supports the card fields: True; limitations sufficient: True  
- strongest SSc-direct human fibroblast card in the pack, but explicitly correlative ('correlate highly with activation of the cGAS-STING/IFN-beta pathway')
- does_not_support already states direct causality is not established

**`SSCCGAS-bcca09cf` — downgrade**  
species: human epithelial (HaCaT keratinocytes), HUVECs, SSc fibroblast conditioned media  
design: in-vitro mechanistic, epithelial focus; cross-sectional=True, longitudinal=False, interventional=in_vitro_on_CLIC4  
excerpt verbatim: True; excerpt actually supports the card fields: False; limitations sufficient: False  
downgrade reason: perturbation targets CLIC4, not cGAS-STING; the study cannot show that cGAS-STING causes fibroblast activation. Cells studied are keratinocytes/endothelium, not fibroblasts.  
- preprint (not peer reviewed)
- card claimed fibroblast_relevance=yes; the studied cells are epithelial
- overlay adds `does_not_support`: ['cgas_sting_perturbation_evidence (intervention is on CLIC4)', 'fibroblast_activation_outcome']

## 2. C3/C4 perturbation audit

Rule applied: a C3/C4 tier requires the abstract to show a perturbation **of cGAS–STING itself**.
Words like *associated, activated, increased, expression, correlation* never justify an upgrade, and a
perturbation aimed at a different molecule does not evidence cGAS–STING causality **for this question**.

| Card | Verdict | Perturbation target | Tier change |
|---|---|---|---|
| `SSCCGAS-41975489` | confirmed | STING signalling in vestibular macrophages (non-SSc) | unchanged |
| `SSCCGAS-41888792` | confirmed | CMPK2 overexpression -> cGAS-STING activation (SLE) | unchanged |
| `SSCCGAS-38671352` | confirmed | mtDNA-cGAS-STING axis in acute pancreatitis lung injury | unchanged |
| `SSCCGAS-38026177` | downgrade | anti-IFN-gamma DNA aptamer (NOT cGAS-STING) | C4 -> C1 |
| `SSCCGAS-41337545` | downgrade | TREX1 augmentation (upstream DNA exonuclease), not cGAS-STING | C3 -> C1 |
| `SSCCGAS-38474239` | confirmed | heat exposure model with cGAS-STING activation readout | unchanged |
| `SSCCGAS-40374521` | confirmed | cGAS (small-molecule cGAS inhibition) | unchanged |
| `SSCCGAS-39827047` | downgrade | STING (small-molecule STING inhibitor) + VDAC1 + mitophagy | unchanged |
| `SSCCGAS-bcca09cf` | downgrade | CLIC4 (NOT cGAS-STING); cGAS used only as a stimulus arm | C3 -> C1 |

**Downgrades: 4** — `SSCCGAS-39827047` scope/claims corrected; `SSCCGAS-38026177` C4 -> C1; `SSCCGAS-41337545` C3 -> C1; `SSCCGAS-bcca09cf` C3 -> C1

Animal multi-model intervention supports **non-human mechanistic** causality only; it is never
promoted to human direct causality.

## 3. Review boundary

All 30 review cards are marked `non_evidentiary_context=true`, carry no independent causal tier, and
are excluded from the core subset. **2** are retained as context-only navigation:

- `SSCCGAS-42222883` — Autoinflammatory syndromes of STING and TREX1 dysfunction.
- `SSCCGAS-40752681` — DAMP signaling networks: from receptors to diverse pathophysiological functions.

## 4. Manual-review assessment (no promotions)

12 items assessed; recommendations: `{'keep_manual': 12}`. **None entered the subset.**

| Identifier | Only missing SSc in title? | Recommendation |
|---|---|---|
| 41453074 | False | keep_manual |
| 41649246 | True | keep_manual |
| 39291025 | True | keep_manual |
| 35686918 | False | keep_manual |
| epmc:PMC13294092 | False | keep_manual |
| 10.64898/2026.06.29.26356321 | True | keep_manual |
| 10.1101/2025.08.13.670134 | True | keep_manual |
| epmc:PMC13058739 | False | keep_manual |
| 10.1101/2021.04.02.438201 | True | keep_manual |
| 39665319 | False | keep_manual |
| 40134549 | False | keep_manual |
| 41090983 | False | keep_manual |

Whether the abstract truly studies SSc and truly measures/perturbs cGAS–STING is marked
`undetermined_without_human_read` — it is deliberately **not** guessed.

## 5. Frozen minimal Canary subset

6 core cards — {'systemic_sclerosis_direct': 3, 'non_ssc_mechanistic_transfer': 3} · tiers `{'C1': 2, 'C3': 2, 'C4': 2}` · preprints 0 · context-only 2

| Card | Scope | Tier (orig→audited) | Verdict | Contradiction |
|---|---|---|---|---|
| `SSCCGAS-40374521` | direct | C4→C4 | confirmed | no |
| `SSCCGAS-36400785` | direct | C1→C1 | confirmed | no |
| `SSCCGAS-39827047` | direct | C3→C3 | downgrade | no |
| `SSCCGAS-38474239` | indirect | C3→C3 | confirmed | yes |
| `SSCCGAS-41888792` | indirect | C4→C4 | confirmed | yes |
| `SSCCGAS-38026177` | indirect | C4→C1 | downgrade | no |

Balance: support vs non-support, direct vs indirect, human vs animal, and design spread (observational / in-vitro / preclinical interventional). 6/6 carry an explicit contradiction or limitation; 2 carry a verbatim contradiction excerpt.

## 6. Evidence gap and what this subset does NOT establish

- `direct_human_causal_count` = **0**
- C5 human-interventional records: **0**
- longitudinal SSc-direct records: **0**

- All selected evidence is ABSTRACT-LEVEL; this is not full-text verification.
- Absence of a C5 record does NOT prove such evidence does not exist.
- Non-SSc mechanistic evidence can NEVER be extrapolated to SSc direct causality.
- Reviews are context-only, are not primary results, and do not affect any causal tier.
- The final scientific conclusion is decided by the downstream Verifier, not by this subset.

**This subset does not state, and must not be read as stating, that cGAS–STING activation directly
causes sustained fibroblast activation in SSc.** The strongest SSc-direct human fibroblast evidence
here is correlative (C1); the strongest perturbation evidence is preclinical (C4, cultured cells /
lung slices / mouse model). Grading is the downstream Verifier's job.

## 7. Integrity

- v1 pack files are byte-identical (verified by the v1 hash tests, which still pass)
- every selected card is referenced by its original `content_hash`
- `subset_hash` is deterministic; offline tests re-verify all of the above
