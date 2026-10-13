# PBCV Tech — jornal diário

Jornal diário em português, montado e publicado automaticamente todo dia de
manhã: mercados, economia, direito e regulação (STF, STJ, TJs…), política,
geopolítica, tecnologia, mercado imobiliário, esporte, natureza e meio ambiente
e cultura — com cotações, clima, um resumo "Em 1 minuto", a análise de como cada
veículo cobriu cada assunto e a lista completa de todas as notícias do dia.

- **Edição do dia:** <https://pbcvphyton.github.io/qi-journal/>
- **Edições anteriores:** <https://pbcvphyton.github.io/qi-journal/edicoes/>

Nada precisa ser feito à mão: o GitHub gera a edição sozinho a partir das
**05:07 (horário de Brasília)**, publica o site e dispara o e-mail.

---

## Como funciona

```
  GitHub Actions — todo dia a partir das 05:07 (Brasília; de hora em hora + disparo da rotina)
        │
        ▼
  1. Coleta ── ~100 feeds (Valor, Folha, Estadão, FT, WSJ, NYT, JOTA, STF, STJ, ge,
        │      g1 Natureza…), cotações (Yahoo Finance, CoinGecko, Banco Central) e
        │      clima (Open-Meteo). Nenhuma editoria é descartada.
        ▼
  2. Edição ── com IA, compilação por editoria: TODAS as notícias em blocos
        │      (Economia & Mercados, Política & Justiça, Empresas/Tecnologia/
        │      Imobiliário, Mundo & Natureza, Esporte/Cultura); em cada bloco a IA
        │      une o que é o mesmo assunto, interpreta o foco de cada veículo e o
        │      lado que ele seguiu, e redige; o fechamento escolhe as matérias.
        │      Se uma IA estourar o limite, a seguinte assume (Gemini → Gemini
        │      Flash-Lite → AIML → SenseNova → Mistral → Kimi → Claude). Sem IA:
        │      edição automática.
        ▼
  2b. Termômetro ── (PBCV Tech) os textos compilados nas matérias passam pelo
        │      Termômetro de Viés Editorial v1.1: a mesma cadeia de IAs aponta os
        │      sinais com citação literal e o cálculo de referência dá o T de cada um
        ▼
  3. Páginas ── capa (index.html), cópia no arquivo (edicoes/), todas as notícias
        │      do dia (edicoes/AAAA-MM-DD-todas.html), aba Analítico Político
        │      (edicoes/analitico.html), e-mail em HTML e texto
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
| Todas as notícias de um dia | `https://pbcvphyton.github.io/qi-journal/edicoes/AAAA-MM-DD-todas.html` |
| Analítico Político (todo o histórico do termômetro) | <https://pbcvphyton.github.io/qi-journal/edicoes/analitico.html> |

Cada matéria tem endereço próprio (`…#s-<id>`). Os links do e-mail apontam para a
cópia arquivada (`edicoes/AAAA-MM-DD.html#s-<id>`), que continua válida depois que a
capa muda no dia seguinte. O
arquivo guarda as edições dos últimos 400 dias (`archive_keep_days` em
`config/site.yaml`).

> **Primeira vez?** Em *Settings → Pages*, escolha *Deploy from a branch*,
> branch `main`, pasta `/ (root)`.

---

## Ativar a edição por IA

Os editores por IA ficam numa **cadeia**, na ordem de `llm.providers` em
`config/site.yaml`. Cada um só entra se o segredo dele existir no GitHub:

