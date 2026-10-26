"""Research commands: python -m pead earnings-options audit|prepare|backtest."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

from .data import audit,prepare
from .engine import Config,run
from .validation import evaluate,purged_folds


ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CACHE=ROOT/"data/selective_earnings"


def load_prepared(cache,start,end):
    cache=Path(cache)
    events=[];quotes=[];manifests=[]
    for year in range(start,end+1):
        p=cache/f"prepared_{year}.json"
        if not p.exists(): raise FileNotFoundError(f"{p}: prepare every requested year first; gaps cannot be silently replaced with cash")
        meta=json.loads(p.read_text())
        if meta.get("max_events") is not None:
            raise ValueError("limited-event smoke cache cannot be used for a performance backtest; choose a fresh cache directory")
        manifests.append(meta)
        events.append(pd.read_parquet(cache/f"events_{year}.parquet"))
        quotes.append(pd.read_parquet(cache/f"quotes_{year}.parquet"))
    e=pd.concat(events,ignore_index=True)
    q=pd.concat(quotes,ignore_index=True).drop_duplicates(["date","secid","optionid"])
    b=pd.read_parquet(cache/"benchmark.parquet")
    b=b[b.date.between(pd.Timestamp(f"{start}-01-01"),pd.Timestamp(f"{end}-12-31"))].copy()
    # Full observation windows are required. Last entries are stopped in advance
    # using the declared sample boundary, never their realized trade outcomes.
    if len(b)<40: raise ValueError("insufficient session calendar")
    e=e[e.entry_date<=b.date.iloc[-22]].copy()
    return e,q,b,manifests


def backtest(cache,start,end,output,strict=True,sleeves=("directional","condor","reversal"),initial_nav=1_000_000):
    if start<=2011<=end: raise ValueError("2011 option data is missing; evaluate contiguous segments separately")
    e,q,b,manifest=load_prepared(cache,start,end)
    cfg=Config(strict=strict,sleeves=sleeves,initial_nav=initial_nav)
    result=run(e,q,b,cfg)
    stats=evaluate(result.daily,result.diagnostics["execution_valid"],True)
    # This adapter does not provide assignment simulation or point-in-time
    # calendar/identifier links; zero trades cannot evade these requirements.
    stats["gates"]["canonical_provenance"]=bool(strict and len(result.trades)>0 and result.diagnostics["execution_valid"])
    stats["gates"]["minimum_100_trades"]=len(result.trades)>=100
    stats["gates"]["multifactor_and_issuer_cluster_inference"]=False
    stats["gates"]["genuine_untouched_holdout"]=False
    stats["accepted"]=all(stats["gates"].values())
    stats["diagnostics"]=result.diagnostics
    stats["trade_count"]=len(result.trades)
    stats["skip_counts"]=result.skips.reason.value_counts().to_dict()
    stats["parameters"]=asdict(cfg)
    stats["source_fingerprints"]=[x["fingerprint"] for x in manifest]
    stats["pandas_version"]=pd.__version__
    code=hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        code.update(path.name.encode());code.update(path.read_bytes())
    stats["research_code_sha256"]=code.hexdigest()
    stats["sleeve_pnl"]=(result.trades.groupby("sleeve").pnl.sum().to_dict() if len(result.trades) else {})
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    result.daily.to_csv(out/"daily.csv.gz",index=False)
    result.trades.to_csv(out/"trades.csv.gz",index=False)
    result.skips.to_csv(out/"skips.csv.gz",index=False)
    (out/"summary.json").write_text(json.dumps(stats,indent=2),encoding="utf-8")
    folds=[]
    all_events=pd.read_parquet(Path(cache)/"all_events.parquet")
    for f in purged_folds(all_events):
        dates=result.daily.date
        test=result.daily[(dates>=pd.Timestamp(f["test_start"]))&(dates<pd.Timestamp(f["test_end"]))]
        if len(test)<100:continue
        score=evaluate(test,result.diagnostics["execution_valid"],True,repetitions=250)
        folds.append(dict(test_start=f["test_start"],test_end=f["test_end"],train_events=len(f["train"]),
                          validation_events=len(f["validation"]),test_events=len(f["test"]),metrics=score))
    (out/"walk_forward.json").write_text(json.dumps(folds,indent=2),encoding="utf-8")
    yearly=[]
    for year,g in result.daily.groupby(result.daily.date.dt.year):
        from .validation import metrics
        st=metrics(g.ret);bm=metrics(g.benchmark)
        t=result.trades[result.trades.exit_date.dt.year==year] if len(result.trades) else result.trades
        yearly.append(dict(year=int(year),strategy_cagr=st["cagr"],spy_cagr=bm["cagr"],
                           positions=len(t),net_pnl=float(t.pnl.sum()) if len(t) else 0.))
    pd.DataFrame(yearly).to_csv(out/"yearly.csv",index=False)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(10,5),layout="constrained")
    for col,label in (("ret","Selective earnings options, net"),("benchmark","SPY total return")):
        ax.plot(result.daily.date,(1+result.daily[col]).cumprod(),label=label)
    ax.set(yscale="log",ylabel="Growth of $1",title="Development research: executable quotes, exploratory provenance" if not strict else "Strict provenance research")
    ax.legend();ax.grid(alpha=.2)
    fig.savefig(out/"equity_curve.png",dpi=160);plt.close(fig)
    s,bench=stats["strategy"],stats["benchmark"]
    text=f"""# Selective Earnings Options: research result

