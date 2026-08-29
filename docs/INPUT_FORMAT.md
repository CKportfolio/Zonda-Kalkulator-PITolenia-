# Format danych wejściowych

Zonda PIT-38 Calculator został zbudowany dla historycznych eksportów CSV giełdy Zonda.

## Wymagania rozpoznane w aplikacji

Program:

- szuka plików CSV w katalogu `_wrzuc_raport`,
- rozpoznaje raporty typu **OPS** i **TRD**,
- dopasowuje odpowiadające sobie raporty m.in. na podstawie dat,
- oczekuje danych zgodnych z oryginalnym schematem eksportów Zonda,
- wykorzystuje m.in. informacje o dacie operacji, rynku/walucie i prowizjach.

W kodzie aplikacji występuje m.in. pole:

```text
Data operacji
```

Nie publikujemy sztucznego „przykładowego raportu”, ponieważ nie chcemy udawać pełnego schematu, którego nie da się wiarygodnie odtworzyć bez oryginalnej dokumentacji eksportu.

## Prywatność

Nie dodawaj do repozytorium własnych plików OPS/TRD.

Raporty mogą ujawniać:

- historię transakcji,
- wartości portfela,
- daty i wielkości operacji,
- prowizje,
- dane istotne podatkowo.

Katalog `_wrzuc_raport` jest wykluczony z Git przez `.gitignore`.
