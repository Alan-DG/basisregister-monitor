"""
app.py — Basisregister Monitor
================================
Streamlit-applicatie voor het bewaken van wijzigingen in het
Basisregister Vlaams Logiesaanbod (Toerisme Vlaanderen).

Versie:  1.0
Opslag:  Privé GitHub-repository via REST API
Hosting: Streamlit Community Cloud
"""

from __future__ import annotations

import base64
import io
import re
import warnings
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from urllib.parse import urljoin

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Constanten — paden in de GitHub-repository
# ─────────────────────────────────────────────────────────────────────────────

PAD_HUIDIG    = "data/basisregister_huidig.csv"
PAD_CHANGELOG = "data/changelog.xlsx"
PAD_ARCHIEF   = "data/archief"

CHANGELOG_KOLOMMEN = [
    "datum_check", "is_laatste_check", "registratienummer",
    "naam", "wijziging_type", "kolom", "oude_waarde", "nieuwe_waarde",
]


# ─────────────────────────────────────────────────────────────────────────────
# Configuratie
# ─────────────────────────────────────────────────────────────────────────────

def _standaard_config() -> dict[str, Any]:
    return {
        "bron": {
            "datasets_pagina_url": "https://linked.toerismevlaanderen.be/datasets",
            "csv_label":           "Basisregister Vlaams Logiesaanbod",
            "csv_scheidingsteken": ";",
        },
        "kolommen": {
            "sleutelkolom":         "business_product_id",
            "naamkolom":            "name",
            "uitgesloten_kolommen": [],
            "postcode_kolom": "postal_code",
            "postcodes": [3000, 3001, 3010, 3012, 3018],
        },
        "archief":  {"bewaarperiode_dagen": 60},
        "netwerk":  {"ssl_verificatie": True},
    }


@st.cache_data(show_spinner=False)
def laad_config() -> dict[str, Any]:
    """Laad config.toml vanuit de deployment-map; val terug op standaardwaarden."""
    standaard = _standaard_config()
    if tomllib is None:
        return standaard
    try:
        with open("config.toml", "rb") as f:
            return tomllib.load(f)
    except Exception:
        return standaard


# ─────────────────────────────────────────────────────────────────────────────
# GitHub-opslag
# ─────────────────────────────────────────────────────────────────────────────

