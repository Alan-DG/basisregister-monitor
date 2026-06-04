# Basisregister Monitor

Streamlit-applicatie voor het automatisch bewaken van wijzigingen in het
[Basisregister Vlaams Logiesaanbod](https://linked.toerismevlaanderen.be/datasets)
van Toerisme Vlaanderen.

## Wat doet de applicatie?

Bij elk bezoek controleert de applicatie of er al een versie van het register
is opgeslagen voor vandaag. Zo niet, wordt de meest recente versie automatisch
gedownload, veld-voor-veld vergeleken met de vorige versie, en opgeslagen in
de GitHub-repository.

Gebruikers kunnen elke beschikbare archiefversie vergelijken met de huidige versie.
Hierdoor is de applicatie ook nuttig voor wie niet dagelijks inlogt: kies gewoon
de datum van uw laatste controle als vergelijkingsbasis.

## Archiefbeleid

- Eén archiefbestand per kalenderdag (meerdere bezoeken op dezelfde dag overschrijven elkaar niet).
- Archieven worden automatisch verwijderd na 60 dagen (instelbaar in `config.toml`).
- De changelog (`data/changelog.xlsx`) bevat de volledige historiek en wordt nooit automatisch verwijderd.

## Technische opzet

| Onderdeel | Keuze |
|---|---|
| Frontend | Streamlit Community Cloud |
| Opslag | GitHub-repository (privé) via REST API |
| Authenticatie | GitHub Personal Access Token opgeslagen als Streamlit-secret |
| Runtime | Python 3.11+ |

## Secrets instellen (eenmalig)

Voeg de volgende secrets toe via **App settings → Secrets** in Streamlit Cloud:

```toml
GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"   # Classic PAT met 'repo'-scope
GITHUB_REPO  = "gebruikersnaam/reponaam"    # Naam van de privé-repository
```

Het token heeft alleen de `repo`-scope nodig. Geen vervaldatum instellen is
aan te raden zodat de applicatie niet plotseling stopt met werken.

## Bestandsstructuur in de repository

```
app.py                              Hoofdapplicatie
config.toml                         Standaardconfiguratie
requirements.txt                    Python-afhankelijkheden
data/
  basisregister_huidig.csv          Meest recente versie van het register
  changelog.xlsx                    Volledige historiek van alle wijzigingen
  archief/
    basisregister_YYYY-MM-DD.csv    Dagelijkse archieven (max. 60 dagen)
```

De map `data/` wordt automatisch aangemaakt door de applicatie bij het eerste gebruik.

## Configuratie aanpassen (config.toml)

Wijzigingen in `config.toml` zijn van kracht na de volgende herdeployment.
Tijdelijke per-sessie overrides zijn beschikbaar via het zijpaneel in de app.

| Instelling | Standaard | Omschrijving |
|---|---|---|
| `bron.datasets_pagina_url` | URL Toerisme Vlaanderen | Pagina waarop de download-link gevonden wordt |
| `bron.csv_label` | `Basisregister Vlaams Logiesaanbod` | Koptekst waarnaar gezocht wordt op de pagina |
| `bron.csv_scheidingsteken` | `;` | Veldscheidingsteken in het CSV |
| `kolommen.sleutelkolom` | `business_product_id` | Unieke sleutel voor de vergelijking |
| `kolommen.naamkolom` | `name` | Kolom met leesbare naam (voor rapporten) |
| `kolommen.uitgesloten_kolommen` | `[]` | Kolommen die niet worden vergeleken |
| `archief.bewaarperiode_dagen` | `60` | Aantal dagen dat archieven bewaard blijven |
| `netwerk.ssl_verificatie` | `true` | SSL-verificatie in-/uitschakelen |

## Veelvoorkomende problemen

**Fout bij downloaden / SSL-fout**
Alleen relevant bij lokaal draaien via een bedrijfsnetwerk. Schakel tijdelijk
SSL-verificatie uit via het zijpaneel in de app.

**Dataset niet gevonden op de pagina**
Toerisme Vlaanderen heeft mogelijk de paginastructuur gewijzigd. Controleer
`bron.csv_label` in `config.toml` of pas het tijdelijk aan via het zijpaneel.

**Token ongeldig of verlopen**
Genereer een nieuw PAT op GitHub en pas de `GITHUB_TOKEN` secret aan in Streamlit Cloud.

**Meer details**
De uitvoeringslog onderaan de pagina geeft bij elke sessie een gedetailleerd overzicht
van wat er is gebeurd.

## Lokaal draaien (voor ontwikkelaars)

```bash
pip install -r requirements.txt

# Maak .streamlit/secrets.toml aan:
# GITHUB_TOKEN = "ghp_..."
# GITHUB_REPO  = "gebruiker/repo"

streamlit run app.py
```
