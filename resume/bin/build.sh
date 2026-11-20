#!/usr/bin/env bash
# Build resume PDFs and check each one: one page, nothing past the margin, no unescaped % or ~.
#
#   resume/bin/build.sh                    build every variant in resume/variants/
#   resume/bin/build.sh general ml         build the named variants
#   resume/bin/build.sh path/to/x.tex      build any other .tex (e.g. in jobs/applications/); PDF lands beside it
#   resume/bin/build.sh --watch general    rebuild whenever a source file changes (Ctrl-C to stop)
#
# Variants go to resume/build/Resume-<name>.pdf, with a PNG preview of every build in build/.
# A build that fails its checks never replaces the output PDF; it goes to build/<name>.FAILED.pdf.
set -euo pipefail

RESUME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$RESUME/build"
for tool in tectonic pdfinfo pdftoppm; do
  command -v "$tool" >/dev/null || { echo "$tool not found: brew install tectonic poppler" >&2; exit 1; }
done

watch=0
if [[ "${1:-}" == "--watch" ]]; then watch=1; shift; fi

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  for f in "$RESUME"/variants/*.tex; do targets+=("$(basename "$f" .tex)"); done
fi

# In LaTeX a bare % starts a comment and a bare ~ is a space, so either silently eats text.
lint() {
  local files=("$@") bad
  bad="$(grep -nE '(^|[^\\])[%~]' "${files[@]}" /dev/null | grep -vE '^[^:]+:[0-9]+:[[:space:]]*%' || true)"
  [[ -z "$bad" ]] && return 0
  echo "unescaped % or ~ (write \\% or \\~{}); a full-line comment must start with %:" >&2
  echo "${bad//$PWD\//}" >&2
  return 1
}

build_one() {
  local target="$1" src out name
  if [[ "$target" == *.tex ]]; then
    src="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
  else
    src="$RESUME/variants/$target.tex"
  fi
  [[ -f "$src" ]] || { echo "FAIL $target: no such file $src" >&2; return 1; }
  name="$(basename "${src%.tex}")"
  if [[ "$(dirname "$src")" == "$RESUME/variants" ]]; then
    out="$BUILD/Resume-$name.pdf"; target="$name"
  else
    out="${src%.tex}.pdf"; name="$(basename "$(dirname "$src")")-$name"
  fi

  lint "$src" "$RESUME"/content/*.tex "$RESUME"/content/*/*.tex || { echo "FAIL $target" >&2; return 1; }

  local tmp; tmp="$(mktemp -d)"
  # search-path lets any .tex, anywhere in the repo, use \usepackage{resume} and \input{content/...}.
  if ! tectonic -X compile --keep-logs -Z search-path="$RESUME" --outdir "$tmp" "$src" >"$tmp/stdout" 2>&1; then
    grep -v -E 'accessing absolute path|rsrc|GID|UCS32|forbidden' "$tmp/stdout" >&2 || true
    echo "FAIL $target: LaTeX error (see above); $out not updated" >&2
    rm -rf "$tmp"; return 1
  fi

  local pdf="$tmp/$(basename "${src%.tex}").pdf" log="$tmp/$(basename "${src%.tex}").log" pages status=0
  pages="$(pdfinfo "$pdf" | awk '/^Pages:/ {print $2}')"
  if grep -q 'Overfull \\hbox' "$log"; then
    echo "text runs past the right margin:" >&2
    grep -A1 'Overfull \\hbox' "$log" >&2; status=1
  fi
  if [[ "$pages" != 1 ]]; then
    echo "$pages pages (must be 1). Cut or tighten content; don't shrink the style." >&2; status=1
  fi

  mkdir -p "$BUILD"
  if [[ $status -ne 0 ]]; then out="$BUILD/$name.FAILED.pdf"; fi
  mv "$pdf" "$out"
  rm -rf "$tmp"
  pdftoppm -r 110 -png -singlefile "$out" "${out%.pdf}-preview" 2>/dev/null
  [[ "$out" == "$BUILD"/* ]] || mv "${out%.pdf}-preview.png" "$BUILD/$name-preview.png"
  if [[ $status -eq 0 ]]; then
    echo "ok   $target -> ${out#"$PWD"/}"
  else
    echo "FAIL $target: inspect ${out#"$PWD"/}" >&2
  fi
  return $status
}

build_all() {
  local failed=0
  for t in "${targets[@]}"; do build_one "$t" || failed=1; done
  return $failed
}

if [[ $watch -eq 0 ]]; then build_all; exit; fi

# Watch mode: poll source mtimes once a second (no extra dependencies).
stamp() {
  { find "$RESUME/resume.sty" "$RESUME/content" "$RESUME/variants" -type f \( -name '*.tex' -o -name '*.sty' \)
    for t in "${targets[@]}"; do if [[ "$t" == *.tex ]]; then echo "$t"; fi; done
  } | tr '\n' '\0' | xargs -0 stat -f '%m %N' 2>/dev/null | md5 || true
}
last=""
while true; do
  now="$(stamp)"
  if [[ "$now" != "$last" ]]; then last="$now"; build_all || true; echo "watching... (Ctrl-C to stop)"; fi
  sleep 1
done