class GitHubOpslag:
    """CRUD-operaties op bestanden in een GitHub-repository via de Contents API."""

    _API = "https://api.github.com"

    def __init__(self, token: str, repo: str) -> None:
        self._repo = repo.strip()
        self._hdrs = {
            "Authorization":        f"token {token}",
            "Accept":               "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, pad: str) -> str:
        return f"{self._API}/repos/{self._repo}/contents/{pad}"

    def _meta(self, pad: str) -> dict | None:
        """Geeft bestandsmetadata terug, of None als het pad niet bestaat."""
        r = requests.get(self._url(pad), headers=self._hdrs, timeout=15)
        if r.status_code == 404:
            return None
        self._controleer_status(r)
        data = r.json()
        return data if isinstance(data, dict) else None

    @staticmethod
    def _controleer_status(r: requests.Response) -> None:
        """Gooit beschrijvende fouten voor veelvoorkomende HTTP-problemen."""
        if r.ok:
            return
        if r.status_code in (401, 403):
            raise PermissionError(
                "GitHub-toegangsfout: het token is ongeldig of heeft onvoldoende rechten. "
                "Controleer GITHUB_TOKEN in de Streamlit-secrets."
            )
        if r.status_code == 404:
            raise FileNotFoundError(
                "Repository niet gevonden. Controleer GITHUB_REPO in de Streamlit-secrets."
            )
        r.raise_for_status()

    def lees(self, pad: str) -> bytes | None:
        meta = self._meta(pad)
        if meta is None:
            return None
        content = meta.get("content", "").strip()
        if content:
            return base64.b64decode(content)
        # Bestand te groot voor inline content (>1MB) — gebruik download_url
        download_url = meta.get("download_url")
        if download_url:
            r = requests.get(download_url, headers=self._hdrs, timeout=120)
            self._controleer_status(r)
            return r.content
        return None

    def schrijf(self, pad: str, inhoud: bytes, bericht: str) -> None:
        """Maak een nieuw bestand aan of overschrijf een bestaand bestand."""
        meta = self._meta(pad)
        payload: dict[str, Any] = {
            "message": bericht,
            "content": base64.b64encode(inhoud).decode(),
        }
        if meta:
            payload["sha"] = meta["sha"]
        r = requests.put(self._url(pad), headers=self._hdrs, json=payload, timeout=30)
        self._controleer_status(r)

    def verwijder(self, pad: str, bericht: str) -> None:
        """Verwijder een bestand. Geen actie als het niet bestaat."""
        meta = self._meta(pad)
        if meta is None:
            return
        r = requests.delete(
            self._url(pad), headers=self._hdrs,
            json={"message": bericht, "sha": meta["sha"]}, timeout=15,
        )
        self._controleer_status(r)

    def lijst(self, pad: str) -> list[dict]:
        """Geeft een lijst van bestanden in een map. Leeg als de map niet bestaat."""
        r = requests.get(self._url(pad), headers=self._hdrs, timeout=15)
        if r.status_code == 404:
            return []
        self._controleer_status(r)
        result = r.json()
        return result if isinstance(result, list) else []


# ─────────────────────────────────────────────────────────────────────────────
# Archiefbeheer
# ─────────────────────────────────────────────────────────────────────────────

def archief_pad(dag: date) -> str:
    return f"{PAD_ARCHIEF}/basisregister_{dag.strftime('%d-%m-%Y')}.csv"


def beschikbare_datums(opslag: GitHubOpslag) -> list[date]:
    """Geeft alle beschikbare archiefdatums terug, aflopend gesorteerd."""
    datums: list[date] = []
    for item in opslag.lijst(PAD_ARCHIEF):
        m = re.match(r"basisregister_(\d{2}-\d{2}-\d{4})\.csv", item.get("name", ""))
        if m:
            try:
                datums.append(datetime.strptime(m.group(1), "%d-%m-%Y").date())
            except ValueError:
                pass
    return sorted(datums, reverse=True)


def verwijder_verouderd(opslag: GitHubOpslag, bewaar_dagen: int) -> list[date]:
    """Verwijder archiefbestanden ouder dan bewaar_dagen dagen."""
    grens      = date.today() - timedelta(days=bewaar_dagen)
    verwijderd : list[date] = []
    for d in beschikbare_datums(opslag):
        if d < grens:
            opslag.verwijder(archief_pad(d), f"Archief opruimen: {d}")
            verwijderd.append(d)
    return verwijderd


# ─────────────────────────────────────────────────────────────────────────────
# CSV downloaden
# ─────────────────────────────────────────────────────────────────────────────

def zoek_csv_url(pagina: str, label: str, ssl: bool) -> str:
    """
    Scrape de datasets-pagina om de huidige CSV-download-URL te vinden.
    De UUID in de URL verandert bij elke nieuwe release van het register.
    """
    try:
        r = requests.get(pagina, timeout=30, verify=ssl)
        r.raise_for_status()
    except requests.exceptions.SSLError:
        raise ConnectionError(
            "SSL-certificaatfout bij verbinding met de datasets-pagina. "
            "Schakel SSL-verificatie uit in de instellingen als u via een "
            "bedrijfsnetwerk werkt."
        )
    except requests.RequestException as exc:
        raise ConnectionError(f"Datasets-pagina niet bereikbaar: {exc}")

    soup = BeautifulSoup(r.text, "html.parser")
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        tekst = h.get_text(" ", strip=True)
        if label in tekst and "CSV" in tekst:
            a = h.find_next("a", href=True)
            if a:
                return urljoin(pagina, str(a["href"]))

    raise RuntimeError(
        f"Dataset '{label} (CSV)' niet gevonden op de pagina. "
        "Controleer de instelling 'CSV-label' of of de paginastructuur "
        "van Toerisme Vlaanderen gewijzigd is."
    )


def download_csv(url: str, ssl: bool) -> bytes:
    """Stream-download een CSV en geef de ruwe bytes terug."""
    try:
        with requests.get(url, stream=True, timeout=120, verify=ssl) as r:
            r.raise_for_status()
            return r.content
    except requests.RequestException as exc:
        raise ConnectionError(f"CSV-download mislukt: {exc}")


def csv_naar_df(b: bytes, sep: str) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(b), sep=sep, dtype=str).fillna("")

