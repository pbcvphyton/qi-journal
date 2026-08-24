# Dashboard de Fechamento de Exercicio — FIIs

Passo a passo para montar a planilha do zero, colar o Apps Script, autorizar os triggers e validar o funcionamento.

---

## 0. Visao geral da arquitetura

Tres abas:

| Aba | Papel | Estado |
|---|---|---|
| `🖥️ Dashboard` | KPIs + painel de alertas + botao "Gerar Relatorio de Auditoria" | Visivel |
| `📊 Operacional` | Base de dados operacional; **onde os times editam** | Visivel |
| `⚙️ Config & Logs` | Listas de dominio (fundos, responsaveis) + log de auditoria append-only | **Oculta e protegida** |

Blocos de colunas na Operacional (A..O):

| Bloco | Cor cabecalho | Colunas |
|---|---|---|
| Identificacao | Cinza escuro `#3f3f3f` | A `ID` · B `Fundo (FII)` · C `Nome do Imovel` · D `Data Base Fechamento` |
| Admin. Fiduciaria | Azul escuro `#0b3d91` | E `Responsavel (Admin)` · F `Link Drive` · G `Status do Laudo` · H `Data Recebimento Laudo` |
| Precificacao | Verde escuro `#0f5132` | I `Responsavel (Precificacao)` · J `Valor Contabil Anterior` · K `Novo Valor (Laudo)` · L `Variacao %` · M `Comentarios/Justificativas` |
| Auditoria | Roxo escuro `#4b246c` | N `Ultima Atualizacao` (auto) · O `Status Final` |

---

## 1. Criar a planilha e colar o script

1. Crie uma nova planilha no Google Sheets.
2. Menu **Extensoes → Apps Script**.
3. Apague o conteudo do arquivo `Code.gs` padrao e cole o conteudo do `fii-dashboard/Code.gs` deste repositorio.
4. **Salvar** (Ctrl+S). Nomeie o projeto (ex.: `FII Fechamento`).

---

## 2. Bootstrap automatico da estrutura

O script traz uma rotina que monta tudo — cabecalhos, cores, congelamento de painel, formatos, formulas, validacoes e formatacao condicional.

1. Volte a planilha e **recarregue a pagina**. O menu **📋 FII Fechamento** aparece na barra.
2. Clique em **📋 FII Fechamento → Setup / Manutencao → 1) Criar/Recriar Estrutura de Abas**.
3. Autorize quando o Google pedir (ver secao 6).
4. Ao final, ele avisa que a estrutura foi criada.

Isso ja aplica automaticamente:
- As tres abas com nome e cor da guia.
- Cabecalhos coloridos por bloco.
- Congelamento: primeira linha e tres primeiras colunas na Operacional.
- Formato de data em D/H/N, moeda em J/K, percentual em L.
- Formula `ARRAYFORMULA` na coluna **L (Variacao %)**: `=(K−J)/J`.
- Aba `⚙️ Config & Logs` oculta e protegida.

Opcional: **📋 FII Fechamento → Setup → 4) Popular linhas de exemplo** — insere 4 imoveis de teste para validar visualmente.

---

## 3. Preencher os dominios (Config & Logs)

A aba fica oculta por padrao. Para editar: guia inferior → **botao "+"** ao lado das abas → **"Ocultar abas ocultas"** → reexibir `⚙️ Config & Logs`.

Preencha:

- **A3:A** — lista de fundos (ex.: `FII ABCD11`, `FII XYZW11`, …).
- **C3:D** — mapa de responsaveis: coluna C nome, coluna D e-mail. Ex.:

  | Nome | Email |
  |---|---|
  | Ana Admin | ana.admin@empresa.com |
  | Bruno Prec | bruno.prec@empresa.com |

Depois **📋 FII Fechamento → Setup → 2) Aplicar Validacoes de Dados** para que os menus suspensos de Fundo / Responsavel apontem para essas listas.

---

## 4. Fluxo de Status do Laudo (validacao + cores)

A validacao é aplicada automaticamente por `setupValidacoes()`. As cores por status vem de `setupFormatacaoCondicional()`.

| # | Status | Cor de fundo | Cor da fonte | Dono |
|---|---|---|---|---|
| 1 | `[ADMIN] Aguardando Laudo` | `#d9d9d9` cinza | `#1f1f1f` | Admin |
| 2 | `[ADMIN] Laudo em Conferencia` | `#fff2cc` amarelo | `#7f6000` | Admin |
| 3 | `[PRECIFICACAO] Disponivel para Precificar` | `#cfe2f3` azul | `#0b3d91` | **Gatilho: envia email** |
| 4 | `[PRECIFICACAO] Em Analise de Valor` | `#fce5cd` laranja | `#b45f06` | Precificacao |
| 5 | `[PRECIFICACAO] Valor Final Definido` | `#d9ead3` verde | `#274e13` | Precificacao |
| 6 | `[AUDITORIA] Pronto para Fechamento` | `#38761d` verde escuro | `#ffffff` | Auditoria |

