# Functionele Analyse — Basisregister Monitor
**Versie:** 1.0  
**Datum:** 05 juni 2026  
**Opgesteld door:** Alan De Geest - Stagiair Dienst Visit Leuven  
**Bestemd voor:** Stad Leuven

---

## 1. Samenvatting

De **Basisregister Monitor** is een Python-webapplicatie die automatisch wijzigingen
opspoort in het *Basisregister Vlaams Logiesaanbod*, een publiek register dat Toerisme
Vlaanderen bijhoudt van alle aangemelde en erkende logiesuitbatingen in Vlaanderen. De applicatie
vergelijkt bij elk gebruik de meest recente versie van dit register veld-per-veld met
een eerder opgeslagen versie, en toont wat er nieuw bij is gekomen, wat verdwenen is en
welke gegevens gewijzigd zijn. Daarnaast visualiseert ze alle logies, zoals beschreven in het register,
op een interactieve kaart.

Het instrument is bedoeld voor zij die het logiesaanbod in Leuven willen opvolgen 
zonder elke keer handmatig het volledige register te doorzoeken.

---

## 2. Context en aanleiding

Het Basisregister Vlaams Logiesaanbod wordt door Toerisme Vlaanderen periodiek
bijgewerkt. De download-URL van het register bevat telkens een nieuwe unieke code
(UUID) bij elke nieuwe versie, wat automatisch downloaden bemoeilijkt. De applicatie
omzeilt dit door de datasets-pagina van Toerisme Vlaanderen telkens opnieuw te raadplegen
om de actuele download-URL dynamisch op te zoeken.

De applicatie is ontwikkeld als onderdeel van een ruimer compliance-instrument (de
4×30-dagen-regel voor kortetermijnverhuur) maar is functioneel volledig op zichzelf
staand en bruikbaar voor elke dienst die dit register wil bewaken.

---

## 3. Huidige technische infrastructuur

De applicatie maakt in haar huidige vorm gebruik van drie externe diensten:

| Component | Dienst | Rol |
|---|---|---|
| Webinterface | Streamlit Community Cloud | Hosting van de applicatie (gratis tier) |
| Gegevensopslag | Privé GitHub-repository | Opslag van registers, archief en wijzigingslog |
| Authenticatie | GitHub Personal Access Token (PAT) | Toegang tot de opslagrepository vanuit de app |

De applicatie draait als een gedeelde sessie: alle gebruikers die de URL bezoeken,
werken met dezelfde centrale toestand. Er is geen onderscheid tussen gebruikers en er
is geen aparte aanmelding vereist.

### 3.1 Bestandsstructuur in de GitHub-repository

```
data/
  basisregister_huidig.csv          Meest recente registerversie (referentiepunt)
  archief/
    basisregister_DD-MM-YYYY.csv    Één bestand per kalenderdag (max. 60 dagen bewaard)
```

Het wijzigingsoverzicht wordt on-the-fly gegenereerd bij elke vergelijking en kan als
Excel-bestand worden gedownload.

---

## 4. Gegevensstromen

### 4.1 Bij het eerste bezoek van de dag (nieuwe download)

