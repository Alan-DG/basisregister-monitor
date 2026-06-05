"""
app.py — Basisregister Monitor
================================
Streamlit-applicatie voor het bewaken van wijzigingen in het
Basisregister Vlaams Logiesaanbod (Toerisme Vlaanderen).

Versie:  2.1
Opslag:  Privé GitHub-repository via REST API
Hosting: Streamlit Community Cloud
"""

from __future__ import annotations

import base64
import io
import json
import re
import warnings
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
from urllib.parse import urljoin

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

try:
    import folium
    FOLIUM_BESCHIKBAAR = True
except ImportError:
    FOLIUM_BESCHIKBAAR = False

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Constanten — paden in de GitHub-repository
# ─────────────────────────────────────────────────────────────────────────────

PAD_HUIDIG  = "data/basisregister_huidig.csv"
PAD_ARCHIEF = "data/archief"

DIFF_KOLOMMEN = [
    "registratienummer", "naam", "wijziging_type",
    "kolom", "oude_waarde", "nieuwe_waarde",
]

# ─────────────────────────────────────────────────────────────────────────────
# Constanten — kaartvisualisatie
# ─────────────────────────────────────────────────────────────────────────────

# Kleur per discriminator-waarde (API-sleutels, niet de weergavenamen)
DISC_KLEUREN: dict[str, str] = {
    "BED_AND_BREAKFAST":   "#a65628",
    "CAMPSITE":            "#4daf4a",
    "HOLIDAY_COTTAGE":     "#f781bf",
    "HOSTEL":              "#984ea3",
    "HOTEL":               "#377eb8",
    "MOTOR_HOME_TERRAIN":  "#4daf4a",
    "TOURIST_RESIDENCE":   "#e41a1c",
    "VACATION_PARK":       "#ff7f00",
    "YOUTH_ACCOMMODATION": "#ff7f00",
}

# Leesbare Nederlandse labels voor de legenda
_DISC_LABELS: dict[str, str] = {
    "BED_AND_BREAKFAST":   "B&B",
    "CAMPSITE":            "Camping",
    "HOLIDAY_COTTAGE":     "Vakantiewoning",
    "HOSTEL":              "Hostel",
    "HOTEL":               "Hotel",
    "MOTOR_HOME_TERRAIN":  "Camperterrein",
    "TOURIST_RESIDENCE":   "Kamergerelateerde logies",
    "VACATION_PARK":       "Vakantiepark",
    "YOUTH_ACCOMMODATION": "Jeugdverblijf",
}

_SIZE_BUCKETS  = ["1", "2–9", "10–30", "31–75", "76–100", "101+"]
_SIZE_RADII_PX = [4, 6, 9, 12, 15, 18]


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
            "postcode_kolom":       "postal_code",
            "postcodes":            [3000, 3001, 3010, 3012, 3018],
        },
        "archief": {"bewaarperiode_dagen": 60},
        "netwerk": {"ssl_verificatie": True},
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
        # Bestand te groot voor inline content (>1 MB) — gebruik download_url
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
    verwijderd: list[date] = []
    for d in beschikbare_datums(opslag):
        if d < grens:
            opslag.verwijder(archief_pad(d), f"Archief opruimen: {d}")
            verwijderd.append(d)
    return verwijderd


# ─────────────────────────────────────────────────────────────────────────────
# CSV downloaden en verwerken
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
        "Controleer de instelling 'CSV-label' of de paginastructuur "
        "van Toerisme Vlaanderen is gewijzigd."
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

def _diff_rij(nr: str, naam: str, wijz: str, kol: str, oud: str, nieuw: str) -> dict:
    return {
        "registratienummer": nr,
        "naam":              naam,
        "wijziging_type":    wijz,
        "kolom":             kol,
        "oude_waarde":       oud,
        "nieuwe_waarde":     nieuw,
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
                "Controleer de waarde van 'sleutelkolom' in config.toml."
            )

    df_n = df_n.copy()
    df_o = df_o.copy()
    df_n[sleutel] = df_n[sleutel].astype(str).str.strip()
    df_o[sleutel] = df_o[sleutel].astype(str).str.strip()

    nrs_n, nrs_o = set(df_n[sleutel]), set(df_o[sleutel])
    rijen: list[dict] = []

    def _naam(rij: pd.Series) -> str:
        return str(rij.get(naam_kol, "—")) or "—"

    for nr in sorted(nrs_n - nrs_o):
        r = df_n.loc[df_n[sleutel] == nr].iloc[0]
        rijen.append(_diff_rij(nr, _naam(r), "nieuw", "—", "—", "—"))

    for nr in sorted(nrs_o - nrs_n):
        r = df_o.loc[df_o[sleutel] == nr].iloc[0]
        rijen.append(_diff_rij(nr, _naam(r), "verdwenen", "—", "—", "—"))

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
                rijen.append(_diff_rij(nr, naam, "gewijzigd", col, oud, nieuw))

    return pd.DataFrame(rijen) if rijen else pd.DataFrame(columns=DIFF_KOLOMMEN)


