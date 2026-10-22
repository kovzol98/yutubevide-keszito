# YouTube Műhely 2 – fotóból mozgó jelenet

Változat: `2026.10.22-attention-v18` · 18. javítás

Magyar Windows-program, angol narrációval és hozzáadható háttérzenével. Szövegből vagy fotóból mozgó jeleneteket készít, majd feltölthető MP4-videóvá állítja össze őket. Alapból helyben számol; külön választható, fizetős Grok internetes kapcsolat is van. A kezelőfelület meleg grafit, krém és borostyán színű.

A fő videóbeállítások a szélesebb **bal oldalon** találhatók: kép és fény, referenciafotók és videók, tánc és mozgás, valamint mentés, zene, jelenetjavítás és munkasor. A **jobb oldalon** az angol jelenetleírás és narráció kisebb szövegmezőket kapott. Mindkét oldal külön görgethető; hosszabb szöveg a kisebb mezőkbe is beírható.

## Indítás

1. Csomagold ki a teljes ZIP-et, majd indítsd a `YouTubeMuhely2.exe` fájlt. Az `_internal` és `hangmotor` mappák maradjanak mellette.
2. A **Videó forrása** legyen **AI-videó • helyi**. Adj hozzá egy fotót, és írd le angolul, mi történjen rajta. A **Kar- és testmozgás**, **Séta** vagy **Tánc** gomb egy átírható angol mintát illeszt a leírás helyére.
3. A **Fotóanimáció minősége** legyen **Élethű • arc és fény**. Új projektnél a részletesség **Normál – kisebb AI-részletesség**; a betöltött projektek megőrzik a mentett értéküket. Első próbához 2 másodperces jelenetet használj. A részletes számítás rövid jelenetnél is hosszú lehet.
4. Természetes fotóhoz válaszd az **Eredeti világítás** és **Finom képjavítás** beállításokat. Az **AI-arcjavítás** alapból kikapcsolt: így a program nem rajzolja át előre a forrás arcát. Ezt csak külön igény esetén kapcsold be, mert bizonytalan szem- vagy szemüvegrészleteket is megváltoztathat.
5. A narrációhoz írj angol szöveget. Az **Angol női LJ • offline** hang a csomag része; internet nélkül is működik. Narráció nélkül hagyd üresen a szöveget, vagy válaszd a **Nincs narráció** beállítást.
6. Ha zenét szeretnél, a **Háttérzene** résznél válassz egy hangfájlt, és állítsd be a hangerejét.
7. Ha a szereplő kicsi egy széles fotón, a **Fotó keretezése → Alanyra közelítés** beállítással szűkítheted a képet. Alapból a **Teljes fotó** marad meg. Válassz mentési mappát, majd kattints a **Videó készítése** gombra.

Ezen a gépen a motor már telepítve van. Ha a program más helyre kerül, az **AI-motor és erőforrások** résznél válaszd ki az `AI_Motor` mappát. A mellékelt `AI_MOTOR_HELYE.txt` az itteni motor helyét tartalmazza.

A készítés indításakor a program minden felhasznált fotó és videó olvashatóságát ellenőrzi, még az első jelenet elkészítése vagy AI-generálása előtt. A hiányzó vagy sérült forrást előbb cseréld ki.

## A helyi AI-motor indítása

A 17. javítás a csomagolt Windows-program helyi kapcsolati hibáját javítja. Az ellenőrzéskor a motor már futott és válaszolt, de a régi EXE a kapcsolat létrehozása előtt hibázott, majd három perc után sikertelen indítást jelzett. A helyi HTTP-kapcsolat most külön, közvetlen útvonalat használ; az internetes HTTPS-kapcsolat előkészítését ehhez nem indítja el, és a rendszer internetes proxybeállítását sem használja. A csomag hiányzó hálózati biztonsági adatfájlja is bekerült; a tanúsítvány-ellenőrzés megmarad.

A motor elérhetőségét a helyi munkasor válasza igazolja. Ez nem kér videókártya-adatokat, és nem indít generálást. A videókészítéshez a program továbbra is megvárja az indítás befejezését.

Az indítási várakozás legfeljebb három perc. Ha a motor kilép vagy nem válaszol időben, a hibaüzenet megkülönbözteti az indítás és a kapcsolat hibáját, és megadja a napló helyét. A részletek az aktuális indításhoz tartoznak, így egy régebbi sikeres futás nem rejti el az új hibát.

A javításhoz a nagy modelleket nem kell újra letölteni. A motor indulásának ellenőrzése nem gyorsítja az AI-jelenetek számítását; az alábbi GPU- és képkockaellenőrzések továbbra is működnek.

## A 18. javítás: memóriahasználat álló képeknél

Egy részletes, álló képből készített jelenetnél a motor 71,08 GiB videomemóriát próbált egyszerre lefoglalni. A hiba a figyelemszámítás darabolásában volt: ha a képből és a képkockaszámból adódó méret nem volt pontosan osztható a darabok számával, a régi eljárás visszaváltott a teljes mátrixra. A 18. javítás a saját Élethű motorban a maradékot is kisebb darabban számolja, így ennél a lépésnél nem tér vissza a teljes, 71 GiB-os foglaláshoz.

