# @altera-ai/taxonomy

Versioned canonical taxonomy for retailer category mapping.

**Status:** placeholder. The real tree is built out in a later phase.
See `docs/data/taxonomy.md` for the canonical specification, including
the rule that a published `tree.yaml` is immutable — corrections ship
as a new version, never as an in-place edit.

## Layout

```
packages/taxonomy/
├── src/index.ts            # version constants
├── versions/
│   └── 0.0.1/
│       └── tree.yaml       # the canonical tree for v0.0.1
└── package.json
```