# ─────────────────────────────────────────────────────────────────────────────
# Kaartvisualisatie
# ─────────────────────────────────────────────────────────────────────────────

def _coord_normaliseer(val: Any, lo: float, hi: float) -> float | None:
    """
    Normaliseer een coördinaatwaarde naar het bereik [lo, hi].
    Corrigeert het verschoven formaat dat soms in het register voorkomt.
    Geeft None terug bij een ongeldige of lege waarde.
    """
    try:
        v = float(str(val).replace(",", "."))
        if v == 0 or pd.isna(v):
            return None
        while v >= hi * 10:
            v /= 10
        if not (lo - 2 < v < hi + 2):
            return None
        return v
    except (ValueError, TypeError):
        return None


def _eenheid_straal(n: int) -> int:
    """Geeft de cirkelstraal in pixels terug op basis van het aantal eenheden."""
    for grens, straal in zip([1, 9, 30, 75, 100], _SIZE_RADII_PX):
        if n <= grens:
            return straal
    return _SIZE_RADII_PX[-1]


def _eenheid_bucket(n: int) -> str:
    """Deelt het aantal eenheden in een groottecategorie in voor de legenda."""
    for grens, bucket in zip([1, 9, 30, 75, 100], _SIZE_BUCKETS):
        if n <= grens:
            return bucket
    return _SIZE_BUCKETS[-1]