A javított eljárás a figyelemszámítás egyes, 32 bites részpontszámpuffereit legfeljebb **128 MiB** méretűre korlátozza. Ez **pufferenkénti korlát**, nem a teljes GPU-memóriahasználat felső határa. A modell, a további köztes adatok és a képkockák feldolgozása is memóriát kér; más lépésben továbbra is előfordulhat memóriahiány. A kért AI-részletesség, képkockaszám és videóhossz ettől nem csökken.

Memóriahiánynál a felugró ablak rövid, olvasható hibát mutat: a kért memória, a kártya teljes memóriája és az érintett lépés szerepelhet benne. A hosszú számítási tömbök helyett megadja a jelenetrész **`history.json`** fájljának helyét, amely megőrzi a technikai részleteket. Memóriahiány esetén a munkasor is megáll; a következő videók várakozók maradnak. Újrapróbálás előtt választhatsz Normál részletességet, és bezárhatod a többi GPU-t használó programot.

A nagy modelleket emiatt nem kell újra letölteni. A javítás a saját motor kiegészítőjével kerül be, a rendszer és az illesztőprogram beállításain nem változtat. Nem ad új arc- vagy mozgásmodellt, és nem garantál hibátlan generált arcot vagy gyorsabb futást. A Windows 11-re váltás önmagában ezt a számítási hibát nem javítja ki.

## GPU-hiba és hosszabb helyi jelenetek

A 16. javítástól az Élethű motor hosszabb jelenetei kisebb, egymás utáni AI-feladatokban készülnek. Részletes módban egy feladat legfeljebb **33**, Normál módban **49** natív képkockát számol 16 képkocka/mp sebességgel. Egy 10 másodperces részletes jelenet így öt részből áll. A következő rész az előző rész utolsó elfogadott képkockáját használja; az átfedő első képkocka csak egyszer kerül a videóba. A kért videóhossz megmarad, a program nem ismétli vissza a klipet. A határoknál a mozgás vagy a megjelenés változhat; ez nem garantál tökéletes arcot vagy folytonosságot.

A kisebb egyszeri terhelés óvatos programkorlát, nem garancia egy adott illesztőprogram stabilitására. A részek külön betöltése időt kérhet. A pontos méret, képkockaszám és részterv a jelenet naplójában és `reszterv.json` fájljában látható.

A saját helyi motor darabolt GPU-s figyelemszámítással, szinkron memóriaátadással és laprögzített memória nélkül indul. A szövegmodell CPU-n marad a kezdeti betöltésétől a számítás végéig, GPU-s GGUF-kibontás nélkül. Az animáció és a képkockák VAE-feldolgozása a videókártyán fut. A teljesen CPU-s képfeldolgozás túl nagy lassulást okozott a próbában, ezért azt a kiadás nem kényszeríti. A szöveg CPU-s feldolgozása önmagában is több percet kérhet. A program más felhasználó meglévő ComfyUI-folyamatát nem állítja át.

