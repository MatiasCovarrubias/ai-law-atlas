# Notice: AGORA-derived content (CC BY-NC 4.0)

Parts of this dataset are derived from **AGORA** (AI Governance and Regulatory Archive) by the Emerging Technology
Observatory (ETO), Center for Security and Emerging Technology, Georgetown University, licensed under the Creative
Commons Attribution-NonCommercial 4.0 International license (https://creativecommons.org/licenses/by-nc/4.0/).

The following are AGORA-derived and are licensed under **CC BY-NC 4.0** (attribution: ETO, AGORA), not CC BY 4.0:

- **Registry records whose `provenance.added_by` is `connector:agora`** (923 records): their metadata (title,
  dates, jurisdiction and stage as inferred from AGORA) comes from AGORA.
- **Rows of `registry/links.csv` whose `from` field starts with `agora:`**: the links between our instruments and
  AGORA documents.
- **The files in `registry/crosswalk/`**: the matching of our records to AGORA documents and AGORA metadata.

Annotations of instruments whose text was taken from AGORA's plaintext collection (`text_version.source` mentions
AGORA) were produced by us; the annotation content is ours (CC BY 4.0), while the verbatim quotes are excerpts of
official legal texts.

Everything else in this release is CC BY 4.0 (`LICENSE`), except the code, which is MIT (`LICENSE-code`).