def _popup_html(rij: pd.Series, naam: str) -> str:
    """
    Bouw een scrollbare HTML-tabel met alle niet-lege veldwaarden van de rij.
    Wordt getoond als popup bij klikken op een markering.
    """
    def _esc(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    rijen_html = "".join(
        f'<tr>'
        f'<td style="color:#777;padding:2px 8px 2px 0;vertical-align:top;'
        f'white-space:nowrap;font-weight:600">{_esc(kol)}</td>'
        f'<td style="padding:2px 0;word-break:break-word">{_esc(waarde)}</td>'
        f'</tr>'
        for kol, waarde in rij.items()
        if str(waarde).strip() not in ("", "nan", "None", "NaN")
    )
    return (
        f'<div style="font-family:Arial,sans-serif;min-width:260px;max-width:380px">'
        f'<b style="font-size:13px">{_esc(naam)}</b>'
        f'<div style="max-height:300px;overflow-y:auto;margin-top:6px">'
        f'<table style="font-size:11px;border-collapse:collapse;width:100%">'
        f'{rijen_html}'
        f'</table></div></div>'
    )


def maak_kaart(df: pd.DataFrame, naam_kol: str = "name") -> "folium.Map | None":
    """
    Bouw een interactieve Folium-kaart van alle logies in df.

    - Cirkels geschaald naar aantal eenheden, gekleurd per logiestype.
    - Klik op een markering voor een scrollbare popup met alle registergegevens.
    - Interactieve legenda rechtsonder: klik om te filteren op type of grootte.

    Geeft None terug als folium niet beschikbaar is of er geen geldige
    coördinaten in de data aanwezig zijn.
    """
    if not FOLIUM_BESCHIKBAAR:
        return None

    # Kolommen automatisch opzoeken op naam (case-insensitive)
    col_lower    = {c.lower(): c for c in df.columns}
    lat_kol      = col_lower.get("lat") or col_lower.get("latitude")
    lon_kol      = (col_lower.get("lon") or col_lower.get("long")
                    or col_lower.get("lng") or col_lower.get("longitude"))
    disc_kol     = col_lower.get("discriminator")
    eenh_kol     = col_lower.get("number_of_units")

    if not lat_kol or not lon_kol:
        return None

    df = df.copy()
    df["__lat"] = df[lat_kol].apply(lambda v: _coord_normaliseer(v, 49.0, 52.0))
    df["__lon"] = df[lon_kol].apply(lambda v: _coord_normaliseer(v,  2.0,  7.0))
    df = df.dropna(subset=["__lat", "__lon"])

    if df.empty:
        return None

    m = folium.Map(
        location=[df["__lat"].median(), df["__lon"].median()],
        zoom_start=13,
        tiles="CartoDB positron",
    )

    # Markerdata voor de JS-legenda (disc + bucket per marker, in volgorde van toevoeging)
    markers_data: list[dict] = []

    for _, rij in df.iterrows():
        disc_raw = (
            str(rij[disc_kol]).strip()
            if disc_kol and str(rij.get(disc_kol, "")).strip() not in ("", "nan")
            else "ONBEKEND"
        )
        disc_label = _DISC_LABELS.get(disc_raw, disc_raw.replace("_", " ").title())

        try:
            n_eenh = int(float(str(rij[eenh_kol]))) if eenh_kol else 1
        except (ValueError, TypeError):
            n_eenh = 1

        naam   = str(rij.get(naam_kol, "—")) or "—"
        kleur  = DISC_KLEUREN.get(disc_raw, "#888888")
        bucket = _eenheid_bucket(n_eenh)

        # Verwijder interne hulpkolommen vóór de popup
        rij_clean = rij.drop(labels=["__lat", "__lon"], errors="ignore")

        cm = folium.CircleMarker(
            location=[rij["__lat"], rij["__lon"]],
            radius=_eenheid_straal(n_eenh),
            color=kleur,
            fill=True,
            fill_color=kleur,
            fill_opacity=0.75,
            weight=1.5,
        )
        cm.add_child(folium.Popup(_popup_html(rij_clean, naam), max_width=400))
        cm.add_child(folium.Tooltip(
            f"{naam} · {disc_label} · "
            f"{n_eenh} {'eenheid' if n_eenh == 1 else 'eenheden'}"
        ))
        cm.add_to(m)

        markers_data.append({"disc": disc_raw, "bucket": bucket})

    # ── Stadsgrens ────────────────────────────────────────────────────────────
    # Laad leuven_boundary.geojson vanuit de repo-root (naast app.py).
    # Geen fout als het bestand ontbreekt — kaart werkt gewoon zonder grens.
    try:
        with open("leuven_boundary.geojson", encoding="utf-8") as _f:
            _grens = json.load(_f)
        folium.GeoJson(
            _grens,
            name="Stadsgrens",
            style_function=lambda x: {
                "color":       "#333333",
                "weight":      2,
                "fillOpacity": 0,
                "dashArray":   "6 4",
            },
        ).add_to(m)
    except FileNotFoundError:
        pass

    # ── Interactieve legenda ──────────────────────────────────────────────────
    map_var = m.get_name()

    # Alleen de typen die daadwerkelijk in de data voorkomen
    disc_typen        = sorted(set(d["disc"] for d in markers_data))
    disc_kleur_subset = {k: DISC_KLEUREN.get(k, "#888888") for k in disc_typen}
    disc_label_subset = {
        k: _DISC_LABELS.get(k, k.replace("_", " ").title())
        for k in disc_typen
    }

    legende_html = f"""
<style>
  .leg-panel{{
    position:fixed;bottom:30px;right:30px;z-index:9999;
    background:white;border-radius:8px;padding:10px 13px;
    box-shadow:0 2px 14px rgba(0,0,0,.18);font-family:Arial,sans-serif;
    min-width:165px;max-height:90vh;overflow-y:auto;
  }}
  .leg-title{{
    font-weight:700;font-size:10px;margin-bottom:4px;color:#555;
    text-transform:uppercase;letter-spacing:.06em;
  }}
  .leg-sep{{border:none;border-top:1px solid #eee;margin:7px 0;}}
  .leg-item{{
    display:flex;align-items:center;margin-bottom:3px;cursor:pointer;
    user-select:none;border-radius:4px;padding:2px 4px;transition:background .12s;
  }}
  .leg-item:hover{{background:#f2f2f2;}}
  .leg-item.inactive{{opacity:.28;}}
  .disc-dot{{
    display:inline-block;border-radius:50%;width:9px;height:9px;
    margin-right:6px;border:1.5px solid rgba(0,0,0,.2);flex-shrink:0;
  }}
  .size-dot{{
    display:inline-block;border-radius:50%;
    background:#666;border:1.5px solid #444;flex-shrink:0;
  }}
  .leg-label{{font-size:10px;color:#333;}}
</style>

<div class="leg-panel">
  <div class="leg-title">Logiestype</div>
  <div id="disc-legend"></div>
  <hr class="leg-sep">
  <div class="leg-title">Aantal eenheden</div>
  <div id="size-legend"></div>
</div>

<script>
(function(){{
  var markersData      = {json.dumps(markers_data)};
  var kleurMap         = {json.dumps(disc_kleur_subset)};
  var labelMap         = {json.dumps(disc_label_subset)};
  var discTypen        = {json.dumps(disc_typen)};
  var sizeBuckets      = {json.dumps(_SIZE_BUCKETS)};
  var sizeRadii        = {json.dumps(_SIZE_RADII_PX)};

  var activeDiscs  = new Set(discTypen);
  var activeSizes  = new Set(sizeBuckets);
  var leafletMarkers = [];

  function init() {{
    var mapObj = window["{map_var}"];
    if (!mapObj) {{ setTimeout(init, 250); return; }}

    // Verzamel alle CircleMarker-lagen in volgorde van toevoeging
    mapObj.eachLayer(function(layer) {{
      if (layer instanceof L.CircleMarker) leafletMarkers.push(layer);
    }});

    // Koppel filterattributen aan elke marker (zelfde volgorde als markersData)
    for (var i = 0; i < Math.min(leafletMarkers.length, markersData.length); i++) {{
      leafletMarkers[i]._disc   = markersData[i].disc;
      leafletMarkers[i]._bucket = markersData[i].bucket;
    }}

    buildDiscLegend(mapObj);
    buildSizeLegend(mapObj);
  }}

  function applyFilters(mapObj) {{
    leafletMarkers.forEach(function(c) {{
      var show = activeDiscs.has(c._disc) && activeSizes.has(c._bucket);
      if (show  && !mapObj.hasLayer(c)) mapObj.addLayer(c);
      if (!show &&  mapObj.hasLayer(c)) mapObj.removeLayer(c);
    }});
  }}

  function buildDiscLegend(mapObj) {{
    var el = document.getElementById('disc-legend');
    discTypen.forEach(function(disc) {{
      var item = document.createElement('div');
      item.className = 'leg-item';
      item.innerHTML =
        '<span class="disc-dot" style="background:' + (kleurMap[disc] || '#888') + '"></span>' +
        '<span class="leg-label">' + (labelMap[disc] || disc) + '</span>';
      item.addEventListener('click', function() {{
        if (activeDiscs.has(disc)) {{ activeDiscs.delete(disc); item.classList.add('inactive'); }}
        else                       {{ activeDiscs.add(disc);    item.classList.remove('inactive'); }}
        applyFilters(mapObj);
      }});
      el.appendChild(item);
    }});
  }}

  function buildSizeLegend(mapObj) {{
    var el = document.getElementById('size-legend');
    var maxDiam = sizeRadii[sizeRadii.length - 1] * 2;
    sizeBuckets.forEach(function(bucket, i) {{
      var r = sizeRadii[i], diam = r * 2;
      var mL = (maxDiam - diam) / 2, mR = 9 + mL;
      var item = document.createElement('div');
      item.className = 'leg-item';
      item.innerHTML =
        '<span class="size-dot" style="width:' + diam + 'px;height:' + diam + 'px;' +
        'margin-left:' + mL + 'px;margin-right:' + mR + 'px;"></span>' +
        '<span class="leg-label">' + bucket + '</span>';
      item.addEventListener('click', function() {{
        if (activeSizes.has(bucket)) {{ activeSizes.delete(bucket); item.classList.add('inactive'); }}
        else                         {{ activeSizes.add(bucket);    item.classList.remove('inactive'); }}
        applyFilters(mapObj);
      }});
      el.appendChild(item);
    }});
  }}

  setTimeout(init, 400);
}})();
</script>"""

    m.get_root().html.add_child(folium.Element(legende_html))
    return m


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
        "diff_vandaag":       pd.DataFrame(columns=DIFF_KOLOMMEN),
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
      Ja  → laad het bestaande archief; klaar.
      Nee → download de nieuwste versie van Toerisme Vlaanderen, sla ze op
            als archief en als huidige momentopname, en ruim verouderde
            archieven op.
    """
    vandaag    = date.today()
    ssl        = cfg["netwerk"]["ssl_verificatie"]
    sep        = cfg["bron"]["csv_scheidingsteken"]
    sleutel    = cfg["kolommen"]["sleutelkolom"]
    naam_k     = cfg["kolommen"]["naamkolom"]
    uitgesl    = cfg["kolommen"]["uitgesloten_kolommen"]
    postcode_k = cfg["kolommen"].get("postcode_kolom", "")
    postcodes  = [str(p) for p in cfg["kolommen"].get("postcodes", [])]

    try:
        _log("Beschikbare archieven ophalen...")
        datums = beschikbare_datums(opslag)
        st.session_state.archief_datums = datums
        _log(f"{len(datums)} archiefversie(s) beschikbaar.")

        if vandaag in datums:
            # Vandaag al een archief — gewoon laden
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
            # Nog geen archief voor vandaag — downloaden en opslaan
            _log("Actuele versie downloaden van Toerisme Vlaanderen...")
            url = zoek_csv_url(
                cfg["bron"]["datasets_pagina_url"],
                cfg["bron"]["csv_label"],
                ssl,
            )
            _log("Download-URL gevonden.")
            csv_bytes = download_csv(url, ssl)
            df_nieuw  = csv_naar_df(csv_bytes, sep)
            df_nieuw  = filter_op_postcodes(df_nieuw, postcode_k, postcodes)
            gefilterd_bytes = df_nieuw.to_csv(index=False, sep=sep).encode("utf-8")
            _log(f"Gedownload: {len(df_nieuw):,} rijen.")

            # Bereken diff t.o.v. vorige versie (enkel voor de statusmelding)
            vorige = opslag.lees(PAD_HUIDIG)
            if vorige is None:
                _log("Geen vorige momentopname gevonden — dit is het eerste gebruik.", "warn")
                st.session_state.eerste_gebruik = True
                diff_df = pd.DataFrame(columns=DIFF_KOLOMMEN)
            else:
                _log("Vergelijken met vorige versie...")
                df_oud  = csv_naar_df(vorige, sep)
                df_oud  = filter_op_postcodes(df_oud, postcode_k, postcodes)
                diff_df = bereken_diff(df_nieuw, df_oud, sleutel, naam_k, uitgesl)
                _log(f"Diff berekend: {len(diff_df):,} wijziging(en) t.o.v. vorige versie.", "ok")

            _log("Nieuwe versie opslaan in repository...")
            opslag.schrijf(archief_pad(vandaag), gefilterd_bytes, f"Archief: {vandaag}")
            opslag.schrijf(PAD_HUIDIG,           gefilterd_bytes, f"Momentopname: {vandaag}")
            _log("Versie opgeslagen.", "ok")

            verwijderd = verwijder_verouderd(opslag, cfg["archief"]["bewaarperiode_dagen"])
            if verwijderd:
                _log(f"Opruimen: {len(verwijderd)} verouderd(e) archief/archieven verwijderd.")

            st.session_state.archief_datums     = beschikbare_datums(opslag)
            st.session_state.huidig_df          = df_nieuw
            st.session_state.diff_vandaag       = diff_df
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
# Hulpfunctie: vergelijkingsresultaten tonen
# ─────────────────────────────────────────────────────────────────────────────

def toon_vergelijking(
    df_huidig: pd.DataFrame,
    df_vergelijk: pd.DataFrame,
    vergelijk_label: str,
    bestandsnaam: str,
    cfg: dict,
) -> None:
    """Bereken en toon de diff tussen twee registerversies."""
    try:
        diff = bereken_diff(
            df_huidig,
            df_vergelijk,
            cfg["kolommen"]["sleutelkolom"],
            cfg["kolommen"]["naamkolom"],
            cfg["kolommen"]["uitgesloten_kolommen"],
        )
    except ValueError as exc:
        st.error(str(exc))
        return

    datum_huidig = date.today().strftime("%d-%m-%Y")
    st.markdown(f"**Huidige versie** ({datum_huidig}) t.o.v. **{vergelijk_label}**")

    if diff.empty:
        st.success("✅ Geen wijzigingen gevonden tussen deze twee versies.")
        return

    n_nieuw     = int((diff["wijziging_type"] == "nieuw").sum())
    n_verdwenen = int((diff["wijziging_type"] == "verdwenen").sum())
    n_gewijzigd = int((diff["wijziging_type"] == "gewijzigd").sum())

    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("🆕 Nieuw",             n_nieuw,
               help="Nieuw toegevoegde logies")
    mc2.metric("🗑️ Verdwenen",         n_verdwenen,
               help="Verwijderde of uitgeschreven logies")
    mc3.metric("✏️ Gewijzigde velden", n_gewijzigd,
               help="Aantal gewijzigde veldwaarden over alle logies")

    weergave_df = diff.rename(columns={
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
            "Type":          st.column_config.TextColumn(width="small"),
            "Kolom":         st.column_config.TextColumn(width="medium"),
            "Vorige waarde": st.column_config.TextColumn(width="large"),
            "Nieuwe waarde": st.column_config.TextColumn(width="large"),
        },
    )

    # ── Volledige gegevens per betrokken logies ───────────────────────────────
    # Nieuw / gewijzigd → volledige rij uit de huidige versie.
    # Verdwenen         → volledige rij uit de vergelijkingsversie.
    sleutel = cfg["kolommen"]["sleutelkolom"]

    def _zoek_rij(df: pd.DataFrame, nr: str) -> dict | None:
        match = df[df[sleutel].astype(str).str.strip() == nr]
        return match.iloc[0].to_dict() if not match.empty else None

    volledig_rijen: list[dict] = []
    for nr, groep in diff.groupby("registratienummer"):
        nr = str(nr)
        types = set(groep["wijziging_type"])
        if "verdwenen" in types:
            rij = _zoek_rij(df_vergelijk, nr)
            label_type = "verdwenen"
        elif "nieuw" in types:
            rij = _zoek_rij(df_huidig, nr)
            label_type = "nieuw"
        else:
            rij = _zoek_rij(df_huidig, nr)
            label_type = "gewijzigd"
        if rij is not None:
            volledig_rijen.append({"wijziging_type": label_type, **rij})

    df_volledig = pd.DataFrame(volledig_rijen) if volledig_rijen else pd.DataFrame()

    # ── Excel-export met twee tabbladen ──────────────────────────────────────
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        diff.to_excel(writer, sheet_name="Wijzigingen", index=False)
        if not df_volledig.empty:
            df_volledig.to_excel(writer, sheet_name="Volledige gegevens", index=False)

    st.download_button(
        "📥 Download dit overzicht als Excel",
        buf.getvalue(),
        bestandsnaam,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help=(
            "Tabblad 'Wijzigingen': overzicht van alle gewijzigde velden. "
            "Tabblad 'Volledige gegevens': volledige registerrij per betrokken logies."
        ),
    )


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

# ── Zijpaneel ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Instellingen")

    with st.expander("Gegevensbron", expanded=False):
        pagina_url = st.text_input(
            "Datasets-pagina URL",
            value=cfg_standaard["bron"]["datasets_pagina_url"],
            help=(
                "Pas enkel aan als Toerisme Vlaanderen de URL van de "
                "datasets-pagina heeft gewijzigd."
            ),
        )
        st.caption(
            "Alle andere instellingen (kolomnamen, scheidingsteken, bewaarperiode, …) "
            "zijn vastgelegd in de configuratie en hoeven normaal niet gewijzigd te worden."
        )

    st.divider()

    if st.button("🔄 Sessie opnieuw starten", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    st.caption(
        "Gebruik 'Sessie opnieuw starten' als de pagina vastloopt "
        "of als u een aangepaste URL wilt toepassen."
    )

# Actieve configuratie — alleen de bron-URL is overschrijfbaar via het zijpaneel
cfg_sessie: dict[str, Any] = {
    "bron": {
        "datasets_pagina_url": pagina_url,
        "csv_label":           cfg_standaard["bron"]["csv_label"],
        "csv_scheidingsteken": cfg_standaard["bron"]["csv_scheidingsteken"],
    },
    "kolommen": dict(cfg_standaard["kolommen"]),
    "archief":  dict(cfg_standaard["archief"]),
    "netwerk":  dict(cfg_standaard["netwerk"]),
}


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
    st.markdown("### ⏳ Basisregister wordt gecontroleerd…")
    st.warning(
        "**Als dit de eerste controle van de dag is, wordt het volledige register gedownload "
        "en opgeslagen als nieuwe archiefversie.**\n\n"
        "Dit kan **5 à 10 minuten** duren, afhankelijk van de verbindingssnelheid. "
        "De pagina laadt automatisch opnieuw zodra de verwerking voltooid is — "
        "sluit dit venster of tabblad niet."
    )
    with st.spinner("Downloaden en verwerken — even geduld…"):
        run_pipeline(opslag, cfg_sessie)
    st.rerun()


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
    "Wijzigingen t.o.v. vorige versie",
    len(st.session_state.diff_vandaag) if st.session_state.vandaag_opgeslagen else "—",
    help=(
        "Aantal gedetecteerde wijzigingen bij de vergelijking van de zojuist gedownloade "
        "versie met de meest recente vorige opslag. Alleen zichtbaar na een nieuwe download."
    ),
)

st.divider()


# ── Registerwijzigingen controleren ──────────────────────────────────────────

st.subheader("Registerwijzigingen controleren")
st.caption(
    "Vergelijk de huidige registerversie met een eerder opgeslagen archiefversie "
    "of met een bestand dat u zelf heeft gedownload. Kies de datum van uw vorige "
    "controle als vergelijkingsbasis om alle tussentijdse wijzigingen in één overzicht te zien."
)

if st.session_state.huidig_df is None:
    st.error("Huidige versie is niet geladen. Klik op 'Sessie opnieuw starten'.")
    st.stop()

vergelijk_modus = st.radio(
    "Vergelijk met:",
    ["Archiefversie", "Zelf geüpload bestand"],
    horizontal=True,
    help=(
        "Kies 'Archiefversie' om te vergelijken met een versie die door deze tool is opgeslagen. "
        "Kies 'Zelf geüpload bestand' als u een eerder gedownloade registerversie van uw eigen "
        "computer wilt gebruiken als vergelijkingsbasis."
    ),
)

sep = cfg_sessie["bron"]["csv_scheidingsteken"]

# ── Modus A: Archiefversie ────────────────────────────────────────────────────

if vergelijk_modus == "Archiefversie":

    vergelijk_opties = [d for d in archief_datums if d != vandaag]

    if not vergelijk_opties:
        if st.session_state.eerste_gebruik:
            st.info(
                "Dit is het eerste gebruik van de tool. Er zijn nog geen eerdere "
                "archiefversies beschikbaar om mee te vergelijken."
            )
        else:
            st.info("Er zijn geen eerdere archiefversies beschikbaar.")

    else:
        label_naar_datum = {d.strftime("%d-%m-%Y"): d for d in vergelijk_opties}

        geselecteerd_label = st.selectbox(
            "Selecteer archiefversie:",
            options=list(label_naar_datum.keys()),
            index=0,
            help=(
                "Kies de datum van uw vorige controle. Alle wijzigingen die sindsdien "
                "zijn opgetreden — over meerdere tussenliggende versies heen — worden "
                "in één overzicht getoond."
            ),
        )
        geselecteerde_datum = label_naar_datum[geselecteerd_label]

        # Laad archiefversie, gecachet per sessie
        if geselecteerde_datum not in st.session_state.archief_cache:
            _laad_info = st.info(
                f"📂 Archiefversie van **{geselecteerd_label}** wordt opgehaald uit de "
                "repository. Dit kan 1 à 2 minuten duren — even geduld…"
            )
            with st.spinner(f"Archiefversie van {geselecteerd_label} laden…"):
                try:
                    b = opslag.lees(archief_pad(geselecteerde_datum))
                    if b:
                        df_arch = csv_naar_df(b, sep)
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
            _laad_info.empty()

        df_archief = st.session_state.archief_cache.get(geselecteerde_datum)

        if df_archief is None:
            st.error(f"Archiefversie van {geselecteerd_label} kon niet worden geladen.")
        else:
            toon_vergelijking(
                df_huidig     = st.session_state.huidig_df,
                df_vergelijk  = df_archief,
                vergelijk_label = f"archiefversie van {geselecteerd_label}",
                bestandsnaam  = (
                    f"vergelijking_{geselecteerde_datum.isoformat()}"
                    f"_vs_{vandaag.isoformat()}.xlsx"
                ),
                cfg = cfg_sessie,
            )

# ── Modus B: Zelf geüpload bestand ───────────────────────────────────────────

else:
    geupload = st.file_uploader(
        "Upload een eerder gedownloade registerversie (CSV)",
        type=["csv"],
        help=(
            "Upload een CSV-bestand dat u eerder heeft gedownload via de knop "
            "'Download huidige registerversie als CSV' onderaan deze pagina. "
            "Het bestand wordt niet opgeslagen — de vergelijking vindt alleen "
            "lokaal in uw browsersessie plaats. "
            "Het bestand dient .csv formaat te zijn en geen verdere "
            "formaatwijzigingen te bevatten. "
        ),
    )

    if geupload is None:
        st.info(
            "Upload een eerder gedownloade registerversie om de vergelijking te starten."
        )
    else:
        try:
            df_upload = csv_naar_df(geupload.read(), sep)
            df_upload = filter_op_postcodes(
                df_upload,
                cfg_sessie["kolommen"].get("postcode_kolom", ""),
                [str(p) for p in cfg_sessie["kolommen"].get("postcodes", [])],
            )
        except Exception as exc:
            st.error(f"Kon het geüploade bestand niet verwerken: {exc}")
            df_upload = None

        if df_upload is not None:
            basis        = re.sub(r"[^\w\-]", "_", geupload.name.rsplit(".", 1)[0])
            bestandsnaam = f"vergelijking_{basis}_vs_{vandaag.isoformat()}.xlsx"

            toon_vergelijking(
                df_huidig       = st.session_state.huidig_df,
                df_vergelijk    = df_upload,
                vergelijk_label = f"geüpload bestand '{geupload.name}'",
                bestandsnaam    = bestandsnaam,
                cfg             = cfg_sessie,
            )


# ── Kaart ─────────────────────────────────────────────────────────────────────

st.divider()

st.subheader("🗺️ Logiesoverzicht")
st.caption(
    "Alle logies uit de huidige registerversie op kaart. "
    "Klik op een punt voor de volledige registergegevens. "
    "Gebruik de legenda rechtsonder om te filteren op type of grootte."
)

if not FOLIUM_BESCHIKBAAR:
    st.info(
        "📦 Voeg `folium` toe aan `requirements.txt` om de kaart te tonen."
    )
elif st.session_state.huidig_df is not None:
    with st.spinner("Kaart wordt opgebouwd…"):
        kaart = maak_kaart(
            st.session_state.huidig_df,
            naam_kol=cfg_sessie["kolommen"]["naamkolom"],
        )
    if kaart is None:
        st.warning(
            "⚠️ Geen kaart beschikbaar — coördinatenkolommen (`lat` / `long`) "
            "niet gevonden in het register, of alle coördinaten zijn ongeldig."
        )
    else:
        components.html(kaart._repr_html_(), height=560)


# ── Download huidige registerversie ──────────────────────────────────────────

st.divider()

buf_huidig = io.BytesIO()
st.session_state.huidig_df.to_csv(buf_huidig, index=False, sep=sep)
st.download_button(
    "📥 Download huidige registerversie als CSV",
    buf_huidig.getvalue(),
    f"basisregister_{vandaag.isoformat()}.csv",
    "text/csv",
    help=(
        "Sla deze versie lokaal op als u haar later wilt gebruiken als "
        "vergelijkingsbasis via 'Zelf geüpload bestand'."
    ),
)


# ── Uitvoeringslog ────────────────────────────────────────────────────────────

with st.expander("📄 Uitvoeringslog"):
    for regel in st.session_state.run_log:
        st.text(regel)
    if not st.session_state.run_log:
        st.text("Geen logberichten.")

# Toon een melding als de gebruiker de datasets-URL heeft aangepast
if cfg_sessie["bron"]["datasets_pagina_url"] != cfg_standaard["bron"]["datasets_pagina_url"]:
    with st.expander("⚠️ Aangepaste sessie-instelling"):
        st.caption("De datasets-pagina URL wijkt af van de standaardwaarde in config.toml:")
        st.text(f"• datasets_pagina_url  →  {cfg_sessie['bron']['datasets_pagina_url']}")
