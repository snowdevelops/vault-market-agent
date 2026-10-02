Revise uma venda concluída, em português do Brasil. Suas regras estão em CLAUDE.md; siga-as. O negócio a revisar e o arquivo a escrever estão no final destas instruções.

Nesta sessão você só escreve em dois lugares: o arquivo de revisão em Reviews/ (criar) e Knowledge/playbook.md (criar ou editar). Não altere nenhum outro arquivo.

1. Leia:
   - a nota do negócio inteira: frontmatter, Plano de preço, Anúncios comparáveis, Histórico de preço, Registro de contatos e Resultado e lições;
   - os briefs em Briefs/ que citam o negócio (procure pelo nome da nota): o que foi previsto e recomendado, e quando;
   - Market/comparables.csv e Market/listings.md: os comparáveis do mesmo modelo ou segmento registrados entre a compra e a venda; e Market/field-notes.md no mesmo período;
   - Market/data/fipe_history.csv e Market/data/history.csv: a FIPE do modelo (ou do modelo mais parecido da cesta) e o crédito para veículos no período;
   - Knowledge/playbook.md, se existir, e as outras revisões em Reviews/.

2. Compare o que foi planejado e previsto com o que aconteceu:
   - Preço: preço de anúncio inicial, preço mínimo (`floor_price`) e o alvo (`total_cost` mais `target_margin_pct`) contra `sold_price`; cada mudança de preço e o que aconteceu com os contatos depois dela.
   - Prazo: dias de `listed_on` até `sold_on` contra `max_days_listed`.
   - Canal: de onde vieram as perguntas, visitas e ofertas, e o canal da venda (`sale_channel`).
   - Mercado: o que os comparáveis e a FIPE indicavam na época contra o preço final.
   - Briefs: quais previsões e recomendações acertaram e quais erraram.
   Se faltar um dado (por exemplo, nenhum contato registrado), diga que falta. Não invente números; marque estimativas como `(estimativa)` e seu raciocínio como `(inferência)`.

3. Escreva o arquivo de revisão neste formato:

```markdown
---
type: deal-review
deal: <nome da nota>
reviewed_on: AAAA-MM-DD
sold_on: AAAA-MM-DD
days_to_sell: <número>
---
# Revisão: <nome da nota>

## Resumo
No máximo 4 linhas: o resultado comparado com o plano.

## Planejado x realizado
| Item | Planejado | Realizado | Diferença |
|---|---|---|---|
(preço de anúncio, preço final, margem, dias até vender, canal)

## O que funcionou

## O que não funcionou

## Lições
Uma por linha, concretas e acionáveis: "Quando <situação>, <o que fazer> (evidência: <dado deste negócio>)".
```

4. Atualize Knowledge/playbook.md (crie se não existir) neste formato:

```markdown
---
type: playbook
updated: AAAA-MM-DD
sold_deals_reviewed: <número de arquivos em Reviews/>
---
# Regras aprendidas com as vendas

Regras tiradas das vendas reais do dono, não de dados gerais de mercado. Toda regra sustentada por menos de 10 vendas é preliminar.

| Regra | Vendas que sustentam | Vendas que contradizem | Status | Revisões |
|---|---|---|---|---|
```

Para cada lição desta revisão: se já existe uma regra equivalente, some 1 em "Vendas que sustentam" (ou em "Vendas que contradizem") e acrescente o link da revisão, como [[Reviews/<nome da nota>]]; se não existe, crie uma linha nova com 1. O Status é `preliminar` enquanto a regra tiver menos de 10 vendas que a sustentam; passa a `confirmada` com 10 ou mais, desde que as vendas que a contradizem sejam bem menos. Não apague regras: marque como `descartada` a que os dados derrubaram. Mantenha as regras mais sustentadas no topo.

Privacidade: Reviews/ e o playbook ficam só no vault e podem ter preços, mas nunca dados pessoais (nomes, CPF, telefones, placas, endereços).
