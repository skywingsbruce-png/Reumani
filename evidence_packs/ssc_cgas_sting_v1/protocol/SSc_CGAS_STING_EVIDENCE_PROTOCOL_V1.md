# SSc – cGAS–STING Evidence Pack: Search & Extraction Protocol V1

**Status:** FROZEN before any search was executed. Any change requires a V2 file with a new
hash and a stated reason; V1 is never overwritten.

## 1. Research question

> Does current evidence support the causal statement that **cGAS–STING activation directly
> causes sustained fibroblast activation in systemic sclerosis (SSc)**?

This pack **collects and grades evidence only**. It does not state a final scientific
conclusion, and it is not a systematic review.

## 2. Search date / cutoff

- `search_date`: the UTC date recorded in `manifests/evidence_pack_manifest.json` (`cutoff_date`).
- Records published after the cutoff are out of scope for this pack version.

## 3. Databases and sources

Free, public, academic sources only.

**T1 — primary verification sources**
1. PubMed / NCBI E-utilities (`esearch`, `esummary`)
2. Europe PMC REST search
3. Crossref REST (`/works/{doi}`)
4. doi.org handle resolution (exact DOI only)
5. PubMed Central — open-access content only

**T2 — supplementary discovery** — bioRxiv/medRxiv APIs; publisher open metadata pages.

**T3 — NEVER acceptable as verification** — general web search snippets, press releases,
blogs, social posts, LLM-generated text, unverified reference lists.

Every final EvidenceCard requires **at least one T1 source**. Preprint-only records are
labelled separately and never counted as peer-reviewed evidence.

## 4. Query strings (frozen)

Stored verbatim and hashed in `queries/*.json`. Term groups:

- **Disease**: `"Systemic Sclerosis"[Mesh]`, `"systemic sclerosis"`, `scleroderma`,
  `"systemic scleroderma"`. Note: `scleroderma` also retrieves localized scleroderma /
  morphea, which is **not** SSc and must be separated at the inclusion step.
- **cGAS–STING**: `"cyclic GMP-AMP synthase"`,
  `"cyclic guanosine monophosphate-adenosine monophosphate synthase"`, `cGAS`, `MB21D1`,
  `STING`, `TMEM173`, `"stimulator of interferon genes"`, `cGAMP`, `"DNA sensing"`,
  `"cytosolic DNA"`.
- **Fibrosis/cell**: `fibrosis`, `fibroblast`, `myofibroblast`, `collagen`,
  `"extracellular matrix"`, `"TGF-beta"`, `senescence`, `"DNA damage"`, `micronuclei`,
  `"chromosomal instability"`.

Query layers:

| Layer | Purpose | Shape |
|---|---|---|
| A | SSc direct evidence | disease AND cGAS–STING |
| B | SSc + fibrosis/fibroblast | disease AND cGAS–STING AND fibrosis |
| C | SSc + upstream mechanism | disease AND (cytosolic DNA / DNA damage / micronuclei / CIN) AND (cGAS/STING/MB21D1/TMEM173) |
| D | non-SSc fibrosis mechanism | cGAS–STING AND fibrosis **NOT** disease |
| E | review navigation | SSc innate immunity / cGAS–STING fibrosis / DNA sensing fibrosis reviews |

**Layer D results may only enter the indirect-mechanism tier** and can never support an
SSc-direct causal statement. **Layer E (reviews) is for discovering primary studies,
expanding terminology and checking citation chains only** — a review never replaces a
primary-study EvidenceCard.

Preprints are searched separately (bioRxiv/medRxiv) and marked
`publication_status=preprint`; if a peer-reviewed version exists, the published version is
kept and the preprint is linked as a previous version (never double counted).

## 5. Inclusion criteria

**SSc direct evidence** — all of:
- study population/material explicitly includes systemic sclerosis;
- the record explicitly measures or perturbs cGAS, STING, MB21D1, TMEM173, cGAMP, or an
  explicitly named cGAS–STING pathway;
- study type is determinable;
- a verifiable PMID or DOI exists (preprints excepted, and marked);
- at least abstract-level verifiable content exists.

**Indirect mechanistic evidence** — other-organ fibrosis, other autoimmune disease, human
fibroblasts, animal fibrosis models, in-vitro mechanism. Must carry
`disease_scope != systemic_sclerosis_direct` and an explicit note that it is mechanistic
transfer evidence only. It is never auto-extrapolated to SSc.

## 6. Exclusion criteria (all logged, never silently dropped)

`STING` used only in its ordinary English sense; no cGAS–STING biology; non-SSc with no
transferable mechanism; comment/editorial/news; unverifiable identifier; duplicate;
retracted; second-hand narrative with no locatable primary study; conference abstract with
insufficient detail; non-biomedical context; test fixture; title-only hit unsupported by the
abstract; duplicate publication of the same study.

## 7. Evidence stratification (two independent dimensions)

**Disease relevance** — `S1` SSc human direct · `S2` SSc human tissue/primary cells ·
`S3` SSc animal model · `S4` non-SSc human fibrosis/autoimmune · `S5` non-SSc animal ·
`S6` in-vitro/cell line · `S7` review/navigation · `S8` preprint.

