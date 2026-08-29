# Zonda PIT-38 Calculator

Desktopowe narzędzie do przetwarzania raportów transakcyjnych CSV z giełdy Zonda i przygotowania rocznych zestawień potrzebnych do rozliczenia kryptowalut w PIT-38.

Projekt powstał z bardzo konkretnego problemu: raporty giełdowe zawierały różne rodzaje transakcji, operacji oraz prowizji, które należało połączyć, odpowiednio wycenić w PLN i rozdzielić na poszczególne lata podatkowe.

Logika programu została przygotowana tak, aby sposób liczenia odpowiadał metodzie stosowanej przez księgowego mojego znajomego, przy rzeczywistym rozliczeniu kryptowalut.

## Co robi program

Do katalogu `_wrzuc_raport` można wrzucić raporty CSV pobrane z Zonda, również obejmujące kilka różnych lat.

Program automatycznie:

- rozpoznaje i łączy odpowiadające sobie raporty transakcji i operacji,
- przyporządkowuje dane do właściwego roku,
- tworzy osobne rozliczenie dla każdego roku,
- oblicza przychody i koszty,
- wylicza wynik podatkowy oraz 19% podatku,
- generuje gotowe zestawienia CSV.

## Przeliczanie wartości na PLN

Jeżeli transakcja nie była wykonana bezpośrednio w PLN, program ustala jej wartość w złotych zgodnie z odpowiednim kursem dla daty operacji.

W czasie przetwarzania budowana jest mapa kursów wykorzystywana następnie do wyceny poszczególnych operacji.

## Obsługa prowizji

Istotną częścią kalkulatora jest analiza prowizji pobieranych przez giełdę.

Program rozróżnia między innymi:

- prowizje związane z transakcjami **krypto ↔ FIAT**,
- prowizje pobierane przy transakcjach **krypto ↔ krypto**, w tym transakcjach ze stablecoinami.

Jeżeli fee zostało pobrane w kryptowalucie, program ustala ilość pobranego aktywa, a następnie wycenia jego wartość w PLN dla odpowiedniego dnia.

Dzięki temu końcowy raport pokazuje osobno m.in.:

- prowizje przypisane do transakcji FIAT,
- łączną wartość prowizji pobranych w kryptowalutach,
- liczbę prowizji poprawnie wycenionych,
- operacje, których nie udało się automatycznie wycenić.

## Dane wejściowe

Program został zbudowany dla historycznych raportów CSV udostępnianych przez Zonda poprzez funkcję **„Pobierz raport CSV”**.

Wymaga dwóch odpowiadających sobie typów raportów OPS/TRD.

Repozytorium celowo nie zawiera rzeczywistych raportów użytkownika. Pliki te mogą zawierać pełną historię transakcji i dane finansowe.

Ze względu na zmiany po stronie Zonda uzyskanie dziś raportów dokładnie w tym samym historycznym formacie może nie być możliwe.

## Jak użyć

1. Pobierz wersję programu z sekcji **Releases**.
2. Rozpakuj paczkę.
3. Umieść raporty CSV Zonda w katalogu:

```text
_wrzuc_raport/