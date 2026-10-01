# Business folder (lives in your private vault)

Copy this whole `Business/` folder into your Obsidian vault root. Everything in it
stays on your machines. Only the code repository goes to GitHub.

Add this line to the vault's root `CLAUDE.md`:

```
For anything in Business/, read Business/_rules.md first and follow it.
```

## New deal checklist
1. Copy `Templates/vehicle-deal.md` into `Deals/` and rename it.
2. Do the due diligence section before paying.
3. Add the FIPE code to `Market/watchlist.json`.
4. Log comparables, price changes and leads as they happen. The brief is only as good as these logs.

## Dashboard (Dataview plugin)

~~~
```dataview
TABLE vehicle, status, total_cost, fipe_at_purchase, listed_on, (date(today) - listed_on).days AS "days listed"
FROM "Business/Deals"
WHERE status != "sold" AND status != "dropped"
SORT listed_on ASC
```
~~~