**Causal design tier** — `C0` background only · `C1` cross-sectional association ·
`C2` temporal/longitudinal · `C3` mechanistic perturbation · `C4` multi-model
interventional · `C5` human interventional / near-direct causal.

Rules: journal prestige and citation counts never imply a causal tier; undetermined →
`unknown`; C3/C4 are never inferred when the abstract shows no explicit perturbation;
animal knockout/inhibition never becomes human direct causality; reviews get no independent
causal tier; tiers are assigned by deterministic rules and the rule ID is stored; anything
requiring judgement goes to the manual-review queue.

## 8. Study-design fields

`study_design, species, disease_scope, sample_source, sample_size, longitudinal_status,
interventional_status, perturbation, comparator, outcome, fibroblast_relevance,
cgas_sting_measurement, causal_tier, content_level`.

Extracted **only** from title, abstract, or open-access full text that explicitly states it.
Absent → `unknown`; never inferred from background knowledge. `sample_size` records explicit
numbers only, never cell counts as patient counts and never technical replicates as
biological replicates. Not determinable from the abstract → `manual_needed`.
**No paid LLM is used for any extraction or judgement.**

## 9. Citation verification

Pipeline is strictly staged:

```
DiscoveryCandidate → ExactCitationVerification → EligibleStudy → EvidenceCard
```

A search hit is a candidate, never evidence. Each candidate is verified for PMID, PMCID,
DOI, title, authors, journal, year, publication type, abstract presence, published/preprint
status, retraction/erratum status, source, exact-query status and verification time.

Status ∈ `verified | mismatch | not_found | suspicious | manual_needed`.

PMIDs are verified against PubMed exactly; DOIs against Crossref/doi.org/Europe PMC exactly;
title/year/journal are cross-compared. **Network faults are never recorded as `not_found`** —
timeout/429/5xx become `source_error`/`manual_needed`. Conflicting metadata for one ID goes
to manual review. `not_found`, `mismatch` and `suspicious` never become EvidenceCards.

## 10. Negative, contradictory and gap evidence

Actively sought and recorded: no direct SSc evidence; SSc studied but cGAS–STING not
measured; activation not associated with fibrosis; STING inhibition without endpoint
improvement; opposite directions across models; inflammation-only without sustained
fibroblast activation; other-organ fibrosis only; in-vitro/animal only; missing
longitudinal/interventional evidence.

A search miss is **not** a negative experimental result: `zero_hits` means only that this
exact query returned no records. No "proves non-existence" claim is ever produced; gaps are
recorded separately as `evidence_gap`.

## 11. Deduplication

Priority: identical PMID → identical normalized DOI → preprint↔published mapping →
normalized title + year + first author → manual review for suspected duplicates.

On merge: PubMed/Europe PMC win for medical metadata; Crossref supplies DOI/publication data;
open-access sources supply content locators. Conflicting fields are **never overwritten** —
they are recorded in `provenance.conflicts`; all source IDs are retained; distinct
experiments are never merged.

## 12. EvidenceCard rules

Stable deterministic `evidence_id`; SHA-256 `content_hash` whose payload excludes
`retrieved_at`, run ids and temporary paths. `content_level=abstract` for abstract-level
evidence; `metadata_only` records cannot support a key claim. Open-access full-text evidence
records PMCID and a section/paragraph locator. `supporting_excerpt` is a genuine, minimal
verbatim excerpt from the source — never machine- or LLM-rewritten, and never a large
copyrighted extract. `supports_claims` lists only claims the evidence explicitly supports;
everything else goes to `does_not_support`. `limitations` must state species, design,
abstract-level and unknown-sample-size boundaries.

## 13. Termination conditions

Stop when the frozen query layers are exhausted for the cutoff date, or a source becomes
persistently unavailable (recorded in `source_failures`). Freezing with
`direct_ssc_evidence_count = 0` and `evidence_gap = true` is an acceptable and honest
outcome; standards are never lowered to make the pack look richer.

## 14. Data versions and output schema

`schema_version` per artifact; `pack_id = ssc_cgas_sting_v1`; hashes in
`manifests/hashes.sha256`; deterministic `pack_hash` (excludes `created_at`).
Outputs: `queries/`, `discovery/`, `verification/`, `normalized/`, `evidence/`, `manifests/`,
`reports/`.

## 15. Hard prohibitions

No paid LLM call of any kind (target: 0). No LLM cleaning, rewriting, completion or
study-design judgement. No fabricated DOI/PMID/sample size/design/conclusion. No test
fixtures (explicitly: `PMID 1002` is banned). No search candidate treated as an exact hit.
No `zero_hits` reported as "no research exists". No non-SSc study presented as SSc-direct
evidence. No review presented as a primary result. No animal/in-vitro result extrapolated to
human clinical causality. No paywalled full text downloaded or committed. No keys, cookies,
auth headers, patient data or absolute local paths in any artifact.
