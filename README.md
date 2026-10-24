# YouTube Műhely 2

Magyar felületű Windows-program képekből, szövegből és saját videókból készülő videókhoz. Angol narrációt, feliratot és saját háttérzenét is hozzáadhatsz, majd az elkészült MP4-et feltöltheted a YouTube-ra.

A helyi változat használatához nincs előfizetés és nincs kötelező fizetős API. Az AI-modelleket és az angol hangmotort az első használat előtt külön kell letölteni. A letöltés után a helyi videógenerálás és a Piper-narráció internet nélkül működik. Az áram- és hardverigény a saját gépedet terheli.

Ez a forráskiadás a **19. javítás – Windows 11** változatát tartalmazza.
Az alkalmazás a tényleges rendszerbuildből ismeri fel a Windows 11-et.
Az **AI-motor és erőforrások → Windows 11 és AI ellenőrzése** gomb a
kiválasztott AI-motor saját Pythonjával rövid, valódi GPU-számítást ellenőriz,
és helyi jelentést ment. Kiírja a GPU-t, videomemóriát és a ROCm/HIP vagy CUDA
hátteret. Natív motorhiba esetén rövid hibát jelez és megállítja a munkasort.
A folytatási napló Windows alatt atomi, felülírást tiltó átnevezéssel készül:
nincs szüksége hard-link létrehozási jogosultságra. A csomagolt AMD motorhoz
64 bites Windows 11 / x64 kell; a meglévő modellek újra használhatók.
A program nem módosít Windows-védelmet és nem telepít illesztőprogramot.
A célzott Windows-eseménynapló-lekérdezés időkorlátja lezáratlan segédfolyamat-cső esetén is véges marad. A saját ComfyUI offline módban indul.

A **18. javítás** memóriavédelmei megmaradtak. A saját Élethű motor modellhez kötött figyelemszámítása a nem pontosan osztható álló képméreteket is kisebb részekben kezeli. Megszünteti a hibát, amely 832×1056-os képnél, 33 képkockával egyszerre 71,08 GiB videomemóriát próbált lefoglalni. Egy 32 bites pontszámpuffer legfeljebb 128 MiB; ez nem a teljes GPU-memóriahasználat korlátja. A teljes kulcssor részt vesz a számításban, a felbontás és videóhossz megmarad. Memóriahiánynál rövid hiba jelenik meg, a teljes technikai előzmény a helyi `history.json` fájlban marad; a munkasor következő elemei várakoznak. Új modellletöltés nem szükséges. A javítás nem garantál gyorsabb generálást vagy jobb arcminőséget.

A 17. javítás motorindítási javítása megmaradt: közvetlen helyi HTTP-kapcsolat, munkasor alapú készenléti ellenőrzés, a csomag hálózati biztonsági adatfájljának megőrzése, változatlan tanúsítvány-ellenőrzés.

A 16. javítás védelmei megmaradtak. Az Élethű motor a hosszabb jeleneteket kisebb feladatokban számolja, az előző rész utolsó elfogadott képkockájából folytatva. A szövegmodell CPU-n, az animáció és a VAE GPU-n fut. A program ellenőrzi a hibás számértékeket, a PNG-k épségét és a Windows elérhető driver-újraindulási eseményeit; ilyen hiba esetén a munkasor is leáll. Ez hibakezelési védelem, nem garantált driverjavítás vagy arcminőség.

A fő videóbeállítások továbbra is a szélesebb bal oldalon vannak, az angol jelenetleírás és narráció két kompakt mezőben jobbra került. A nyolc táncmozdulat és a mentett munkasor megmaradt. A választható Grok-kapcsolat fizetős külső szolgáltatás, saját xAI API-kulcsot igényel; az ingyenes helyi munkafolyamathoz nem szükséges. Valódi Grok-generálás minőségét ez a kiadás nem igazolja.

## Mire képes?

