#!/bin/sh
# Tests of docs-site/links.lua against a composed site. Needs pandoc. Run: sh tests/test_links.sh
set -eu

lua="$(cd "$(dirname "$0")/.." && pwd)/docs-site/links.lua"
site="$(mktemp -d)"
trap 'rm -rf "$site"' EXIT
cd "$site"

mkdir -p hub dir1 img .hidden
: > hub/reference.md      # another layer's file: an empty placeholder
: > hub/overview.html
: > img/logo.png
: > dir1/doc1.md
: > .hidden/x.md

cat > good.md <<'MD'
# Good

[markdown](hub/reference.md), [rendered](hub/reference.md.html#ids), [legacy](hub/reference.html),
[html](hub/overview.html), [root](/dir1/doc1.md), [up](dir1/../hub/reference.md), [anchor](#good),
[external](https://example.com/x.md), [protocol](//cdn.example.com/x.md), [mail](mailto:a@example.com),
![image](img/logo.png) <a href="dir1/doc1.md.html">raw</a>

A repository's docs/x.md is at the site root, where ../ stays at the root: [reference](../hub/reference.md).
MD
cat > dir1/nested.md <<'MD'
# Nested

[back](../good.md) [sibling](doc1.md)
MD
cat > bad.md <<'MD'
# Bad

[missing](nope.md) [missing page](nope.md.html) [legacy only](overview.md.html) [page of page](hub/reference.md.md.html)
[directory](hub/) [up](../outside.md) [hidden](.hidden/x.md)
MD
printf '<a href="missing.html">x</a> <img src="img/logo.png">\n' > page.html

printf 'good.md\ndir1/nested.md\n' > good.txt
out="$(pandoc lua "$lua" good.txt 2>&1)" || { echo "FAIL good links: $out"; exit 1; }
[ "$out" = "Checked 15 link(s): 0 problem(s)" ] || { echo "FAIL good links: $out"; exit 1; }

printf 'bad.md\npage.html\n' > bad.txt
if out="$(pandoc lua "$lua" bad.txt 2>&1)"; then
  echo "FAIL bad links passed: $out"
  exit 1
fi
for want in \
  "bad.md: link nope.md does not exist" \
  "bad.md: link nope.md.html does not exist" \
  "bad.md: link overview.md.html does not exist" \
  "bad.md: link hub/reference.md.md.html does not exist" \
  "bad.md: link hub/ is a directory; the site has no index pages" \
  "bad.md: link ../outside.md does not exist" \
  "bad.md: link .hidden/x.md points into a hidden path, which is not published" \
  "page.html: link missing.html does not exist" \
  "Checked 9 link(s): 8 problem(s)"; do
  case "$out" in
    *"$want"*) ;;
    *) echo "FAIL missing \"$want\" in:"; echo "$out"; exit 1 ;;
  esac
done
echo "links.lua tests passed"
