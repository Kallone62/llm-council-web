# Native engine source snapshot

Unofficial patched Amiable llm-council-core, MIT licensed. Upstream commit: 7a26506ceb6ece564699c20a095215cf13f1a14b (0.53.0). Exact original package source snapshot: 8dced6fdfabad27a093db2c8313611e5cbf267e1. Local package version: 0.53.0+eng18.1. ENG-18 patch is limited to the native gateway policy, compatibility transport, configuration opt-in provenance, and L4 routing. No runtime monkeypatch. Original upstream LICENSE and README retained. This is a candidate, not an official upstream release.

ENG-21 local package version 0.53.0+eng21.1, based on c9230676efb35f5a728e889ee383e2321b18307b (ENG-18 gateway fix). Native run_full_council passes actual safety checks into native normalized raw score aggregation; cap each valid score before averaging, preserve ballots/Borda and missing scores. This is not an official upstream release or a content filtering guarantee.

ENG-22 local package version 0.53.0+eng22.1, based on 3955e022be0aff82ef61f53893dd12ec88909c4c (ENG-21 safety cap). Clamp Pearson r to its mathematical domain and coerce finite model-emitted scores through the native ranking score parser before bias statistics; preserve raw ballots and existing audit formulas. Unofficial source candidate.

ENG-23 local package version 0.53.0+eng23.1, based on 7a598edc36932904ec236ae451ece4e1eb02cf2b (ENG-22 bias fix). Stage 1.5 usage is attributed to the actual normalizer model. Registry estimates require valid raw prompt/completion counts (provider-reported costs, including zero, remain authoritative). The native cost summary labels incomplete known subtotals and unknown costs. No change to Council prompts, ranking, stage execution or gateway routing. Unofficial source candidate.
