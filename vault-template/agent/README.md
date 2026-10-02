# Pasta do agente (fica no seu vault privado)

Copie esta pasta para o seu vault do Obsidian e dê a ela o nome definido em `AGENT_DIR` no `.env` (padrão `Business`). O agente só lê e escreve dentro desta pasta; o resto do seu vault fica invisível para ele.

- `CLAUDE.md`: as regras do agente. Ele as carrega automaticamente em toda execução.
- `Knowledge/`: construída pelo agente com pesquisa. Comece a ler por `Knowledge/_index.md`.
- `Research/log.md`: uma entrada curta por sessão de pesquisa.
- `Briefs/`: briefs semanais.
- `Deals/`: suas notas de negócios (o agente lê, mas nunca edita). Copie `Templates/vehicle-deal.md` para começar uma.
- `Market/`: dados do script diário, sua lista de veículos acompanhados, suas hipóteses e os dados que você registra pelo Telegram (`comparables.csv` e `field-notes.md`, criados no primeiro uso) e `listings.md`, onde você anota à mão os anúncios do mercado local.
- `Reviews/`: uma revisão por venda, escrita pelo agente depois de cada `/venda`. As lições vão para `Knowledge/playbook.md`.

Os nomes de pastas, de notas e as chaves do frontmatter ficam em inglês porque os scripts e o Dataview dependem deles.

## Painel (plugin Dataview)

~~~
```dataview
TABLE vehicle, status, total_cost, fipe_at_purchase, listed_on, (date(today) - listed_on).days AS "dias anunciado"
FROM "Business/Deals"
WHERE status != "sold" AND status != "dropped"
SORT listed_on ASC
```
~~~
Troque `"Business/Deals"` se você deu outro nome à pasta.