| Ordem | Editor | Segredo | Custo e limite |
|---|---|---|---|
| 1 | [Gemini](https://aistudio.google.com/) (`gemini-flash-latest`) | `GEMINI_API_KEY` | grátis (Google AI Studio, modelos Flash); limites por minuto e por dia |
| 1b | Gemini Flash-Lite (`gemini-flash-lite-latest`) | `GEMINI_API_KEY` (a mesma) | grátis; refaz a chamada quando o Flash está sobrecarregado (503) |
| 2 | [AIML API](https://aimlapi.com/) (`openai/gpt-5-5`) | `AIMLAPI_KEY` | grátis; 10 requisições por hora |
| 3 | [SenseNova](https://platform.sensenova.ai/) (`sensenova-6.8-flash-lite`) | `SENSENOVA_API_KEY` | grátis (beta); 1.500 requisições a cada 5 horas |
| 4 | [Mistral](https://mistral.ai/) (`mistral-large-latest`) | `MISTRAL_API_KEY` | grátis (*Experiment*); poucas requisições por minuto |
| 5 | [Kimi](https://platform.kimi.ai/) (`kimi-k3`) | `MOONSHOT_API_KEY` | pago (recarga mínima de US$ 1) |
| 6 | [Claude](https://www.anthropic.com/) (`claude-opus-5-5`) | `ANTHROPIC_API_KEY` | pago |

O Gemini é o mesmo do jornal **TSA Tech** (`pbcvphyton/pbcvphyton-tsa-tech`), que
já sai com IA por ele: basta cadastrar aqui o segredo `GEMINI_API_KEY` com a
mesma chave (o GitHub não deixa um repositório ler o segredo de outro). Com a
mesma chave, os dois jornais dividem os limites diários do plano grátis do
Google; uma chave de outro projeto do Google AI Studio tem limites próprios.

**Quando uma IA estoura o limite, a seguinte assume**, chamada a chamada, até
toda a demanda ser compilada: se o editor da vez esgotar o teto de requisições
da edição (ex.: as 10 por hora da AIML), receber 429 que persiste, ficar sem
cota ou tiver a chave recusada, ele sai da cadeia e a mesma chamada é refeita
no seguinte. Outros erros (resposta cortada, JSON inválido) refazem só aquela
chamada no seguinte. Se nenhum conseguir, a edição sai no modo automático e a
execução no GitHub mostra um aviso amarelo explicando o motivo.

Para ativar um editor: crie a chave no site dele e, no GitHub, *Settings →
Secrets and variables → Actions → New repository secret*, com o nome da tabela
e a chave como valor. **Nunca** coloque a chave no código ou em arquivos do
repositório (ele é público). O modelo de cada um pode ser trocado pela variável
`QIJ_<NOME>_MODEL` (aba *Variables*): `QIJ_GEMINI_MODEL`, `QIJ_GEMINI_LITE_MODEL`,
`QIJ_AIML_MODEL`, `QIJ_SENSENOVA_MODEL`, `QIJ_MISTRAL_MODEL`, `QIJ_KIMI_MODEL` e
`QIJ_MODEL` (Claude). Os limites de cada
um (janela de contexto, saída máxima, teto de requisições, chamadas simultâneas)
ficam em `llm.apis` no `config/site.yaml`, com os padrões em
`qijournal/config.py` (`DEFAULT_APIS`).

### Compilação por editoria (modo "blocos")

Todas as notícias do dia (1.000 a 1.500) são divididas em **blocos por
editoria** (`llm.block_groups`), e cada bloco vai numa chamada:

1. Economia & Mercados · 2. Política & Justiça · 3. Empresas, Tecnologia &
   Imobiliário · 4. Mundo & Natureza · 5. Esporte, Cultura & Variedades

Em cada bloco, a IA lê todas as notícias dele, **une as que tratam do mesmo
assunto**, faz a **análise da cobertura** (abaixo) e **redige as matérias mais
importantes**. Uma **chamada de fechamento** recebe o que todos os blocos
produziram, junta assuntos que se repetiram entre blocos, escolhe as matérias da
edição (`target_stories`) e a manchete e escreve o editorial e o "Em 1 minuto".

O limite de cada chamada é respeitado: um bloco que não cabe na entrada
(janela de contexto do modelo menos a saída) ou na saída máxima do modelo é
dividido em partes de tamanho igual, até **9 chamadas no total** (`max_calls`,
contando o fechamento). Com a cadeia, os blocos são planejados pelo menor limite
entre os editores ativos, para que qualquer um deles consiga assumir qualquer
bloco. Uma edição típica usa 6 chamadas (5 blocos + fechamento).

O modo alternativo `etapas` (`llm.apis.<nome>.mode: etapas`) faz agrupamento,
pauta, redação e cobertura em chamadas separadas (10 a 15 por edição).

### Análise da cobertura (o analítico)

Para cada assunto coberto por dois ou mais veículos, a IA devolve:

- os **dois lados do debate**, como enfoques neutros ("Destaca o risco fiscal" ×
  "Destaca a arrecadação recorde") — ou nenhum, quando todos relatam o fato do
  mesmo jeito;
- para **cada veículo**, o lado que ele seguiu (A, B ou neutro) e a
  **interpretação do foco dele**: o que priorizou, que enquadramento deu (tom,
  personagens, dados que destacou ou deixou de lado em comparação com os
  outros) e por que isso o põe naquele lado;
- a **conclusão**: em que sentido a cobertura seguiu e quem destoou.

No site, um **medidor** mostra cada veículo como um trecho da barra (lado A à
esquerda, neutros no meio, lado B à direita): a barra cresce para o lado com
mais veículos, com o rótulo "Pende para: …". Ele aparece no card de cada
matéria, na janela da matéria (com a conclusão e a **análise por veículo**) e na
seção **Cobertura comparada**, com os demais assuntos do dia. No e-mail, cada
matéria ganha a barra e a conclusão. A análise usa só o que os veículos
publicaram; quando o trecho não permite dizer, o veículo fica como neutro.

### Termômetro de Viés Editorial e Analítico Político (só PBCV Tech)

Depois da edição, os textos compilados nas matérias do dia passam pelo
**Termômetro de Viés Editorial, versão 1.1** — a especificação em
`termometro-referencia-v1.3`, que é o pilar desta parte. Ele é **estritamente
adicional**: a análise da cobertura acima continua igual, e o termômetro aparece
**depois** dela, no fim da edição, e na aba **Analítico Político** (botão
*Analítico* no cabeçalho, ao lado de *Edições* e *Buscar*). A marca PBCV
Advogados não tem nada disso (`termometro.marcas` em `config/site.yaml`).

Como cada texto é analisado:

1. **Texto**: das matérias da edição (política, jurídico, economia, mundo,
   mercados, natureza, tecnologia e imobiliário, nessa prioridade; esporte e
   variedades ficam de fora), um veículo de cada matéria por vez. O texto segue o
   formato da especificação: `P0` = título, `P1` = linha fina e o corpo extraído da
   página original (até 8.000 caracteres, com as falas curtas; o pedido avisa quando foi cortado). Texto
   com menos de 150 palavras não vai à IA (seria inconclusivo). Só português.
2. **IA**: o pré-processamento da referência (`preparar`: falas diretas entre
   aspas e termos do dicionário) vai no pedido; a mesma cadeia de IAs da edição
   (o que a edição já gastou do limite de cada uma continua contando) devolve a
   saída no formato v1.1 (triagem, atores, fontes, enquadramento, evidências com
   citação literal, teste de inversão e qualidade), 4 textos por chamada, até 20
   textos e 6 chamadas por edição (`termometro` em `config/site.yaml`).
3. **Cálculo**: cada saída é validada contra `config/saida_modelo.schema.json` e
   passa pelo cálculo de referência **sem nenhuma alteração**
   (`qijournal/termometro/referencia.py`, cópia literal): ele confere cada citação
   no texto, aplica a regra da voz, os pesos dos 11 critérios e dá **T** (−100 a
   +100), o rótulo, a confiança e a **qualidade da apuração Q** (0 a 100).

No fim da edição, o **Termômetro do dia** resume as notícias analisadas: a média
de T com o intervalo de 95% numa escala de −100 a +100, quantos textos ficaram em
cada rótulo, a qualidade média e, abrindo, cada texto com o resumo do cálculo e os
sinais que pontuaram (citação, critério e contribuição). Opinião fica à parte.

A aba **Analítico Político** (`edicoes/analitico.html`) agrega **todo o
histórico** (`qijournal/termometro/agregacao.py`, conferida pelos testes da
seção 14.7 da especificação): onde estamos (média geral), para onde estamos indo
(tendência mensal de T e de Q, a janela recente comparada ao histórico e quem
puxou a mudança, por editoria e por veículo), as séries semanais, os rankings
por veículo e por editoria (T ajustado e qualidade, mínimo de 30 notícias), os
recortes por eixo, a tabela de cada dia e a lista de todas as análises.

- **Histórico**: as análises de cada dia ficam em `data/termometro/AAAA-MM-DD.json`
  e **nunca são apagadas** (a limpeza do arquivo de edições não toca nessa pasta).
  Um texto já analisado não volta à IA: é reaproveitado e conta uma vez só.
- **Dicionário e âncoras**: `config/lexico.yaml` (vocabulário codificado; hoje com
  os termos do caso de referência) e `config/atores.yaml` (campo fixo de partidos e
  grupos; vazio — sem âncora, a IA usa a filiação e a posição que o texto informa).
- **Precisa de IA**: sem nenhum segredo de IA configurado (o mais simples é o
  `GEMINI_API_KEY`, o mesmo do TSA Tech), a edição sai com "Sem análise nesta
  edição: nenhuma IA disponível nesta execução".
- **Desligar** sem mexer no código: variável `QIJ_NO_TERMOMETRO` = `1` no GitHub
  (*Settings → Secrets and variables → Actions → Variables*).
- **Testes da especificação**: `tests/termometro/` guarda os scripts e casos como
  vieram (`testes.py`, `testes_ranking.py`); `tests/test_termometro_especificacao.py`
  roda os dois e exige `FALHAS: nenhuma`.

### Racional da edição e lista completa do dia

- **Racional da edição:** uma faixa no topo da capa e uma seção própria dizem
  como a edição foi compilada — quantas notícias e veículos, os blocos por
  editoria (notícias lidas, matérias redigidas e assuntos comparados em cada
  um), quantos assuntos sobraram depois de unir as repetidas e qual IA fez o
  trabalho. Cada matéria mostra de quantas notícias e veículos foi compilada e
  em que bloco.
- **Nenhuma notícia é descartada:** a página *Todas as notícias do dia*
  (`edicoes/AAAA-MM-DD-todas.html`, com link na capa e no e-mail) lista todas as
  notícias coletadas, por editoria e por assunto (as repetidas ficam juntas),
  com link para o original. Só ficam de fora da coleta publieditoriais,
  resultados de loteria, horóscopo e vídeos sem texto (`exclude_url_patterns` e
  `exclude_title_patterns` em `config/site.yaml`).

---

## Receber por e-mail

O e-mail traz a manchete com imagem, o resumo do dia, cotações, clima, o
racional da compilação e as principais matérias por seção, com links para a
edição completa e para todas as notícias do dia. Há dois
caminhos, que podem ser usados juntos ou separados.

### Opção 1 — Rotina diária do Claude (Gmail) — já configurada

Uma rotina agendada do Claude, com o Gmail conectado, roda todo dia às
**06:54 (Brasília)** e envia o e-mail do dia para o endereço cadastrado nela (o
endereço fica só na rotina, não no repositório, que é público). Se a edição do
dia ainda não tiver saído (agendamento do GitHub atrasado), ela mesma dispara o
workflow (`repository_dispatch` com `event_type` `edicao`), espera a edição sair
(alguns minutos) e envia. Se ainda assim não sair, tenta de novo às **09:04**;
só então, se a edição não saiu, manda um aviso curto. Nunca envia duas vezes no
mesmo dia. Para pausar, mudar o horário ou
apagar: lista de *Routines* do Claude Code em <https://claude.ai/code>.

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
| `topics` | seções prováveis (`brasil`, `mercados`, `juridico`, `politica`, `mundo`, `tecnologia`, `imobiliario`, `esporte`, `natureza`, `variedades`); em feeds gerais (capas), use `[]` e a dica sai do endereço da matéria (`/internacional/` → `mundo`, `/esporte/` → `esporte`…) |
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

A marca PBCV Tech usa o logo **Sinal** (direção 06 do projeto *PBCV Tech — Logo
2026* no Claude Design): letreiro próprio em grade de pixels e a tarja laranja
"Tech", num cabeçalho claro como o do antigo QI Journal. A paleta vem do quadro
do Sinal: marinho `#1B2745` (ticker, rodapé, títulos), ardósia `#394A7A` (links e
réguas), laranja de sinal `#FF5A1F` (destaques; só decoração no fundo branco) e
altas/quedas do ticker em `#7FC8FF`/`#FF5C9A`, com os cinzas azulados do layout
original.

- `assets/pbcv-tech-logo.svg` e `assets/pbcv-tech-logo-dark.svg` — logo do
  cabeçalho para os modos claro e escuro (`logo_svg` e `logo_svg_dark`; o site
  troca de SVG, sem filtro de inversão). Por ser pixel art, as alturas
  (`logo_height: 60`, `logo_height_mobile: 40`) são múltiplas de 20, a grade do
  SVG, para os pixels ficarem nítidos;
- `assets/pbcv-tech-favicon.svg` — o símbolo de 16 × 16 do quadro;
- `assets/pbcv-tech-logo-email.png` e `…-email-dark.png` — logo do e-mail
  (clientes de e-mail não exibem SVG), servidos pelo GitHub Pages
  (`email_logo`/`email_logo_dark`); a versão escura entra pelo CSS do modo
  escuro de clientes como o Apple Mail.

Outra marca pode usar cabeçalho em bloco de cor com `colors.masthead` e
`colors.on_masthead` (logo em `currentColor`).

---

## Segredos e variáveis do GitHub

| Nome | Tipo | Para quê |
|---|---|---|
| `GEMINI_API_KEY` | segredo | editor 1 da cadeia (Gemini Flash e Flash-Lite, grátis; a mesma chave do TSA Tech) |
| `AIMLAPI_KEY` | segredo | editor 2 (AIML, grátis) |
| `SENSENOVA_API_KEY` | segredo | editor 3 (SenseNova, grátis) |
| `MISTRAL_API_KEY` | segredo | editor 4 (Mistral, grátis) |
| `MOONSHOT_API_KEY` | segredo | editor 5 (Kimi, pago) |
| `ANTHROPIC_API_KEY` | segredo | editor 6 (Claude, pago) |
| `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | segredos | envio do e-mail por SMTP |
| `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT` | segredos (opcionais) | ajustes do SMTP |
| `QIJ_BRAND` | variável | marca (`tech` ou `pbcv`) |
| `QIJ_NO_TERMOMETRO` | variável | `1` desliga o Termômetro de Viés e a aba Analítico Político |
| `QIJ_MODEL` | variável | modelo do Claude |
| `QIJ_GEMINI_MODEL`, `QIJ_GEMINI_LITE_MODEL`, `QIJ_AIML_MODEL`, `QIJ_SENSENOVA_MODEL`, `QIJ_MISTRAL_MODEL`, `QIJ_KIMI_MODEL` | variáveis | modelo de cada editor (padrões na tabela de editores acima) |

Todos são opcionais: sem nenhum deles, o jornal é gerado no modo automático e
publicado normalmente.

---

## Quando algo dá errado

- **A execução falhou com código 2:** poucas fontes responderam; nada foi
  publicado e a edição anterior continua no ar. A execução da hora seguinte
  tenta de novo sozinha; também dá para rodar à mão (*Run workflow*).
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
- **O agendamento atrasou ou não rodou:** o GitHub atrasa ou descarta
  agendamentos (em outubro de 2026, o das 05:07 chegava entre 10h e 14h). Por
  isso o workflow é agendado de hora em hora: a primeira execução que chega a
  partir das **05:00** (Brasília) gera a edição do dia, e as demais terminam em
  segundos. A rotina do e-mail também dispara o workflow às 06:54 e às 09:04 se
  a edição ainda não saiu (`repository_dispatch`, sem depender da fila de
  agendamentos). Mudar o `daily.yml` na `main` também gera a edição do dia, se
  ela ainda não saiu.
- **O agendamento parou:** o GitHub desativa agendamentos de repositórios sem
  atividade por 60 dias (o commit diário da edição evita isso). Reative em
  *Actions → Edição diária → Enable workflow*.

---

## Estrutura de pastas

```
qijournal/                código do jornal
  collect/                coleta: feeds, páginas das matérias, cotações, clima
  edit/                   edição: agrupamento, IA (llm.py = Claude e fluxo geral;
                          chatapi.py = AIML, SenseNova, Mistral, Kimi; chain.py =
                          cadeia de editores; blocks.py = compilação por
                          editoria; topics.py e coverage.py = modo etapas;
                          index.py = todas as notícias do dia) e modo automático
  render/                 páginas HTML e e-mail (modelos em render/templates/;
                          termometro.py e analitico.py = termômetro do dia e aba
                          Analítico Político)
  termometro/             Termômetro de Viés: referencia.py (cálculo, cópia literal
                          da especificação), agregacao.py (aba analítica),
                          analise.py (textos → IA → cálculo) e historico.py
  deliver/smtp.py         envio do e-mail por SMTP
  pipeline.py             orquestra coleta → edição → páginas → e-mail
  __main__.py             comandos (python -m qijournal …)
config/site.yaml          marca, seções, cotações, clima, limites da edição
config/sources.yaml       fontes de notícia
config/saida_modelo.schema.json, lexico.yaml, atores.yaml  termômetro (formato da
                          saída da IA, dicionário e âncoras de campo)
assets/                   logos e ícones das marcas
index.html                edição do dia (gerado automaticamente)
edicoes/                  arquivo de edições, todas as notícias de cada dia,
                          e-mail e latest.json (gerado)
data/                     cada edição em JSON e, em data/termometro/, as análises
                          do termômetro de cada dia, guardadas para sempre (gerado)
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
