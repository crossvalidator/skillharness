# Measurements

Each context-management step is measured on the same eval prompt as the plain loop. The prompt is
the unfold skill's "seasons" eval: *"I teach grade 6 science. Make me an interactive figure for
tomorrow's lesson on why summer is hotter than winter. I've got about 10 minutes before my next
class."*, level grades 5-8, profile `deep` (8 builds, 10 minutes, $4 cap), model `claude-opus-5-5`.
The transcripts and results are in `docs/measurements/seasons-deep/`.

## Elision (2026-10-30)

Three runs. The baseline ran through the product's Modal path on library v0.1.0; the other two ran
through the library's command line on a laptop (same model, same skill hash, the build on local
Chrome), so the model numbers compare and the wall time does not.

| | Baseline, plain loop | Elision, automatic cache breakpoint only | Elision with the explicit cache point |
|---|---|---|---|
| Builds / requests | 2 / 5 | 4 / 6 | 4 / 6 |
| Input tokens per request | 10.7k, 15.6k, 32.1k, 48.4k, 49.6k | 11.0k, 13.2k, 23.2k, 28.3k, 35.0k, 30.8k | 11.0k, 14.6k, 30.6k, 40.6k, 37.8k, 38.6k |
| of which cache reads | 0, 10.7k, 15.6k, 32.1k, 48.4k | 0, 11.0k, 13.2k, 13.2k, 13.2k, 13.2k | 0, 11.0k, 14.6k, 14.6k, 23.8k, 25.8k |
| Output tokens | 19.7k | 21.6k | 30.9k |
| Elided | none | 7 images, 3 versions | 7 images, 3 versions |
| `fetch_result` calls | n/a | 0 | 0 |
| Model cost | $0.66 | $0.83 | $1.05 |
| Figure | fine | fine | fine |

What the numbers say:

1. **Elision removes what it should.** In the final request of the third run, the stubs stand in
   for about 13k image tokens and 6.5k tokens of superseded source (Anthropic's image rule, the long
   side scaled to 1568 px, then width x height / 750). The model never asked for any of it back,
   and the four-build figures are as good as the two-build baseline's.
2. **The cache point is not optional.** With only the provider's automatic breakpoint, the cache
   read stalled at 13k tokens from the third request on: the automatic breakpoint sits at the end
   of the previous request, inside the turn that is rewritten next, and a version stub rewrites a
   `write_file` call deep in the prefix. With one explicit cache point at the end of the last stable
   request the read grows again turn by turn, one request behind the history. The one-step stall at
   request 4 is expected: the first stub lands before anything the explicit point has covered yet.
3. **At this depth the dollar effect is noise.** Re-sent history is billed as cache reads at
   $0.20 per million tokens, so the 19k tokens elision kept out of the final request were worth
   about half a cent per request. What moves the bill is output tokens ($20 per million; the three
   runs wrote 19.7k, 21.6k and 30.9k of them, which is the model's choice from one run to the next)
   and the fresh tokens of each turn, which both loops pay. The cost column therefore tracks how
   many builds the model chose, not elision. Elision earns its keep on long runs, where it keeps the
   history bounded (an advanced run with 10 builds and 8 images each would otherwise carry about
   150k image tokens by the end), and as the foundation for `fetch_result` and compaction.

Not measured yet: the advanced profile, where the saving should be visible in the cost line; the
OpenAI models, which cache by prefix on their own.
