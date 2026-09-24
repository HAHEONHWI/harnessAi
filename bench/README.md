# Benchmark tasks

`harness bench` runs the same tasks with the harness and with single agents, then scores each result with hidden tests.

```bash
python3 engine/harness.py bench validate          # check every task (starter fails, solution passes)
python3 engine/harness.py bench --arms harness,sol,claude --trials 3
python3 engine/harness.py bench --task 01-bugfix-textstats --arms harness,claude
python3 engine/harness.py bench report --out ~/.ai-harness/bench/<run>
```

Each approach gets a fresh Git repo with the starter files, the same task text, and the same visible test command:
the harness uses it as `verify_command`, a single agent is told to run it before finishing.
Single agents run once with no fallback (`solo` mode: may edit and run commands; Claude's budget is `--budget`, default $20).
After an approach finishes, the hidden tests are copied in and run. Results go to `~/.ai-harness/bench/<timestamp>/`
(`results.json`, `report.md`, and `work/` with every repo for inspection). Reusing `--out` resumes unfinished trials.

| task | size | what it tests |
|---|---|---|
| 01-bugfix-textstats | small | bug report to fix in one file (Unicode words, ties, rounding) |
| 02-feature-inventory | medium | several features in one class (CSV import with errors, undo, JSON) |
| 03-multi-library | large | four features across modules (search, fines, reservation holds, reports) |
| 04-rework-pricing | medium | exact money rules with Decimal (bulk tiers, coupons, tax, shipping) |
| 05-spreadsheet-engine | hard | formula parser, precedence, lazy IF, error propagation, cycles, 3000-cell chains |
| 06-mini-sql | hard | SQL lexer/parser/executor: joins, GROUP BY/HAVING, NULL logic, ORDER BY rules |

## Adding a task

```
bench/tasks/<id>/
  task.json     {"title", "size", "command", "scope": [...], "visible_test", "hidden_count"}
  repo/         starter files the agents see (include visible tests)
  hidden/       test_*.py copied in only for scoring
  solution/     reference files overlaid on repo/ by `bench validate`; never shown to agents
```

The task text must specify everything the hidden tests check. Keep tasks standard-library Python so they run anywhere.
`hidden_count` is the number of hidden tests, so an import error still scores 0 out of the full count.