- Szöveg vagy referenciafotó alapján helyi AI-jelenetek készítése ComfyUI/Wan motorral.
- Teljes alakos mozgás, természetes mozgás és külön portré mód.
- Nyolc táncminta: oldalazó ringás, oldallépés zárással, csípőringás, diszkó karmozdulat, finom twist, tapsolós tánc, egyszerű shuffle és vidám rugózás; két erősséggel és több szereplő közös táncával.
- Angol női hang helyben, saját hangfájl vagy narráció nélküli videó.
- Saját zenefájl hozzáadása, hangerő, ismétlés és halkítás.
- 16:9, Shorts és eredeti fotóarány; beállítható jelenethossz.
- Jelenetek előnézete és hibás jelenet cseréje saját klippel, a hang és a zene megtartásával.
- Zene cseréje az elkészült videón új AI-generálás nélkül.
- Megszakadt helyi munka folytatása: a kész jelenetek átvétele új angol felolvasás és AI-generálás nélkül; csak a hiányzó részek készülnek újra.
- Mentett munkasor: több helyi videó egymás után, külön beállításokkal; sorrendcsere, hibajelzés és kézi újrapróbálás.

Az AI eredménye változó: az arc, a kezek és a ruharészletek torzulhatnak. A helyi motor minősége nem garantáltan egyezik meg a Grok mintavideókéval. A felbontás növelése önmagában nem javítja meg az arcot. Egy öt másodperces részletes jelenet is hosszú ideig készülhet.

## Futtatás forrásból Windows alatt

Python **3.12**, Tkinter és 64 bites Windows szükséges. A helyi AI-motorhoz megfelelő GPU, elegendő RAM és több tíz GB szabad tárhely kell. A fejlesztés AMD RX 9070 XT 16 GB / 96 GB RAM gépen történt; ez nem általános sebességígéret más gépekre.