Coluna O (`Status Final`) tem a lista: `Em Andamento`, `Aguardando Revisao`, `Pronto para Fechamento`, `Bloqueado`.

---

## 5. Formatacao Condicional (o que a rotina cria)

Aplicada por `setupFormatacaoCondicional()`:

1. **Coluna G — Status do Laudo**: cada status ganha as cores da tabela acima.
2. **Coluna L — Variacao %**: `=AND(ISNUMBER(L2), ABS(L2)>0.10)` → fundo `#f8d7da`, fonte `#7a1620`, negrito. Sinaliza variacoes maiores que ±10%.
3. **Linha inteira quando `Status Final = Pronto para Fechamento`**: `=$O2="Pronto para Fechamento"` sobre `A2:O` → fundo `#f1f3f4`, fonte `#6b7280`. Retira foco visual.
4. **Coluna D — Data Base**: `=AND(ISDATE(D2), D2<TODAY(), $O2<>"Pronto para Fechamento")` → fundo `#fde2e2`, negrito. Destaca datas ja vencidas.

Se quiser reaplicar (ex.: apos edicao manual): **📋 FII Fechamento → Setup → 3) Aplicar Formatacao Condicional**.

---

## 6. Autorizacao e criacao dos gatilhos (triggers)

### 6.1 Trigger simples (ja incluso)

- `onOpen()` cria o menu customizado. Nao precisa configuracao manual: sempre roda ao abrir a planilha.

### 6.2 Trigger `onEdit` **instalavel** (obrigatorio)

O `onEdit` **simples** do Google nao consegue enviar email nem ler `Session.getActiveUser().getEmail()` de forma confiavel. Por isso a solucao usa um trigger **instalavel**:

1. No editor do Apps Script, painel lateral esquerdo → icone **relogio** ("Acionadores" / "Triggers").
2. Botao **+ Adicionar acionador** (canto inferior direito).
3. Preencha:
   - **Funcao a ser executada**: `onEditInstallable`
   - **Implantacao a ser executada**: `Head`
   - **Origem do evento**: `Da planilha`
   - **Tipo de evento**: `Ao editar`
   - **Notificacoes de falha**: `Notificar-me imediatamente`
4. **Salvar**. O Google pedira autorizacao — aceite os escopos:
   - Ver, editar, criar e excluir planilhas nas quais voce foi adicionado.
   - Enviar e-mail em seu nome (para `MailApp.sendEmail`).
   - Acessar `SpreadsheetApp` / `Session`.
5. Se aparecer o aviso "Este app nao foi verificado", clique em **Configuracoes avancadas → Acessar (nome do projeto) (nao seguro)** e prossiga (aplicavel a scripts internos que voce escreve).

Pronto. Toda edicao nas colunas G, K, M e O gera log e/ou notificacao.

### 6.3 (Opcional) Trigger diario de verificacao

Se quiser um "watchdog" que roda a cada manha e alerta pendencias:

1. **+ Adicionar acionador**.
2. Funcao: `verificarPendencias` · Origem: `Baseado em tempo` · Tipo: `Dia` · Horario: `08:00`.
3. Salvar. (Obs.: `verificarPendencias` usa `ui.alert` — quando disparado por trigger de tempo nao existe UI, entao ele so mostrara o alerta se for chamado pelo botao/menu. Para versao por email, adapte para usar `MailApp.sendEmail`.)

---

## 7. Botao "Gerar Relatorio de Auditoria" no Dashboard

1. Va para a aba `🖥️ Dashboard`.
2. Menu **Inserir → Desenho**.
3. Crie um retangulo, escreva `▶ Gerar Relatorio de Auditoria`, aplique cor de fundo (`#0b3d91`) e fonte branca.
4. **Salvar e fechar** — o desenho aparece na aba.
5. Clique nele, canto superior direito → **⋮ → Atribuir script** → digite `verificarPendencias` → OK.
6. Teste clicando: um `ui.alert` lista os imoveis com Data Base a ≤ 5 dias que ainda nao estao "Pronto para Fechamento".

---

## 8. Formulas do Dashboard (ja criadas pelo bootstrap)

