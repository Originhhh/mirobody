# Genetic data

Upload a consumer genotype export or a single-sample VCF to keep variant calls
in the selected person's record. Genetic test reports remain ordinary documents;
raw genotype files use a separate import and query workflow.

## Supported uploads

The importer recognizes supported text and CSV exports, including 23andMe,
AncestryDNA, MyHeritage, FTDNA and WeGene, plus VCF and supported compressed
containers. A ZIP with multiple genotype files and a multi-sample VCF are
rejected because the intended file or sample is ambiguous.

The Data page shows import status. A successful import activates the new set;
re-uploading replaces the active collection. A failed import leaves the
previous active set available.

## Queries

| Tool | Use |
| --- | --- |
| `query_genetic_data` | Find calls by rsID, gene or a bounded chromosome region. |
| `query_pharmacogenomics` | Check CPIC drug–gene coverage and which defining sites are present. |

Region queries use `GRCh37`, `GRCh38` or explicitly `raw` coordinates. Results
retain call status and source information. An unsupported or ambiguous call
stays unresolved; an absent site or `no_call` is not a normal result.

Pharmacogenomic coverage does not establish a phenotype or prescribing advice.
The current tool returns `not_determined` for phenotype rather than inferring
star alleles from incomplete array data. Rare variant calls are not converted
into disease diagnoses.

## Export

The active collection can be exported as VCF, or selected rsIDs as FHIR variant
observations. Use your own account token; see
[data export](https://docs.mirobody.ai/en/deployment/export).
Build handling, supported layouts and query examples are in the
[genetics reference](../genetics.md).
