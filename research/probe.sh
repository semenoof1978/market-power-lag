#!/bin/bash
# поиск рабочего источника дивидендов на Мосбирже
for u in \
 "https://iss.moex.com/iss/securities/SBER/dividends.json?iss.meta=off&iss.only=dividends" \
 "https://iss.moex.com/iss/securities/SBER/dividends.json" \
 "https://iss.moex.com/iss/cci/corp-actions/dividends.json?iss.meta=off&secid=SBER" \
 "https://iss.moex.com/iss/cci/corp-actions.json?iss.meta=off" \
 "https://iss.moex.com/iss/securities/SBER/dividends" ; do
  echo "### $u"
  curl -sS -m 30 "$u" | tr -s ' \n' ' ' | cut -c1-700; echo
done
