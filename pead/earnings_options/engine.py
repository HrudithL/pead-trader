"""Conservative EOD ledger for the RESEARCH.md portfolio, independent of old engines."""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import numpy as np
import pandas as pd

from .structures import select_structure
from .quote_store import YearQuoteStore


@dataclass(frozen=True)
class Config:
    initial_nav: float = 1_000_000
    hold_sessions: int = 20
    commission: float = .65
    directional_risk: float = .003
    condor_risk: float = .002
    reversal_risk: float = .002
    issuer_risk: float = .01
    sector_risk: float = .05
    aggregate_risk: float = .25
    cohort_risk: float = .05
    participation: float = .05
    rank_tail: float = .10
    iv_rv_threshold: float = 1.30
    min_adv: float = 25_000_000
    require_price_edge: bool = True
    min_edge_fraction: float = .05
    strict: bool = True
    current_risk_budget: bool = False
    expiry_buffer_days: int = 7
    condor_stops: bool = True
    defer_unquoted_exits: bool = False
    cash_settlement: bool = False
    settlement_risk_exit: bool = False
    sleeves: tuple = ("directional", "condor", "reversal")

    def __post_init__(self):
        if self.cash_settlement and self.current_risk_budget:
            raise ValueError("cash settlement does not support automatic risk rebalancing")
        if self.settlement_risk_exit and not self.cash_settlement:
            raise ValueError("settlement risk exit requires cash settlement")
        numbers=[v for v in asdict(self).values() if isinstance(v,(float,int))]
        if not all(np.isfinite(x) for x in numbers):
            raise ValueError("config must be finite")
        if self.initial_nav<=0 or self.hold_sessions<1 or self.commission<0 or self.expiry_buffer_days<1:
            raise ValueError("invalid capital, holding period or commission")
        for name in ("directional_risk","condor_risk","reversal_risk","issuer_risk",
                     "sector_risk","aggregate_risk","cohort_risk","participation"):
            if not 0<getattr(self,name)<=1:
                raise ValueError(f"invalid risk fraction: {name}")
        if not 0<self.rank_tail<.5 or self.iv_rv_threshold<0 or self.min_adv<0 or self.min_edge_fraction<0:
            raise ValueError("invalid signal parameters")
        if not self.sleeves or set(self.sleeves)-{"directional","condor","reversal"}:
            raise ValueError("unsupported sleeves")

    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class Result:
    daily: pd.DataFrame
    trades: pd.DataFrame
    skips: pd.DataFrame
    diagnostics: dict


