"""International Cost of Capital Lab. Run with `streamlit run app.py`."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd
import pycountry
import requests
import streamlit as st
import xlrd
from babel import Locale
from babel.numbers import get_territory_currencies
from bs4 import BeautifulSoup

TITLE = "International Cost of Capital Lab"
SOFTWARE = "Software (System & Application)"
BETA_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
SPREAD_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
TAX_URL = "https://www.oecd.org/en/data/datasets/corporate-income-tax-rates-database.html"
TAX_API = ("https://sdmx.oecd.org/public/rest/data/"
           "OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/.A.CIT_C.ST..S13...")
BONDS_URL = "https://tradingeconomics.com/bonds"
TE_ROOT = "https://tradingeconomics.com"
ALIASES = {
    "USA": "united-states", "GBR": "united-kingdom", "KOR": "south-korea",
    "CZE": "czech-republic", "VNM": "vietnam", "RUS": "russia",
    "TWN": "taiwan", "HKG": "hong-kong", "IRN": "iran", "TUR": "turkey",
    "SVK": "slovakia", "ARE": "united-arab-emirates", "CIV": "ivory-coast",
    "MKD": "macedonia", "BOL": "bolivia", "VEN": "venezuela", "TZA": "tanzania",
    "MDA": "moldova", "LAO": "laos", "SYR": "syria", "BRN": "brunei",
    "COD": "congo-democratic-republic", "COG": "congo", "PSE": "palestine",
}


@dataclass(frozen=True)
class Observation:
    value: float
    as_of: str
    source: str
    url: str
    retrieved: str = ""


@dataclass(frozen=True)
class CurrencyResult:
    equity: float
    debt: float
    after_tax_debt: float
    wacc: float
    spread: float


@dataclass(frozen=True)
class Result:
    debt_equity: float
    levered_beta: float
    factor: float
    auto_spread: float
    usd: CurrencyResult
    local: CurrencyResult


def calculate(beta_u: float, tax: float, erp: float, debt_share: float,
              yield_us: float, yield_local: float, spread_us: float,
              spread_override: float | None = None) -> Result:
    """Class packet pp. 24–28. Rates are decimals and beta is unitless."""
    values = [beta_u, tax, erp, debt_share, yield_us, yield_local, spread_us]
    if spread_override is not None:
        values.append(spread_override)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Use finite numbers for all inputs.")
    if not 0 <= debt_share < 1 or not 0 <= tax <= 1:
        raise ValueError("Debt share must be below 100%; tax must be between 0% and 100%.")
    if min(yield_us, yield_local) <= -1:
        raise ValueError("Government yields must be greater than −100%.")
    if min(erp, spread_us, spread_override if spread_override is not None else 0) < 0:
        raise ValueError("Enter nonnegative credit spreads and equity risk premium.")
    de = debt_share / (1 - debt_share)
    beta_e = beta_u * (1 + (1 - tax) * de)
    factor = (1 + yield_local) / (1 + yield_us)
    equity_us = yield_us + beta_e * erp
    equity_local = equity_us if factor == 1 else (1 + equity_us) * factor - 1
    auto_spread = spread_us * factor
    spread_local = auto_spread if spread_override is None else spread_override

    def leg(equity: float, bond_yield: float, spread: float) -> CurrencyResult:
        debt = bond_yield + spread
        after_tax = debt * (1 - tax)
        return CurrencyResult(equity, debt, after_tax,
                              (1 - debt_share) * equity + debt_share * after_tax, spread)

    return Result(de, beta_e, factor, auto_spread, leg(equity_us, yield_us, spread_us),
                  leg(equity_local, yield_local, spread_local))


def fetch(url: str, *, params=None, accept=None) -> requests.Response:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; InternationalFinanceClassApp/1.0)"}
    if accept:
        headers["Accept"] = accept
    response = requests.get(url, params=params, headers=headers, timeout=(5, 14))
    response.raise_for_status()
    return response


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_betas(content: bytes, retrieved: str) -> dict[str, Observation]:
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_name("Industry Averages")
    as_of = "Source date not stated"
    header = None
    for i in range(min(sheet.nrows, 24)):
        row = [str(v).strip().casefold() for v in sheet.row_values(i)]
        if row[0] == "date updated:" and sheet.cell_value(i, 1):
            raw = sheet.cell_value(i, 1)
            as_of = (xlrd.xldate_as_datetime(raw, book.datemode).date().isoformat()
                     if isinstance(raw, (float, int)) else str(raw))
        if "industry name" in row and "unlevered beta" in row:
            header = (i, row.index("industry name"), row.index("unlevered beta"))
            break
    if header is None:
        raise ValueError("Industry Name or Unlevered beta header not found.")
    start, industry_col, beta_col = header
    results = {}
    for i in range(start + 1, sheet.nrows):
        industry = str(sheet.cell_value(i, industry_col)).strip()
        beta = sheet.cell_value(i, beta_col)
        if industry and not industry.casefold().startswith("total market") and isinstance(beta, (float, int)) and math.isfinite(beta):
            results[industry] = Observation(float(beta), as_of, "Damodaran · US industry workbook", BETA_URL, retrieved)
    if SOFTWARE not in results or len(results) < 50:
        raise ValueError("The industry workbook did not contain the expected US sector data.")
    return results


def parse_spread(content: bytes, retrieved: str) -> Observation:
    soup = BeautifulSoup(content, "html.parser")
    whole = " ".join(soup.stripped_strings)
    as_of = re.search(r"Date of Analysis\s*:\s*Data used is as of ([A-Za-z]+\s+\d{4})", whole, re.I)
    for table in soup.find_all("table"):
        if "large non-financial service firms" not in " ".join(table.stripped_strings).casefold():
            continue
        for row in table.find_all("tr"):
            cells = [" ".join(cell.stripped_strings) for cell in row.find_all("td", recursive=False)]
            # The large nonfinancial firm's rating/spread are the first block.
            if len(cells) >= 4 and re.sub(r"\s+", "", cells[2]).upper() == "BAA2/BBB":
                return Observation(float(cells[3].rstrip("%")) / 100,
                                   as_of[1] if as_of else "Source date not stated",
                                   "Damodaran · Baa2/BBB, large nonfinancial firms", SPREAD_URL, retrieved)
    raise ValueError("The Baa2/BBB row for large nonfinancial firms was not found.")


def parse_taxes(content: bytes, retrieved: str) -> dict[str, Observation]:
    results = {}
    for row in csv.DictReader(io.StringIO(content.decode("utf-8-sig"))):
        if (row.get("MEASURE"), row.get("TARGETING"), row.get("SECTOR"), row.get("UNIT_MEASURE")) != (
                "CIT_C", "ST", "S13", "PT_INC_TAX"):
            continue
        code, year, raw = row["REF_AREA"], row["TIME_PERIOD"], row["OBS_VALUE"]
        if not year.isdigit() or int(year) > date.today().year or not raw:
            continue
        value = float(raw) * 10 ** int(row.get("UNIT_MULT") or "0") / 100
        if 0 <= value <= 1 and (code not in results or year > results[code].as_of):
            results[code] = Observation(value, year, "OECD · combined statutory CIT (general government)", TAX_URL, retrieved)
    if "USA" not in results:
        raise ValueError("OECD did not return the combined statutory US corporate tax series.")
    return results


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")


def bond_url(code: str, name: str) -> str:
    return f"{TE_ROOT}/{ALIASES.get(code, slugify(name))}/government-bond-yield"


def iter_json_items(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_json_items(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_json_items(value)


def parse_yield(content: bytes, url: str, retrieved: str) -> Observation:
    soup = BeautifulSoup(content, "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    if not re.search(r"\b10[\s-]*(?:year|y\b)", title, re.I) or not re.search(r"\byield\b", title, re.I):
        raise ValueError("No matching 10-year benchmark yield was found.")
    if re.search(r"inflation.linked|\breal yield\b|\bTIPS\b", title, re.I):
        raise ValueError("The page returned an inflation-linked benchmark.")
    canonical = soup.select_one('link[rel="canonical"]')
    if canonical and canonical.get("href", "").rstrip("/").casefold() != url.rstrip("/").casefold():
        raise ValueError("The bond page points to a different benchmark.")
    quote = soup.select_one("#market_last")
    if quote is None:
        raise ValueError("The public page did not contain an Actual quote.")
    amount = quote.get_text(strip=True).replace(",", "").replace("−", "-").rstrip("%")
    if not re.fullmatch(r"-?\d+(\.\d+)?", amount):
        raise ValueError("The Actual yield was unavailable.")
    value = float(amount) / 100
    if not -1 < value < 5:
        raise ValueError("The quote was outside the supported percentage range.")
    as_of = "Quote date not stated"
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        for item in iter_json_items(data):
            if item.get("@type") != "Dataset":
                continue
            end = str(item.get("temporalCoverage", "")).split("/")[-1]
            publication = str(item.get("dateModified", ""))
            m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", end or publication)
            if m:
                as_of = "-".join(m.groups())
                break
        if as_of != "Quote date not stated":
            break
    return Observation(value, as_of, "Trading Economics · 10-year benchmark (Actual)", url, retrieved)


@st.cache_data(ttl=43200, show_spinner=False)
def beta_data():
    return parse_betas(fetch(BETA_URL).content, timestamp())


@st.cache_data(ttl=43200, show_spinner=False)
def spread_data():
    return parse_spread(fetch(SPREAD_URL).content, timestamp())


@st.cache_data(ttl=43200, show_spinner=False)
def taxes_data():
    query = {"startPeriod": date.today().year - 12, "dimensionAtObservation": "AllDimensions"}
    response = fetch(TAX_API, params=query, accept="text/csv")
    return parse_taxes(response.content, timestamp())


@st.cache_data(ttl=1800, show_spinner=False)
def yield_data(url):
    response = fetch(url)
    if response.url.rstrip("/").casefold() != url.rstrip("/").casefold():
        raise ValueError("Trading Economics redirected away from the requested country benchmark.")
    return parse_yield(response.content, url, timestamp())


def collect(key, loader, *args):
    try:
        return loader(*args), None
    except (requests.RequestException, ValueError, LookupError, TypeError, xlrd.XLRDError) as exc:
        detail = (f"HTTP {exc.response.status_code}" if isinstance(exc, requests.HTTPError) and exc.response is not None
                  else "request timed out" if isinstance(exc, requests.Timeout)
                  else "connection failed" if isinstance(exc, requests.RequestException)
                  else str(exc))
        return None, f"{key}: {detail}"


@st.cache_data(show_spinner=False)
def countries():
    names = Locale.parse("en").territories
    out = {}
    for country in pycountry.countries:
        name = names.get(country.alpha_2, country.name)
        currencies = get_territory_currencies(country.alpha_2, start_date=date.today(), tender=True)
        out[country.alpha_3] = (name, currencies[0] if len(currencies) == 1 else None)
    out["USA"] = ("United States", "USD")
    return dict(sorted(out.items(), key=lambda item: item[1][0].casefold()))


def classroom_example():
    st.session_state.update({
        "country": "COL", "industry": SOFTWARE, "tax_choice": "Selected country",
        "erp": 6.5, "debt_share": 30.0, "beta_toggle": True, "beta_number": 0.8,
        "tax_toggle": True, "tax_number": 35.0, "yield_us_toggle": True, "yield_us_number": 4.0,
        "yield_local_toggle": True, "yield_local_number": 12.0, "spread_us_toggle": True,
        "spread_us_number": 1.5, "spread_local_toggle": False, "example_loaded": True,
    })


def reset_defaults():
    st.session_state.update({
        "country": "USA", "industry": SOFTWARE, "tax_choice": "United States",
        "erp": 6.5, "debt_share": 30.0, "example_loaded": False,
        **{f"{key}_toggle": False for key in ("beta", "tax", "yield_us", "yield_local", "spread_us", "spread_local")},
    })


def refresh_data():
    for loader in (beta_data, spread_data, taxes_data, yield_data):
        loader.clear()


def input_value(key, label, obs, fallback, lo, hi, *, factor=100, help_text=None):
    on = st.checkbox(f"Override {label}", key=f"{key}_toggle", help=help_text)
    if not on:
        return obs
    start = min(hi, max(lo, obs.value * factor if obs else fallback))
    value = st.number_input(f"{label}{' (%)' if factor == 100 else ''}", value=float(start),
                            min_value=float(lo), max_value=float(hi), step=0.01,
                            format="%.4f", key=f"{key}_number")
    return Observation(value / factor, "User-provided", "Manual override", "")


def pct(value):
    return f"{value:.4%}"


def describe(obs, is_rate=True):
    if obs is None:
        return "Unavailable"
    return pct(obs.value) if is_rate else f"{obs.value:.6g}"


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def result_row(industry, code, currency, tax_country, assumptions, erp, d, result):
    row = {"industry": industry, "country_iso3": code, "local_currency": currency,
           "tax_benchmark_country": tax_country, "equity_risk_premium_pct": erp * 100,
           "debt_share_pct": d * 100, "debt_to_equity": result.debt_equity,
           "levered_beta": result.levered_beta, "conversion_factor": result.factor,
           "auto_local_spread_pct": result.auto_spread * 100}
    for k, obs in assumptions.items():
        row[f"{k}{'_pct' if k != 'unlevered_beta' else ''}"] = obs.value * (1 if k == 'unlevered_beta' else 100)
        row[f"{k}_source"] = obs.source
        row[f"{k}_url"] = obs.url
        row[f"{k}_as_of"] = obs.as_of
        row[f"{k}_retrieved_utc"] = obs.retrieved
    for prefix, leg in (("usd", result.usd), ("local", result.local)):
        row.update({f"{prefix}_equity_cost_pct": leg.equity * 100, f"{prefix}_pretax_debt_cost_pct": leg.debt * 100,
                    f"{prefix}_after_tax_debt_cost_pct": leg.after_tax_debt * 100, f"{prefix}_wacc_pct": leg.wacc * 100})
    return row


CSS = """
<style>
.stApp,[data-testid=stAppViewContainer],[data-testid=stHeader]{background:#fff!important;color:#282039;color-scheme:light}
[data-testid=stSidebar]{background:#f6f1fb!important;border-right:1px solid #e8def0}
[data-testid=stSidebar] h2,h1,h2,h3{color:#4e2a84!important;letter-spacing:-.025em}
.block-container{max-width:1190px;padding-top:4.25rem}
.wacc-eyebrow{color:#6e4c96;font-weight:750;letter-spacing:.16em;font-size:.75rem}
.wacc-intro{font-size:1.05rem;color:#5c4a70;margin-bottom:1.2rem}
[data-testid=stMetric]{background:#f8f4fc;border:1px solid #e8ddf2;border-radius:12px;padding:17px 22px}
[data-testid=stMetricValue]{color:#4e2a84!important}
[data-testid=stDownloadButton] button,[data-testid=stBaseButton-primary]{background:#4e2a84!important;color:white!important;border-color:#4e2a84!important}
[data-testid=stDownloadButton] button *,[data-testid=stBaseButton-primary] *{color:white!important}
</style>
"""


def main():
    st.set_page_config(page_title=TITLE, page_icon="◈", layout="wide", initial_sidebar_state="auto")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown('<div class="wacc-eyebrow">INTERNATIONAL FINANCE · PROJECT VALUATION</div>', unsafe_allow_html=True)
    st.title(TITLE)
    st.markdown('<div class="wacc-intro">Compare the same project’s financing costs in US dollars and local currency.</div>', unsafe_allow_html=True)
    lookup = countries()
    with st.sidebar:
        st.subheader("Project setup")
        top = st.columns(2)
        top[0].button("Refresh data", on_click=refresh_data, width="stretch")
        top[1].button("Reset defaults", on_click=reset_defaults, width="stretch")
        st.button("Load Colombia class example", on_click=classroom_example, width="stretch")
        codes = list(lookup)
        code = st.selectbox("Target country", codes, index=codes.index("USA"),
                            format_func=lambda c: lookup[c][0], key="country")
        name, currency = lookup[code]
        if not currency:
            currency = st.text_input("Local currency code", max_chars=3, key=f"currency_{code}",
                                     help="This territory has no unique active tender code. Enter the currency of the nominal benchmark.").strip().upper()
            if not pycountry.currencies.get(alpha_3=currency):
                currency = ""
        erp = st.number_input("Equity risk premium (%)", 0.0, 100.0, 6.5, 0.1, key="erp") / 100
        d = st.number_input("Debt share, D/(D + E) (%)", 0.0, 99.9, 30.0, 0.1, key="debt_share") / 100
        tax_choice = st.radio("Tax benchmark for both columns", ["United States", "Selected country"], key="tax_choice")
    target_url = bond_url(code, name)
    us_url = bond_url("USA", "United States")
    jobs = {"betas": ("Industry beta workbook", beta_data), "taxes": ("OECD tax data", taxes_data),
            "spread": ("USD BBB spread", spread_data), "usd": ("US 10-year yield", yield_data, us_url)}
    if code != "USA" and currency:
        jobs["local"] = (f"{name} 10-year yield", yield_data, target_url)
    with st.spinner("Reading the latest available source data…"):
        with ThreadPoolExecutor(max_workers=5) as pool:
            pending = {key: pool.submit(collect, *call) for key, call in jobs.items()}
            got = {key: future.result() for key, future in pending.items()}
    betas = got["betas"][0] or {}
    taxes = got["taxes"][0] or {}
    usd_quote = got["usd"][0]
    local_quote = usd_quote if code == "USA" else got.get("local", (None, None))[0]
    choices = sorted(set(betas) | {SOFTWARE}, key=str.casefold)
    if st.session_state.get("industry", SOFTWARE) not in choices:
        choices = sorted(set(choices) | {st.session_state["industry"]}, key=str.casefold)
    with st.sidebar:
        industry = st.selectbox("Industry (US benchmark)", choices, index=choices.index(SOFTWARE), key="industry")
        st.divider()
        st.subheader("Manual overrides")
        st.caption("An override replaces that input until you turn it off. Refresh keeps your overrides.")
        tax_code = "USA" if tax_choice == "United States" else code
        beta = input_value("beta", "unlevered beta", betas.get(industry), .8, -25, 25, factor=1)
        tax = input_value("tax", "project tax rate", taxes.get(tax_code), 35, 0, 100)
        y_us = input_value("yield_us", "US 10-year yield", usd_quote, 4, -99.9, 499.99)
        y_loc_default = y_us if code == "USA" else local_quote
        y_local = input_value("yield_local", "target-country 10-year yield", y_loc_default, 12, -99.9, 499.99)
        s_us = input_value("spread_us", "USD BBB spread", got["spread"][0], 1.5, 0, 100)
        auto_obs = None
        if y_us and y_local and s_us:
            auto_obs = Observation(s_us.value * (1+y_local.value)/(1+y_us.value), "Current scenario", "Calculated: USD spread × F", "")
        s_loc = input_value("spread_local", "local BBB spread", auto_obs, 1.5, 0, 5000,
                            help_text="Off uses the class formula. On replaces only the local debt spread.")
        override = bool(st.session_state.get("spread_local_toggle"))
        st.caption(f"Automatic local spread: **{describe(auto_obs)}**")
    if st.session_state.get("example_loaded"):
        st.info("Colombia classroom inputs loaded as manual values. Adjust them to test other scenarios; source data remain visible below.")
    st.subheader(f"{industry} · {name}")
    left, right = st.columns(2)
    result = None
    if currency and all(v is not None for v in (beta, tax, y_us, y_local, s_us, s_loc)):
        try:
            result = calculate(beta.value, tax.value, erp, d, y_us.value, y_local.value, s_us.value,
                               s_loc.value if override else None)
        except ValueError as exc:
            st.error(str(exc))
    if result:
        for col, label, leg in [(left, "US dollar (USD)", result.usd), (right, f"Local currency ({currency})", result.local)]:
            col.markdown(f"### {label}")
            col.metric("Weighted average cost of capital", pct(leg.wacc))
            col.markdown(f"**Cost of equity:** {pct(leg.equity)}  \n**Cost of debt before tax:** {pct(leg.debt)}  \n"
                         f"**Cost of debt after tax:** {pct(leg.after_tax_debt)}  \n**BBB spread applied:** {pct(leg.spread)}")
        st.caption(f"Shared inputs: {pct(tax.value)} project tax · {erp:.2%} equity premium · {d:.1%} debt · "
                   f"levered beta {result.levered_beta:.5f} · conversion factor F = {result.factor:.8f}")
    else:
        needed = [label for label, value in (("industry beta", beta), ("project tax rate", tax), ("US yield", y_us),
                  ("target yield", y_local), ("USD spread", s_us), ("local currency code", currency)) if value is None or value == ""]
        if needed:
            st.warning("Enter the missing inputs to calculate results: " + ", ".join(needed) + ".")

    with st.expander("Inputs, sources & dates", expanded=result is None):
        rows = []
        for label, source, used, is_rate, link in (
            ("Unlevered industry beta", betas.get(industry), beta, False, BETA_URL),
            ("US corporate tax rate", taxes.get("USA"), None, True, TAX_URL),
            ("Target corporate tax rate", taxes.get(code), None, True, TAX_URL),
            ("Project tax used in both currencies", taxes.get(tax_code), tax, True, TAX_URL),
            ("US government yield", usd_quote, y_us, True, us_url),
            ("Target government yield", local_quote, y_local, True, target_url),
            ("USD BBB borrowing spread", got["spread"][0], s_us, True, SPREAD_URL),
            ("Local BBB borrowing spread", auto_obs, s_loc, True, None),
        ):
            rows.append({"Input": label, "Source value": describe(source, is_rate),
                         "Source date / year": source.as_of if source else "—",
                         "Fetched (UTC)": source.retrieved if source and source.retrieved else "—",
                         "Applied value": describe(used, is_rate) if used else "—",
                         "Applied origin": used.source if used else "—", "Source link": link})
        st.dataframe(rows, hide_index=True, width="stretch", column_config={"Source link": st.column_config.LinkColumn(display_text="Open source")})
        for _, error in got.values():
            if error:
                st.caption(error)
        if code not in taxes and got["taxes"][1] is None:
            st.caption(f"No combined general-government statutory OECD rate was returned for {name}. Use the US rate or enter the target rate manually.")
        st.caption("Source date is the observation period or publisher date, not the time we downloaded it. "
                   "Coverage varies by country. Check the bond is nominal and denominated in the intended local currency before using a quote; "
                   "enter a manual benchmark if no comparable quote is available.")
        st.markdown(f"[Trading Economics bond directory]({BONDS_URL})")

    if result:
        selected_inputs = {"unlevered_beta": beta, "project_tax": tax, "us_gov_yield": y_us,
                           "local_gov_yield": y_local, "usd_bbb_spread": s_us, "local_bbb_spread": s_loc}
        selected_row = result_row(industry, code, currency, tax_code, selected_inputs, erp, d, result)
        st.download_button("Download project CSV", csv_bytes([selected_row]),
                           f"capital_cost_project_{code}.csv", "text/csv")
        st.subheader("Compare all industries")
        if betas:
            overview = []
            export = []
            for sector, sector_obs in sorted(betas.items(), key=lambda kv: kv[0].casefold()):
                # A manual beta applies only to the selected industry, never to every sector.
                sector_beta = beta if sector == industry else sector_obs
                r = calculate(sector_beta.value, tax.value, erp, d, y_us.value, y_local.value,
                              s_us.value, s_loc.value if override else None)
                overview.append({"Industry": sector, "Unlevered beta": sector_beta.value,
                                 "Equity · USD": r.usd.equity * 100, f"Equity · Local ({currency})": r.local.equity * 100,
                                 "WACC · USD": r.usd.wacc * 100, f"WACC · Local ({currency})": r.local.wacc * 100})
                export.append(result_row(sector, code, currency, tax_code,
                                         {**selected_inputs, "unlevered_beta": sector_beta}, erp, d, r))
            fmt = {k: st.column_config.NumberColumn(format="%.4f%%") for k in overview[0] if k.startswith(("WACC", "Equity"))}
            fmt["Unlevered beta"] = st.column_config.NumberColumn(format="%.4f")
            st.caption("The table holds financing, tax and market assumptions fixed. A manual beta changes only the selected industry.")
            st.dataframe(pd.DataFrame(overview), hide_index=True, width="stretch", column_config=fmt)
            st.download_button("Download all industries CSV", csv_bytes(export), f"capital_cost_industries_{code}.csv", "text/csv")
        else:
            st.info("The industry workbook is unavailable. Refresh to restore the all-industry comparison.")
    with st.expander("Methodology & assumptions"):
        st.markdown("**One project. Two nominal currencies.** Both columns use the same selected tax rate, debt share, "
                    "US market equity premium and project beta. The app uses government yields as risk-free proxies "
                    "and does not add a separate country equity premium.")
        st.latex(r"\beta_E=\beta_U[1+(1-T)d/(1-d)],\qquad R_{e,USD}=y_{USD}+\beta_E\,ERP")
        st.latex(r"F=\frac{1+y_{local}}{1+y_{USD}},\qquad R_{e,local}=(1+R_{e,USD})F-1")
        st.latex(r"R_{d,USD}=y_{USD}+s_{USD},\qquad R_{d,local}=y_{local}+s_{local},\qquad s_{local,auto}=s_{USD}F")
        st.latex(r"WACC=(1-d)R_e+dR_d(1-T)")
        st.markdown("The class conversion assumes equal real rates and relative purchasing power parity. "
                    "Government yields can include sovereign credit, term and liquidity premia. "
                    "The model assumes zero debt beta and fully usable interest deductions. "
                    "The local spread override affects local debt and local WACC only. Calculations retain full precision.")


if __name__ == "__main__":
    main()
