# Agent folder (lives in your private vault)

Copy this folder into your Obsidian vault and name it whatever you set as `AGENT_DIR` in `.env` (default `Business`). The agent can only read and write inside this folder; the rest of your vault is invisible to it.

- `CLAUDE.md`: the agent's rules. It loads them automatically on every run.
- `Knowledge/`: built by the agent through research. Start reading at `Knowledge/_index.md`.
- `Research/log.md`: one short entry per research run.
- `Briefs/`: weekly briefs.
- `Deals/`: your deal notes (the agent can read but never edit them). Copy `Templates/vehicle-deal.md` to start one.
- `Market/`: data from the daily script, your watchlist and your hypotheses.

## Dashboard (Dataview plugin)

~~~
```dataview
TABLE vehicle, status, total_cost, fipe_at_purchase, listed_on, (date(today) - listed_on).days AS "days listed"
FROM "Business/Deals"
WHERE status != "sold" AND status != "dropped"
SORT listed_on ASC
```
~~~
Change `"Business/Deals"` if you named the folder differently.
