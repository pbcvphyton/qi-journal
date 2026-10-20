#!/usr/bin/env bash
# Grava $MEDIA_DIR (fila.json + imagens dos últimos dias) como um único commit na
# branch "instagram", substituindo o anterior: o histórico não cresce.
# Uso: instagram-branch.sh "mensagem do commit"
set -euo pipefail
message="${1:?mensagem do commit}"
remote="https://x-access-token:${GITHUB_TOKEN:?}@github.com/${GITHUB_REPOSITORY:?}.git"
work=$(mktemp -d)
cp -a "${MEDIA_DIR:?}/." "$work/"
cd "$work"
git init -q -b instagram
git add -A
git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "$message"
for attempt in 1 2 3; do
  if git push -q -f "$remote" instagram; then
    echo "Branch instagram atualizada: $message"
    exit 0
  fi
  sleep $((attempt * 5))
done
echo "::error::Não foi possível atualizar a branch instagram."
exit 1
