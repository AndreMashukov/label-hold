# The three fixture triples (HK-HOLD-MILK, HK-RELEASE,
# HK-INCOMPLETE) use synthetic-but-plausible
# spec PDFs, CoA PDFs, and label photos. For now each directory exists
# empty so the build script paths are stable.

HK-HOLD-MILK: Honey BBQ. Ingredients include butter and whey (dairy). No "Contains: Milk" on the printed label. Expected verdict: HOLD, undeclared = [milk].

HK-RELEASE: Honey BBQ with "Contains: Milk" on the printed label. Expected verdict: RELEASE.

HK-INCOMPLETE: CoA file is missing. Expected verdict: HOLD, reason = incomplete_packet. LoopAgent must iterate at least once before exiting.