Az AMD PyTorch/ROCm 7.2.1 Windows-csomag hivatalos követelménye **Windows 11**. Windows 10 alatti GPU-stabilitás nem igazolt. A program az illesztőprogramon és a rendszer védelmein nem változtat. [AMD kiadási megjegyzések](https://www.amd.com/en/resources/support-articles/release-notes/RN-AMDGPU-WINDOWS-PYTORCH-7-2-1.html)

PowerShellben a repository gyökeréből:

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe scripts\setup_voice.py
& .\.venv\Scripts\python.exe work\v2_source\ytmuhely.py
```

A hangletöltő a rögzített Piper Windows-kiadást és az angol LJ Speech hangot tölti le, és fájlonként ellenőrzi az SHA-256 értékeket. Nem indít AI-generálást. Internet nélkül kihagyható, ha a teljes korábbi programcsomag `hangmotor` mappáját bemásolod a `work/v2_source` mappába. Hangmotor nélkül válaszd a **Nincs narráció** vagy **Saját hangfájl** módot.

A grafikus program **AI-motor és erőforrások** részén választhatsz meglévő motormappát, vagy elindíthatod a szükséges helyi összetevők letöltését. A modellek nem részei a Git-repositorynak. Meglévő ComfyUI esetén annak címe és egy támogatott munkafolyamat is megadható; a megfelelő node-ok és modellek külön szükségesek. A részletes útmutató: [HASZNALAT.md](work/v2_source/HASZNALAT.md).

## Videó készítése

1. Adj hozzá egy képet vagy saját videót, és írd le angolul a kívánt jelenetet/mozgást.
2. Maradjon a **helyi AI-videó** forrás, ha fizetős szolgáltatás nélkül dolgoznál.
3. Állítsd be a jelenet hosszát. Narráció esetén a jelenet a felolvasás idejéhez igazodik.
4. Adj meg angol narrációt, vagy válaszd a narráció nélküli módot. Saját zenét a hangbeállításoknál választhatsz.
5. Indítsd a készítést. Az eredményt és a jeleneteket a választott kimeneti mappában találod.

Az alkalmazás MP4-et készít; nem jelentkezik be a YouTube-fiókodba, és nem publikál automatikusan.

## Tánc a referenciafotón

A bal oldali **Tánc és mozgás** résznél válassz **Táncmozdulatot**, **Mozgás erősségét**, és szükség esetén **Több szereplő együtt** módot, majd kattints a **Táncmozdulat alkalmazása** gombra. Ez a jobb oldali mezőbe illeszti a szerkeszthető angol mozgásleírást és kiválasztja a teljes alakos tánc módot; a generálást a szokásos készítésgombbal vagy a munkasorban indíthatod. A leírás cseréje egy Ctrl+Z-vel visszavonható. A beállított leírás a projektben és a munkasorban is megmarad.

Ezek szöveges mozdulatminták a meglévő helyi AI-motorhoz, új modellt nem igényelnek. A pontos koreográfia, a zenével való ütemegyezés és a hibátlan arc nem garantált. Teljes alakos fotóval érdemes kezdeni; a levágott lábak helyét a minta nem kéri kitalálni. A program a teljes jelenetet együtt generálja.

## Több videó előkészítése

A **05 Munkasor → Hozzáadás a munkasorhoz** elmenti az aktuális beállításokat. Ezután készítsd elő és add hozzá a következő videót. A **Munkasor megnyitása… → Munkasor indítása** sorban feldolgozza őket. Mentett projektfájlok egyszerre is hozzáadhatók. A sor helyi videóval és offline angol hanggal, saját hanggal vagy narráció nélkül használható.

A **Leállás a jelenlegi után** befejezi az aktuális videót, majd megáll. A **Jelenlegi megszakítása** a futó videót is leállítja. A munkasor ablakának bezárásakor a feldolgozás tovább fut; a teljes program maradjon nyitva. Windows alatt a program a munkasor idejére kéri az automatikus alvás felfüggesztését, a kijelző kikapcsolhat. Sikertelen ébren tartási kérésnél jelzést ad.

A munkasor a Windows felhasználó helyi alkalmazásadatai között marad meg. Újraindítás után nem indul el magától. A félbemaradt munka kézzel újrapróbálható; ez a teljes feladatot újrakezdi. A már megtartott jelenetek felhasználásához külön a **Megszakadt helyi munka folytatása…** használható. A forrásképek, videók és hangok maradjanak eredeti helyükön.

## EXE készítése

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

A szkript helyi virtuális környezetet készít, telepíti a rögzített Python-függőségeket, majd a `dist/YouTubeMuhely2` mappába fordít. Előtte futtasd a hangletöltőt, ha az offline angol hangot is a csomagba szeretnéd tenni. A teljes kimeneti mappát terjeszd, ne csak az EXE-t. A nagy AI-modellek a fordításba sem kerülnek bele.

## Ellenőrzés fejlesztőknek

```powershell
& .\.venv\Scripts\python.exe -B work\v2_tests\run_release_tests.py
```

A tesztek CPU-n futnak, és kis mesterséges képeket/hangokat/videókat készítenek. Nem töltenek le modelleket, nem indítanak fizetős generálást. A külön OpenCV-tesztekhez `numpy` és `opencv-python` szükséges ugyanebben a környezetben, vagy `--portrait-python` argumentummal külön Python adható meg. Hiányzó OpenCV esetén a futtató külön jelzi a kihagyást, nem állít teljes ellenőrzést. A tesztek nem bizonyítják az AI-videó vizuális minőségét.

## Repository felépítése

| Mappa/fájl | Tartalom |
| --- | --- |
| `work/v2_source` | Program, felület, helyi generálás és videófeldolgozás |
| `work/v2_tests` | Modell nélküli regressziós és kis médiatesztek |
| `scripts/setup_voice.py` | Külön telepíthető angol offline hang |
| `work/YouTubeMuhely2.spec` | Hordozható PyInstaller-konfiguráció |
| `LICENSES`, `voice_notices`, `THIRD_PARTY.md` | Megőrzött licenc- és modellkártyák |

A repository nem tartalmaz személyes képet, videót, API-kulcsot, AI-modellsúlyt, Piper-futtatókörnyezetet vagy kész EXE-csomagot. A telepítéshez szükséges kis, verziózott eszközarchívumok a forrással együtt szerepelnek. Licencek: [LICENSE.txt](LICENSE.txt), [THIRD_PARTY.md](THIRD_PARTY.md).
