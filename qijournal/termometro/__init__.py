"""Termômetro de Viés Editorial (v1.1) e aba "Analítico Político".

- :mod:`~qijournal.termometro.referencia`: implementação de referência do
  cálculo, exatamente como veio da especificação (não editar: é o pilar);
- :mod:`~qijournal.termometro.agregacao`: agregação da aba analítica (linhas
  diárias, séries, ranking, tendência, desvio e puxadores);
- :mod:`~qijournal.termometro.analise`: os textos compilados na edição do dia
  passam pela cadeia de IAs e pelo cálculo;
- :mod:`~qijournal.termometro.historico`: o registro de cada dia, guardado para
  sempre em ``data/termometro/``.
"""