def run(events, quotes, benchmark, config=Config(), selector=None, selector_context=False, corporate_actions=None, settlements=None, exit_capacity=None):
    """Entries and exits occur at EOD; same-close fills use lagged liquidity only.

    Benchmark date rows are the authoritative session calendar. Missing quotes are
    charged contractual worst liquidation value and counted as execution failures.
    This is a risk reserve, not an asserted executable fill. Output names make this
    distinction explicit. No forward-filled marks or independent event compounding.
    """
    cfg=config
    streaming=isinstance(quotes,YearQuoteStore)
    e,b=events.copy(),benchmark.copy()
    q=None if streaming else quotes.copy()
    required_e={"event_id","secid","sector","entry_date","release_date","rank","reaction","rv","adv"}
    required_q={"date","secid","optionid","cp","strike","expiry","bid","ask","delta",
                "prior_volume","prior_oi","iv","standard_contract"}
    inputs=[(e,required_e),(b,{"date","benchmark","rf"})]
    if not streaming:inputs.append((q,required_q))
    for frame,required in inputs:
        if required-set(frame):
            raise ValueError(f"missing columns: {sorted(required-set(frame))}")
    for col in ("entry_date","release_date","scheduled_date"):
        if col in e:
            e[col]=pd.to_datetime(e[col]).dt.normalize()
    if not streaming:
        for col in ("date","expiry"):
            q[col]=pd.to_datetime(q[col]).dt.normalize()
    b["date"]=pd.to_datetime(b.date).dt.normalize()
    if b.empty or b.date.duplicated().any() or (not streaming and q.duplicated(["secid","date","optionid"]).any()) or e.event_id.duplicated().any():
        raise ValueError("empty calendar or duplicate input keys")
    b=b.sort_values("date").reset_index(drop=True)
    if not np.isfinite(b[["benchmark","rf"]].to_numpy(dtype=float)).all() or (b[["benchmark","rf"]]<=-1).any().any():
        raise ValueError("benchmark and rf must be finite returns > -1")
    if e.entry_date.isna().any() or e.release_date.isna().any():
        raise ValueError("event dates must be nonmissing")
    if not e.entry_date.isin(b.date).all():
        raise ValueError("entry date outside calendar")
    if streaming:
        quotes.prepare(e)
        quote_lookup=quotes.quotes;qgroups=quotes.groups
    else:
        quote_lookup={(r.date,r.secid,int(r.optionid)):r for r in q.itertuples(index=False)}
        entry_keys=pd.MultiIndex.from_frame(e[["entry_date","secid"]].rename(columns={"entry_date":"date"}))
        entry_quotes=q[pd.MultiIndex.from_frame(q[["date","secid"]]).isin(entry_keys)]
        qgroups={k:v for k,v in entry_quotes.groupby(["date","secid"],sort=False)}
    egroups={k:v for k,v in e.groupby("entry_date",sort=False)}
    calendar=b.date.tolist()
    settlement_lookup={}
    if cfg.cash_settlement:
        if settlements is None or {'secid','expiry','settlement_date','value'}-set(settlements):
            raise ValueError("cash settlement requires explicit resolution data")
        resolved=settlements.copy()
        for col in ('expiry','settlement_date'):
            resolved[col]=pd.to_datetime(resolved[col]).dt.normalize()
        if (resolved.duplicated(['secid','expiry']).any() or
            resolved[['expiry','settlement_date']].isna().any().any() or
            not np.isfinite(resolved.value).all() or (resolved.value<=0).any() or
            (resolved.settlement_date>resolved.expiry).any()):
            raise ValueError("invalid or ambiguous cash settlement")
        settlement_lookup={(r.secid,r.expiry):(r.settlement_date,float(r.value)) for r in resolved.itertuples()}
    action_days={}
    if corporate_actions is not None:
        a=corporate_actions.copy()
        if {'secid','known_date','effective_date'}-set(a):raise ValueError("invalid corporate-action schema")
        for col in ('known_date','effective_date'):a[col]=pd.to_datetime(a[col]).dt.normalize()
        if a[['known_date','effective_date']].isna().any().any() or (a.known_date>a.effective_date).any():raise ValueError("invalid corporate-action dates")
        for day in calendar:
            action_days[day]=set(a.loc[(a.known_date<=day)&(a.effective_date>=day),'secid'])
    open_book=[]; trades=[]; skips=[]; daily=[]
    entry_leg_usage={}
    cash=float(cfg.initial_nav); prior_nav=cash
    diag=dict(missing_marks=0,execution_valid=True,unverified_events=0,
              assignment_unverified=0,calendar_unverified=0,forced_reserves=0,
              config_hash=cfg.fingerprint(),strict=cfg.strict)

    def risk_value(position):
        return position.get("risk_mark",position["risk"]) if cfg.current_risk_budget else position["risk"]

    def skip(row,sleeve,reason):
        skips.append(dict(event_id=row.event_id,date=row.entry_date,sleeve=sleeve,reason=reason))

    quote_failure_today=False

    def mark(position,date):
        nonlocal diag,quote_failure_today
        if cfg.cash_settlement and date>=position['settlement_date']:
            spot=position['settlement_value']
            value=sum(leg.quantity*max(0.,spot-leg.strike if leg.cp=='C' else leg.strike-spot)
                      for leg in position['structure'].legs)*100*position['contracts']
            return value,False
        value=0.;valid=True
        for leg in position["structure"].legs:
            row=quote_lookup.get((date,position["secid"],leg.optionid))
            if row is None or not (np.isfinite(row.bid) and np.isfinite(row.ask) and 0<=row.bid<=row.ask and row.standard_contract):
                valid=False;break
            current_factor=float(getattr(row,"cfadj",1.))
            entry_factor=position["adjustment_factors"].get(leg.optionid,1.)
            ratio=current_factor/entry_factor
            if not np.isfinite(ratio) or ratio<=0:
                valid=False;break
            value+=leg.quantity*(row.bid if leg.quantity>0 else row.ask)*ratio
        if valid:
            st=position["structure"]
            if len(st.legs)==1:
                leg=st.legs[0]
                if leg.quantity==1 and leg.cp=="C":
                    upper=float(getattr(row,"underlying_price",np.nan))*ratio
                    if not np.isfinite(upper) or upper<=0:valid=False
                elif leg.quantity==1 and leg.cp=="P":upper=leg.strike
                else:
                    upper=0.
                    if leg.cp=="P" and leg.quantity==-1 and value < -leg.strike-.01:valid=False
            else:upper=abs(st.legs[0].strike-st.legs[1].strike) if st.cashflow<0 else 0.
            if value>upper+.01:valid=False
        if valid:
            total=value*100*position["contracts"]
            if cfg.current_risk_budget:
                st=position["structure"]
                worst=(-st.max_loss-st.cashflow)*100*position["contracts"]
                position["risk_mark"]=max(0.,total-worst+2*position["exit_fee"])
            return total,False
        if cfg.current_risk_budget:position["risk_mark"]=position["risk"]
        quote_failure_today=True
        diag["missing_marks"]+=1
        diag["execution_valid"]=False
        s=position["structure"]
        return (-s.max_loss-s.cashflow)*100*position["contracts"],True

    for idx,day in b.iterrows():
        date=day.date
        quote_failure_today=False
        collateral=sum(p["collateral"] for p in open_book)
        cash_interest=max(0.,cash-collateral)*getattr(day,"cash_rf",day.rf)
        cash+=cash_interest
        risk_total=0.;risk_ceiling=np.inf
        if cfg.current_risk_budget and open_book:
            prior_values=[mark(p,date)[0]-p["exit_fee"] for p in open_book]
            marked_nav=cash+sum(prior_values)
            risk_total=sum(risk_value(p) for p in open_book)
            risk_ceiling=max(0.,marked_nav*cfg.aggregate_risk)
        retained=[]
        remaining_collateral=sum(p["collateral"] for p in open_book)
        for p in open_book:
            value,missing=mark(p,date)
            liquidation=value-p["exit_fee"]
            age=idx-p["entry_idx"]
            exit_reason=p.get("pending_exit_reason")
            if idx>=p["exit_idx"]: exit_reason="time"
            if date>=p["expiry_stop"]: exit_reason="expiry_buffer"
            if pd.notna(p["scheduled_date"]) and date>=p["scheduled_stop"]: exit_reason="known_next_earnings"
            if p["sleeve"]=="condor" and cfg.condor_stops:
                pnl=p["entry_cashflow"]+liquidation-p["entry_fee"]
                credit=p["entry_cashflow"]
                if pnl>=.5*credit: exit_reason="profit"
                if pnl<=-1.75*credit: exit_reason="loss"
            if p["secid"] in action_days.get(date,set()):exit_reason="announced_corporate_action"
            if getattr(day,"risk_off",False):exit_reason="risk_off"
            signal=int(getattr(day,"direction_signal",0))
            if signal and p.get("direction",0) not in (0,signal):exit_reason="regime_change"
            if cfg.cash_settlement:
                # A declared hold-to-settlement position cannot be liquidated after
                # its last trading session. It resolves to the actual cash payoff.
                if date>=p['settlement_date']:exit_reason="cash_settlement"
                elif cfg.settlement_risk_exit and (getattr(day,'risk_off',False) or p.get('pending_exit_reason')=='risk_off'):
                    exit_reason="risk_off"
                else:exit_reason=None
            elif idx>=p["last_trade_idx"]:exit_reason="contract_terminal"
            if idx==len(b)-1: exit_reason="sample_end"
            if cfg.current_risk_budget and risk_total>risk_ceiling and not exit_reason:exit_reason="risk_rebalance"
            if exit_reason and missing and cfg.defer_unquoted_exits and idx<min(len(b)-1,p["last_trade_idx"]):
                if exit_reason!="risk_rebalance":p["pending_exit_reason"]=exit_reason
                retained.append(p)
                continue
            if exit_reason:
                original_contracts=p["contracts"]
                closed=original_contracts
                if exit_reason=="risk_rebalance":
                    unit_risk=risk_value(p)/original_contracts
                    closed=min(original_contracts,max(1,int(np.ceil((risk_total-risk_ceiling)/unit_risk))))
                if exit_capacity is not None and exit_reason!='cash_settlement' and not missing:
                    live=pd.DataFrame([quote_lookup.get((date,p['secid'],leg.optionid))._asdict()
                                       for leg in p['structure'].legs])
                    limit=exit_capacity(live,dict(date=date,used_lots=entry_leg_usage,structure=p['structure']))
                    if not np.isfinite(limit) or limit<0 or limit!=int(limit):
                        raise ValueError("invalid exit capacity")
                    closed=min(closed,int(limit))
                    # Release only this position's collateral. A wide executable
                    # exit can cost more than terminal width; it must not spend
                    # collateral reserved for the remaining book.
                    unit_need=max(0.,-(liquidation+p['collateral'])/original_contracts)
                    if unit_need>0:
                        funded=int(max(0.,cash-remaining_collateral)//unit_need)
                        closed=min(closed,funded)
                    if closed<1:
                        if exit_reason!='risk_rebalance':p['pending_exit_reason']=exit_reason
                        retained.append(p)
                        continue
                fraction=closed/original_contracts
                risk_total-=risk_value(p)*fraction
                cash+=liquidation*fraction
                remaining_collateral-=p["collateral"]*fraction
                if exit_capacity is not None and exit_reason!='cash_settlement' and not missing:
                    for leg in p['structure'].legs:
                        key=(date,leg.optionid)
                        entry_leg_usage[key]=entry_leg_usage.get(key,0)+closed*abs(leg.quantity)
                if missing:diag["forced_reserves"]+=1
                record={k:p[k] for k in ("event_id","secid","sector","sleeve","entry_date","contracts","entry_cashflow","entry_fee","risk")}
                record["contracts"]=closed
                for key in ("entry_cashflow","entry_fee","risk"):record[key]*=fraction
                trades.append(record|dict(exit_date=date,exit_value=value*fraction,exit_fee=p["exit_fee"]*fraction,
                    pnl=(p["entry_cashflow"]+liquidation-p["entry_fee"])*fraction,
                    reason=exit_reason,exit_is_worst_loss_reserve=missing,age_sessions=age,
                    optionids=",".join(str(x.optionid) for x in p["structure"].legs),
                    selection_key=p.get("selection_key"),direction=p.get("direction"),source_event_id=p.get("source_event_id",p["event_id"])))
                if closed<original_contracts:
                    if exit_reason!='risk_rebalance':p['pending_exit_reason']=exit_reason
                    remainder=1-fraction
                    p["contracts"]=original_contracts-closed
                    for key in ("entry_cashflow","entry_fee","exit_fee","risk","collateral","risk_mark"):
                        if key in p:p[key]*=remainder
                    retained.append(p)
            else:
                retained.append(p)
        open_book=retained
        marks=[mark(p,date)[0]-p["exit_fee"] for p in open_book]
        nav=cash+sum(marks)
        if nav<=0: raise ValueError("portfolio insolvent")
        today=egroups.get(date)
        if today is not None and idx<len(b)-1:
            ordered=today.sort_values(["entry_priority","event_id"],ascending=[False,True]) if "entry_priority" in today else today.sort_values("event_id")
            for row in ordered.itertuples():
                flags=("timing_verified","consensus_verified","identity_verified","corporate_actions_verified")
                verified=all(getattr(row,k,False) is True or getattr(row,k,False)==True for k in flags)
                for sleeve in cfg.sleeves:
                    if row.secid in action_days.get(date,set()):
                        skip(row,sleeve,"announced_corporate_action");continue
                    if exit_capacity is not None and any(p.get('pending_exit_reason') for p in open_book):
                        skip(row,sleeve,"pending_capacity_exit");continue
                    if cfg.defer_unquoted_exits and quote_failure_today:
                        skip(row,sleeve,"unquoted_open_position");continue
                    if not verified:
                        if cfg.strict:
                            skip(row,sleeve,"unverified_provenance"); continue
                    if not np.isfinite(row.adv) or row.adv<cfg.min_adv:
                        skip(row,sleeve,"underlying_liquidity");continue
                    if sleeve=="reversal":
                        if not getattr(row,"schedule_known",False) or not date<row.release_date:
                            skip(row,sleeve,"no_asof_pre_event_schedule");continue
                        rank=getattr(row,"prior_rank",np.nan)
                        if not np.isfinite(rank) or cfg.rank_tail<rank<1-cfg.rank_tail:
                            skip(row,sleeve,"no_extreme_prior_signal");continue
                        direction=-1 if rank>=1-cfg.rank_tail else 1
                    else:
                        if not date>row.release_date:
                            skip(row,sleeve,"release_not_before_entry_session");continue
                        if sleeve=="directional":
                            if not np.isfinite(row.rank) or not np.isfinite(row.reaction):
                                skip(row,sleeve,"missing_signal");continue
                            direction=1 if row.rank>=1-cfg.rank_tail else -1 if row.rank<=cfg.rank_tail else 0
                            learned=selector is not None and hasattr(row,"trade_direction")
                            if learned:direction=int(row.trade_direction)
                            if direction not in (-1,1) or (not learned and direction*row.reaction<=0):
                                skip(row,sleeve,"unconfirmed_direction");continue
                        else:
                            if not np.isfinite(row.rv) or row.rv<=0:
                                skip(row,sleeve,"missing_rv");continue
                            direction=1
                    ch=qgroups.get((date,row.secid))
                    if ch is None:
                        skip(row,sleeve,"no_chain");continue
                    ch=ch[ch.standard_contract.eq(True)]
                    choice_cap=None
                    planned_exit=None
                    selection_key=None
                    if selector is None:
                        structure=select_structure(ch,sleeve,direction)
                        edge=getattr(row,sleeve+"_edge",np.nan)
                    else:
                        if selector_context:
                            fraction=getattr(cfg,sleeve+"_risk")
                            room=min(nav*fraction,
                                nav*cfg.aggregate_risk-sum(risk_value(p) for p in open_book),
                                nav*cfg.issuer_risk-sum(risk_value(p) for p in open_book if p["secid"]==row.secid),
                                nav*cfg.sector_risk-sum(risk_value(p) for p in open_book if p["sector"]==row.sector),
                                nav*cfg.cohort_risk-sum(risk_value(p) for p in open_book if p["entry_date"]==date))
                            context=dict(nav=nav,risk_budget=room,free_cash=cash-sum(p["collateral"] for p in open_book),
                                         commission=cfg.commission,used_lots=entry_leg_usage,date=date)
                            units=0.;greeks_complete=True
                            for held in open_book:
                                if held["secid"]!=row.secid:continue
                                for leg in held["structure"].legs:
                                    live=quote_lookup.get((date,row.secid,leg.optionid))
                                    if live is None or not np.isfinite(live.delta):greeks_complete=False;continue
                                    factor=float(getattr(live,"cfadj",1.))/held["adjustment_factors"].get(leg.optionid,1.)
                                    units+=leg.quantity*live.delta*100*held["contracts"]*factor
                            context["open_delta_units"]=units
                            context["greeks_complete"]=greeks_complete
                            if getattr(row,"portfolio_stock_limit",0)>0:
                                bound=0.;complete=True
                                for held in open_book:
                                    for leg in held["structure"].legs:
                                        if held["entry_date"]==date:
                                            known_spot=held.get("entry_spot",np.nan)
                                            basis=quote_lookup.get((date,held["secid"],leg.optionid))
                                        else:
                                            basis=quote_lookup.get((calendar[max(0,idx-1)],held["secid"],leg.optionid))
                                            known_spot=float(getattr(basis,"underlying_price",np.nan)) if basis is not None else np.nan
                                        factor=float(getattr(basis,"cfadj",1.))/held["adjustment_factors"].get(leg.optionid,1.) if basis is not None else np.nan
                                        if not np.isfinite(known_spot) or known_spot<=0 or not np.isfinite(factor) or factor<=0:complete=False;continue
                                        bound+=abs(leg.quantity)*100*held["contracts"]*known_spot*factor
                                context["open_stock_notional_bound"]=bound
                                context["stock_notional_complete"]=complete
                            choice=selector(ch,sleeve,direction,row,context)
                        else:choice=selector(ch,sleeve,direction,row)
                        if choice is None:structure,edge,choice_cap=None,np.nan,0
                        else:
                            structure,edge,choice_cap=choice[:3]
                            planned_exit=pd.Timestamp(choice[3]) if len(choice)>3 else None
                            if len(choice)>4:direction=int(choice[4])
                            if len(choice)>5:selection_key=str(choice[5])
                    if structure is None:
                        skip(row,sleeve,"no_liquid_structure");continue
                    if selector is not None:
                        actual=0.
                        for leg in structure.legs:
                            matched=ch[ch.optionid==leg.optionid]
                            if len(matched)!=1:raise ValueError("selector references unquoted contract")
                            quote=matched.iloc[0]
                            if not (np.isclose(quote.bid,leg.bid) and np.isclose(quote.ask,leg.ask)):
                                raise ValueError("selector entry cashflow uses mismatched quotes")
                            actual-=leg.quantity*(quote.ask if leg.quantity>0 else quote.bid)
                        if not np.isclose(actual,structure.cashflow):
                            raise ValueError("selector entry cashflow differs from quoted execution")
                        width=max(x.strike for x in structure.legs)-min(x.strike for x in structure.legs)
                        if sleeve=="condor":
                            width=max(structure.legs[1].strike-structure.legs[0].strike,structure.legs[3].strike-structure.legs[2].strike)
                        if len(structure.legs)==1 and actual>0:
                            leg=structure.legs[0]
                            if leg.cp!='P' or leg.quantity!=-1:raise ValueError("unsupported single short option")
                            risk=leg.strike-actual
                        else: risk=width-actual if actual>0 else -actual
                        if risk<=0 or not np.isclose(risk,structure.max_loss):raise ValueError("selector maximum loss differs from payoff")
                    if cfg.require_price_edge and (not np.isfinite(edge) or edge<cfg.min_edge_fraction):
                        skip(row,sleeve,"forecast_does_not_cover_option_cost");continue
                    if sleeve=="condor":
                        ids=[x.optionid for x in structure.legs if x.quantity<0]
                        iv=ch[ch.optionid.isin(ids)].iv.mean()
                        if not np.isfinite(iv) or iv/row.rv<cfg.iv_rv_threshold:
                            skip(row,sleeve,"iv_not_rich");continue
                    settlement_date=None;settlement_value=None
                    if cfg.cash_settlement:
                        resolution=settlement_lookup.get((row.secid,structure.expiry))
                        if resolution is None:
                            raise ValueError("missing cash settlement for selected contract")
                        settlement_date,settlement_value=resolution
                        if settlement_date<=date or settlement_date not in calendar:
                            raise ValueError("cash settlement must follow entry within sample")
                    nlegs=len(structure.legs)
                    fee=cfg.commission*nlegs
                    risk_unit=structure.max_loss*100+2*fee
                    risk_fraction=getattr(cfg,sleeve+"_risk")
                    book_risk=sum(risk_value(p) for p in open_book)
                    issuer=sum(risk_value(p) for p in open_book if p["secid"]==row.secid)
                    sector=sum(risk_value(p) for p in open_book if p["sector"]==row.sector)
                    cohort=sum(risk_value(p) for p in open_book if p["entry_date"]==date)
                    budget=min(nav*risk_fraction,nav*cfg.aggregate_risk-book_risk,
                               nav*cfg.issuer_risk-issuer,nav*cfg.sector_risk-sector,
                               nav*cfg.cohort_risk-cohort)
                    collateral_unit=(structure.max_loss+structure.cashflow)*100 if structure.cashflow>0 else 0.
                    free=cash-sum(p["collateral"] for p in open_book)
                    cash_unit=collateral_unit-structure.cashflow*100+2*fee
                    capacity=int(min(x.volume for x in structure.legs)*cfg.participation) if choice_cap is None else int(choice_cap)
                    available=max(0,min(capacity-entry_leg_usage.get((date,leg.optionid),0) for leg in structure.legs))
                    contracts=max(0,min(int(budget//risk_unit),int(free//cash_unit),available))
                    if contracts<1:
                        skip(row,sleeve,"risk_cash_or_capacity");continue
                    if not verified:
                        diag["execution_valid"]=False;diag["unverified_events"]+=1
                    if not getattr(row,"assignment_verified",False):
                        diag["assignment_unverified"]+=1;diag["execution_valid"]=False
                    scheduled=getattr(row,"scheduled_date",pd.NaT)
                    known=getattr(row,"schedule_known",False)
                    if not known:
                        scheduled=pd.NaT
                        if not getattr(row,"earnings_calendar_not_applicable",False):
                            diag["calendar_unverified"]+=1;diag["execution_valid"]=False
                    expiry_stop=structure.expiry-pd.Timedelta(days=cfg.expiry_buffer_days)
                    if sleeve=="reversal":
                        # Quotes after the actual release may be used only as exits.
                        ex=int(np.searchsorted(calendar,row.release_date,side="right"))
                        expiry_stop=structure.expiry-pd.Timedelta(days=1)
                        scheduled=pd.NaT
                    else:
                        ex=idx+cfg.hold_sessions
                        if planned_exit is not None:
                            if planned_exit<=date or planned_exit>structure.expiry or planned_exit not in calendar:
                                raise ValueError("invalid predeclared exit date")
                            ex=int(np.searchsorted(calendar,planned_exit,side="left"))
                    if cfg.cash_settlement:expiry_stop=settlement_date
                    if expiry_stop<=date:
                        skip(row,sleeve,"insufficient_expiry_buffer");continue
                    factors={int(r.optionid):float(getattr(r,"cfadj",1.)) for r in ch.itertuples() if int(r.optionid) in {x.optionid for x in structure.legs}}
                    if any(not np.isfinite(v) or v<=0 for v in factors.values()):
                        skip(row,sleeve,"invalid_adjustment_factor");continue
                    p=dict(event_id=row.event_id,source_event_id=getattr(row,"source_event_id",row.event_id),secid=row.secid,sector=row.sector,sleeve=sleeve,
                           entry_spot=float(getattr(row,"spot",np.nan)),adjustment_factors=factors,selection_key=selection_key,direction=direction,
                           structure=structure,contracts=contracts,risk=risk_unit*contracts,
                           collateral=collateral_unit*contracts,entry_date=date,entry_idx=idx,exit_idx=ex,
                           expiry_stop=expiry_stop,
                           last_trade_idx=min(len(calendar)-1,max(0,int(np.searchsorted(calendar,structure.expiry-pd.Timedelta(days=getattr(row,"last_trade_buffer_days",1)),side="right"))-1)),scheduled_date=scheduled,
                           scheduled_stop=calendar[max(0,int(np.searchsorted(calendar,scheduled,side="left"))-1)] if pd.notna(scheduled) else pd.NaT,
                           entry_cashflow=structure.cashflow*100*contracts,entry_fee=fee*contracts,exit_fee=fee*contracts)
                    if cfg.cash_settlement:
                        p['settlement_date']=settlement_date
                        p['settlement_value']=settlement_value
                        p['last_trade_idx']=calendar.index(settlement_date)
                    cash+=p["entry_cashflow"]-p["entry_fee"]
                    open_book.append(p)
                    for leg in structure.legs:
                        key=(date,leg.optionid)
                        entry_leg_usage[key]=entry_leg_usage.get(key,0)+contracts*abs(leg.quantity)
                    nav=cash+sum(mark(x,date)[0]-x["exit_fee"] for x in open_book)
        nav=cash+sum(mark(p,date)[0]-p["exit_fee"] for p in open_book)
        collateral=sum(p["collateral"] for p in open_book)
        daily.append(dict(date=date,nav=nav,ret=nav/prior_nav-1,cash=cash,free_cash=cash-collateral,
                          reserved=sum(risk_value(p) for p in open_book),positions=len(open_book),
                          benchmark=day.benchmark,rf=day.rf,cash_interest=cash_interest))
        prior_nav=nav
    return Result(pd.DataFrame(daily),pd.DataFrame(trades),pd.DataFrame(skips,columns=["event_id","date","sleeve","reason"]),diag)