def filter_op_postcodes(df: pd.DataFrame, kolom: str, postcodes: list[str]) -> pd.DataFrame:
    if not kolom or kolom not in df.columns:
        return df
    df = df[df[kolom].str.strip() != ""].copy()
    if postcodes:
        df = df[df[kolom].str.strip().isin(postcodes)]
    return df

# ─────────────────────────────────────────────────────────────────────────────
# Diff-berekening
# ─────────────────────────────────────────────────────────────────────────────

def _changelog_rij(
    datum: str, nr: str, naam: str,
    wijz: str, kol: str, oud: str, nieuw: str,
) -> dict:
    return {
        "datum_check": datum, "is_laatste_check": True,
        "registratienummer": nr, "naam": naam,
        "wijziging_type": wijz, "kolom": kol,
        "oude_waarde": oud, "nieuwe_waarde": nieuw,
    }


def bereken_diff(
    df_n: pd.DataFrame,
    df_o: pd.DataFrame,
    sleutel: str,
    naam_kol: str,
    uitgesloten: list[str],
) -> pd.DataFrame:
    """
    Veld-voor-veld vergelijking tussen twee versies van het register.

    Detecteert: nieuwe rijen, verdwenen rijen en gewijzigde veldwaarden.
    Kolommen in `uitgesloten` worden overgeslagen bij de veldvergelijking.

    Raises ValueError als de sleutelkolom ontbreekt in een van de DataFrames.
    """
    for df, label in ((df_n, "nieuwste"), (df_o, "vorige")):
        if sleutel not in df.columns:
            raise ValueError(
                f"Sleutelkolom '{sleutel}' ontbreekt in de {label} versie. "
                "Pas de instelling 'Sleutelkolom' aan in het zijpaneel."
            )

    datum = date.today().strftime("%d-%m-%Y")
    df_n  = df_n.copy()
    df_o  = df_o.copy()
    df_n[sleutel] = df_n[sleutel].astype(str).str.strip()
    df_o[sleutel] = df_o[sleutel].astype(str).str.strip()

    nrs_n, nrs_o = set(df_n[sleutel]), set(df_o[sleutel])
    rijen: list[dict] = []

    def _naam(rij: pd.Series) -> str:
        return str(rij.get(naam_kol, "—")) or "—"

    for nr in sorted(nrs_n - nrs_o):
        r = df_n.loc[df_n[sleutel] == nr].iloc[0]
        rijen.append(_changelog_rij(datum, nr, _naam(r), "nieuw", "—", "—", "—"))

    for nr in sorted(nrs_o - nrs_n):
        r = df_o.loc[df_o[sleutel] == nr].iloc[0]
        rijen.append(_changelog_rij(datum, nr, _naam(r), "verdwenen", "—", "—", "—"))

    uitgesloten_set = set(uitgesloten) | {sleutel}
    diff_cols       = [c for c in df_n.columns if c not in uitgesloten_set]
    idx_n           = df_n.set_index(sleutel)
    idx_o           = df_o.set_index(sleutel)

    for nr in sorted(nrs_n & nrs_o):
        r_n = idx_n.loc[nr]
        r_o = idx_o.loc[nr]
        if isinstance(r_n, pd.DataFrame): r_n = r_n.iloc[0]
        if isinstance(r_o, pd.DataFrame): r_o = r_o.iloc[0]
        naam = _naam(r_n)
        for col in diff_cols:
            oud, nieuw = str(r_o.get(col, "")), str(r_n.get(col, ""))
            if oud != nieuw:
                rijen.append(_changelog_rij(datum, nr, naam, "gewijzigd", col, oud, nieuw))

    return pd.DataFrame(rijen) if rijen else pd.DataFrame(columns=CHANGELOG_KOLOMMEN)