Referencia rapida caso queira editar manualmente:

- **KPI "Total de Imoveis"** (B5:C5):
  ```
  =COUNTA('📊 Operacional'!C2:C)
  ```
- **KPI "Aguardando Laudo"** (D5:E5):
  ```
  =COUNTIF('📊 Operacional'!G2:G,"[ADMIN] Aguardando Laudo")
  ```
- **KPI "Disponivel p/ Precificar"** (F5:G5):
  ```
  =COUNTIF('📊 Operacional'!G2:G,"[PRECIFICACAO] Disponivel para Precificar")
  ```
- **KPI "Prontos para Fechamento"** (H5:I5):
  ```
  =COUNTIF('📊 Operacional'!O2:O,"Pronto para Fechamento")
  ```
- **Painel de Alertas — Laudos Atrasados** (B10):
  ```
  =IFERROR(QUERY('📊 Operacional'!B2:O,
    "select B, C, D, TODAY()-D, G
     where D is not null and D < date '"&TEXT(TODAY(),"yyyy-mm-dd")&"'
       and O <> 'Pronto para Fechamento'
     order by D asc
     label TODAY()-D 'Dias em Atraso'", 0),
    "Sem pendencias.")
  ```

### Graficos (inserir manualmente)

O Google Sheets nao permite criar graficos programaticamente com boa formatacao. O bootstrap deixa duas areas reservadas (B22 e E22). Passos:

**Grafico 1 — Status dos Laudos (Pizza)**
1. Numa area auxiliar (ex.: `Q2:R8`) monte a tabela:
   ```
   Status                                       | Qtd
   [ADMIN] Aguardando Laudo                     | =COUNTIF('📊 Operacional'!G:G, Q3)
   [ADMIN] Laudo em Conferencia                 | =COUNTIF('📊 Operacional'!G:G, Q4)
   [PRECIFICACAO] Disponivel para Precificar    | =COUNTIF('📊 Operacional'!G:G, Q5)
   [PRECIFICACAO] Em Analise de Valor           | =COUNTIF('📊 Operacional'!G:G, Q6)
   [PRECIFICACAO] Valor Final Definido          | =COUNTIF('📊 Operacional'!G:G, Q7)
   [AUDITORIA] Pronto para Fechamento           | =COUNTIF('📊 Operacional'!G:G, Q8)
   ```
2. Selecione `Q2:R8` → **Inserir → Grafico** → tipo **Pizza**.
3. Mova o grafico sobre a marca "Grafico: Status dos Laudos".

**Grafico 2 — Imoveis por Fundo (Barras)**
1. Em `T2:U`, use:
   ```
   =QUERY('📊 Operacional'!B2:B, "select B, count(B) where B is not null group by B label count(B) 'Qtd'")
   ```
2. Selecione, **Inserir → Grafico** → tipo **Barras** → mova para a marca correspondente.

---

## 9. Teste rapido de aceitacao

Depois de tudo configurado, valide:

1. **Log de auditoria**: edite a coluna G de qualquer linha. Reexiba `⚙️ Config & Logs`. Deve haver nova linha em F/M com timestamp, seu email, linha, ID, coluna, valor anterior e novo.
2. **Carimbo de tempo**: mudar G, K, M ou O deve atualizar a coluna N (`Ultima Atualizacao`).
3. **Notificacao por email**: mude o Status de uma linha para `[PRECIFICACAO] Disponivel para Precificar` **e garanta** que o responsavel de precificacao esta preenchido (col I) e mapeado em `Config C:D`. Voce (dono do script) recebe/envia o email em nome proprio.
4. **Formatacao condicional**:
   - Coloque `1000000` em J e `1200000` em K de alguma linha → L mostra `20%` em vermelho.
   - Marque `Status Final = Pronto para Fechamento` → a linha inteira fica cinza.
5. **Botao Gerar Relatorio**: clique no desenho → alert lista pendencias dos proximos 5 dias.

---

## 10. Manutencao

- **Trocar cor de um status**: edite `CFG.STATUS_LAUDO` no `Code.gs` e rode **Setup → 3) Aplicar Formatacao Condicional**.
- **Trocar janela de alerta de 5 para N dias**: edite `CFG.DIAS_ALERTA_PENDENCIA`.
- **Adicionar coluna monitorada no log**: acrescente o indice em `CFG.AUDITED_COLS`.
- **Ver historico completo**: reexiba `⚙️ Config & Logs`; log fica em `F2:M` (append-only). Recomendado nunca deletar; se precisar arquivar, mova para outra aba.
