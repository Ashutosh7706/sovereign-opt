# Third-party notices

Shipped inside the product (`frontend/vendor/`, licence texts alongside):
- React 18.3.1 and React DOM 18.3.1: MIT License, (c) Meta Platforms, Inc. and affiliates
- htm 3.1.1: Apache License 2.0, (c) Google LLC
- three.js r128 (three.min.js + examples/js/controls/OrbitControls.js): MIT License, (c) 2010-2021 three.js authors (frontend/vendor/LICENSE-three)

Python runtime dependencies are installed from `requirements.txt`. All are permissively licensed
(MIT / BSD / Apache-2.0 / PSF). The full list with licences is produced by
`python backend/scripts/supply_chain.py licenses` and recorded in `sbom.cdx.json`.

Optional components under their own terms: CuPy (MIT), Gurobi (commercial licence, not
distributed), the Anthropic SDK (MIT; SKU-1 builds only), and Ollama/model weights (per-model
licence; check before on-prem deployment).