# ─────────────────────────────────────────────────────────────────────────────
# Changelog bijwerken
# ─────────────────────────────────────────────────────────────────────────────

def update_changelog(df_nieuw: pd.DataFrame, bestaand: bytes | None) -> bytes:
    """
    Voeg nieuwe diff-rijen toe aan de changelog.
    Zet is_laatste_check van alle vorige rijen op False.
    """
    if bestaand:
        df_oud = pd.read_excel(io.BytesIO(bestaand), dtype=str)
        df_oud["is_laatste_check"] = False
        gecombineerd = pd.concat([df_oud, df_nieuw], ignore_index=True)
    else:
        gecombineerd = df_nieuw.copy()
    buf = io.BytesIO()
    gecombineerd.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Sessie-hulpfuncties
# ─────────────────────────────────────────────────────────────────────────────

def _log(bericht: str, niveau: str = "info") -> None:
    icoon = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "fout": "❌"}.get(niveau, "•")
    st.session_state.run_log.append(f"{icoon} {bericht}")


def _sessie_init() -> None:
    standaard: dict[str, Any] = {
        "run_log":            [],
        "huidig_df":          None,
        "archief_datums":     [],
        "archief_cache":      {},
        "fout":               None,
        "pipeline_klaar":     False,
        "eerste_gebruik":     False,
        "vandaag_opgeslagen": False,
        "diff_vandaag":       pd.DataFrame(columns=CHANGELOG_KOLOMMEN),
    }
    for k, v in standaard.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ─────────────────────────────────────────────────────────────────────────────
