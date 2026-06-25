# CLI-discovery eval results

This directory holds Inspect AI eval run artifacts (`.eval` logs) for the
CLI-discovery suite. The logs are run artifacts and are gitignored — they
stay local. Run the suite with:

```bash
inspect eval evals/cli_discovery/cli_eval.py \
  --model together/moonshotai/Kimi-K2.7-Code \
  --log-dir evals/cli_discovery/results
```

The kimi validation run for togethercomputer/mcp-common#95 wrote its
`.eval` log here; inspect it with `inspect log dump <file>`.
