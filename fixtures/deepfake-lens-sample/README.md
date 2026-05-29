# Deepfake Lens Sample Dataset Layout

This fixture shows the folder layout expected by the local Deepfake Lens dataset,
evaluation, and benchmark commands. The text files are tiny placeholders; they
are not a real benchmark.

```sh
python -m deepfake_lens dataset fixtures/deepfake-lens-sample --manifest-out /tmp/dfl-manifest.json --audit-out /tmp/dfl-audit.json --split-out /tmp/dfl-split.json
python -m deepfake_lens eval fixtures/deepfake-lens-sample --pixel off --json-out /tmp/dfl-eval.json
```