```
Gebruiker bezoekt applicatie
        │
        ▼
Applicatie controleert: bestaat er al een archief voor vandaag?
        │ Nee
        ▼
Scrape datasets-pagina Toerisme Vlaanderen
  → zoek koptekst "Basisregister Vlaams Logiesaanbod (CSV)"
  → extraheer download-URL (bevat release-specifieke UUID)
        │
        ▼
Stream-download van het volledige Vlaamse CSV-bestand (~enkele MB, heel Vlaanderen)
        │
        ▼
Inladen als DataFrame + opschonen (lege waarden → lege tekst)
        │
        ▼
POSTCODE-FILTER — behoud enkel rijen waarvan postal_code voorkomt in de
   geconfigureerde lijst (standaard: 3000, 3001, 3010, 3012, 3018)
   → rijen zonder postcode worden ook verwijderd
   → resultaat: enkel Leuvense logies
        │
        ▼
Laad vorige momentopname (basisregister_huidig.csv) uit de repository
        │
        ▼
POSTCODE-FILTER — zelfde filter opnieuw toegepast op de vorige versie
   (defensieve maatregel: de opgeslagen versie is al gefilterd, zie hieronder)
        │
        ▼
Vergelijk veld-per-veld: gefilterde nieuwe versie vs. gefilterde vorige versie
  → nieuwe rijen        (registratienummer niet gekend in vorige versie)
  → verdwenen rijen     (registratienummer niet meer aanwezig)
  → gewijzigde velden   (per kolom, elke wijziging afzonderlijk)
        │
        ▼
Sla de GEFILTERDE versie op in de GitHub-repository:
  → data/archief/basisregister_DD-MM-YYYY.csv  (nieuwe archiefversie)
  → data/basisregister_huidig.csv              (overschrijft vorige momentopname)

Het volledige Vlaamse register wordt nooit opgeslagen —
        archiefbestanden bevatten uitsluitend de gefilterde subset.
        │
        ▼
Verwijder archieven ouder dan 60 dagen
        │
        ▼
Toon resultaten in de webinterface
```

### 4.2 Bij een volgend bezoek dezelfde dag (archief al aanwezig)

```
Gebruiker bezoekt applicatie
        │
        ▼
Applicatie controleert: bestaat er al een archief voor vandaag?
        │ Ja
        ▼
Laad archief van vandaag uit de repository
        │
        ▼
POSTCODE-FILTER — opnieuw toegepast bij het inladen
   (redundant: archief is al gefilterd opgeslagen, maar de code past de
    filter defensief toe op elke CSV die wordt ingeladen)
        │
        ▼
Toon interface (vergelijkingen op aanvraag beschikbaar)
```

### 4.3 Vergelijking op aanvraag met een archiefversie

```
Gebruiker selecteert een archiefversie via de dropdown
        │
        ▼
Laad het gekozen archiefbestand uit de repository
        │
        ▼
POSTCODE-FILTER — opnieuw toegepast (zie noot bij 4.2)
        │
        ▼
Vergelijk met de huidige versie → toon wijzigingstabel + Excel-download
```

### 4.4 Vergelijking op aanvraag met een zelf geüpload bestand

```
Gebruiker uploadt een eerder gedownloade CSV
        │
        ▼
Inladen als DataFrame + opschonen
        │
        ▼
POSTCODE-FILTER — noodzakelijk hier: een zelf gedownloaden bestand
   kan het volledige Vlaamse register bevatten (onafgezien van wat er
   ooit opgeslagen werd)
        │
        ▼
Vergelijk met de huidige versie → toon wijzigingstabel + Excel-download
```

---

## 5. Functionele modules

### 5.1 Gegevensbron (scraping + download)

De functie `zoek_csv_url` raadpleegt de webpagina
`https://linked.toerismevlaanderen.be/datasets`, doorzoekt de HTML-kopteksten op de
tekst "Basisregister Vlaams Logiesaanbod" gevolgd door "CSV", en extraheert de
bijbehorende hyperlink. Deze aanpak is robuust voor URL-wijzigingen bij nieuwe
releases, maar kwetsbaar voor structuurwijzigingen op de pagina zelf (zie §8).

De CSV wordt vervolgens via een streaming HTTP-verbinding binnengehaald. Het bestand
bevat voor heel Vlaanderen doorgaans enkele duizenden rijen (35.000+ op 05/06/2026).

### 5.2 Veld-voor-veld vergelijking (diff)

De kern van de applicatie is de functie `bereken_diff`. Deze vergelijkt twee versies
van het register op basis van een instelbare sleutelkolom (standaard:
`business_product_id`) en detecteert:

- **Nieuwe rijen:** registratienummer aanwezig in de nieuwe versie, afwezig in de oude.
- **Verdwenen rijen:** registratienummer aanwezig in de oude versie, afwezig in de nieuwe.
- **Gewijzigde velden:** voor elke rij die in beide versies voorkomt, wordt elke kolom
  afzonderlijk vergeleken. Elke individuele veldwijziging genereert één regel in het
  resultaat.

Het resultaat is een tabel met kolommen: `registratienummer`, `naam`, `wijziging_type`,
`kolom`, `oude_waarde`, `nieuwe_waarde`.

Configureerbare parameters van de diff:

| Parameter | Standaard | Omschrijving |
|---|---|---|
| `sleutelkolom` | `business_product_id` | Unieke sleutel per logies |
| `naamkolom` | `name` | Leesbare naam voor rapportage |
| `uitgesloten_kolommen` | _(leeg)_ | Kolommen die niet vergeleken worden |

### 5.3 Archiefbeheer

Elke kalenderdag waarop de applicatie voor het eerst bezocht wordt, wordt automatisch
één archiefbestand aangemaakt. Bij meerdere bezoeken op dezelfde dag wordt het archief
niet overschreven. Archieven worden automatisch verwijderd na 60 dagen (instelbaar
via `config.toml`).

Bestandsnaamformaat: `basisregister_DD-MM-YYYY.csv`

### 5.4 Kaartvisualisatie

Als de Python-bibliotheek `folium` beschikbaar is, worden alle logies uit het huidige
register weergegeven op een interactieve kaart. Elke logies wordt afgebeeld als een
gekleurde cirkel, waarbij:

- **Kleur** het type logies aanduidt (hotel, B&B, vakantiewoning, camping, enz.)
- **Grootte** het aantal eenheden (kamers/appartementen) weergeeft
- **Klik** een popup opent met alle registergegevens van dat logies

De legenda rechtsboven op de kaart is interactief: klikken op een type of
groothecategorie toont of verbergt de bijbehorende punten.

Er is een GeoJSON-bestand `leuven_boundary.geojson` in de root van de
repository geplaatst om de stadsgrens van Leuven op de kaart te tekenen.
Het ontbreken van dit bestand genereert geen fout.

### 5.5 Excel-export

Bij elke vergelijking (zowel met een archiefversie als met een zelf geüpload bestand)
kan het resultaat worden gedownload als Excel-werkmap met twee tabbladen:

- **Wijzigingen:** het veld-voor-veld vergelijkingsoverzicht
- **Volledige gegevens:** de volledige registerrij voor elk betrokken logies

Huidig register als CSV: onderaan de pagina staat altijd een knop 
"Download huidige registerversie als CSV". Deze downloadt de huidige gefilterde 
momentopname (enkel de geconfigureerde postcodes) als plat CSV-bestand 
met de bestandsnaam basisregister_YYYY-MM-DD.csv. Dit bestand is bedoeld 
om lokaal bij te houden als persoonlijke vergelijkingsbasis: 
de gebruiker kan het op een later tijdstip opnieuw uploaden via Modus B 
om alle tussentijdse wijzigingen in één overzicht te zien. 

### 5.6 GitHub-opslag

De klasse `GitHubOpslag` verzorgt alle communicatie met de GitHub-repository via de
officiële GitHub Contents API (REST). Ondersteunde operaties: lezen, schrijven
(aanmaken of overschrijven), verwijderen en mapinhoud opvragen. Bestanden groter dan
1 MB worden via de `download_url` opgehaald in plaats van inline base64-decodering.

---

## 6. Configuratie

De applicatie wordt geconfigureerd via het bestand `config.toml` in de root van de
deployment. Wijzigingen vereisen een herdeployment op Streamlit Cloud. Een tijdelijke
override van de datasets-pagina-URL is beschikbaar via het zijpaneel in de interface.

Relevante instellingen:

```toml
[bron]
datasets_pagina_url = "https://linked.toerismevlaanderen.be/datasets"
csv_label           = "Basisregister Vlaams Logiesaanbod"
csv_scheidingsteken = ";"

[kolommen]
sleutelkolom         = "business_product_id"
naamkolom            = "name"
uitgesloten_kolommen = []

[archief]
bewaarperiode_dagen = 60

[netwerk]
ssl_verificatie = true
```

> **Noot voor IT:** De postcode-filter (voor Leuvense postcodes) die aanwezig is in
> `config.toml` is een restant uit de bredere 4×30-pipeline en kan verwijderd worden
> bij internalisering. In de huidige applicatie is de filter uitgeschakeld (de code
> filtert alleen als postcodes geconfigureerd zijn én de kolom aanwezig is in het
> register). De monitoringtool werkt correct op het volledige Vlaamse register.

---

## 7. Gebruikersinterface

De webinterface bestaat uit de volgende secties, van boven naar beneden:

1. **Statusbalk** : toont of er vandaag al een nieuwe versie werd gedownload, het
   aantal beschikbare archieven, en het aantal wijzigingen t.o.v. de vorige versie.

2. **Registerwijzigingen controleren** : laat de gebruiker kiezen tussen twee modi:
   - *Archiefversie:* vergelijk met een eerder opgeslagen versie via een dropdown
   - *Zelf geüpload bestand:* upload een eerder gedownloade CSV als vergelijkingsbasis

3. **Logiesoverzicht (kaart)** : interactieve kaart met alle logies.

4. **Download huidige registerversie** : knop om de huidige gefilterde versie te
   downloaden als CSV (voor gebruik als toekomstige vergelijkingsbasis).

5. **Uitvoeringslog** : inklap­baar logvenster met een gedetailleerd overzicht van
   wat er in de huidige sessie is gebeurd.

---

## 8. Bekende beperkingen en aandachtspunten

### 8.1 Paginastructuur Toerisme Vlaanderen

De download-URL wordt gevonden via de HTML-koptekststructuur van de datasets-pagina.
Als Toerisme Vlaanderen de pagina herstructureert of de koptekst hernoemt, zal de
applicatie de URL niet meer vinden en een foutmelding geven. De koptekst waarop
gezocht wordt (`csv_label`) is instelbaar in `config.toml`.

### 8.2 Geen automatische planning

De applicatie downloadt enkel een nieuwe versie als een gebruiker de pagina bezoekt
en er die dag nog geen archief bestaat. Er is geen achtergrondtaak of geplande
uitvoering. Als niemand de applicatie bezoekt op de dag dat het register wordt
bijgewerkt, wordt die wijziging pas bij het volgende bezoek opgepikt.

### 8.3 Gedeelde sessie zonder gebruikersbeheer

Alle gebruikers die de URL kennen, zien dezelfde toestand en kunnen dezelfde acties
uitvoeren. Er is geen aanmelding, geen rolbeheer en geen auditlog per gebruiker.

### 8.4 Streamlit-sessiestatus is vluchtig

De resultaten van een vergelijking worden opgeslagen in de browsersessie en zijn
verloren na sluiten van de tab of na een sessiereset. Enkel de archiefbestanden in
de GitHub-repository zijn permanent.

### 8.5 Laadtijd bij eerste dagbezoek

Het downloaden, verwerken en uploaden van het register kan 5 à 10 minuten in beslag
nemen bij het eerste bezoek van de dag. Tijdens dit proces mag de gebruiker het
tabblad niet sluiten. Dit is een inherente beperking van de gratis Streamlit-tier
(geen achtergrondtaken), maar lijkt enkel zonder postcodefilter te gelden.
Sinds de applicatie momenteel alleen met een postcodefilter werkt, is deze laadtijd niet
waargenomen. Bij internalisering is het aan te raden een achtergrondtaak of geplande
uitvoering toe te voegen om deze vertraging bij gebruikers te vermijden.

