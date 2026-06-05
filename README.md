# Basisregister Monitor

Streamlit-applicatie voor het bewaken van wijzigingen in het
[Basisregister Vlaams Logiesaanbod](https://linked.toerismevlaanderen.be/datasets)
van Toerisme Vlaanderen, gefilterd op de Leuvense postcodes.

## Wat doet de applicatie?

Bij elk bezoek controleert de applicatie of er al een versie van het register
is opgeslagen voor vandaag. Zo niet, wordt de meest recente versie automatisch
gedownload van de datasets-pagina van Toerisme Vlaanderen (de download-URL bevat
een release-specifieke UUID die bij elke nieuwe versie wijzigt; de applicatie
zoekt deze dynamisch op via de paginatekst), gefilterd op de ingestelde postcodes,
veld-voor-veld vergeleken met de vorige versie, en opgeslagen in de GitHub-repository.

Gebruikers kunnen elke beschikbare archiefversie vergelijken met de huidige versie.
Hierdoor is de applicatie ook nuttig voor wie niet dagelijks inlogt: kies gewoon
de datum van uw laatste controle als vergelijkingsbasis om alle tussentijdse
wijzigingen in één overzicht te zien.

## Functies

**Registerwijzigingen controleren**
Vergelijk de huidige registerversie veld-voor-veld met een eerder opgeslagen
archiefversie of een zelf geüpload CSV-bestand. De vergelijking detecteert nieuwe
logies, verdwenen logies en gewijzigde veldwaarden. Resultaten zijn exporteerbaar
als Excel-bestand met twee tabbladen: een wijzigingsoverzicht en de volledige
registerrijen van alle betrokken logies.

**Kaartoverzicht**
Interactieve kaart van alle logies in de huidige registerversie, met de stadsgrens
van Leuven als referentielijn. Elke markering is gekleurd op logiestype en geschaald
naar het aantal eenheden. Klik op een markering voor een scrollbare popup met alle
registergegevens van dat logies. Gebruik de legenda rechtsonder om te filteren op
type of grootte. Logies op hetzelfde adres worden licht gespreid weergegeven zodat
ze afzonderlijk aanklikbaar zijn.

**Archiefbeheer**
Één archiefbestand per kalenderdag; meerdere bezoeken op dezelfde dag overschrijven
elkaar niet. Archieven ouder dan de ingestelde bewaarperiode worden automatisch
opgeruimd.

## Technische opzet

| Onderdeel | Keuze |
|---|---|
| Frontend | Streamlit Community Cloud |
| Opslag | GitHub-repository (privé) via REST API |
| Authenticatie | GitHub Personal Access Token opgeslagen als Streamlit-secret |
| Runtime | Python 3.11+ |
| Kaart | Folium (Leaflet) via `st.components.v1.html` |

## Bestandsstructuur in de repository

```
app.py                              Hoofdapplicatie
config.toml                         Standaardconfiguratie
requirements.txt                    Python-afhankelijkheden
leuven_boundary.geojson             Stadsgrens Leuven (wordt op kaart getoond)
data/
  basisregister_huidig.csv          Meest recente opgeslagen versie
  archief/
    basisregister_DD-MM-YYYY.csv    Dagelijkse archieven
```

De map `data/` wordt automatisch aangemaakt door de applicatie bij het eerste gebruik.

## Secrets instellen (eenmalig)

Voeg de volgende secrets toe via **App settings → Secrets** in Streamlit Cloud:

```toml
GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"   # Classic PAT met 'repo'-scope
GITHUB_REPO  = "gebruikersnaam/reponaam"    # Naam van de privé-repository
```

Het token heeft alleen de `repo`-scope nodig. Stel geen vervaldatum in zodat
de applicatie niet plotseling stopt met werken.

## Configuratie aanpassen (config.toml)

Wijzigingen in `config.toml` zijn van kracht na de volgende herdeployment.
De datasets-pagina URL is ook tijdelijk aanpasbaar via het zijpaneel in de app.

| Instelling | Standaard | Omschrijving |
|---|---|---|
| `bron.datasets_pagina_url` | URL Toerisme Vlaanderen | Pagina waarop de download-link gevonden wordt |
| `bron.csv_label` | `Basisregister Vlaams Logiesaanbod` | Koptekst waarnaar gezocht wordt op de pagina |
| `bron.csv_scheidingsteken` | `;` | Veldscheidingsteken in het CSV |
| `kolommen.sleutelkolom` | `business_product_id` | Unieke sleutel voor de veld-voor-veld vergelijking |
| `kolommen.naamkolom` | `name` | Kolom met leesbare naam (voor rapporten en kaart) |
| `kolommen.postcode_kolom` | `postal_code` | Kolom waarop de postcode-filter wordt toegepast |
| `kolommen.postcodes` | `[3000, 3001, 3010, 3012, 3018]` | Postcodes die worden meegenomen |
| `kolommen.uitgesloten_kolommen` | `[]` | Kolommen die worden overgeslagen bij de vergelijking |
| `archief.bewaarperiode_dagen` | `60` | Aantal dagen dat archieven bewaard blijven |
| `netwerk.ssl_verificatie` | `true` | SSL-verificatie in-/uitschakelen |

## Veelvoorkomende problemen

**Wijzigingen zijn niet zichtbaar na aanpassing van app.py**
Streamlit Cloud deployt bij elke push naar de gekoppelde branch. Controleer in het
Streamlit Cloud-dashboard of de meest recente commit daadwerkelijk is uitgerold
(het commit-SHA staat onderaan in de deploy-log). Gebruik **Manage app → Reboot app**
om een herstart te forceren als de deploy vast lijkt te zitten.

**Stadsgrens verschijnt niet op de kaart**
Controleer of `leuven_boundary.geojson` aanwezig is in de root van de repository
(naast `app.py`). Als het bestand ontbreekt gooit de applicatie een zichtbare fout;
er is bewust geen stille fallback.

**Dataset niet gevonden op de pagina**
Toerisme Vlaanderen heeft mogelijk de paginastructuur gewijzigd. Controleer
`bron.csv_label` in `config.toml` of pas het tijdelijk aan via het zijpaneel.

**Fout bij downloaden / SSL-fout**
Alleen relevant bij lokaal draaien via een bedrijfsnetwerk. Schakel tijdelijk
SSL-verificatie uit via het zijpaneel in de app.

**Token ongeldig of verlopen**
Genereer een nieuw PAT op GitHub en pas de `GITHUB_TOKEN` secret aan in Streamlit Cloud.

**Meer details**
De uitvoeringslog onderaan de pagina geeft bij elke sessie een gedetailleerd overzicht
van wat er is gebeurd.

## Lokaal draaien (voor ontwikkelaars)

```bash
pip install -r requirements.txt

# Maak .streamlit/secrets.toml aan met:
# GITHUB_TOKEN = "ghp_..."
# GITHUB_REPO  = "gebruiker/repo"

streamlit run app.py
```

Zorg dat de terminal gestart wordt vanuit de map die `app.py` bevat, zodat
`leuven_boundary.geojson` en `config.toml` correct worden gevonden.