Status: **{'PASS' if stats['accepted'] else 'NOT APPROVED FOR INVESTMENT'}**.
Mode: {'strict' if strict else 'exploratory proxies'}; sample {b.date.min().date()} to {b.date.max().date()}.
Fixed parameters; expanding matured-history price model; no test-period parameter search.

| Metric | Strategy, net | SPY total return |
|---|---:|---:|
| CAGR | {s['cagr']:.2%} | {bench['cagr']:.2%} |
| Sharpe (zero cash rate) | {s['sharpe']:.2f} | {bench['sharpe']:.2f} |
| Maximum drawdown | {s['max_drawdown']:.2%} | {bench['max_drawdown']:.2%} |

Closed positions: {len(result.trades)}. Market-model alpha: {stats['alpha']:.2%}/year.
Config fingerprint: `{cfg.fingerprint()}`.

![Development equity curve](equity_curve.png)

## Interpretation

Daily EOD buys at ask and sales at bid; $0.65/contract/side commissions; integer lots;
previous-session volume and OI; maximum-loss and collateral budgets; idle cash earns zero.
Commission is an assumption, not a broker schedule. Missing marks receive contractual
worst-loss reserves and invalidate execution. These are not asserted trade fills.
Assignment, corporate-action and historical calendar/identifier validation remain open.
The reversal sleeve cannot trade without a historical known-at-the-time schedule and
independent event-window price model. This adapter deliberately does not invent them.
Market alpha is not multifactor alpha. Paired monthly-block intervals do not replace
issuer/date cluster inference. This is development research, not an untouched holdout.

## Investment gates

"""
    text+="\n".join(f"- {key}: {'pass' if value else 'FAIL'}" for key,value in stats["gates"].items())
    text+="\n\n## Skipped candidates\n\n"+"\n".join(f"- {key}: {value:,}" for key,value in stats["skip_counts"].items())
    (out/"report.md").write_text(text+"\n",encoding="utf-8")
    return stats,result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    for command in ("audit","prepare"):
        s=sub.add_parser(command)
        s.add_argument("--data-dir",type=Path,required=True,help="original data directory, read only")
        s.add_argument("--om-dir",type=Path,default=Path("D:/OptionMetrics/parquet"))
        s.add_argument("--cache",type=Path,default=DEFAULT_CACHE)
        if command=="prepare":
            s.add_argument("--start",type=int,default=1999)
            s.add_argument("--end",type=int,default=2010)
            s.add_argument("--max-events",type=int,help="smoke test only; use separate cache")
    s=sub.add_parser("backtest")
    s.add_argument("--cache",type=Path,default=DEFAULT_CACHE)
    s.add_argument("--start",type=int,default=1999)
    s.add_argument("--end",type=int,default=2010)
    s.add_argument("--exploratory",action="store_true")
    s.add_argument("--sleeves",nargs="+",choices=["directional","condor","reversal"],default=["directional","condor","reversal"])
    s.add_argument("--initial-nav",type=float,default=1_000_000)
    s.add_argument("--output",type=Path,default=ROOT/"results/selective_earnings_options")
    args=p.parse_args(argv)
    if getattr(args,"end",0)>=2012:
        p.error("2012–2013 are reserved for a future frozen final holdout; do not inspect them during development")
    if args.command=="audit":
        result=audit(args.data_dir,args.om_dir)
        args.cache.mkdir(parents=True,exist_ok=True)
        (args.cache/"audit.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result,indent=2))
    elif args.command=="prepare":
        if args.start>args.end:p.error("start must be <= end")
        prepare(args.data_dir,args.om_dir,args.cache,range(args.start,args.end+1),args.max_events)
    else:
        stats,_=backtest(args.cache,args.start,args.end,args.output,strict=not args.exploratory,
                         sleeves=tuple(args.sleeves),initial_nav=args.initial_nav)
        print(json.dumps({k:stats[k] for k in ("strategy","benchmark","trade_count","accepted","gates")},indent=2))


if __name__=="__main__":main()