### 8.6 Afhankelijkheid van twee externe gratis diensten

De applicatie is afhankelijk van de gratis tiers van zowel Streamlit Community Cloud
als GitHub. Wijzigingen in de gebruiksvoorwaarden of beschikbaarheid van deze diensten
kunnen de werking onderbreken zonder voorafgaande kennisgeving.

---

## 9. Beveiligingsaandachtspunten

### 9.1 Afhankelijkheid van een persoonlijk account

De applicatie en alle opgeslagen gegevens (archieven, huidige momentopname) leven in een
**privérepository onder het persoonlijk GitHub-account van de stagiair**, die na
**12 juni 2026 niet meer in dienst is**. Na dat moment is er geen gegarandeerde toegang
meer tot de repository, de Streamlit-deployment of de bijbehorende secrets (token).

**Aanbevolen actie:**
1. Transfereer de GitHub-repository naar een organisatieaccount van Stad Leuven of een
   beheerde account van de dienst (via *Settings → Transfer repository* op GitHub).
2. Maak een nieuw GitHub Personal Access Token aan onder dat nieuwe account en pas de
   Streamlit-secret `GITHUB_TOKEN` aan.
3. Transfereer of hermaak de Streamlit-deployment onder een gedeeld e-mailadres of
   dienstaccount.

Zonder deze stappen bestaat het risico dat de applicatie na het vertrek van de stagiair
niet meer bereikbaar of aanpasbaar is.

---

### 9.2 Credentialbeheer

Het GitHub Personal Access Token dat de applicatie toegang geeft tot de repository, is
correct opgeslagen als Streamlit-secret en staat niet in de broncode of in de
repository. Zorg er bij de overdracht voor dat dit token opnieuw wordt aangemaakt onder
het nieuwe account (zie §9.1) en dat het oude token wordt ingetrokken.

### 9.3 Geen toegangsbeveiliging op de webinterface

De Streamlit-applicatie is toegankelijk voor iedereen met de URL. Er is geen
aanmeldingsvereiste. Voor intern gebruik is het aan te raden de toegang te beperken
via Streamlit Cloud's ingebouwde functie "Restrict access to specific email domains"
(beschikbaar op de Starter-abonnementstier).

### 9.4 SSL-verificatie

In de huidige configuratie staat SSL-verificatie ingeschakeld (`ssl_verificatie = true`
in `config.toml`). Dit is de correcte instelling voor een omgeving zonder
bedrijfsproxy. Schakel dit niet uit zonder technische noodzaak.

---

## 10. Aanbevelingen voor internalisering door IT

Bij overname door de ICT-dienst zijn de volgende aanpassingen aan te raden, in
volgorde van prioriteit:

| Prioriteit | Maatregel | Motivering |
|---|---|---|
| 🔴 Dringend | Transfereer repository en Streamlit-deployment vóór 12 juni 2026 (zie §9.1) | Toegang geborgd na vertrek stagiair |
| 🔴 Dringend | Verplaats opslag naar een intern systeem | Onafhankelijkheid van GitHub gratis tier |
| 🟠 Hoog | Voeg authenticatie toe aan de webinterface | Enkel medewerkers mogen toegang hebben |
| 🟠 Hoog | Vervang Streamlit Cloud door interne hosting | Controle over beschikbaarheid en updates |
| 🟡 Middel | Voeg geplande uitvoering toe (bv. dagelijkse cronjob) | Geen manuele trigger meer nodig |
| 🟡 Middel | Voeg een auditlog per gebruiker toe | Traceerbaarheid van raadplegingen |
| 🟢 Laag | Vervang GitHub-opslag door een database of fileserver | Robuustere en vertrouwdere opslaglaag |

### 10.1 Minimale opslagvereisten

