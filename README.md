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

## Swobodny lot WSAD

Kliknij **ikonę samolotu** w lewym dolnym rogu viewera, aby rozwinąć podpowiedzi,
a następnie **Lot WSAD**. Panel schowa się po rozpoczęciu lotu, mysz zostanie
przechwycona, a kamera przełączy się na widok perspektywiczny. Sterowanie działa jak w grach:

- **W / S** — lot do przodu / do tyłu w kierunku, w którym patrzysz;
- **A / D** — lot w lewo / w prawo;
- **mysz** — rozglądanie bez trzymania przycisku;
- **Spacja lub E** — lot w górę;
- **Ctrl lub Q** — lot w dół;
- **Shift** — trzykrotnie szybszy lot;
- **kółko myszy** — zwiększanie / zmniejszanie prędkości;
- **Esc** — zakończenie lotu i powrót do poprzedniego sterowania.

Prędkość jest podawana w jednostkach współrzędnych chmury na sekundę. Możesz
latać swobodnie w przestrzeni, bez grawitacji i kolizji. Lot zatrzymuje się również
po przełączeniu okna lub karty; ponowne wejście wymaga rozwinięcia ikony samolotu
i kliknięcia **Lot WSAD**. Podpowiedzi można zamknąć ikoną, przyciskiem ×,
klawiszem Esc albo kliknięciem poza panelem.

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
`conversion.log`. Plik `cloud-info.json` przechowuje lokalne informacje o
pochodzeniu chmury i przygotowuje projekt do późniejszego podłączenia kont oraz
bazy danych.

## Wycinanie fragmentu chmury

Segmentacja działa lokalnie i wymaga chmury, która ma zachowany oryginalny plik
LAS lub LAZ. Do odczytu i zapisu punktów wykorzystywana jest biblioteka
`tools/PotreeConverter/laszip.dll`, a skrypt segmentacji uruchamia systemowy
Python 3.

1. Wczytaj chmurę źródłową.
2. Otwórz panel `Chmury i segmentacja`.
3. Kliknij `Dodaj box`, a następnie ustaw jego pozycję, obrót i skalę w Potree.
4. Wybierz zachowanie punktów wewnątrz albo poza clipping boxem.
5. Podaj unikalną nazwę wynikowej chmury i kliknij `Wytnij i przekonwertuj`.

Serwer wybiera punkty z oryginalnego LAS/LAZ, zapisuje wynik jako
`pointclouds/<nazwa>/<nazwa>.laz`, uruchamia PotreeConverter i automatycznie
wczytuje nową chmurę. Segmentacja obsługuje również obrócone clipping boxy.

Katalog wynikowy zawiera:

- wynikowy plik LAZ;
- dane Potree (`metadata.json`, `octree.bin`, `hierarchy.bin`);
- `segmentation.log` oraz `conversion.log`;
- `cloud-info.json` z nazwą chmury źródłowej, trybem `inside`/`outside`,
  transformacją clipping boxa i liczbą wybranych punktów.

Chmury bez oryginalnego LAS/LAZ nadal można wyświetlać, ale przycisk
segmentacji będzie dla nich nieaktywny.

## Ręczne usuwanie punktów z sąsiedztwem

Narzędzie pędzla pozwala usuwać małe, nieregularne fragmenty bez używania
clipping boxa. Każde kliknięcie wskazuje rzeczywisty punkt chmury i tworzy
wokół niego sferyczne zaznaczenie o podanym promieniu.

1. Wczytaj chmurę z zachowanym plikiem źródłowym LAS/LAZ.
2. W sekcji `Usuń punkty z sąsiedztwem` ustaw promień.
3. Kliknij `Wybierz punkty w widoku`, a następnie klikaj niepasujące punkty.
   Przeciąganie nadal obraca widok, a klawisz `Esc` kończy wybieranie.
4. Otwórz panel ponownie. W razie potrzeby cofnij ostatnie zaznaczenie albo
   wyczyść wszystkie.
5. Podaj nazwę wyniku i kliknij `Usuń zaznaczone i przekonwertuj`.

Czerwone kule są tylko podglądem. Podczas zapisu serwer usuwa z oryginalnego
LAS/LAZ wszystkie punkty znajdujące się wewnątrz co najmniej jednej zaznaczonej
kuli, zapisuje nowy LAZ i uruchamia PotreeConverter. Oryginał pozostaje bez
zmian. `cloud-info.json` zachowuje środki i promienie zaznaczeń, statystyki oraz
powiązanie z chmurą źródłową, a pełny przebieg trafia do
`point-removal.log` i `conversion.log`.

## Transformacje współrzędnych

Transformacje działają lokalnie bez PDAL. Skrypt
`tools/transform_pointcloud.py` odczytuje i zapisuje punkty przez dołączoną
bibliotekę `laszip.dll`, dlatego zachowuje atrybuty punktów oraz obsługuje pliki
LAS i LAZ.

1. Wczytaj chmurę, która ma zachowany plik źródłowy LAS/LAZ.
2. Otwórz panel `Chmury i przetwarzanie`.
3. W sekcji `Transformuj chmurę` wpisz przesunięcie XYZ, obrót XYZ w stopniach
   oraz skalę XYZ.
4. Wybierz punkt odniesienia: środek granic chmury, początek układu albo własne
   współrzędne.
5. Podaj unikalną nazwę wyniku i kliknij `Transformuj i przekonwertuj`.

Obroty są wykonywane kolejno wokół osi X, Y i Z. Serwer wykonuje dwa przebiegi
po punktach: najpierw oblicza nowe granice i bezpieczny offset nagłówka LAS,
a następnie zapisuje przeliczone współrzędne do nowego LAZ. Oryginalna chmura
nie jest modyfikowana.

Katalog wynikowy zawiera wynikowy LAZ, dane Potree, `transformation.log`,
`conversion.log` oraz `cloud-info.json`. W metadanych zachowywane są parametry,
rzeczywisty punkt odniesienia, granice wejściowe i wynikowe, skala zapisu,
offset oraz powiązanie z chmurą źródłową.

Ta operacja jest transformacją liczbową w bieżącym układzie współrzędnych. Nie
wykonuje reprojekcji pomiędzy układami geodezyjnymi, np. PL-2000 i WGS84.

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

- `npm.cmd start` — uruchamia lokalny viewer, upload, segmentację, transformacje i kolejkę
  PotreeConvertera.
- `npm.cmd run build` — przebudowuje bibliotekę Potree.
- `npm.cmd run test:flight` — sprawdza sterowanie lotem i zoom kółkiem myszy (Node.js 22 lub nowszy).
- `npm.cmd run dev` — przebudowuje bibliotekę i obserwuje jej pliki źródłowe; serwer uruchom osobno przez `npm.cmd start`.

Serwer nasłuchuje wyłącznie lokalnie, a pliki robocze uploadu znajdują
się w ignorowanym przez Git katalogu `.runtime`.
