# International Cost of Capital Lab

An English Streamlit app for International Finance. Compare the same project's cost of equity, cost of debt and weighted average cost of capital (WACC) in USD and a selected country's currency. The default scenario uses Software (System & Application), United States, the US tax rate, a 6.5% equity risk premium and 30% debt.

## Deploy on Streamlit Community Cloud

1. Create a public GitHub repository named `international-cost-of-capital-app`.
2. Upload `app.py`, `requirements.txt`, and this `README.md` to the repository root. If the repository already has a README, replace it with this file. Commit to `main`.
3. In [Streamlit Community Cloud](https://share.streamlit.io/), sign in with GitHub. Click **Create app → Yup, I have an app**. Choose your repository, branch `main`, and file `app.py`. In **Advanced settings**, select **Python 3.12** and leave **Secrets** empty. Click **Deploy**.
4. Open the resulting `.streamlit.app` URL and run the checks below. See the [official deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).

No API key is required for these sources. For optional local use, run `pip install -r requirements.txt` and then `streamlit run app.py`.

## Data and controls

| Input | Source |
|---|---|
| Unlevered industry beta | [Damodaran US industry workbook](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls), **Industry Averages** sheet, **Unlevered beta** column. The cash-corrected column is not used. |
| USD BBB borrowing spread | [Damodaran ratings table](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html), **Baa2/BBB**, large nonfinancial firms. |
| Corporate tax | [OECD Corporate Income Tax Rates Database](https://www.oecd.org/en/data/datasets/corporate-income-tax-rates-database.html), latest reported annual combined statutory rate for general government, where available. This uses the active OECD data series instead of a frozen 2024 report. |
| US and target 10-year yields | [Trading Economics bond directory](https://tradingeconomics.com/bonds) and each country's public **Actual** nominal 10-year benchmark quote. The quote's publication or observation date can differ across countries. |

**Inputs, sources & dates** shows the fetched value, source date/year, retrieval time and value actually applied. Use **Refresh data** to request updated values without disabling manual overrides. Automatic data are cached for 30 minutes for government yields and 12 hours for other sources. Public-source coverage varies, and some countries have no valid benchmark or no applicable OECD rate. In that case, enter the missing value manually. Confirm the quote is nominal and in the intended currency; use a manual benchmark if the public series is not comparable. The app does not substitute fabricated live observations.

**Manual overrides** can replace the selected industry's beta, the shared tax rate, the two government yields, the USD BBB spread and the local BBB spread. If the target is the US and there is no separate target-yield override, the target uses the applied US yield. When the local spread override is off, the automatic value is applied and displayed. When the override is on, the app displays both the automatic value and the applied manual value. The all-industry comparison holds financing assumptions fixed; the beta override applies only to the selected industry. Download the current project or all-industry results, including input provenance, as CSV.

## Class formulas

Based on Sergio Rebelo's *CasePacket2026-part9*, pp. 24–29. Yields and financing costs are nominal and annual. In formulas, rates use decimals, `d` is the target `D/(D+E)`, `T` is the selected corporate tax rate applied to both currencies, and `ERP` is the shared equity risk premium.

```text
D/E             = d / (1 - d)
Levered beta    = Unlevered beta × [1 + (1 - T) × D/E]
USD equity cost = US 10Y yield + Levered beta × ERP
USD debt cost   = US 10Y yield + USD BBB spread
F               = (1 + Local 10Y yield) / (1 + US 10Y yield)
Local equity    = (1 + USD equity cost) × F - 1
Auto local spread = USD BBB spread × F
Local debt cost = Local 10Y yield + Applied local spread
After-tax debt  = Pretax debt cost × (1 - T)
WACC            = (1 - d) × Equity cost + d × After-tax debt cost
```

Compute the WACC separately in each currency. Do not convert WACC directly with `F`. The model uses government yields as risk-free proxies, adds no separate country equity premium, assumes zero debt beta and fully usable interest deductions, and applies the conversion from the class's equal-real-rates and relative-PPP assumptions. Government yields can contain sovereign credit, term and liquidity premia. Matching the discount-rate currency to the cash-flow currency matters: different nominal WACCs alone do not show that the same project is riskier in one currency. Full precision is used for calculations; the app rounds displayed rates.

## Verify the deployment

Start with **Load Colombia class example**, which loads the fixed classroom inputs rather than the latest market values. It uses unlevered beta `0.80`, debt share `30%`, project tax `35%`, ERP `6.5%`, US yield `4%`, Colombia yield `12%`, and USD BBB spread `1.5%`. The automatic local spread is `1.6154%`.

| Check | USD | COP |
|---|---:|---:|
| Equity cost | 10.6486% | 19.1600% |
| Pretax cost of debt | 5.5000% | 13.6154% |
| After-tax cost of debt | 3.5750% | 8.8500% |
| WACC | 8.5265% | 16.0670% |

Then run these three checks. Reload the example between checks to restore the starting inputs.

1. **Equal yields:** Set the target yield to `4%`, matching the US yield. Keep the local spread override off. Both WACCs should read `8.5265%`.
2. **Zero debt:** Set the debt share to `0%`. In each column WACC should equal the cost of equity in that same currency.
3. **Local override:** Set the local BBB spread to `2%`. Local pretax debt becomes `14.0000%` and local WACC becomes `16.1420%`. USD WACC remains `8.5265%`, and equity costs remain unchanged.

For the assignment, include the published app URL, the GitHub repository URL, AI chat snapshots, and a short note documenting your independently reproduced calculation and one app design choice you checked.
