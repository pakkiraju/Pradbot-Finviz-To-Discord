"""Canonical registry of Finviz scans posted to Discord.

Fully self-contained — all export and screener URLs are inlined here so there
is zero dependency on the Market Metrics Dashboard codebase.

Each entry maps a scan_id (matching webhooks.json keys) to:
  - title:        Human-readable name shown in the Discord embed.
  - export_urls:  FinViz Elite export.ashx URL(s) that return CSV data.
  - screener_url: FinViz screener.ashx URL for the "View on FinViz" embed link.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScanDef:
    scan_id: str
    title: str
    export_urls: list[str] = field(default_factory=list)
    screener_url: str = ""


SCANS: list[ScanDef] = [
    # --- Qullamaggie (three sub-scans) ---
    ScanDef(
        scan_id="qulla_episodic",
        title="Qullamaggie \u2014 Episodic Pivot",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=geo_usa,ta_gap_u10,sh_relvol_o2,sh_price_o1,sh_avgvol_o1000&o=-change&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=141&f=geo_usa%2Cta_gap_u10%2Csh_relvol_o2%2Csh_price_o1%2Csh_avgvol_o1000",
    ),
    ScanDef(
        scan_id="qulla_breakouts",
        title="Qullamaggie \u2014 Breakouts",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=geo_usa,sh_avgvol_o1000,sh_price_o1,ta_highlow52w_0to25-bhx,ta_perf_30to-4w,tad_0_close::close:d|abvpct::10:|sma:20:sma:d&o=-change&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=141&f=geo_usa,sh_avgvol_o1000,sh_price_o1,ta_highlow52w_0to25-bhx,ta_perf_30to-4w,tad_0_close::close:d|abvpct::10:|sma:20:sma:d&ft=3&o=-change",
    ),
    ScanDef(
        scan_id="qulla_parabolic_short",
        title="Qullamaggie \u2014 Parabolic Short",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_largeover,geo_usa,ta_perf_50to-4w&o=-change&c=1,47,61,62,63,64,65",
            "https://elite.finviz.com/export.ashx?v=141&f=cap_to9,geo_usa,ta_perf_300to-4w,ta_perf2_100to-1w&ft=4&o=-change&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=141&f=cap_to9,geo_usa,ta_perf_300to-4w,ta_perf2_100to-1w&ft=4&o=-change",
    ),
    # --- Jeff Sun ---
    ScanDef(
        scan_id="jeff_sun_canslim",
        title="Jeff Sun \u2014 CANSLIM",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_midover,fa_salesqoq_high,fa_salesyoyttm_high,sh_avgvol_500to,sh_curvol_o2000,sh_insttrans_pos,ta_highlow20d_a5h,ta_highlow50d_a5h,ta_volatility_wo4&ft=4&o=-change&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_midover,fa_salesqoq_high,fa_salesyoyttm_high,sh_avgvol_500to,sh_curvol_o2000,sh_insttrans_pos,ta_highlow20d_a5h,ta_highlow50d_a5h,ta_volatility_wo4&ft=4",
    ),
    ScanDef(
        scan_id="jeff_sun_high_adr",
        title="Jeff Sun \u2014 High ADR% Hottest Stock",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_midover,geo_usa,sh_avgvol_500to,sh_curvol_o2000,sh_relvol_o2,ta_volatility_wo10&ft=4&o=-change&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_midover,geo_usa,sh_avgvol_500to,sh_curvol_o2000,sh_relvol_o2,ta_volatility_wo10&ft=4",
    ),
    ScanDef(
        scan_id="jeff_sun_extended_bases",
        title="Jeff Sun \u2014 Extended Bases",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_smallover,sh_avgvol_o1000,sh_curvol_o1000,sh_insttrans_pos,sh_price_o1,ta_alltime_b70h,ta_highlow50d_a15h,ta_highlow52w_b30h,ta_perf_ytddown,ta_sma200_-20to20-a,ta_volatility_wo4&ft=4&o=-change&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_smallover,sh_avgvol_o1000,sh_curvol_o1000,sh_insttrans_pos,sh_price_o1,ta_alltime_b70h,ta_highlow50d_a15h,ta_highlow52w_b30h,ta_perf_ytddown,ta_sma200_-20to20-a,ta_volatility_wo4&ft=4",
    ),
    ScanDef(
        scan_id="jeff_sun_1w20",
        title="Jeff Sun \u2014 Strongest 1-Week +20%",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_1w20o,ta_volatility_wo4&ft=4&o=-marketcap&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_1w20o,ta_volatility_wo4&ft=4&o=-marketcap",
    ),
    ScanDef(
        scan_id="jeff_sun_4w30",
        title="Jeff Sun \u2014 Strongest 1-Month +30%",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_4w30o,ta_volatility_mo5&ft=4&o=-marketcap&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_4w30o,ta_volatility_mo5&ft=4&o=-marketcap",
    ),
    ScanDef(
        scan_id="jeff_sun_4w50",
        title="Jeff Sun \u2014 Strongest 1-Month +50%",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_4w50o,ta_volatility_mo5&ft=4&o=-marketcap&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_4w50o,ta_volatility_mo5&ft=4&o=-marketcap",
    ),
    ScanDef(
        scan_id="jeff_sun_13w50",
        title="Jeff Sun \u2014 Strongest 3-Month +50%",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_13w50o,ta_volatility_mo5&ft=4&o=-marketcap&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_13w50o,ta_volatility_mo5&ft=4&o=-marketcap",
    ),
    ScanDef(
        scan_id="jeff_sun_26w100",
        title="Jeff Sun \u2014 Strongest 6-Month +100%",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_26w100o,ta_volatility_mo5&ft=4&o=-marketcap&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_smallover,sh_avgvol_o300,sh_curvol_o100,ta_perf_26w100o,ta_volatility_mo5&ft=4&o=-marketcap",
    ),
    ScanDef(
        scan_id="jeff_sun_ipo",
        title="Jeff Sun \u2014 IPO",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_midover,fa_epsyoy1_pos,geo_usa,ipodate_prevyear,sh_avgvol_o1000&ft=4&o=industry&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=cap_midover,fa_epsyoy1_pos,geo_usa,ipodate_prevyear,sh_avgvol_o1000&ft=4&o=industry",
    ),
    ScanDef(
        scan_id="jeff_sun_high_short_float",
        title="Jeff Sun \u2014 High Short Float",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=131&f=cap_smallover,ind_stocksonly,sh_avgvol_o1000,sh_float_u100,sh_short_o30&ft=4&c=1,32,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=131&f=cap_smallover,ind_stocksonly,sh_avgvol_o1000,sh_float_u100,sh_short_o30&ft=4",
    ),
    ScanDef(
        scan_id="jeff_sun_liquid_etfs",
        title="Jeff Sun \u2014 Liquid ETFs",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=111&f=ind_exchangetradedfund,sh_avgvol_o1000,ta_volatility_wo3&ft=4&o=-volume&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=ind_exchangetradedfund,sh_avgvol_o1000,ta_volatility_wo3&ft=4&o=-volume",
    ),
    # --- Julian Komar ---
    ScanDef(
        scan_id="julian_komar_strongest",
        title="Julian Komar \u2014 Strongest Stocks",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=cap_smallover,ind_stocksonly,sh_avgvol_o100,sh_price_o7,ta_highlow52w_a70h,ta_sma50_pa&ft=4&o=-low52w&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=211&f=cap_smallover,ind_stocksonly,sh_avgvol_o100,sh_price_o7,ta_highlow52w_a70h,ta_sma50_pa&ft=4&ta=0&p=w&o=-low52w",
    ),
    # --- Earnings Calendar ---
    ScanDef(
        scan_id="earnings_calendar_week",
        title="Earnings Calendar \u2014 This Week",
        export_urls=[
            "https://elite.finviz.com/export.ashx?v=141&f=earningsdate_thisweek,geo_usa,sh_avgvol_o1000,sh_price_o1&ft=4&o=-marketcap&c=1,47,61,62,63,64,65",
        ],
        screener_url="https://elite.finviz.com/screener.ashx?v=111&f=earningsdate_thisweek,geo_usa,sh_avgvol_o1000,sh_price_o1&ft=4&o=-marketcap",
    ),
]

SCAN_BY_ID: dict[str, ScanDef] = {s.scan_id: s for s in SCANS}