De applicatie heeft enkel twee persistente bestanden nodig:
- Een huidig CSV-bestand (~1–5 MB)
- Een map met archiefbestanden (max. 60 × ~5 MB = ~300 MB)

Elke gedeelde bestandsopslag die leesbaar en schrijfbaar is vanuit een Python-omgeving
is geschikt: een netwerkschijf, een S3-compatibele objectopslag, een interne
SharePoint-bibliotheek of een eenvoudige database.

### 10.2 Minimale hostingvereisten

De applicatie is een standaard Python-webapplicatie op basis van het Streamlit-framework.
Vereisten:
- Python 3.11 of hoger
- Afhankelijkheden: zie `requirements.txt` (pandas, requests, beautifulsoup4, streamlit,
  openpyxl, folium)
- Geen databank vereist in de basisversie
- Geheugen: ~512 MB volstaat voor de huidige schaal

De applicatie kan draaien op elke Linux-server of containerplatform (Docker) met
internettoegang naar `linked.toerismevlaanderen.be`.

### 10.3 Aanpassing opslaglaag

De klasse `GitHubOpslag` in `app.py` isoleert alle opslagoperaties in vier methoden:
`lees`, `schrijf`, `verwijder` en `lijst`. Bij vervanging van GitHub door een intern
opslagsysteem volstaat het deze klasse te herschrijven met dezelfde interface; de
rest van de applicatie hoeft niet aangepast te worden.

---

## 11. Technische afhankelijkheden

### Python-pakketten (`requirements.txt`)

| Pakket | Versie | Doel |
|---|---|---|
| `streamlit` | ≥ 1.35 | Webinterface |
| `pandas` | ≥ 2.0 | Gegevensverwerking en vergelijking |
| `openpyxl` | ≥ 3.1 | Excel-export |
| `requests` | ≥ 2.31 | HTTP-communicatie (download + GitHub API) |
| `beautifulsoup4` | ≥ 4.12 | HTML-parsing voor URL-detectie |
| `tomli` | alleen Python < 3.11 | TOML-configuratie lezen |
| `folium` | ≥ 0.14.0 | Kaartvisualisatie (optioneel) |

### Externe diensten

| Dienst | URL | Doel |
|---|---|---|
| Toerisme Vlaanderen datasets | `https://linked.toerismevlaanderen.be/datasets` | Bron van het register |
| GitHub Contents API | `https://api.github.com` | Persistente opslag (huidig) |

---

## 12. Registerstructuur (gegevensmodel)

Het basisregister bevat per logies (unieke business_product_id) de volgende velden (kolommen uit het CSV):

| Kolom | Omschrijving |
|---|---|
| `business_product_id` | Uniek identificatienummer (sleutelkolom) |
| `product_type` | Basistype (doorgaans `BASE`) |
| `name` / `name_or_number` | Naam van het logies |
| `discriminator` | Logiestype (HOTEL, BED_AND_BREAKFAST, HOLIDAY_COTTAGE, enz.) |
| `street`, `house_number`, `box_number` | Adres |
| `postal_code`, `city_name` | Gemeente |
| `lat`, `long` | Geografische coördinaten (decimale graden) |
| `x`, `y` | Belgisch coördinatenstelsel (Lambert 72) |
| `promotional_region` | Toeristische regio |
| `changed_time` | Tijdstip van laatste wijziging in het register |
| `last_status_change_date` | Tijdstip van laatste statuswijziging |
| `phone1/2/3`, `email`, `website` | Contactgegevens |
| `status` | Erkenningsstatus (bv. `ACKNOWLEDGED`, `NOTIFIED`) |
| `comfort_class` | Comfortclassificatie (sterren) |
| `number_of_units` | Aantal verhuurbare eenheden |
| `maximum_capacity` | Maximale bezetting (personen) |
| _(capaciteitskolommen)_ | Specifieke aantallen per type slaapplaats |
| `iconic_cycling_route_label` | Kwaliteitslabel fietsvriendelijk logies |

---