Faça uma sessão de pesquisa para ampliar a base de conhecimento. Escreva tudo em português do Brasil. Suas regras e o formato das notas estão em CLAUDE.md; siga-os à risca.

1. Leia Knowledge/_index.md. Se algum dos temas obrigatórios abaixo não tiver linha no índice (procure pelo nome da nota), acrescente a linha no final da tabela exatamente como está aqui, com status `empty` e sem data nem confiança. Não altere as linhas que já existem.

   Temas obrigatórios:

| Nota | Tema | Prioridade | Status | Última pesquisa | Confiança |
|---|---|---|---|---|---|
| [[vehicle-auctions]] | Leilões de veículos como canal de compra: como funcionam (bancos, seguradoras, órgãos públicos), taxas e comissão do leiloeiro, riscos (sinistro, documentação, débitos, retirada) e quais leiloeiros atuam perto de Uberlândia | 1 | empty | | |
| [[consignment-sales]] | Venda em consignação: como as lojas trabalham com carros consignados, contrato, responsabilidades de cada lado e comissões típicas | 2 | empty | | |
| [[sales-calendar]] | Calendário anual de vendas: 13º salário, temporada de IPVA, restituição do Imposto de Renda, feriados, volta às aulas e o efeito de cada um na demanda e nos preços | 1 | empty | | |
| [[insurance-by-model]] | Custo do seguro de cada modelo popular e como ele pesa na escolha do comprador | 2 | empty | | |
| [[local-auto-financing]] | Bancos e financeiras que financiam compradores de carros populares usados em Uberlândia e as exigências típicas (entrada, score, idade do veículo, prazo, taxa) | 1 | empty | | |

2. Escolha até 2 temas, nesta ordem de preferência:
   - status `empty`, maior prioridade primeiro (1 antes de 2, 2 antes de 3);
   - temas de prioridade 1 pesquisados há mais de 30 dias, depois os demais pesquisados há mais de 60 dias;
   - temas com mais perguntas em aberto.
   Se Market/data/latest.md mostrar uma mudança grande (por exemplo, uma nova decisão sobre a Selic), atualize primeiro o tema afetado.

3. Para cada tema:
   - Pesquise na web (no máximo 12 buscas por tema). Prefira fontes primárias e abra a página para confirmar qualquer número antes de anotá-lo.
   - Crie ou atualize Knowledge/<note>.md no formato de CLAUDE.md. Ao atualizar, mantenha os fatos que ainda valem, substitua os desatualizados e acrescente uma linha curta "Mudou desde a última revisão" logo abaixo do Resumo.
   - Ligue notas relacionadas com [[note-name]].

4. Se encontrar um tema importante que falta no índice, acrescente uma linha (status `empty`, a prioridade que você julgar, uma descrição de uma linha).

5. Atualize as linhas em que trabalhou em Knowledge/_index.md: status `researched` (ou `partial` se restarem lacunas grandes), a data de hoje e a confiança.

6. Tradução: se uma nota de conhecimento que você for atualizar, ou o próprio Knowledge/_index.md, ainda estiver em inglês, traduza para o português enquanto atualiza. Mantenha sem traduzir: nomes de arquivos, chaves de frontmatter, links [[note-name]], os valores de status (`empty`, `partial`, `researched`) e de confiança (`low`, `medium`, `high`).

7. Acrescente ao final de Research/log.md:

## AAAA-MM-DD
- Temas: <notas pesquisadas>
- Principal descoberta: <uma linha>
- Próximo: <o que pesquisar a seguir e por quê>

A entrada do log é enviada para o celular do dono: use menos de 6 linhas e nunca inclua dados pessoais nem nada de Deals/.
