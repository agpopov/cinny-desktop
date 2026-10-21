#!/usr/bin/env bash
# Сливает релиз апстрима (по умолчанию последний тег vX.Y.Z) в corp обоих репозиториев.
# Использование: scripts/sync-upstream.sh [--push] [ТЕГ_CINNY [ТЕГ_DESKTOP]]. Описание процесса — в README_05RU.md.
set -euo pipefail
cd "$(dirname "$0")/.."

PUSH=0
[ "${1:-}" = "--push" ] && { PUSH=1; shift; }

latest_tag() { git -C "$1" tag -l 'v[0-9]*' --sort=-v:refname | head -1; }

git -C cinny fetch upstream --tags
git fetch upstream --tags
CINNY_TAG="${1:-$(latest_tag cinny)}"
DESKTOP_TAG="${2:-$(latest_tag .)}"
echo "Сливаем cinny $CINNY_TAG, cinny-desktop $DESKTOP_TAG"

git -C cinny checkout corp
git -C cinny merge --no-edit "$CINNY_TAG"

git checkout corp
if ! git merge --no-edit "$DESKTOP_TAG"; then
  # Удалённые у нас апстримовские workflow: при конфликте «изменён/удалён» оставляем удалёнными.
  for f in $(git diff --name-only --diff-filter=U | grep '^\.github/workflows/' | grep -v 'corp-build.yml'); do
    git rm -q "$f"
  done
  if git diff --name-only --diff-filter=U | grep -q .; then
    echo "Остались конфликты, разрешите вручную:"; git diff --name-only --diff-filter=U; exit 1
  fi
  git commit --no-edit
fi

git add cinny
git diff --cached --quiet || git commit -q -m "chore: сабмодуль cinny обновлён до $CINNY_TAG"

# В апстриме есть ошибки линтера (Message.tsx, useMemberSort.ts, cryptE2ERoomKeys.js), поэтому проверяем только наши файлы.
(cd cinny && npm ci \
  && git diff --name-only --diff-filter=d "$CINNY_TAG" -- src | grep -E '\.(ts|tsx|js)$' | xargs -r npx eslint --quiet \
  && npm run build)
(cd cinny && git checkout -q config.json 2>/dev/null || true)

if [ "$PUSH" = 1 ]; then
  git -C cinny push origin corp
  git push origin corp
else
  echo "Готово локально. Проверьте и выполните: git -C cinny push origin corp && git push origin corp"
fi
