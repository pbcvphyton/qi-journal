#!/usr/bin/env bash
# Grava $MEDIA_DIR (fila.json + imagens dos últimos dias) como um único commit na
# branch "instagram", substituindo o anterior: o histórico não cresce.
# Uso: instagram-branch.sh "mensagem do commit"
#
# $RUNNER_TEMP/ig-branch-tip guarda o commit que esta execução baixou (vazio se a
# branch não existia). Se a branch mudou por fora desde então, nada é gravado:
# sobrescrever perderia publicações registradas por outro escritor.
set -euo pipefail
message="${1:?mensagem do commit}"
remote="https://x-access-token:${GITHUB_TOKEN:?}@github.com/${GITHUB_REPOSITORY:?}.git"
tip_file="${RUNNER_TEMP:?}/ig-branch-tip"
if [ ! -f "$tip_file" ]; then
  echo "::error::A fila não foi baixada nesta execução ($tip_file não existe); a branch instagram não é gravada."
  exit 1
fi
expected=$(cat "$tip_file")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp -a "${MEDIA_DIR:?}/." "$work/"
cd "$work"
git init -q -b instagram
git add -A
git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "$message"
ours=$(git rev-parse HEAD)

# Commit atual da branch no GitHub (vazio se ela não existe); tenta de novo em erro de rede.
remote_tip() {
  local out rc
  for attempt in 1 2 3; do
    rc=0
    out=$(git ls-remote --exit-code "$remote" refs/heads/instagram) || rc=$?
    if [ "$rc" -eq 0 ]; then echo "${out%%[[:space:]]*}"; return 0; fi
    if [ "$rc" -eq 2 ]; then echo ""; return 0; fi
    sleep $((attempt * 5))
  done
  return 1
}

current=$(remote_tip) || { echo "::error::Não deu para consultar a branch instagram."; exit 1; }
if [ "$current" != "$expected" ]; then
  echo "::error::A branch instagram mudou desde o início desta execução (${expected:-nenhuma} → ${current:-nenhuma}); nada foi gravado."
  exit 1
fi
if [ -n "$current" ] && git fetch -q --depth 1 "$remote" refs/heads/instagram \
  && [ "$(git rev-parse 'FETCH_HEAD^{tree}')" = "$(git rev-parse 'HEAD^{tree}')" ]; then
  echo "Branch instagram já está atualizada (nada mudou): $message"
  exit 0
fi

for attempt in 1 2 3; do
  if git push -q --force-with-lease="refs/heads/instagram:$current" "$remote" HEAD:refs/heads/instagram; then
    echo "$ours" > "$tip_file"
    echo "Branch instagram atualizada: $message"
    exit 0
  fi
  # O push pode ter chegado mesmo com erro de rede na resposta.
  if now=$(remote_tip) && [ "$now" = "$ours" ]; then
    echo "$ours" > "$tip_file"
    echo "Branch instagram atualizada: $message"
    exit 0
  fi
  sleep $((attempt * 5))
done
echo "::error::Não foi possível atualizar a branch instagram."
exit 1