Az AMD PyTorch/ROCm 7.2.1 Windows-csomagjához az AMD **Windows 11** rendszert sorol támogatottnak. Windows 10 alatt a GPU-stabilitás nem igazolt. A program ezt a rendszerkorlátot nem tudja megszüntetni; nem állítja át az illesztőprogramot vagy a Windows védelmeit. [AMD kompatibilitás és kiadási megjegyzések](https://www.amd.com/en/resources/support-articles/release-notes/RN-AMDGPU-WINDOWS-PYTORCH-7-2-1.html)

A motor mindkét számítási szakasz után ellenőrzi a hibás számértékeket. A program minden visszakapott képkockát megnyit és ellenőriz. Ha egy részletgazdag fotóból csak egyszínű szürke vagy fekete képkockák készülnek, nem jelzi kész videónak. A valódi, részletgazdag állókép vagy egy szándékosan egyszínű referencia ettől nem hibás.

Windows alatt a program a generálás idejére figyeli az elérhető Display/4101 illesztőprogram-újraindulási eseményeket. Ilyen hiba, memóriahiány, hibás számítás vagy hibás képkockák esetén a munkasor is megáll; a következő videó várakozó marad. A köztes fájlok diagnosztikához megmaradnak. A `gpu_health.json` jelzi, ha a Windows eseménynaplója nem olvasható; a számítás- és képkockaellenőrzés ettől még működik. Újrapróbálás előtt indítsd újra az AI-motort és csökkentsd a részletességet. A régi, már hibás videó utólag ettől nem javul meg.

A kis biztonsági kiegészítőt a program a következő helyi Élethű generálás előtt frissíti a saját motorában. A nagy modelleket emiatt nem kell újra letölteni. A rendszer és a videókártya beállításain nem változtat.

## Táncmozdulatok a fotón szereplő karakterekhez

A bal oldali **Tánc és mozgás** részben a **Táncmozdulat** választóval kérhetsz konkrét mozgásmintát a fotó szereplőinek. A **Mozgás erőssége** lehet **Finom** vagy **Élénk**. Ha több karakter táncoljon együtt, jelöld be a **Több szereplő együtt** lehetőséget, majd kattints a **Táncmozdulat alkalmazása** gombra.

Nyolc minta választható: **Oldalazó ringás**, **Oldallépés zárással**, **Csípőringás**, **Diszkó karmozdulat**, **Finom twist**, **Tapsolós tánc**, **Egyszerű shuffle** és **Vidám rugózás**.

A gomb átírja az **Angol jelenetleírás** mezőt a kiválasztott, szerkeszthető táncmozdulatokra, és a mozgásmódot **Teljes alak • tánc (AI-jelenet)** értékre állítja. Az alkalmazás önmagában nem indít videókészítést. A szöveg cseréje a mezőben **Ctrl+Z**-vel visszavonható. Alkalmazás után olvasd át és szükség szerint módosítsd az angol leírást. A projektbe és a munkasorba a beillesztett jelenetleírás kerül; a választók későbbi módosítása csak újabb alkalmazáskor változtatja meg azt.

Válassz éles, jól megvilágított fotót, amelyen az egész alak, a kezek és a lábfejek is látszanak, körülöttük pedig marad hely a mozgásnak. Több szereplőnél a **Fotó keretezése → Teljes fotó** segít megtartani a teljes csoportot. A minták a meglévő képkivágás megtartását és csak a látható ízületek mozgatását kérik. Arcképből így nem várható teljes alakos tánc; a modell a kérés ellenére is kitalálhat hiányzó testrészeket, ezért azok mozgása és aránya bizonytalan lehet.

A kiválasztott mozdulatok angol utasításként kerülnek a már telepített AI-motorhoz. Ez a fejlesztés nem igényel új táncmodellt vagy mintavideót. A fej, a test és a környezet közös jelenetként készül; a program nem illeszt külön fejet a táncoló testre. A modell a mozdulatoktól eltérhet, több karakter mozgása sem garantáltan azonos, és az arc vagy a végtagok továbbra is torzulhatnak. Elsőre rövid, finom mozgást próbálj. A hozzáadott zene az elkészült videóhoz keveredik; a tánc nem kap automatikus ütemillesztést.

Ha többféle táncot szeretnél, alkalmazz egy mintát, add a videót a munkasorhoz, majd válassz másik mintát és add hozzá a következő munkát. A korábban hozzáadott munkák leírása megmarad.

## Több videó egymás után: munkasor

A **05 Munkasor → Hozzáadás a munkasorhoz** gomb elmenti az éppen beállított videó külön munkapéldányát. Utána átírhatod a címet, választhatsz más fotót, jelenetleírást, angol hangot vagy zenét, és hozzáadhatod a következő videót. A későbbi szerkesztés nem változtatja meg a már felvett munkák beállításait. A képeket, videókat, zenét és saját narrációs fájlokat a munkasor nem másolja át: hagyd őket az eredeti helyükön a munka végéig.

A **Munkasor megnyitása…** gomb mutatja az előkészített, futó, kész és hibás munkákat. A hozzáadás önmagában nem indít számítást. A munkasor ablakában a **Munkasor indítása** gombbal külön indítsd el a feldolgozást. A **Mentett projektek hozzáadása…** gombbal korábban mentett helyi projektfájlokat is felvehetsz. A várakozó munkákat a fel és le gombokkal átrendezheted. A program egyszerre egy videót készít; így a helyi AI-feladatok nem versenyeznek egymással a videókártyáért. Az elkészült videók a munkánként megadott eredménymappába kerülnek. A kész munkát újabb indítás nem készíti el ismét.

A munkasor helyi **AI-videó**, **Saját videók** és **Képes diavideó** készítésére használható. Az **Angol női LJ • offline**, saját narrációs hangfájl vagy narráció nélküli beállítás támogatott. **Grok-videót és internetes narrációt nem vesz fel**, és nem indít automatikus fizetős generálást. A helyi AI-hoz szükséges motort és modelleket az éjszakai használat előtt telepítsd és próbáld ki.

A **Leállás a jelenlegi után** gomb a futó videót hagyja befejeződni, majd megáll. A **Jelenlegi megszakítása** a futó videót állítja le, és a következő munkát sem indítja el. A kész eredmények megmaradnak. Egy szokásos bemeneti hiba után a program a következő munkával folytatja a sort. GPU-újraindulás, hibás AI-számítás vagy képkockahiba esetén a sor megáll.

A munkák és állapotuk a gépen tartós mentést kapnak. A munkasor a saját Windows-felhasználódhoz tartozik, így egy új programmappa kicsomagolásától nem tűnik el. A program újraindítása után a félbeszakadt munka nem indul el magától: kijelölés után az **Újrapróbálás** gombbal ismét várakozóvá teheted, majd indítsd el a sort. Ez a teljes videó újrakezdése, nem a félbeszakadt AI-számítás folytatása. Ha a félkész eredménymappában már vannak kész jelenetek és `folytatas.json`, azokat külön a **04 Jelenetek és javítás → Megszakadt helyi munka folytatása…** gombbal hasznosíthatod újra.

Éjszakai használathoz a programnak futnia kell, a gépnek bekapcsolva kell maradnia. A Windows energiabeállításaiban a munka idejére kapcsold ki az automatikus alvást; a kijelző kikapcsolása önmagában nem akadály. A program a munkasor végén nem kapcsolja ki a számítógépet. A munkasor időt takarít meg az indításoknál, a videónkénti AI-számítást nem gyorsítja és a képminőséget nem módosítja.

## Megszakadt helyi munka folytatása

A 12. javítástól **újonnan indított helyi, offline projektek** a kész jelenetek után `folytatas.json` mentést is kapnak. Ez a helyi AI-videóhoz, saját videókhoz és képes diavideóhoz használható, az offline LJ hanggal, saját narrációs hangfájllal vagy narráció nélkül. Ha megszakítod a munkát, hiba történik, vagy bezárod a programot, a **04 Jelenetek és javítás → Megszakadt helyi munka folytatása…** gombbal a félkész projekt mappájából válaszd ki a `projekt.json` vagy `folytatas.json` fájlt. A folytatáshoz őrizd meg a teljes eredménymappát, annak JSON-fájljait és az elkészült jeleneteket.

A program ellenőrzi a mentett beállításokat és a bemeneti fájlok tartalmát. Az eredeti fotókat, videókat, zenét és saját narrációs hangot hagyd a korábbi helyükön, változatlanul; az átnevezett, elmozdított vagy módosított forrást elutasítja. Így nem kever össze másik fotóból vagy másik beállítással készült jeleneteket.

Az ellenőrzött, már teljesen elkészült jeleneteket a saját angol hangjukkal és feliratukkal együtt átveszi. Ezeket nem generálja és nem olvassa fel újra. Csak a még hiányzó jeleneteket készíti el, majd újra összeállítja a teljes videót az eredeti zenei és hangbeállításokkal. A megszakadt, még be nem fejezett **aktuális jelenet újrakezdődik**; a funkció nem ígér képkockánkénti vagy félbeszakadt modell-számítási folytatást. Ha már minden jelenet elkészült, és csak a végső összeállítás hiányzik, ehhez a folytatáshoz nem kell AI-motort indítani.

A folytatás **külön eredménymappába**, a korábbi projekt mellé dolgozik. A korábbi félkész projekt változatlanul megmarad. Az új mappát is egyben őrizd meg a későbbi folytatáshoz, zenecseréhez vagy jelenetjavításhoz.

Ez a lehetőség helyi számításhoz és offline hanghoz készült, fizetős kérés nélkül. **Grok-munkát vagy internetes narrációt nem folytat.** Ezeket választó mentést még a munka indítása előtt elutasítja. A Grok-feladatoknak továbbra is külön eredménymentő gombjuk van. A 11. és régebbi javítással indított projektek nem tartalmazzák az új folytatási mentést; azokhoz a funkció nem készít utólag ilyen mentést. Az elkészült régebbi MP4-ek saját videóként továbbra is felhasználhatók.

## Jelenetek visszanézése és javítása

Az újonnan elkészített videók külön jelenetei a projekt **`jelenetek`** mappájában maradnak meg. A **04 Jelenetek és javítás → Elkészült jelenetek…** gombbal megnyithatod őket. Ha nincs megnyitott eredmény, válaszd ki a kész videót, annak `projekt.json` fájlját vagy a teljes projektmappát. A jelenetlista hosszt és előnézeti képet mutat; a **Jelenet lejátszása** a géped videólejátszóját nyitja meg.

1. Válassz egy jelenetet, majd a **Cserevideó kiválasztása…** gombbal adj hozzá egy jobb mozgóképet. Több jelenethez is kijelölhetsz cserét.
2. A cserevideó legyen legalább olyan hosszú, mint az eredeti jelenet. A hosszabbat a program a jelenet végén levágja. A túl rövidet hibaüzenettel jelzi; nem ismétli meg és nem fagyasztja ki hosszasan.
3. Az **Új változat mentése** külön `Jelenetcsere_…` almappába menti a javított MP4-et. Az eredeti jelenet hossza, felirata, valamint a kész videó teljes angol hangja és zenéje megmarad. A csereklip saját hangját nem használja. A képet a korábbi keretbe illeszti, és egységesen újrakódolja.
4. Az **Eredeti kép visszaállítása** törli a kijelölt cserét. A **Megszakítás** a folyamatban lévő helyi munkát állítja le; a kész eredeti videó megmarad.

A külön jelenet lejátszása a jelenet saját hangját tartalmazza. A végső háttérzene és az egész videóhoz hozzáadott saját narráció az összeállított MP4-ben hallható. A javított változat saját hangalapot is kap, így később annak a zenéjét is módosíthatod a **Zene cseréje kész videón…** gombbal.

Ha már készítettél más zenével egy `video_zene_…mp4` változatot, azt a konkrét MP4-et válaszd ki. A szerkesztő fejlécében a **Hang és zene** felirat mutatja, melyik kész videó teljes hangsávját tartja meg. A javított változat normál projektként is visszatölthető; ilyenkor a teljes kész videót használja saját klipként, a külön jelenettár pedig megmarad a további jelenetcserékhez.

A jelenetcsere a PC-n történik, új AI-generálás és új fizetős Grok-kérés nélkül. Ehhez már meglévő cserevideót kell kiválasztani; a funkció önmagában nem javítja újra AI-val az arcot. A teljes programmappa helyett a teljes **eredményprojektet** őrizd meg a `jelenetek`, `hang_alap` és JSON-fájlokkal együtt. Ezek több lemezhelyet igényelnek, mint az önálló `video.mp4`.

Ha a készítés megszakadt, az addig elmentett jelenetek visszanézhetők. A jelenetcsere az eredeti teljes videó és hangalap elkészülte után használható. A 10. és korábbi javítással készített projektekhez nincs ilyen jelenettár; azokat saját videóként továbbra is felhasználhatod.

## Választható Grok-videó interneten

A **Kép és fény → Videó forrása → Grok videó • internet** az xAI `grok-imagine-video-1.5` modelljét használja. Ez **fizetős, kísérleti kapcsolat**. Valódi, hitelesített API-generálási próba még nem történt; a működést szimulált válaszokkal és helyi videóexporttal ellenőriztük. Az arc minősége, a futási idő és a korábban küldött Grok-mintával való egyezés nincs igazolva. A helyi motor marad az alapbeállítás.

1. Válaszd a **Grok videó • internet** forrást. Adj angol jelenetleírást, és ha képből indulsz, egy referenciafotót. Kép nélkül szöveges jelenetet kér.
2. A megjelenő **xAI API-kulcs · csak ebben a futó programban** mezőbe írd a saját kulcsodat. Külön xAI API-hozzáférés és számlázási fedezet kell; a Grok fogyasztói előfizetése nem jelent ingyenes API-használatot. A kulcs takart mezőben van, a program nem menti a projektbe, a munkanaplóba vagy a beállításfájlba. Újraindítás után ismét meg kell adni. Parancssoros használatkor az `XAI_API_KEY` környezeti változóból is olvasható; a program ezt sem írja fájlba.
3. A **Grok generálási felbontás** lehet **720p** vagy **1080p**. A **Legfeljebb ennyi fizetős jelenet** alapértéke **1**. Több fotó vagy szövegrész több jelenetet eredményezhet; a korlát túllépésénél a program még generálás előtt megáll. Ez darabszámkorlát, nem pénzkeret.
4. Egy jelenet **1–15 másodperc** lehet. Narráció nélkül a **Hang és mentés → Jelenethossz narráció nélkül · másodperc** mező számít. Narrációval a felolvasás tényleges hosszához igazodik; az összes jelenet hosszát az első fizetős kérés előtt ellenőrzi. Az API-nak küldött hossz egész másodpercre felfelé kerekítődik, ami az árat is befolyásolhatja.
5. Állítsd be az angol hangot és a zenét, majd kattints a **Fizetős Grok-videó indítása** gombra. A külső szolgáltatás hang nélküli jelenetet kap feladatként; az angol narrációt, a feliratot és a választott zenét a program a PC-n teszi a végső videóba.

A teljes referenciafotó és az angol jelenetleírás az xAI szolgáltatásához kerül. A Grok generálása az internetes szolgáltatás gépén fut; ehhez nem kell a helyi AI-motort letölteni vagy elindítani. A helyi **Alanyra közelítés**, **Fotóanimáció minősége** és **AI-arcjavítás** itt nem érvényes. A mozgásválasztó angol utasítást ad; a helyi portrémotort nem indítja el. Képből a jelenet a fotó arányát követi, kép nélkül a Shorts álló, a többi formátum fekvő jelenetet kér. A végső exportkeretet külön választhatod.

A 2026.10.20-án ellenőrzött [hivatalos árlista](https://docs.x.ai/developers/pricing) alapján a választott modell becsült alapára **720p-n 0,14 USD/másodperc**, **1080p-n 0,25 USD/másodperc**, továbbá **0,01 USD/bemeneti kép**. Egy képből kért 5 másodperc becslése így **0,71 USD**, illetve **1,26 USD**. A felület dátummal jelöli a becslést; ez nem ellenőrzött fiókterhelés, és nem helyettesíti az aktuális xAI árlistát vagy számlát. További részletek: [videós útmutató](https://docs.x.ai/developers/model-capabilities/video/overview), [külön API-számlázás](https://docs.x.ai/developers/faq/accounts).

### Megszakadt Grok-feladat eredményének mentése

A **Megszakítás** a program várakozását állítja le. A már elküldött feladat a szolgáltatásnál tovább futhat és számlázható. Az időtúllépés vagy hálózati hiba nem indít automatikusan új fizetős generálást.

Őrizd meg az eredménymappát, azon belül az **`AI_jelenetek/jelenet_001/grok_job.json`** fájlt; több jelenetnél mindegyiknek külön fájlja van. A Grok-panel **Grok-feladat eredményének mentése…** gombjánál add meg a kulcsot, és válaszd ki a mentett feladatfájlt. Ez a meglévő azonosító állapotát és eredményét kéri le, **új videógenerálást nem küld be**. A letöltött MP4-et teljes lejátszási ellenőrzés után jelzi késznek. A nyers klip a **Saját videók** forrásnál betölthető angol narráció és zene hozzáadásához.

Ha a beküldés megszakadt, mielőtt a szolgáltatás feladatazonosítója megérkezett volna, a program bizonytalan beküldésként őrzi meg. Azonosító nélkül ezzel a gombbal nem tudja lekérni. Új generálás indítása előtt ellenőrizd az xAI fiókodban, létrejött-e és számlázódott-e a kérés. Lejárt vagy a szolgáltató által elutasított feladatból nem állítható elő kész videó.

## Helyi Élethű mód

Fotóból nagyobb, Wan2.2 I2V 14B modell generálja együtt a fejet, a hajat, a nyakat, a testet és a környezetet. Alapból a forrás arcát változatlanul használja. A külön **AI-arcjavítás** kapcsoló egyszeri, GFPGAN-alapú rekonstrukciót kér az induló fotón; ettől a szem, a szemüveg vagy más arcrészlet is megváltozhat. Ez nem biztosítja a videóban megjelenő arcrészletek azonosságát. A **Finom képjavítás** a végső élesítést és az MP4 mentési minőségét szabályozza, az **Eredeti megjelenés** az utólagos képjavító szűrést hagyja ki.

A korábbi változat utólag külön fejet és mozdulatlan fotóhátteret illesztett a jelenetre. Ez a változat ezeket a lépéseket elhagyja, mert látható eltérést okozhattak a fej és a test mozgása között. A külön **Portré • finom fejmozgás** mód továbbra is választható arcképekhez.

A generálás 16 új AI-képkockát készít másodpercenként. A RIFE mozgássimító köztes képkockákkal 30 képkocka/másodpercre egészíti ki a jelenetet. Az eredeti 16 képkockás mozgást és a simított változatot is megőrzi az eredmények között. A simítás folytonosabbá teheti a mozgást, de a hibásan generált arcot, kezet vagy testtartást nem javítja ki. A nagy modell számítása ezen a gépen is több tíz percig tarthat. A nagyobb részletesség és a hosszabb jelenet több időt és memóriát kér.

A forrásfotó előkészítése, a modell betöltése, az első és második számítási szakasz, a képkockák véglegesítése és a mozgássimítás külön lépés. A folyamatjelző ezeket mutatja; a mintavétel befejezése után még van feldolgozás. A képkockák véglegesítésekor a program a teljes klip hosszával dolgozik.

Az AI-számítás eredményét még a képkockák véglegesítése előtt elmenti. Ha a saját videómotorban ennél a lépésnél igazolt videomemória-hiány történik, ugyanebből a mentésből egyszer kisebb térbeli darabokkal próbálkozik. Nem kezdi újra a hosszú AI-generálást, és nem rövidíti le az időbeli feldolgozás ablakát. A próbálkozásról külön naplót őriz. Másik kliens feladatai vagy bizonytalan motorállapot mellett nem indít automatikus helyreállítást. Ez a futó feladat külön hibaága. A 12. javítás **Megszakadt helyi munka folytatása…** gombja az előzőleg teljesen elkészült jeleneteket veszi át; a megszakadt aktuális jelenetet újrakezdi.

A Grokhoz hasonló minőség cél, de az AI továbbra is megváltoztathat arcrészleteket, és hibázhat a kezeknél, a ruhánál, a háttérnél vagy nagy fejfordulatnál. Jó megvilágítású, éles forrásfotóval és mérsékelt mozgással érdemes kezdeni. A köztes generált jelenet megmarad az eredmények között.

A külön ellenőrzött kisebb modell és a nagyobb darabokkal végzett képfeldolgozás nem adott jobb arcot vagy fényeket. A kisebb memóriaigényű FP8-próba teljes futása körülbelül ugyanannyi ideig tartott, és az arcot sem javította. Ezek ezért nem változtatták meg a kiadott helyi motor alapbeállítását. A v10 új Grok-választója nem helyi modellminőség-javítás.

## Mozgás és hossz

- **Leírás szerinti mozgás (AI-jelenet):** a megadott angol cselekvést kéri a modelltől. Fotó használatakor ehhez kötelező jelenetleírást írni vagy mozgásmintát választani; üres leírással nem indul el a hosszú számítás. Konkrét mozdulatot írj: például a szereplő engedje le a karját, fordítsa el a törzsét, majd nézzen a kamerára. Az eredmény eltérhet a leírástól.
- **Teljes alak • tánc (AI-jelenet):** a karok, a törzs és a lábak közös táncmozgását kéri. Egész alakos fotót használj, látható lábfejekkel. Példa: `The man dances gently in place, bending his knees, taking small side steps and swinging both arms. He looks toward the camera. Fixed camera, stable natural daylight.`
- **Portré • finom fejmozgás (gyorsabb):** külön portrémotorral kis fejfordulást és billentést készít. A háttér és a ruha a fotóból marad. A szájat nem vezérli beszédre. Öt másodperc korábbi mérése ezen a gépen 87,3 másodperc volt.

A `dance` vagy `dancing` leírást a program tánc módra állítja, korábbi portréprojekt betöltésekor is.

A mozgásminták lecserélik a jelenetleírás mező tartalmát; a beillesztett angol szöveget szerkesztheted. A **Tánc** a tánc módot, a másik két minta a leírás szerinti mozgást választja. Sétához és tánchoz olyan forrásfotót használj, amelyen az egész alak és a lábfej is látszik. Hiányzó testrészeket a modellnek ki kell találnia, ami növeli a torzulás esélyét.

A **Hang és mentés → Jelenethossz narráció nélkül · másodperc** mezőben állíthatod a hosszúságot. Narrációval a jelenet a felolvasás hosszához igazodik. Több leírást üres sorral válassz el; több fotó a lista sorrendjében kerül a jelenetekbe. Az AI-jelenethez elegendő új képkockát számol, nem ismétli a rövid klipet.

## Videomemória ellenőrzése

Az **AI-motor és erőforrások → Videomemória ellenőrzése** gomb megmutatja, mely folyamatok foglalnak legalább 2 GiB videomemóriát. AI-videó indítása előtt ez az ellenőrzés automatikusan is lefut; az eredmény a részletességi beállítás alatt megmarad. Ha egy másik AI-program több GB-ot foglal, kevesebb hely marad a videómodellnek, és a számítás lényegesen lassabb lehet. Az ellenőrzés csak adatot olvas. Ha a Windows nem ad mérhető adatot, a program ezt külön jelzi.

## Háttérzene hozzáadása

A külön **Háttérzene** résznél válassz a gépedről egy zenefájlt, például MP3, WAV, M4A, AAC, FLAC, OGG vagy Opus formátumban. A zene az egész kész videó alatt szól, a jelenetváltásokon át is. A rövid zenét a program ismétli, a hosszabbat a videó végén levágja, az elejét fokozatosan hangosítja, a végét lehalkítja.

A zene hangereje 0–100% között állítható; az alapérték 12%, hogy az angol narráció hallható maradjon. Csak zenés videóhoz válaszd a **Nincs narráció** beállítást. A zenefájl eltávolításához használd a **Zene eltávolítása** gombot. Zene és narráció nélküli videóhoz mindkettőt hagyd kikapcsolva.

A projekt mentése megőrzi a zenefájl helyét és hangerejét. A zenefájlt ezért tartsd meg ugyanott, ha később újra betöltöd a projektet. A korábban mentett projektek is betölthetők; a zene alapértelmezett hangereje ezeknél is 12%.

### Zene cseréje kész videón

Válaszd ki az új zenét és a hangerejét, majd kattints a **Zene cseréje kész videón…** gombra. Ezután válaszd ki az ezzel a változattal készített videó MP4-fájlját vagy az eredménymappa `projekt.json` fájlját. A program a megőrzött alapból készít új változatot: nem indít AI-generálást, és a videó képkockáit változatlanul másolja át.

Az eredeti videó megmarad. Az új fájl neve `video_zene_` előtaggal és időbélyeggel kezdődik; mellette külön `.projekt.json` leírás készül. Ha előbb a **Zene eltávolítása** gombbal kiüríted a zenefájlt, az új változatból eltűnik a hozzáadott háttérzene, miközben a megtartott beszéd és eredeti videóhang megmarad.

Ehhez az eredménymappa **`hang_alap`** almappája is kell. Ez tartalmazza a háttérzene nélküli videót, és külön betöltött narráció esetén annak önálló FLAC-másolatát is. Az eredménymappát egyben őrizd meg; a `hang_alap` törlése után a gyors zenecsere nem használható. A korábbi, ilyen alap nélkül készült videóknál a program ezt üzenetben jelzi. A hangalap a végső zenekeverés előtt mentődik, így egy keverési hiba esetén is megmarad. Ilyenkor a zenecsere gombnál válaszd a megmaradt `projekt.json` fájlt; a `hang_alap/alapvideo.mp4` közvetlenül is kiválasztható.

## Saját videók eredeti hanggal

A **Saját videók** forrásnál a megjelenő beállításokkal megtarthatod a felvételek eredeti hangját. A **Videók eredeti hangereje** 0–100% között állítható, alapértéke 100%; 0%-nál elnémul. Háttérzenével és új narrációval együtt is használható.

A **Videók teljes hosszának megtartása** alapból be van kapcsolva. Narráció nélkül minden hozzáadott klip egyszer, a saját hosszával kerül a videóba. Ha jelenetenként a megadott másodpercszámot szeretnéd használni, vedd ki a pipát. Új narráció esetén a jelenethossz a beszédhez igazodik; külön betöltött saját hangfájlnál annak idejét a klipek eredeti hosszának arányában osztja fel. A hosszú feliratszöveg önmagában nem ismételteti meg a teljes felvételt.

## Méret és fény

A **Fotó keretezése → Teljes fotó** az alapbeállítás: a bemeneti kép minden oldala megmarad, és a generálási méret a fotó arányához igazodik. Az **Alanyra közelítés** egy felismert arc körül keskenyebb, 3:4 arányú képkivágást választ a szélesebb fotóból. Csak az oldalakból vág; a teljes képmagasság, a kép teteje és alja megmarad. Arc hiányában vagy már keskenyebb képnél a teljes fotót használja. Ez a választás az **Élethű** motor teljes jelenetet generáló módjaihoz használható. A kivágás még a videó készítése előtt történik, így a fej és a test továbbra is együtt mozog.

A szűkebb bemenet ugyanakkora számítási keretből több képpontot fordíthat a szereplőre, de oldalsó környezetrészletek elvesznek. A generált jelenetben a modell továbbra is megváltoztathatja a részleteket. Az Élethű Normál mód 640×480-as képpontkeretet használ: egy 3:4 arányú fotó 480×640-es jelenetet ad. A Részletes mód képpontkerete 1280×704; ugyanaz a 3:4-es fotó ebben a módban 800×1088-as jelenetet ad. A korábbi motor Normál kerete 832×480, 24 képkocka/másodperccel.

Az **AI-jelenet részletessége** alatti súgó a kiválasztott motorhoz és az első fotóhoz tartozó várható generálási méretet, képkockasebességet és időigényt mutatja. Alanyra közelítésnél a pontos méret az arcfelismerés utáni kivágástól is függ. A **Videó exportmérete** a végső fájl mérete: lehet 720p, 1080p, Shorts vagy a fotó eredeti aránya. A **Fotó eredeti aránya** az előkészített első fotó arányát követi, tehát alanyra közelítéskor a kivágásét. A nagyobb exportméret a generált jelenetet méretezi át, önmagában nem jelent több natív AI-részletet. A 640×480-ból 1080p-re nagyított videó részleteit ezért az exportméret növelése nem állítja helyre. Az **MP4-mentés erőforrásai** a végső videó kódolására vonatkozik; a videógeneráló motor erőforrásait nem ez a választó állítja.

Az **Eredeti világítás** nem tesz utólagos színkorrekciót a képre. A többi fényprofil finom, az egész klip alatt állandó korrekció. A fotóalapú generálás utasítása az eredeti fények és színek megtartását kéri. A már kiégett képrészletekből nem lehet megbízhatóan eredeti részletet visszaállítani.

## Motor telepítése másik gépre

Az **AI-motor és erőforrások** résznél az **Élethű motor letöltése / folytatása** gomb telepíti a szükséges alapmotort, portrémotort, nagy videómodelleket, arcjavítót és mozgássimítót. Meglévő teljes alapmotorhoz az új kiegészítés körülbelül 20,4 GB letöltés. Új telepítéshez legalább 60 GB szabad lemezterületet hagyj, plusz helyet a videóknak. A nagy modellek külön az `AI_Motor` mappában vannak; a program ZIP-je nem tartalmazza őket. A modellletöltés folytatható, a fájlokat SHA-256 ellenőrzés védi.

Ez a futtatókörnyezet Windowsra és AMD RX 9070 XT-re készült, 16 GB videomemóriával és 96 GB RAM-mal ellenőrizve. Az Élethű mód betöltése körülbelül 50 GB szabad rendszermemóriát is kérhet. Más videókártyához megfelelő ComfyUI/PyTorch futtatókörnyezet szükséges. A mozgássimítás Vulkanon működik; szükség esetén CPU-val folytatódik. Az MP4-mentés GPU-kódolást is használhat.

Az Élethű mód saját, helyi AI-motort indít. A jelenet elkészülte után a saját, igazoltan szabad motort leállítja, és a mozgássimítás a kilépése után indul a videókártyán. Ha a motor másik feladatot kapott vagy az állapota bizonytalan, a kész képkockák simítása közvetlenül CPU-val folytatódik; ez tovább tarthat. A másik alkalmazásból futó ComfyUI feladatait nem módosítja. Az **Alap • korábbi motor** a Wan2.2-TI2V-5B alapmodellt használja. Kép nélküli szöveges jelenet szintén ezt a korábbi motort használja. A korábban letöltött közösségi Turbo 5B fájlt a program nem választja és nem tölti le automatikusan; az eredeti projekt és a GGUF-változat licencjelölése eltér, ezért a kereskedelmi használhatóság nem tekinthető tisztázottnak. A már meglevő modellfájlokat a frissítés nem törli. Az alapértelmezett Élethű 14B motor kiválasztása ettől nem változik.

## Eredmények és adatkezelés

- `video.mp4`: feltölthető H.264/AAC videó, 30 képkocka/másodperccel.
- `AI_jelenetek`: köztes AI-videók, előkészített forrásfotó, simított jelenet, képkockák, beállítások és naplók.
- `felirat.srt`: váltakozó feliratok, a narráció hosszából becsült időzítéssel.
- `boritokep.png`, `youtube_leiras.txt`, `projekt.json`: borítókép, szerkeszthető leírás és visszatölthető projekt.
- `hang_alap`: a későbbi gyors zenecseréhez megtartott videó és szükség esetén narráció.

A helyi AI-módban a képgenerálás és az arcfeldolgozás a PC-n készül; az alapértelmezett angol hang szintén helyi. A helyi motorhoz internet az első modellletöltéshez kell. A külön választott Grok-mód a teljes fotót és az angol jelenetleírást az xAI szolgáltatásához küldi. Az opcionális internetes hang külső szolgáltatásnak küldi a narráció szövegét. A helyi, témából narrációt író segéd külön Ollamát igényel; közvetlen jelenetleíráshoz nem kell.

A **Saját videók** mód meglévő mozgó felvételeket vág össze. A **Képes diavideó** a külön megnevezett képközelítős mód. A program nem tölt fel automatikusan a YouTube-ra.

A forráskód a csomagban, a felhasznált összetevők és licencek a `THIRD_PARTY.md`, `LICENSES` és `face_restore_vendor` mappákban találhatók.
