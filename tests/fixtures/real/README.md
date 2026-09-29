Coleta real de 29/09/2026 (primeira execução no GitHub Actions, modo sem IA:
64/64 feeds, 993 artigos), comprimida. É a mesma entrada de
`build/bundle-2026-09-29.json`, guardada aqui para os testes de regressão não
dependerem da pasta `build/` (que não vai para a main).

Os testes em `tests/test_regressao_real.py` verificam nela os defeitos vistos na
edição publicada naquele dia: paywall e boilerplate no texto, pixel da Agência
Brasil como foto, lista de candidatos como destaque, fatos misturados ou
separados por idioma, Starship/Papa na seção errada, manchete da Nvidia, e-mail
acima de 40 KB.
