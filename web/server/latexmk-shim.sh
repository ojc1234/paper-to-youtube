#!/usr/bin/env bash
# latexmk 대체 (서버 perl 에 Unicode::Normalize 가 없어 진짜 latexmk 가 안 돎)
# 지원: latexmk [-xelatex|-pdfxe|-pdf] [-C|-c] [-interaction=..] main.tex
TEX=""; CLEAN=""
for a in "$@"; do
  case "$a" in
    *.tex) TEX="$a" ;;
    -C|-c) CLEAN=1 ;;
  esac
done
[ -z "$TEX" ] && TEX=$(ls *.tex 2>/dev/null | head -1)
[ -z "$TEX" ] && { echo "latexmk-shim: no .tex"; exit 1; }
B="${TEX%.tex}"
if [ -n "$CLEAN" ]; then rm -f "$B".{aux,log,nav,out,snm,toc,fls,fdb_latexmk,xdv,synctex.gz,vrb}; [ "$CLEAN" ] && exit 0; fi
export TEXINPUTS="./sustech-theme//:${TEXINPUTS}"
rc=0
for i in 1 2 3; do
  xelatex -interaction=nonstopmode -synctex=1 "$TEX" >/dev/null 2>&1; rc=$?
  [ -f "$B.pdf" ] || break
  grep -qE "Rerun to get|Label\(s\) may have changed|rerunfilecheck" "$B.log" || break
done
grep -E "^! " "$B.log" | head -5
[ -f "$B.pdf" ] && grep -q "^! " "$B.log" && rc=12
[ -f "$B.pdf" ] || rc=12
echo "Latexmk-shim: $TEX -> $B.pdf (exit $rc)"
exit $rc
