#!/bin/bash
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
for t in SBER TATNP DOMRF; do
  echo "### $t"
  curl -sS -m 30 -A "$UA" -o p.html -w "HTTP %{http_code} size %{size_download}\n" "https://smart-lab.ru/q/$t/dividend/"
  python3 - <<'PY'
import re
h=open("p.html",encoding="utf-8",errors="replace").read()
i=h.find("<table")
cnt=0
while i>=0 and cnt<6:
    j=h.find("</table>",i)
    t=h[i:j]
    if re.search(r"отсеч|Т\+1|T\+1|дивиденд",t,re.I):
        t=re.sub(r"\s+"," ",t)
        print("TABLE:", t[:2500]); break
    i=h.find("<table",j); cnt+=1
PY
done
