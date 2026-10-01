Faça uma sessão de pesquisa para ampliar a base de conhecimento. Escreva tudo em português do Brasil. Suas regras e o formato das notas estão em CLAUDE.md; siga-os à risca.

1. Leia Knowledge/_index.md.

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
