# Cloud Transformation

Prosty, pełnoekranowy viewer chmur punktów oparty na Potree 1.8. Projekt
uruchamia jeden widok z pliku `index.html`, pozwala przesyłać pliki LAS/LAZ
i konwertuje je lokalnie za pomocą PotreeConverter.

## Uruchomienie

Zainstaluj zależności:

```powershell
npm.cmd install
```

Uruchom lokalny serwer:

```powershell
npm.cmd start
```

Viewer i lokalne API konwersji będą dostępne pod adresem:

    http://localhost:1234/

Na systemie bez blokady skryptów PowerShell można używać standardowych
poleceń `npm install` i `npm start`.

## Import LAS/LAZ

PotreeConverter powinien znajdować się tutaj:

```text
tools/PotreeConverter/PotreeConverter.exe
```

Po uruchomieniu projektu:

1. Kliknij `Dodaj LAS/LAZ` w prawym górnym rogu.
2. Wybierz plik `.las` lub `.laz`.
3. Podaj unikalną nazwę chmury.
4. Kliknij `Prześlij i konwertuj`.

Plik jest przesyłany wyłącznie do lokalnego serwera na tym komputerze.
PotreeConverter zapisuje wynik w `pointclouds/<nazwa>/`, a viewer automatycznie
wczytuje wygenerowany plik `metadata.json`. Konwersje wykonywane są pojedynczo,
aby nie przeciążać komputera.

Po udanej konwersji katalog chmury zawiera również oryginalny plik LAS/LAZ
pod jego pierwotną nazwą oraz pełny zapis działania PotreeConvertera w pliku
`conversion.log`.

## Ręczna podmiana chmury punktów

1. Umieść przekonwertowaną chmurę w katalogu `pointclouds`.
2. Otwórz `index.html`.
3. Zmień wartość `POINT_CLOUD_PATH`:

```js
const POINT_CLOUD_PATH = "./pointclouds/nazwa-chmury/metadata.json";
```

Dla danych z PotreeConverter 1.x wskaż plik `cloud.js`, a dla
PotreeConverter 2.x plik `metadata.json`.

## Polecenia

- `npm.cmd start` — uruchamia lokalny viewer, upload i kolejkę PotreeConvertera.
- `npm.cmd run build` — przebudowuje bibliotekę Potree.
- `npm.cmd run dev` — przebudowuje bibliotekę i obserwuje jej pliki źródłowe; serwer uruchom osobno przez `npm.cmd start`.

Serwer nasłuchuje wyłącznie lokalnie, a pliki robocze uploadu znajdują
się w ignorowanym przez Git katalogu `.runtime`.
