Run one research session to grow the knowledge base. Your rules and the note format are in CLAUDE.md; follow them strictly.

1. Read Knowledge/_index.md.

2. Pick up to 2 topics, in this order of preference:
   - status `empty`, highest priority first (1 before 2 before 3);
   - priority 1 topics last researched more than 30 days ago, then others more than 60 days ago;
   - topics with the most open questions.
   If Market/data/latest.md shows a major change (for example a new Selic decision), refresh the affected topic first.

3. For each topic:
   - Search the web (at most 12 searches per topic). Prefer primary sources and fetch the page to confirm any number before writing it down.
   - Create or update Knowledge/<note>.md in the format from CLAUDE.md. When updating, keep facts that still hold, replace outdated ones, and add a short "Changed since last review" line under Summary.
   - Link related notes with [[note-name]].

4. If you find an important topic missing from the index, add a row (status `empty`, the priority you judge, a one-line description).

5. Update the rows you worked on in Knowledge/_index.md: status `researched` (or `partial` if big gaps remain), today's date, confidence.

6. Append to Research/log.md:

## YYYY-MM-DD
- Topics: <notes researched>
- Biggest finding: <one line>
- Next: <what to research next and why>

The log entry is sent to the owner's phone: keep it under 6 lines and never include personal data or anything from Deals/.