# Hoofd-pipeline (eenmalig per sessie)
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(opslag: GitHubOpslag, cfg: dict) -> None:
    """
    Kernlogica die eenmalig wordt uitgevoerd bij het eerste paginabezoek.

    Stap 1 — Controleer of er al een archief is voor vandaag.
      Ja  → laad het bestaande archief; niets meer te doen.
      Nee → download de nieuwste versie, bereken de diff, werk de changelog
            bij en sla de versie op als archief en als huidige momentopname.
    """
    vandaag = date.today()
    ssl     = cfg["netwerk"]["ssl_verificatie"]
    sep     = cfg["bron"]["csv_scheidingsteken"]
    sleutel = cfg["kolommen"]["sleutelkolom"]
    naam_k  = cfg["kolommen"]["naamkolom"]
    uitgesl = cfg["kolommen"]["uitgesloten_kolommen"]
    postcode_k   = cfg["kolommen"].get("postcode_kolom", "")
    postcodes    = [str(p) for p in cfg["kolommen"].get("postcodes", [])]

    try:
        _log("Beschikbare archieven ophalen...")
        datums = beschikbare_datums(opslag)
        st.session_state.archief_datums = datums
        _log(f"{len(datums)} archiefversie(s) beschikbaar.")

        if vandaag in datums:
            # Today already has an archive — load it, nothing else to do
            _log(f"Versie van vandaag ({vandaag.strftime('%d-%m-%Y')}) bestaat al — laden...")
            inhoud = opslag.lees(archief_pad(vandaag))
            if inhoud:
                df_vandaag = csv_naar_df(inhoud, sep)
                st.session_state.huidig_df = filter_op_postcodes(df_vandaag, postcode_k, postcodes)
                _log(f"Geladen: {len(st.session_state.huidig_df):,} rijen.", "ok")
            else:
                _log("Archief van vandaag kon niet worden geladen.", "warn")
            st.session_state.vandaag_opgeslagen = False

        else:
            # No archive yet for today — download and process
            _log("Actuele versie downloaden van Toerisme Vlaanderen...")
            url = zoek_csv_url(
                cfg["bron"]["datasets_pagina_url"],
                cfg["bron"]["csv_label"],
                ssl,
            )
            _log("Download-URL gevonden.")
            csv_bytes = download_csv(url, ssl)
            df_nieuw  = csv_naar_df(csv_bytes, sep)
            df_nieuw = filter_op_postcodes(df_nieuw, postcode_k, postcodes)
            gefilterd_bytes = df_nieuw.to_csv(index=False, sep=sep).encode("utf-8")
            _log(f"Gedownload: {len(df_nieuw):,} rijen.")

            vorige = opslag.lees(PAD_HUIDIG)

            if vorige is None:
                _log("Geen vorige momentopname gevonden — dit is het eerste gebruik.", "warn")
                st.session_state.eerste_gebruik = True
                diff_df = pd.DataFrame(columns=CHANGELOG_KOLOMMEN)
            else:
                _log("Vergelijken met vorige versie...")
                df_oud  = csv_naar_df(vorige, sep)
                diff_df = bereken_diff(df_nieuw, df_oud, sleutel, naam_k, uitgesl)
                n = len(diff_df)
                _log(f"Diff berekend: {n:,} wijziging(en) gevonden.", "ok")

                if not diff_df.empty:
                    _log("Changelog bijwerken...")
                    opslag.schrijf(
                        PAD_CHANGELOG,
                        update_changelog(diff_df, opslag.lees(PAD_CHANGELOG)),
                        f"Changelog bijgewerkt op {vandaag}",
                    )
                    _log("Changelog opgeslagen.")

            _log("Nieuwe versie opslaan in repository...")
            opslag.schrijf(archief_pad(vandaag), gefilterd_bytes, f"Archief: {vandaag}")
            opslag.schrijf(PAD_HUIDIG,           gefilterd_bytes, f"Momentopname: {vandaag}")
            _log("Versie opgeslagen.", "ok")

            verwijderd = verwijder_verouderd(opslag, cfg["archief"]["bewaarperiode_dagen"])
            if verwijderd:
                _log(f"Opruimen: {len(verwijderd)} verouderd(e) archief/archieven verwijderd.")

            # Refresh archive list after changes
            st.session_state.archief_datums   = beschikbare_datums(opslag)
            st.session_state.huidig_df        = df_nieuw
            st.session_state.diff_vandaag     = diff_df
            st.session_state.vandaag_opgeslagen = True

        st.session_state.pipeline_klaar = True
        st.session_state.fout           = None

    except (ConnectionError, RuntimeError, ValueError,
            PermissionError, FileNotFoundError) as exc:
        st.session_state.fout = str(exc)
        _log(str(exc), "fout")
    except Exception as exc:
        st.session_state.fout = f"Onverwachte fout: {exc}"
        _log(f"Onverwachte fout: {exc}", "fout")


# ─────────────────────────────────────────────────────────────────────────────
# Streamlit-pagina
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Basisregister Monitor",
    page_icon="📋",
    layout="wide",
)

_sessie_init()
cfg_standaard = laad_config()

