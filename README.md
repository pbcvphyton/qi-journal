# PBCV Tech — jornal diário

Jornal financeiro diário em português, montado e publicado automaticamente todo
dia de manhã: mercados, economia, direito e regulação, política, geopolítica,
tecnologia e mercado imobiliário — com cotações, clima e um resumo "Em 1 minuto".

- **Edição do dia:** <https://pbcvphyton.github.io/qi-journal/>
- **Edições anteriores:** <https://pbcvphyton.github.io/qi-journal/edicoes/>

Nada precisa ser feito à mão: o GitHub gera a edição sozinho às **05:07
(horário de Brasília)**, publica o site e dispara o e-mail.

---

## Como funciona

```
  GitHub Actions — todo dia às 05:07 (Brasília)
        │
        ▼
  1. Coleta ── ~64 feeds de notícias (Valor, Folha, Estadão, FT, WSJ, NYT, JOTA…)
        │      cotações (Yahoo Finance, CoinGecko, Banco Central) e clima (Open-Meteo)
        ▼
  2. Edição ── com IA (Mistral; reserva: Claude): lê TODAS as notícias, agrupa por
        │      assunto, escolhe os fatos do dia, escreve em português e compara
        │      como cada veículo cobriu cada assunto (lados do debate + medidor)
        │      sem IA (ou se a IA falhar): edição automática com os textos dos veículos
        ▼
  3. Páginas ── capa (index.html), cópia no arquivo (edicoes/), e-mail em HTML e texto
        │
        ▼
  4. Commit no repositório ──► GitHub Pages publica o site
        │
        ▼
  5. E-mail ── rotina diária do Claude (Gmail) e/ou envio direto por SMTP
```

Garantias importantes:

- **Uma fonte fora do ar não derruba a edição.** Cada feed, cotação e cidade é
  coletado de forma independente.
- **Se quase tudo falhar, nada é publicado.** Abaixo do mínimo de notícias
  (`min_articles`), de feeds ok (`min_sources_ok` e `min_sources_ratio`, a
  fração dos feeds) ou de veículos em português (`min_pt_sources_ok`), a
  execução para com erro e a edição anterior continua no ar — inclusive o
  `latest.json`, então a rotina de e-mail não reenvia nada.
- **Sem IA, o jornal sai do mesmo jeito**, no modo automático (títulos e resumos
  dos próprios veículos, sem tradução).

---

## Onde ler

| O quê | Endereço |
|---|---|
| Edição do dia | <https://pbcvphyton.github.io/qi-journal/> |
| Arquivo (edições anteriores) | <https://pbcvphyton.github.io/qi-journal/edicoes/> |
| Uma edição específica | `https://pbcvphyton.github.io/qi-journal/edicoes/AAAA-MM-DD.html` |

Cada matéria tem endereço próprio (`…#s-<id>`). Os links do e-mail apontam para a
cópia arquivada (`edicoes/AAAA-MM-DD.html#s-<id>`), que continua válida depois que a
capa muda no dia seguinte. O
arquivo guarda as edições dos últimos 400 dias (`archive_keep_days` em
`config/site.yaml`).

> **Primeira vez?** Em *Settings → Pages*, escolha *Deploy from a branch*,
> branch `main`, pasta `/ (root)`.

---

## Ativar a edição por IA

Há dois editores por IA, tentados nesta ordem (`llm.providers` em
`config/site.yaml`); cada um só entra se o segredo dele existir, e se falhar a
edição passa ao próximo e, por fim, ao modo automático:

1. **[Mistral](https://mistral.ai/)** (`MISTRAL_API_KEY`) — editor principal,
   com a **análise completa**: lê *todas* as notícias do dia, junta as que
   tratam do mesmo assunto, faz a pauta, redige e monta a **cobertura
   comparada** (veja abaixo).
2. **[Claude](https://www.anthropic.com/)** (`ANTHROPIC_API_KEY`) — reserva:
   pauta e redação em duas chamadas, sem a cobertura comparada.

### Mistral (gratuito)

O plano gratuito da Mistral (*Experiment*) dá acesso a todos os modelos,
inclusive o `mistral-large-latest` usado aqui, com cota mensal bem acima do que
o jornal usa (da ordem de 250 mil tokens por dia). O limite é de requisições por
minuto, por isso as chamadas saem uma de cada vez, com intervalo mínimo de 30 s
(`mistral_min_interval_seconds`) e novas tentativas quando a API pede para
esperar; uma edição leva de 10 a 15 chamadas. No plano gratuito, a Mistral pode
usar o conteúdo enviado para treinar modelos (aqui, só notícias públicas).

1. Crie a chave em <https://console.mistral.ai/> (*API Keys*; o plano gratuito
   pede só a verificação do telefone).
2. No GitHub: *Settings → Secrets and variables → Actions → New repository
   secret*, nome **`MISTRAL_API_KEY`**, valor = a chave. **Nunca** coloque a
   chave no código ou em arquivos do repositório (ele é público).
3. Opcional: variável **`QIJ_MISTRAL_MODEL`** (aba *Variables*) para trocar o
   modelo, ex.: `mistral-medium-latest`.

A API segue o formato de chat do OpenAI (`/chat/completions` com JSON Schema),
então outro provedor compatível pode entrar mudando `mistral_base_url` e o
modelo em `config/site.yaml`.

### Cobertura comparada

Para cada assunto coberto por dois ou mais veículos, a IA recebe o título e um
trecho de cada um e devolve:

- os **dois lados do debate**, como enfoques neutros ("Destaca o risco fiscal" ×
  "Destaca a arrecadação recorde") — ou nenhum, quando todos relatam o fato do
  mesmo jeito;
- a **posição de cada veículo** (lado A, lado B ou neutro) e o que ele destacou;
- a **conclusão**: em que sentido a cobertura seguiu e quem destoou.

No site, um **medidor** mostra cada veículo como um trecho da barra (lado A à
esquerda, neutros no meio, lado B à direita): a barra cresce para o lado com
mais veículos, com o rótulo "Pende para: …". Ele aparece no card de cada
matéria, na janela da matéria (com a conclusão e a lista de veículos por lado)
e na nova seção **Cobertura comparada**, com os demais assuntos do dia (até
`coverage_max_topics`, os com mais veículos primeiro). No e-mail, cada matéria
ganha a barra e a conclusão. A análise usa só o que os veículos publicaram;
quando o trecho não permite dizer, o veículo fica como neutro.

### Claude (reserva, pago)

1. Crie uma chave em <https://console.anthropic.com/> (menu *API Keys*).
2. No GitHub: segredo **`ANTHROPIC_API_KEY`**, valor = a chave.

- **Custo estimado:** cerca de **US$ 0,50 a 1,00 por dia** (US$ 15 a 30 por
  mês) com o modelo `claude-opus-5-5`: duas chamadas por edição, somando da
  ordem de 50 mil tokens de entrada (US$ 4 por milhão) e 25 mil de saída
  (US$ 20 por milhão). O resumo de cada execução no GitHub mostra os tokens
  realmente usados.
- **Trocar o modelo:** crie a variável (aba *Variables*, não *Secrets*)
  **`QIJ_MODEL`** com o nome do modelo desejado.
- **Sem nenhuma chave**, ou se a IA falhar, a edição sai no modo automático e a
  execução no GitHub mostra um aviso amarelo explicando o motivo.

---

## Receber por e-mail

O e-mail traz a manchete com imagem, o resumo do dia, cotações, clima e as
principais matérias por seção, com links para a edição completa. Há dois
caminhos, que podem ser usados juntos ou separados.

### Opção 1 — Rotina diária do Claude (Gmail) — já configurada

Uma rotina agendada do Claude, com o conector do Gmail, roda todo dia às
**06:54 (Brasília)** — depois da edição das 05:07 — e envia o e-mail do dia
para o endereço cadastrado nela (o endereço fica só na rotina, não no
repositório, que é público). Para pausar, mudar o horário ou apagar: lista de
*Routines* do Claude Code em <https://claude.ai/code>.

> **Importante:** a rotina precisa do **conector do Gmail** anexado a ela. Em
> <https://claude.ai/code>, abra *Routines → "QI Journal — e-mail diário" →
> Edit* e adicione o conector **Gmail**. Sem ele, a rotina roda mas não
> consegue enviar (nesse caso, use a opção 2).

Ela usa os arquivos que o próprio jornal gera:

- `edicoes/latest.json` — dados da última edição (data, assunto, manchete, links);
- `edicoes/email.html` e `edicoes/email.txt` — o e-mail pronto. O HTML é
  enxuto (até 40 KB; matérias saem do e-mail se passar disso), porque a rotina o
  copia inteiro no parâmetro `htmlBody` do Gmail.

O que a rotina faz:

1. Lê `latest.json` em
   `https://raw.githubusercontent.com/pbcvphyton/qi-journal/main/edicoes/latest.json`
   (os campos `email_html_raw_url` e `email_text_raw_url` apontam para o e-mail
   no mesmo lugar; `email_html_url` é a cópia no GitHub Pages).
2. Se `date` for a data de hoje em `America/Sao_Paulo`: não envia se
   `email_sent` for `true` (já saiu por SMTP) ou se um e-mail com o mesmo
   assunto já estiver na pasta *Enviados* do Gmail; senão, envia com `subject`
   como assunto, `email.html` como corpo HTML e `email.txt` como texto.
3. Se `date` **não** for de hoje (a edição do dia não saiu), manda só um aviso
   curto com o link das execuções no GitHub, em vez de reenviar a edição de ontem.

O campo `email_sent` sobrevive a uma nova execução no mesmo dia (inclusive com
*Não enviar o e-mail*), então refazer a edição de manhã não dispara um segundo
envio por SMTP.

### Opção 2 — Envio direto por SMTP (Gmail com senha de app)

O próprio GitHub envia o e-mail logo após gerar a edição.

1. Na conta Google que vai enviar, ative a **verificação em duas etapas**.
2. Crie uma **senha de app** em <https://myaccount.google.com/apppasswords>
   (16 letras; pode colar com ou sem espaços).
3. Cadastre os segredos em *Settings → Secrets and variables → Actions*:

   | Segredo | Valor |
   |---|---|
   | `SMTP_USER` | o endereço Gmail que envia |
   | `SMTP_PASSWORD` | a senha de app (não a senha normal da conta) |
   | `EMAIL_TO` | quem recebe; vários endereços separados por vírgula |
   | `EMAIL_FROM` *(opcional)* | remetente, se diferente de `SMTP_USER` |
   | `SMTP_HOST` / `SMTP_PORT` *(opcionais)* | outro provedor; padrão `smtp.gmail.com` / `465` (use `587` para STARTTLS) |

4. Teste: *Actions → Edição diária → Run workflow*.

O envio acontece **depois** que a edição foi commitada e o GitHub Pages terminou
de publicá-la (a etapa espera até ~3 minutos), para os links do e-mail já
funcionarem; se a publicação falhar, nenhum e-mail sai. Se o envio falhar (senha
errada, por exemplo), a edição continua publicada e o problema aparece na
etapa *Enviar e-mail*. Rodar de novo no mesmo dia não reenvia o e-mail já
enviado; para reenviar de propósito, use `python -m qijournal send-email
--force-email`. Os destinatários ficam só nos segredos — nunca no repositório,
que é público —, e com vários endereços cada leitor recebe como cópia oculta
(ninguém vê o e-mail dos outros).

---

## Rodar manualmente

### No GitHub

*Actions → **Edição diária** → Run workflow.* Duas opções:

- **Gerar sem IA** — força a edição automática (útil para testar sem custo);
- **Não enviar o e-mail por SMTP**.

Rodar de novo no mesmo dia substitui a edição do dia. Ao final, a aba
*Summary* da execução mostra o modo (IA ou automático), os tokens usados, as
fontes com erro e os links da edição.

### No computador

Requer Python 3.11 ou mais novo.

```bash
pip install -r requirements.txt
python -m qijournal run --no-email      # gera index.html, edicoes/ e data/ na pasta atual
```

Para ver uma edição de exemplo sem internet e sem IA:

```bash
python -m qijournal render --bundle tests/fixtures/bundle.json --out /tmp/qij --no-llm
# abra /tmp/qij/index.html no navegador
```

Todos os comandos (`python -m qijournal <comando> --help` mostra os detalhes):

| Comando | O que faz |
|---|---|
| `run [--out DIR] [--bundle ARQ] [--no-llm] [--no-email] [--force-email] [--now ISO] [-v]` | coleta, edita, publica e envia o e-mail (SMTP, se configurado; não reenvia o do dia sem `--force-email`) |
| `collect --out ARQ` | só coleta e salva as notícias em JSON |
| `render --bundle ARQ --out DIR [--no-llm]` | gera a edição a partir de uma coleta salva, sem internet e sem e-mail |
| `send-email [--dir DIR] [--force-email]` | envia por SMTP o e-mail da última edição gerada (uma vez por edição, salvo `--force-email`) |
| `check-sources` | testa cada fonte e mostra quais estão respondendo |

Códigos de saída: `0` sucesso; `1` erro; `2` dados insuficientes (edição não
publicada) ou argumentos inválidos.

---

## Fontes de notícia

As fontes ficam em [`config/sources.yaml`](config/sources.yaml), uma por linha:

```yaml
- {id: valor, name: "Valor Econômico", url: "https://valor.globo.com/rss/valor/brasil/", lang: pt, weight: 1.3, topics: [brasil]}
```

| Campo | Significado |
|---|---|
| `id` | apelido curto do veículo (pode repetir entre feeds do mesmo veículo) |
| `name` | nome mostrado nos créditos |
| `url` | endereço do feed RSS/Atom |
| `lang` | `pt`, `en` ou `es` |
| `weight` | importância editorial (0,5 a 1,5) — pesa na escolha das matérias |
| `topics` | seções prováveis (`brasil`, `mercados`, `juridico`, `politica`, `mundo`, `tecnologia`, `imobiliario`); em feeds gerais (capas), use `[]` e a dica sai do endereço da matéria (`/internacional/` → `mundo`…) |
| `enabled` | `false` desliga a fonte sem apagá-la |
| `exclude_url_patterns` | trechos de endereço a ignorar nessa fonte |

- **Adicionar:** acrescente uma linha e rode `python -m qijournal check-sources`
  para confirmar que o feed responde e tem notícias recentes.
- **Remover:** apague a linha ou use `enabled: false`.

Seções, palavras-chave, cotações do ticker, cidades do clima e limites da edição
(quantidade de matérias, mínimos, idade máxima das notícias) ficam em
[`config/site.yaml`](config/site.yaml), com comentários explicando cada item.

---

## Trocar a marca

Há duas marcas prontas: **PBCV Tech** (`tech`, padrão) e **PBCV Advogados**
(`pbcv`), cada uma com logo próprio em `assets/`. Para trocar:

- de forma permanente: em `config/site.yaml`, mude `brand: tech` para `brand: pbcv`; ou
- sem mexer no código: crie a variável **`QIJ_BRAND`** = `pbcv` em
  *Settings → Secrets and variables → Actions → Variables*.

Cores, nome, slogan e logo de cada marca ficam em `config/site.yaml → brands`.
Uma `QIJ_BRAND` com marca inexistente (como o antigo `qi`) cai na marca padrão.

A marca PBCV Tech usa o logo "Quatro Formas" (projeto *PBCV Tech — Logo 2026*
no Claude Design) e as cores dele: azul `#3322CC` (cabeçalho em bloco), tinta
`#0E1016` (ticker e rodapé) e off-white `#F5F3ED` (logo e fundo da página).

- `assets/pbcv-tech-logo.svg` — logo do cabeçalho, pintado com `currentColor`
  (a cor vem de `colors.on_masthead`);
- `assets/pbcv-tech-favicon.svg` — ícone (símbolo off-white sobre o azul);
- `assets/pbcv-tech-logo-email.png` — logo do e-mail (clientes de e-mail não
  exibem SVG), servido pelo GitHub Pages (`email_logo` em `site.yaml`).

---

## Segredos e variáveis do GitHub

| Nome | Tipo | Para quê |
|---|---|---|
| `MISTRAL_API_KEY` | segredo | editor principal (Mistral, análise completa) |
| `ANTHROPIC_API_KEY` | segredo | editor reserva (Claude) |
| `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | segredos | envio do e-mail por SMTP |
| `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT` | segredos (opcionais) | ajustes do SMTP |
| `QIJ_BRAND` | variável | marca (`tech` ou `pbcv`) |
| `QIJ_MODEL` | variável | modelo do Claude |
| `QIJ_MISTRAL_MODEL` | variável | modelo do Mistral (padrão `mistral-large-latest`) |

Todos são opcionais: sem nenhum deles, o jornal é gerado no modo automático e
publicado normalmente.

---

## Quando algo dá errado

- **A execução falhou com código 2:** poucas fontes responderam; nada foi
  publicado e a edição anterior continua no ar. Basta rodar de novo mais tarde
  (*Run workflow*) ou esperar o dia seguinte.
- **Outros erros:** o GitHub avisa por e-mail o dono do repositório. O log da
  etapa *Gerar a edição* mostra a causa.
- **Investigar uma edição:** cada execução guarda por 7 dias a coleta crua e as
  páginas enriquecidas (artefato `coleta-…` na página da execução, com
  `bundle-AAAA-MM-DD.json` e `pages-AAAA-MM-DD.json`). Para reproduzir a edição
  automática, com o mesmo enriquecimento (o `pages-*.json` ao lado do bundle é
  lido sozinho) e as regras de limpeza atuais:
  `python -m qijournal render --bundle bundle-AAAA-MM-DD.json --out /tmp/x --no-llm`.
  A edição por IA não é reproduzível (o modelo pode escolher e escrever
  diferente a cada chamada).
- **O agendamento parou:** o GitHub pode desativar rotinas agendadas de
  repositórios sem atividade. Reative em *Actions → Edição diária → Enable
  workflow*. O horário também pode atrasar alguns minutos em dias de pico.

---

## Estrutura de pastas

```
qijournal/                código do jornal
  collect/                coleta: feeds, páginas das matérias, cotações, clima
  edit/                   edição: agrupamento, IA (llm.py; mistral.py; topics.py =
                          todas as notícias por assunto; coverage.py = cobertura
                          comparada) e modo automático
  render/                 páginas HTML e e-mail (modelos em render/templates/)
  deliver/smtp.py         envio do e-mail por SMTP
  pipeline.py             orquestra coleta → edição → páginas → e-mail
  __main__.py             comandos (python -m qijournal …)
config/site.yaml          marca, seções, cotações, clima, limites da edição
config/sources.yaml       fontes de notícia
assets/                   logos e ícones das marcas
index.html                edição do dia (gerado automaticamente)
edicoes/                  arquivo de edições, e-mail e latest.json (gerado)
data/                     cada edição em JSON (gerado)
tests/                    testes automáticos
.github/workflows/        daily.yml (edição diária) e ci.yml (testes)
```

`index.html`, `edicoes/` e `data/` são escritos pelo robô — não edite à mão.

---

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Os testes não acessam a internet (usam arquivos de exemplo em `tests/fixtures/`).
A cada envio de código, o GitHub roda os testes e gera uma edição de exemplo
(workflow *CI*).
