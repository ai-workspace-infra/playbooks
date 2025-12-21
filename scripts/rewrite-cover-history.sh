#!/usr/bin/env bash
set -euo pipefail

# 用法：bash rewrite-cover-history.sh path1 [path2 ...]
[ $# -gt 0 ] || { echo "Usage: $0 <path> [path ...]"; exit 1; }
PATHS="$*"

# 记录 remotes，并打个回滚点（本地）
git remote -v | tee remotes.before.txt
git tag "pre-redact-$(date +%Y%m%d-%H%M%S)"

# 核心：对历史中每个提交，若文件存在则用 HEAD 版本覆盖
git filter-branch --force --tree-filter '
for p in '"$PATHS"'; do
  [ -e "$p" ] && git show HEAD:"$p" > "$p" || true
done
' -- --all

# 清理原始引用与垃圾对象（避免泄漏对象残留）
git for-each-ref --format="delete %(refname)" refs/original/ | git update-ref --stdin || true
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 强制推送到所有 remotes（分支与标签）
for r in $(git remote); do
  git push --force-with-lease "$r" --all
  git push --force "$r" --tags
done

echo "✅ Done. 协作者请重新克隆或：git fetch --all && git reset --hard origin/$(git rev-parse --abbrev-ref HEAD) && git gc --prune=now"