# ── Zijpaneel: instellingen ───────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Instellingen")
    st.caption(
        "Wijzigingen gelden uitsluitend voor deze sessie en worden "
        "niet opgeslagen in de repository."
    )

    with st.expander("Gegevensbron"):
        pagina_url = st.text_input(
            "Datasets-pagina URL",
            value=cfg_standaard["bron"]["datasets_pagina_url"],
        )
        csv_label = st.text_input(
            "CSV-label op de pagina",
            value=cfg_standaard["bron"]["csv_label"],
        )
        csv_sep = st.text_input(
            "CSV-scheidingsteken",
            value=cfg_standaard["bron"]["csv_scheidingsteken"],
        )

    with st.expander("Kolominstellingen"):
        sleutelkolom = st.text_input(
            "Sleutelkolom (unieke ID)",
            value=cfg_standaard["kolommen"]["sleutelkolom"],
        )
        naamkolom = st.text_input(
            "Naamkolom",
            value=cfg_standaard["kolommen"]["naamkolom"],
        )
        uitgesl_tekst = st.text_area(
            "Uitgesloten kolommen (één per regel)",
            value="\n".join(cfg_standaard["kolommen"]["uitgesloten_kolommen"]),
            height=100,
        )

    with st.expander("Archief"):
        bewaar_dagen = st.number_input(
            "Bewaarperiode in dagen",
            min_value=1,
            max_value=365,
            value=int(cfg_standaard["archief"]["bewaarperiode_dagen"]),
        )

    with st.expander("Netwerk"):
        ssl_aan = st.toggle(
            "SSL-verificatie inschakelen",
            value=bool(cfg_standaard["netwerk"]["ssl_verificatie"]),
        )
        if not ssl_aan:
            st.warning(
                "SSL-verificatie is uitgeschakeld voor deze sessie. "
                "Gebruik dit alleen bij verbindingsproblemen via een bedrijfsnetwerk."
            )

    st.divider()

    if st.button("🔄 Sessie opnieuw starten", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    st.caption(
        "Klik op 'Sessie opnieuw starten' om gewijzigde instellingen "
        "toe te passen op een nieuwe download."
    )

# Build the active session config from sidebar values
cfg_sessie: dict[str, Any] = {
    "bron": {
        "datasets_pagina_url": pagina_url,
        "csv_label":           csv_label,
        "csv_scheidingsteken": csv_sep,
    },
    "kolommen": {
        "sleutelkolom":         sleutelkolom,
        "naamkolom":            naamkolom,
        "uitgesloten_kolommen": [
            x.strip() for x in uitgesl_tekst.splitlines() if x.strip()
        ],
    },
    "archief":  {"bewaarperiode_dagen": int(bewaar_dagen)},
    "netwerk":  {"ssl_verificatie": ssl_aan},
}
cfg_sessie["kolommen"]["postcode_kolom"] = cfg_standaard["kolommen"].get("postcode_kolom", "")
cfg_sessie["kolommen"]["postcodes"]      = cfg_standaard["kolommen"].get("postcodes", [])


# ── Secrets laden ─────────────────────────────────────────────────────────────

try:
    _token = st.secrets["GITHUB_TOKEN"]
    _repo  = st.secrets["GITHUB_REPO"]
    opslag = GitHubOpslag(_token, _repo)
except KeyError as exc:
    st.error(
        f"**Configuratiefout:** het geheim `{exc.args[0]}` ontbreekt. "
        "Voeg `GITHUB_TOKEN` en `GITHUB_REPO` toe via "
        "*App settings → Secrets* in Streamlit Cloud."
    )
    st.stop()


# ── Pipeline uitvoeren bij eerste laad ───────────────────────────────────────

if not st.session_state.pipeline_klaar:
    with st.spinner("Basisregister controleren — even geduld..."):
        run_pipeline(opslag, cfg_sessie)


# ── Paginatitel ───────────────────────────────────────────────────────────────

st.title("📋 Basisregister Monitor")
st.caption("Toerisme Vlaanderen — Basisregister Vlaams Logiesaanbod")

if st.session_state.fout:
    st.error(f"**Er is een fout opgetreden:**\n\n{st.session_state.fout}")
    st.info(
        "Controleer de instellingen in het zijpaneel en klik op "
        "**Sessie opnieuw starten** om het opnieuw te proberen."
    )
    with st.expander("📄 Uitvoeringslog"):
        for regel in st.session_state.run_log:
            st.text(regel)
    st.stop()


# ── Statusoverzicht ───────────────────────────────────────────────────────────

vandaag        = date.today()
archief_datums = st.session_state.archief_datums

if st.session_state.vandaag_opgeslagen:
    status_tekst = f"✅ Nieuw opgeslagen vandaag ({vandaag.strftime('%d-%m-%Y')})"
elif st.session_state.eerste_gebruik:
    status_tekst = "🆕 Eerste gebruik — beginsituatie opgeslagen"
else:
    status_tekst = f"📁 Al opgeslagen eerder vandaag ({vandaag.strftime('%d-%m-%Y')})"

col1, col2, col3 = st.columns(3)
col1.metric("Status", status_tekst)
col2.metric("Beschikbare archieven", len(archief_datums))
col3.metric(
    "Wijzigingen gevonden vandaag",
    len(st.session_state.diff_vandaag) if st.session_state.vandaag_opgeslagen else "—",
)

st.divider()


# ── Downloads ─────────────────────────────────────────────────────────────────

dcol1, dcol2 = st.columns(2)

with dcol1:
    changelog_bytes = opslag.lees(PAD_CHANGELOG)
    if changelog_bytes:
        st.download_button(
            "📥 Download volledige changelog",
            changelog_bytes,
            "changelog.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Alle historische wijzigingen als Excel-bestand.",
        )
    else:
        st.caption("Changelog nog niet beschikbaar (nog geen wijzigingen geregistreerd).")

with dcol2:
    if st.session_state.huidig_df is not None:
        buf_huidig = io.BytesIO()
        st.session_state.huidig_df.to_csv(
            buf_huidig, index=False,
            sep=cfg_sessie["bron"]["csv_scheidingsteken"],
        )
        st.download_button(
            "📥 Download huidige registerversie als CSV",
            buf_huidig.getvalue(),
            f"basisregister_{vandaag.isoformat()}.csv",
            "text/csv",
            help="De versie van het register die vandaag is opgeslagen.",
        )

st.divider()


# ── Vergelijkingsviewer ───────────────────────────────────────────────────────

st.subheader("Versies vergelijken")

vergelijk_opties = [d for d in archief_datums if d != vandaag]

if not vergelijk_opties:
    if st.session_state.eerste_gebruik:
        st.info(
            "Dit is de eerste keer dat het register is opgeslagen. "
            "Er zijn nog geen eerdere versies beschikbaar om mee te vergelijken."
        )
    else:
        st.info("Er zijn geen eerdere archiefversies beschikbaar.")
else:
    label_naar_datum = {d.strftime("%d-%m-%Y"): d for d in vergelijk_opties}

    geselecteerd_label = st.selectbox(
        "Vergelijk huidige versie met archiefversie van:",
        options=list(label_naar_datum.keys()),
        index=0,
        help=(
            "Selecteer een datum om de huidige versie te vergelijken met die dag. "
            "Handig als u het register al een tijdje niet heeft bekeken: kies dan "
            "de datum van uw laatste controle om alle tussentijdse wijzigingen te zien."
        ),
    )
    geselecteerde_datum = label_naar_datum[geselecteerd_label]

    # Load selected archive, cached per session to avoid repeated API calls
    if geselecteerde_datum not in st.session_state.archief_cache:
        with st.spinner(f"Archiefversie van {geselecteerd_label} laden..."):
            try:
                b = opslag.lees(archief_pad(geselecteerde_datum))
                if b:
                    df_arch = csv_naar_df(b, cfg_sessie["bron"]["csv_scheidingsteken"])
                    df_arch = filter_op_postcodes(
                        df_arch,
                        cfg_sessie["kolommen"].get("postcode_kolom", ""),
                        [str(p) for p in cfg_sessie["kolommen"].get("postcodes", [])],
                    )
                    st.session_state.archief_cache[geselecteerde_datum] = df_arch
                else:
                    st.session_state.archief_cache[geselecteerde_datum] = None
            except Exception as exc:
                st.error(f"Kon archief van {geselecteerd_label} niet laden: {exc}")
                st.session_state.archief_cache[geselecteerde_datum] = None

    df_archief = st.session_state.archief_cache.get(geselecteerde_datum)

    if df_archief is None:
        st.error(f"Archiefversie van {geselecteerd_label} is niet beschikbaar.")
    elif st.session_state.huidig_df is None:
        st.error("Huidige versie is niet geladen. Klik op 'Sessie opnieuw starten'.")
    else:
        try:
            diff_vgl = bereken_diff(
                st.session_state.huidig_df,
                df_archief,
                cfg_sessie["kolommen"]["sleutelkolom"],
                cfg_sessie["kolommen"]["naamkolom"],
                cfg_sessie["kolommen"]["uitgesloten_kolommen"],
            )
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

        datum_huidig = vandaag.strftime("%d-%m-%Y")
        st.markdown(
            f"**Huidige versie** ({datum_huidig}) "
            f"t.o.v. **archiefversie van {geselecteerd_label}**"
        )

        if diff_vgl.empty:
            st.success("✅ Geen wijzigingen gevonden tussen deze twee versies.")
        else:
            n_nieuw     = int((diff_vgl["wijziging_type"] == "nieuw").sum())
            n_verdwenen = int((diff_vgl["wijziging_type"] == "verdwenen").sum())
            n_gewijzigd = int((diff_vgl["wijziging_type"] == "gewijzigd").sum())

            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("🆕 Nieuw",              n_nieuw,
                       help="Nieuw toegevoegde logies")
            mc2.metric("🗑️ Verdwenen",          n_verdwenen,
                       help="Verwijderde of uitgeschreven logies")
            mc3.metric("✏️ Gewijzigde velden",  n_gewijzigd,
                       help="Aantal gewijzigde veldwaarden over alle logies")

            weergave_df = diff_vgl[[
                "registratienummer", "naam", "wijziging_type",
                "kolom", "oude_waarde", "nieuwe_waarde",
            ]].rename(columns={
                "registratienummer": "Registratienummer",
                "naam":              "Naam",
                "wijziging_type":    "Type",
                "kolom":             "Kolom",
                "oude_waarde":       "Vorige waarde",
                "nieuwe_waarde":     "Nieuwe waarde",
            })

            st.dataframe(
                weergave_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Type":           st.column_config.TextColumn(width="small"),
                    "Kolom":          st.column_config.TextColumn(width="medium"),
                    "Vorige waarde":  st.column_config.TextColumn(width="large"),
                    "Nieuwe waarde":  st.column_config.TextColumn(width="large"),
                },
            )

            buf_xlsx = io.BytesIO()
            diff_vgl.to_excel(buf_xlsx, index=False, engine="openpyxl")
            st.download_button(
                "📥 Download dit overzicht als Excel",
                buf_xlsx.getvalue(),
                f"vergelijking_{geselecteerde_datum.isoformat()}"
                f"_vs_{vandaag.isoformat()}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


# ── Uitvoeringslog ────────────────────────────────────────────────────────────

with st.expander("📄 Uitvoeringslog"):
    for regel in st.session_state.run_log:
        st.text(regel)
    if not st.session_state.run_log:
        st.text("Geen logberichten.")

# Show non-default session settings if any are active
afwijkingen: list[str] = []
cfg_s = cfg_standaard
cfg_n = cfg_sessie
if cfg_n["bron"]["datasets_pagina_url"] != cfg_s["bron"]["datasets_pagina_url"]:
    afwijkingen.append(f"datasets_pagina_url  →  {cfg_n['bron']['datasets_pagina_url']}")
if cfg_n["bron"]["csv_label"] != cfg_s["bron"]["csv_label"]:
    afwijkingen.append(f"csv_label  →  {cfg_n['bron']['csv_label']}")
if cfg_n["kolommen"]["sleutelkolom"] != cfg_s["kolommen"]["sleutelkolom"]:
    afwijkingen.append(f"sleutelkolom  →  {cfg_n['kolommen']['sleutelkolom']}")
if cfg_n["kolommen"]["naamkolom"] != cfg_s["kolommen"]["naamkolom"]:
    afwijkingen.append(f"naamkolom  →  {cfg_n['kolommen']['naamkolom']}")
if not cfg_n["netwerk"]["ssl_verificatie"]:
    afwijkingen.append("ssl_verificatie  →  uitgeschakeld")
if cfg_n["archief"]["bewaarperiode_dagen"] != cfg_s["archief"]["bewaarperiode_dagen"]:
    afwijkingen.append(
        f"bewaarperiode_dagen  →  {cfg_n['archief']['bewaarperiode_dagen']}"
    )

if afwijkingen:
    with st.expander("⚠️ Actieve sessie-instellingen (afwijkend van standaard)"):
        st.caption("Deze instellingen wijken af van de waarden in config.toml:")
        for a in afwijkingen:
            st.text(f"• {a}")
