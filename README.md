# Dividendos | Viver de Renda

Site sobre investimentos focado em renda passiva, dividendos e Fundos
Imobiliários (FIIs) — calculadoras, boletim de mercado diário e dados
atualizados automaticamente, direto no navegador.

🔗 [viverderenda.dev.br](https://viverderenda.dev.br)

---

## O que tem no site

- **Calculadoras financeiras** — Juros Compostos, Aposentadoria/Renda Passiva, Financiamento Imobiliário (SAC/Price), Renda Fixa (CDB/LCI/LCA/Tesouro), FIRE, Basileia
- **Boletim de Mercado Diário** — gerado automaticamente todo dia útil, com opção de compartilhar no WhatsApp
- **Notícias do Mercado Econômico** — atualizadas ao longo do dia
- **Carrossel de índices** — Ibovespa, IFIX, Dólar, Euro, Bitcoin e Selic em tempo real
- **Consulta de Ações e FIIs** — cotações, indicadores e comparador de ativos
- **Glossário** de termos do mercado financeiro
- **Conversor de moedas** e outras ferramentas de apoio

## Stack

Site estático — sem servidor próprio, sem build step. Um único
`index.html` com HTML, CSS e JavaScript puro.

| Camada | Tecnologia |
|---|---|
| Front-end | HTML, CSS e JavaScript vanilla |
| Hospedagem | GitHub Pages |
| Automação de dados | GitHub Actions (coleta de cotações, notícias e geração do boletim diário) |
| Gráficos | Chart.js |
| Exportação de relatórios | jsPDF (PDF) e SheetJS (Excel) |
| Banco de dados / login | Supabase |
| Dados de mercado | HG Brasil API |

## Contribuindo

Esse é um projeto pessoal — sugestões e relatos de bugs são bem-vindos
via [Issues](../../issues).

---

*Feito por [Ricardo Marques](https://github.com/RicardoRMarques).*
