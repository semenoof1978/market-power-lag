#!/bin/bash
# альтернативные источники дивидендов
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
for u in "https://smart-lab.ru/q/SBER/dividend/" "https://www.dohod.ru/ik/analytics/dividend/sber" ; do
  echo "### $u"
  curl -sS -m 30 -A "$UA" -o p.html -w "HTTP %{http_code} size %{size_download}\n" "$u"
  grep -oE ".{0,80}(2024|2025|2026)-?[0-9.]{0,6}.{0,120}(руб|₽|дивиденд).{0,40}" p.html | head -5
done
