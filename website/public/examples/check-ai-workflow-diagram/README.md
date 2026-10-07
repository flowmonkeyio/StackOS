# Check a diagram against its written process

This package supplies an authored fictional process, a deliberately flawed
diagram and its corrected counterpart. It demonstrates review of a diagram;
it does not document a real model failure or a customer workflow.

Start with `source-process.md`. Reconstruct the actors, decisions, permitted
transitions and stopping point before looking at the diagrams. Then inspect
`flawed.svg` against that reconstruction. Its appearance is not evidence that
it agrees with the source.

`flawed-text.md` describes the candidate's actual visible nodes and arrows.
After making your own comparison, use `relationship-audit.md` to inspect every
node and relationship, then open `corrected.svg` and `corrected-text.md`.
`diagram-mapping.json` retains node IDs, edge directions, conditions, source
clause references and SVG geometry for both versions.

Both drawings are standalone SVGs with real text, arrowheads, an identifying
title and a description. They were generated deterministically without a
Mermaid runtime, external image, font download or image-generation model. Their
intrinsic size is 360 by 1,136 units. A successful image render establishes
that the SVG can be displayed, not that the process it claims is true.

When embedding a diagram in an article, give the image a short identifying
alternative and make its complete text explanation available nearby. The
internal title and description in an SVG file are not a substitute for the
article's accessible image label and essential explanation. Inspect the actual
published markup and narrow layout rather than assuming file metadata survived.

The written source ends at a Queued confirmation. It deliberately excludes
queue failures, sending, delivery and recipient response. Extending those
boundaries would require a different source process.
