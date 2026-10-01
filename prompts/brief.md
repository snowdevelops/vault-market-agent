Escreva o brief desta semana, em português do Brasil. Suas regras estão em CLAUDE.md; siga-as à risca.

Leia, nesta ordem:
1. Market/data/latest.md e as últimas 12 semanas de Market/data/history.csv
2. Knowledge/_index.md e, depois, as notas de conhecimento ligadas a qualquer coisa que tenha mudado
3. Todas as notas em Deals/ cujo `status` não seja `sold` nem `dropped`
4. Market/hypotheses.md
5. O arquivo mais recente em Briefs/, se houver, para não se repetir

Depois pesquise na web os últimos 7 dias (no máximo 15 buscas):
- Mercado brasileiro de veículos usados (divulgações mensais da Fenauto e da Fenabrave)
- Crédito para veículos: juros, aprovações, entrada, inadimplência
- Preços de carros chineses e elétricos no Brasil e o efeito nos usados a combustão
- Qualquer coisa específica do mercado de carros de Uberlândia ou do Triângulo Mineiro
- Os modelos dos negócios ativos (recalls, lançamentos de novos modelos, mudanças de preço)

Escreva Briefs/AAAA-MM-DD.md (data de hoje) com estas seções, nesta ordem e com estes títulos exatos:

## Resumo
No máximo 5 linhas curtas: a mudança mais importante e o que precisa de atenção em cada negócio ativo (cite os negócios só pelo nome da nota). Esta seção é enviada para o celular do dono, então NÃO pode conter preço de compra, custo, margem, preço mínimo, preço de anúncio nem lucro. Escreva "revisar preço" ou "manter", nunca os números.

## O que mudou na semana
Só mudanças reais, cada uma com o link da fonte. Se nada importante mudou, diga isso em uma linha.

## Negócios ativos
Para cada negócio: dias desde o anúncio comparados com `max_days_listed`, preço de anúncio atual comparado com a FIPE, movimento de contatos e uma recomendação (manter, revisar preço ou mudar de canal) com o raciocínio em duas ou três frases, usando as notas de conhecimento. Mostre os números que usou. Se não houver negócios ativos, diga isso em uma linha.

## Hipóteses
Para cada hipótese, diga se as evidências da semana a reforçam, a enfraquecem ou não a afetam. Acrescente linhas datadas à tabela de evidências no final de Market/hypotheses.md, mantendo as colunas que ela já tem.

## Atualizações de conhecimento
Notas de conhecimento que as notícias da semana deixaram desatualizadas. Corrija fatos pequenos direto nessas notas (com fonte e data); liste aqui as lacunas maiores para a próxima sessão de pesquisa.

## Problemas nos dados
Erros de coleta da seção "Fetch errors" de Market/data/latest.md e qualquer coisa faltando ou inconsistente nas notas de negócios.

## Perguntas para o dono
No máximo três, só as que mudariam uma recomendação se fossem respondidas.

Mantenha o brief com menos de 700 palavras.
