"""One cash ledger for actual stocks and cash-settled European index spreads.

Option quotes are conservative leg-by-leg liquidation values, not complex-order
midpoint fills. Capacity and cash can defer a stop. No code in the original
frozen earnings-options engine is changed by this independent experiment.
"""
from dataclasses import replace
import numpy as np
import pandas as pd
from .policy import LIQUIDITY


class Ledger:
    def __init__(self, initial, policy):
        if not np.isfinite(initial) or initial<=0:raise ValueError('positive capital required')
        self.initial=float(initial);self.policy=policy;self.cash=float(initial)
        self.nav=float(initial);self.options=[];self.stocks=[];self.daily=[];self.trades=[]
        self.cash_interest=0.;self.financing=0.;self.option_cashflows=0.;self.stock_cashflows=0.
        self.stock_fees=0.;self.option_fees=0.;self.premium_spent=0.;self.hedge_budget=0.
        self.missing_marks=0;self.missing_stock_marks=0;self.skips={};self.used={}
        self.stock_pnl_today=0.;self.last_year=None;self.market_history=[];self.stock_history=[]
        self.liquidity=replace(LIQUIDITY,small_lot_floor=policy.small_lot_floor)

    @property
    def collateral(self):
        return sum(p['width']*100*p['lots'] for p in self.options if p['role']=='carry')

    @property
    def stock_value(self):return sum(p['value'] for p in self.stocks)

    @property
    def exit_reserve(self):
        return sum(self.policy.commission*len(p['structure'].legs)*p['lots'] for p in self.options)

    def skip(self,reason):self.skips[reason]=self.skips.get(reason,0)+1

    def _quotes(self,q):
        if q.optionid.duplicated().any():raise ValueError('duplicate option quotes')
        return {int(r.optionid):r for r in q.itertuples(index=False)}

    def _valid(self,r):
        return (r is not None and bool(r.standard_contract) and
                np.isfinite([r.bid,r.ask]).all() and 0<=r.bid<=r.ask)

    def _capacity(self,p):
        caps=[]
        for leg in p['structure'].legs:
            r=self.q.get(leg.optionid)
            if not self._valid(r) or not np.isfinite([r.prior_volume,r.prior_oi]).all():return 0
            cap=min(np.floor(self.liquidity.oi_participation*r.prior_oi),
                    max(self.liquidity.small_lot_floor,np.floor(self.liquidity.participation*r.prior_volume)))
            caps.append(int(cap)-self.used.get(leg.optionid,0))
        return max(0,min(caps))

    def mark_option(self,p):
        value=0.
        for leg in p['structure'].legs:
            r=self.q.get(leg.optionid)
            if not self._valid(r):
                self.missing_marks+=1
                return (-p['width']*100*p['lots'] if p['role']=='carry' else 0.),False
            value+=leg.quantity*(r.bid if leg.quantity>0 else r.ask)*100*p['lots']
        return value,True

    def current_nav(self):
        return self.cash+self.stock_value+sum(self.mark_option(p)[0]-self.policy.commission*len(p['structure'].legs)*p['lots'] for p in self.options)

    def begin(self,date,rf,market,quotes,stock_returns,spot,settlements=None,
              stock_capacity=None,funding_rf=None,quote_lookup=None,stock_prices=None):
        """Apply beginning cash carry, actual stock returns and native resolution."""
        if not np.isfinite([rf,market,spot]).all() or spot<=0:raise ValueError('invalid market inputs')
        self.date=pd.Timestamp(date);self.q=self._quotes(quotes) if quote_lookup is None else quote_lookup
        self.shared_quotes=quote_lookup is not None;self.chain=quotes;self.used={};self.spot=spot
        self.stock_capacity=stock_capacity;self.used_stock={}
        self.previous_nav=self.nav;self.stock_pnl_today=0.;self.market=market;self.rf=rf
        if self.last_year!=self.date.year:
            self.hedge_budget=self.previous_nav*self.policy.annual_premium
            self.premium_spent=0.;self.last_year=self.date.year
        free=self.cash-self.collateral
        if free>=0:
            interest=free*rf;self.cash+=interest;self.cash_interest+=interest
        else:
            cost=-free*(max(0.,rf if funding_rf is None else funding_rf)+self.policy.financing_spread/252)
            self.cash-=cost;self.financing+=cost
        for p in self.stocks:
            ret=stock_returns.get(p['permno'],np.nan)
            if not np.isfinite(ret) or ret < -1:
                if not p.get('impaired',False):
                    self.stock_pnl_today-=p['value'];p['value']=0.;p['impaired']=True
                    self.missing_stock_marks+=1
            else:
                pnl=p['value']*ret;p['value']+=pnl;self.stock_pnl_today+=pnl
                price=(stock_prices or {}).get(p['permno'],np.nan)
                if np.isfinite(price) and price>0 and p['value']>0:
                    # Rebase total-return/DRIP units to raw contemporaneous price
                    # so splits and reverse splits do not corrupt share rounding.
                    p['shares']=p['value']/price
        for p in list(self.stocks):
            if self.date>=p['exit_date'] or p.get('pending'):
                fraction=(1. if self.date>=p['exit_date'] else
                          min(1.,p.get('pending_shares',p['shares'])/p['shares']))
                self.close_stock(p,'terminal_reserve' if p.get('impaired') else
                                 (p.get('pending') or 'time'),fraction)
        for p in list(self.options):
            if self.date>=p['settlement_date']:
                value=(settlements or {}).get(p['structure'].expiry)
                if value is None:raise ValueError('missing actual SET resolution')
                self.close_option(p,quotes,'SET',settlement=value)
            elif p.get('pending'):
                self.close_option(p,quotes,p['pending'])
        self.nav=self.current_nav()
        if self.nav<=0:raise ValueError('insolvent account')

    def delta_units(self,role):
        """Known prior-session delta; incomplete Greek history is not zero risk."""
        value=0.
        for p in self.options:
            if p['role']!=role:continue
            for leg in p['structure'].legs:
                r=self.q.get(leg.optionid)
                delta=getattr(r,'prior_delta',np.nan) if r is not None else np.nan
                if not np.isfinite(delta):return np.nan
                value+=leg.quantity*delta*100*p['lots']
        return value

    def carry_room(self,expiry,delta_per_lot):
        """Original-loss admission caps; not a promise about future drawdown."""
        nav=self.current_nav();carry=[p for p in self.options if p['role']=='carry']
        week=self.date.to_period('W-FRI')
        total=sum(p['risk_unit']*p['lots'] for p in carry)
        cohort=sum(p['risk_unit']*p['lots'] for p in carry if p['entry_date'].to_period('W-FRI')==week)
        exp=sum(p['risk_unit']*p['lots'] for p in carry if p['structure'].expiry==expiry)
        scale=1.
        if self.policy.market_vol_target and len(self.market_history)>=20:
            vol=np.std(self.market_history[-21:],ddof=1)*np.sqrt(252)
            scale=min(1.,self.policy.market_vol_target/max(vol,1e-12))
        room=max(0.,min(nav*self.policy.ticket*scale-cohort,
                       nav*self.policy.aggregate*scale-total,nav*self.policy.expiry_limit*scale-exp))
        if self.policy.require_hedge:
            hd=self.delta_units('hedge');cd=self.delta_units('carry')
            if not np.isfinite([hd,cd]).all() or -hd < self.policy.hedge_cover*(cd+delta_per_lot)-1e-9:
                return 0.
        return room

    def open_option(self,st,lots,role,settlement_date,quotes):
        if role not in ('carry','hedge') or lots<1:return 0
        if pd.Timestamp(settlement_date)<=self.date:raise ValueError('resolution must follow entry')
        width=max(x.strike for x in st.legs)-min(x.strike for x in st.legs)
        p=dict(structure=st,lots=int(lots),role=role,width=width)
        available=self._capacity(p)
        if available<1:self.skip('option_capacity');return 0
        flow=0.
        for leg in st.legs:
            r=self.q.get(leg.optionid)
            if not self._valid(r):self.skip('invalid_entry_quote');return 0
            fill=r.ask*(1+self.policy.slippage) if leg.quantity>0 else r.bid*(1-self.policy.slippage)
            flow-=leg.quantity*fill*100
        fee=self.policy.commission*len(st.legs)
        risk_unit=(width*100-flow if role=='carry' else -flow)+2*fee
        if risk_unit<=0 or (role=='carry' and flow<=0) or (role=='hedge' and flow>=0):return 0
        free=self.cash-self.collateral-self.exit_reserve
        lots=min(int(lots),available,max(0,int(free//risk_unit)))
        if role=='carry':
            delta=sum(x.quantity*x.delta*100 for x in st.legs)
            lots=min(lots,max(0,int(self.carry_room(st.expiry,delta)//risk_unit)))
            if self.policy.require_hedge:
                hd=self.delta_units('hedge');cd=self.delta_units('carry')
                if not np.isfinite([hd,cd]).all() or delta<=0:lots=0
                else:lots=min(lots,max(0,int(((-hd/self.policy.hedge_cover)-cd)//delta)))
        if role=='hedge':lots=min(lots,max(0,int((self.hedge_budget-self.premium_spent)//risk_unit)))
        if lots<1:self.skip('option_cash_or_hedge_budget');return 0
        p.update(lots=lots,risk_unit=risk_unit,entry_flow=flow,entry_fee=fee,
                 entry_date=self.date,settlement_date=pd.Timestamp(settlement_date),pending=None)
        self.options.append(p)
        cashflow=(flow-fee)*lots;self.cash+=cashflow;self.option_cashflows+=cashflow
        self.option_fees+=fee*lots
        if role=='hedge':self.premium_spent+=(-flow+2*fee)*lots
        for leg in st.legs:self.used[leg.optionid]=self.used.get(leg.optionid,0)+lots
        return lots

    def close_option(self,p,quotes,reason,settlement=None):
        if p not in self.options:return 0
        if not (self.shared_quotes and self.chain is quotes):self.q=self._quotes(quotes)
        if settlement is not None:
            value=sum(leg.quantity*max(0.,leg.strike-settlement if leg.cp=='P' else settlement-leg.strike)
                      for leg in p['structure'].legs)*100*p['lots'];valid=True
        else:value,valid=self.mark_option(p)
        if not valid:p['pending']=reason;self.skip('deferred_missing_exit');return 0
        original=p['lots'];lots=original if settlement is not None else min(original,self._capacity(p))
        if settlement is None:
            value=0.
            for leg in p['structure'].legs:
                r=self.q[leg.optionid]
                fill=r.bid*(1-self.policy.slippage) if leg.quantity>0 else r.ask*(1+self.policy.slippage)
                value+=leg.quantity*fill*100*original
            # A legged close beyond terminal width can require extra free cash.
            own_width=p['width']*100 if p['role']=='carry' else 0.
            fee_unit=self.policy.commission*len(p['structure'].legs)
            need=max(0.,-value/original+fee_unit-own_width)
            other_reserve=self.exit_reserve-fee_unit*original
            spendable=max(0.,self.cash-self.collateral-other_reserve)
            if need>0:lots=min(lots,max(0,int(spendable//need)))
        if lots<1:p['pending']=reason;self.skip('deferred_capacity_exit');return 0
        fee=self.policy.commission*len(p['structure'].legs)*lots
        proceeds=value*lots/original-fee
        self.cash+=proceeds;self.option_cashflows+=proceeds;self.option_fees+=fee
        self.trades.append(dict(kind=p['role'],entry_date=p['entry_date'],exit_date=self.date,
            quantity=lots,pnl=(p['entry_flow']-p['entry_fee'])*lots+proceeds,reason=reason,
            optionids=','.join(str(x.optionid) for x in p['structure'].legs),
            expiry=p['structure'].expiry))
        if settlement is None:
            for leg in p['structure'].legs:self.used[leg.optionid]=self.used.get(leg.optionid,0)+lots
        p['lots']-=lots
        if p['lots']==0:self.options.remove(p)
        else:p['pending']=reason
        return lots

    def open_stock(self,permno,price,desired,cost,exit_date,adv):
        if not np.isfinite([price,desired,cost,adv]).all() or min(price,adv)<=0 or cost<0:
            self.skip('invalid_stock_entry');return 0.
        free=self.cash-self.collateral-self.exit_reserve
        leverage=max(0.,self.policy.equity_scale-1.)*self.previous_nav
        capacity=np.inf if self.stock_capacity is None else max(0.,self.stock_capacity.get(int(permno),0.)-self.used_stock.get(int(permno),0.))
        dollars=min(desired,adv*.05,capacity,max(0.,(free+leverage)/(1+cost)))
        # A levered control pays its entry costs in addition to the target debt.
        if self.policy.equity_scale>1 and desired<=free+leverage:dollars=min(desired,adv*.05,capacity)
        shares=int(max(0.,dollars)//price);value=shares*price
        if value<=0:self.skip('stock_cash_or_rounding');return 0.
        fee=value*cost;self.cash-=value+fee;self.stock_cashflows-=value+fee;self.stock_fees+=fee
        self.stocks.append(dict(permno=int(permno),value=value,initial=value,cost=cost,
            entry_date=self.date,exit_date=pd.Timestamp(exit_date),shares=shares))
        self.used_stock[int(permno)]=self.used_stock.get(int(permno),0.)+value
        return value

    def close_stock(self,p,reason,fraction=1.):
        if not 0<fraction<=1:raise ValueError('invalid stock sale fraction')
        requested=(p['shares'] if fraction==1. else
                   min(p['shares'],np.ceil(p['shares']*fraction-1e-10)))
        executed=requested
        if self.stock_capacity is not None and p['value']>0:
            room=max(0.,self.stock_capacity.get(p['permno'],0.)-self.used_stock.get(p['permno'],0.))
            shares=min(p['shares'],np.floor(room/(p['value']/p['shares'])))
            executed=min(requested,shares)
            if executed<=0:
                p['pending']=reason;p['pending_shares']=requested
                self.skip('deferred_stock_capacity');return 0.
        fraction=executed/p['shares']
        sold_shares=executed
        value=p['value']*fraction;fee=value*p['cost'];proceeds=value-fee
        self.cash+=proceeds;self.stock_cashflows+=proceeds;self.stock_fees+=fee
        self.trades.append(dict(kind='stock',entry_date=p['entry_date'],exit_date=self.date,
            quantity=p['shares']*fraction,pnl=proceeds-p['initial']*(1+p['cost'])*fraction,
            reason=reason,permno=p['permno']))
        p['value']-=value;p['initial']*=1-fraction;p['shares']*=1-fraction
        self.used_stock[p['permno']]=self.used_stock.get(p['permno'],0.)+value
        if fraction==1:self.stocks.remove(p)
        elif sold_shares+1e-8<requested:
            p['pending']=reason;p['pending_shares']=requested-sold_shares
        else:
            p.pop('pending',None);p.pop('pending_shares',None)
        return value

    def reconciliation(self):
        return self.cash-(self.initial+self.cash_interest-self.financing+
                          self.option_cashflows+self.stock_cashflows)

    def finish(self):
        self.nav=self.current_nav()
        if self.nav<=0:raise ValueError('insolvent account')
        ret=self.nav/self.previous_nav-1
        self.stock_history.append(self.stock_pnl_today/self.previous_nav)
        self.market_history.append(self.market)
        carry_risk=sum(p['risk_unit']*p['lots'] for p in self.options if p['role']=='carry')
        remaining=sum(max(0.,p['width']*100*p['lots']+self.mark_option(p)[0])
                      for p in self.options if p['role']=='carry')
        self.daily.append(dict(date=self.date,nav=self.nav,ret=ret,rf=self.rf,benchmark=self.market,
            cash=self.cash,free_cash=self.cash-self.collateral,stock_value=self.stock_value,
            option_value=self.nav-self.cash-self.stock_value,collateral=self.collateral,
            carry_admission_risk=carry_risk,carry_risk_nav=carry_risk/self.nav,
            carry_remaining_loss=remaining,carry_remaining_loss_nav=remaining/self.nav,
            hedge_delta=self.delta_units('hedge'),carry_delta=self.delta_units('carry'),
            cash_interest_cumulative=self.cash_interest,financing_cumulative=self.financing,
            hedge_premium_ytd=self.premium_spent,stock_positions=len(self.stocks),
            option_positions=len(self.options),exit_fee_reserve=self.exit_reserve,
            hedge_coverage=(-self.delta_units('hedge')/self.delta_units('carry')
                if self.delta_units('carry')>0 else np.nan),cash_residual=self.reconciliation()))
