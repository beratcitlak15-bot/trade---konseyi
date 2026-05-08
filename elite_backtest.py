"""
ELITE SNIPER — STANDALONE BACKTEST
TwelveData kullanmaz. Sadece Dukascopy XLS okur.
Kullanim: python elite_backtest.py
"""
import glob, json, os
from datetime import datetime, UTC, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

BACKTEST_REPORT = "backtest_report.json"
BACKTEST_TXT = "backtest_results.txt"
BOT_STATE_FILE = "bot_state.json"

BACKTEST_PAIRS = [
    "EUR/USD","GBP/USD","USD/JPY","AUD/USD",
    "USD/CHF","USD/CAD","EUR/AUD","GBP/JPY","EUR/JPY",
]
DUKASCOPY_TF_MAP = {"5min":"5 Mins","15min":"15 Mins","1h":"Hourly","4h":"4 Hours","1week":"Weekly"}
DUKASCOPY_SYM_MAP = {"EUR/USD":"EURUSD","GBP/USD":"GBPUSD","USD/JPY":"USDJPY","AUD/USD":"AUDUSD","USD/CHF":"USDCHF","USD/CAD":"USDCAD","EUR/AUD":"EURAUD","GBP/JPY":"GBPJPY","EUR/JPY":"EURJPY"}
MIN_SIGNAL_SCORE = 68
MIN_SCORE_OUT_KZ = 75
MIN_RR = 1.4
MAX_BARS_MIT = 5
ENTRY_DIST_MULT = 1.20
TP_PROG_BLOCK = 0.65
MIN_TP_MULT = 1.4
MIN_SL_MULT = 0.6
MIN_REM_TP_MULT = 1.2
OTE_MIN = 0.62
OTE_MAX = 0.79
IFVG_LB = 20
LON_OPEN = 6
LON_CLOSE = 10
SMT_REL: Dict[str, List[Tuple[str,str]]] = {
    "EUR/USD":[("GBP/USD","positive"),("USD/CHF","inverse"),("EUR/JPY","positive")],
    "GBP/USD":[("EUR/USD","positive"),("USD/CHF","inverse"),("GBP/JPY","positive")],
    "USD/JPY":[("USD/CHF","positive"),("GBP/JPY","positive"),("EUR/JPY","positive")],
    "AUD/USD":[("EUR/AUD","inverse"),("USD/CAD","inverse")],
    "USD/CHF":[("USD/JPY","positive"),("EUR/USD","inverse"),("GBP/USD","inverse")],
    "USD/CAD":[("AUD/USD","inverse")],
    "EUR/AUD":[("AUD/USD","inverse")],
    "GBP/JPY":[("GBP/USD","positive"),("USD/JPY","positive"),("EUR/JPY","positive")],
    "EUR/JPY":[("EUR/USD","positive"),("USD/JPY","positive"),("GBP/JPY","positive")],
}

def is_jpy(s): return "JPY" in s.upper()
def pdec(s): return 3 if is_jpy(s) else 5
def pip(s): return 0.01 if is_jpy(s) else 0.0001
def rp(v,s=""): return None if v is None else float(f"{v:.{pdec(s)}f}")
def now(): return datetime.now(UTC)

def session(dt):
    if not dt: return "Unknown"
    h=dt.hour
    if h<6: return "Asia"
    if h<12: return "London"
    if h<17: return "New York"
    return "Off Session"

def kz_label(dt=None):
    h=(dt or now()).hour
    if LON_OPEN<=h<LON_CLOSE: return "London Killzone"
    if 12<=h<16: return "New York Killzone"
    return "Killzone Disi"

def kz_active(dt=None): return kz_label(dt)!="Killzone Disi"

def parse_dt(s):
    s=s.strip().strip('"')
    for fmt in ("%Y.%m.%d %H:%M:%S","%Y.%m.%d %H:%M","%Y.%m.%d"):
        try:
            d=datetime.strptime(s,fmt)
            return d.replace(tzinfo=timezone(timedelta(hours=2))).astimezone(UTC).replace(tzinfo=UTC)
        except: pass
    return None

def find_file(sym,tf):
    s=DUKASCOPY_SYM_MAP.get(sym,""); t=DUKASCOPY_TF_MAP.get(tf,"")
    if not s or not t: return None
    for pat in [f"{s}_{t}_Bid*.xls",f"{s}*{t}*Bid*.xls"]:
        files=glob.glob(pat)
        if files: return max(files,key=os.path.getsize)
    return None

def read_xls(fp):
    candles=[]
    try:
        with open(fp,"r",encoding="utf-8",errors="ignore") as f: raw=f.read()
        lines=raw.strip().splitlines(); start=0
        for i,l in enumerate(lines):
            if "Time" in l and "Open" in l: start=i+1; break
        for line in lines[start:]:
            line=line.strip()
            if not line: continue
            p=line.split(";") if ";" in line else line.split(",")
            if len(p)<5: continue
            try:
                dt=parse_dt(p[0])
                if not dt: continue
                o,h,l,c=float(p[1]),float(p[2]),float(p[3]),float(p[4])
                candles.append({"datetime":dt.strftime("%Y-%m-%d %H:%M:%S"),"dt":dt,"open":o,"high":h,"low":l,"close":c})
            except: continue
    except Exception as e: print(f" Hata ({fp}): {e}")
    candles.sort(key=lambda x:x["dt"])
    return candles

def load_data():
    print("\n Dukascopy XLS yukleniyor..."); print("="*50)
    m={}
    for sym in BACKTEST_PAIRS:
        print(f"\n {sym}"); m[sym]={}
        for tf in DUKASCOPY_TF_MAP:
            fp=find_file(sym,tf)
            if fp:
                c=read_xls(fp); m[sym][tf]=c
                print(f" OK {tf}: {len(c)} bar")
            else:
                m[sym][tf]=[]
                print(f" -- {tf}: bulunamadi")
    return m

def cb(c): return abs(c["close"]-c["open"])
def cr(c): return c["high"]-c["low"]
def bull(c): return c["close"]>c["open"]
def bear(c): return c["close"]<c["open"]
def avg_body(cs,n=10):
    s=cs[-n:] if len(cs)>=n else cs
    return sum(cb(x) for x in s)/len(s) if s else 0.0
def avg_rng(cs,n=10):
    s=cs[-n:] if len(cs)>=n else cs
    return sum(cr(x) for x in s)/len(s) if s else 0.0

def ph(cs,i,l=2,r=2):
    if i-l<0 or i+r>=len(cs): return False
    cur=cs[i]["high"]
    return all(cs[j]["high"]<cur for j in range(i-l,i+r+1) if j!=i)
def pl(cs,i,l=2,r=2):
    if i-l<0 or i+r>=len(cs): return False
    cur=cs[i]["low"]
    return all(cs[j]["low"]>cur for j in range(i-l,i+r+1) if j!=i)
def swings(cs,l=2,r=2):
    H,L=[],[]
    for i in range(len(cs)):
        if ph(cs,i,l,r): H.append({"i":i,"price":cs[i]["high"],"dt":cs[i]["datetime"]})
        if pl(cs,i,l,r): L.append({"i":i,"price":cs[i]["low"],"dt":cs[i]["datetime"]})
    return H,L
def l2(items):
    if len(items)>=2: return items[-2],items[-1]
    if len(items)==1: return None,items[-1]
    return None,None
def apeq(a,b,t=0.0008): return False if a==0 or b==0 else abs(a-b)/max(abs(a),abs(b))<=t
def eq_highs(cs):
    H,_=swings(cs)
    if len(H)<2: return None
    r=H[-6:]
    for i in range(len(r)):
        for j in range(i+1,len(r)):
            if apeq(r[i]["price"],r[j]["price"]): return max(r[i]["price"],r[j]["price"])
    return None
def eq_lows(cs):
    _,L=swings(cs)
    if len(L)<2: return None
    r=L[-6:]
    for i in range(len(r)):
        for j in range(i+1,len(r)):
            if apeq(r[i]["price"],r[j]["price"]): return min(r[i]["price"],r[j]["price"])
    return None

def htf_bias(cs):
    H,L=swings(cs,3,3)
    h1,h2=l2(H); l1,l2_=l2(L)
    if not h2 or not l2_: return "Notr"
    if h1 and l1 and h2["price"]>h1["price"] and l2_["price"]>l1["price"]: return "Yukselis"
    if h1 and l1 and h2["price"]<h1["price"] and l2_["price"]<l1["price"]: return "Dusus"
    return "Notr"

def mss_choch(cs):
    H,L=swings(cs); h1,h2=l2(H); l1,l2_=l2(L)
    if not h2 or not l2_: return "Yok","Yok"
    lc=cs[-1]["close"]
    mss_="Bullish MSS" if lc>h2["price"] else ("Bearish MSS" if lc<l2_["price"] else "Yok")
    ch="Bullish CHoCH" if h1 and lc>h1["price"] else ("Bearish CHoCH" if l1 and lc<l1["price"] else "Yok")
    return mss_,ch

def sweep(cs):
    if len(cs)<8: return "Yok"
    last=cs[-1]; r=cs[-8:-1]
    ph_=max(x["high"] for x in r); pl_=min(x["low"] for x in r)
    if last["high"]>ph_ and last["close"]<ph_: return "Ust likidite sweep"
    if last["low"]<pl_ and last["close"]>pl_: return "Alt likidite sweep"
    return "Yok"

def fvg(cs):
    if len(cs)<3: return "Yok"
    a,_,c=cs[-3],cs[-2],cs[-1]
    if c["low"]>a["high"]: return "Bullish FVG"
    if c["high"]<a["low"]: return "Bearish FVG"
    return "Yok"

def pd_zone(cs):
    if len(cs)<20: return "Notr"
    r=cs[-20:]
    mid=(max(x["high"] for x in r)+min(x["low"] for x in r))/2
    p=cs[-1]["close"]
    return "Premium" if p>mid else ("Discount" if p<mid else "Notr")

def displacement(cs):
    if len(cs)<12: return "Yok"
    ab=avg_body(cs,10); ar=avg_rng(cs,10)
    if ab==0 or ar==0: return "Yok"
    if cb(cs[-1])/ab>=2.0 and cr(cs[-1])/ar>=1.5: return "Guclu"
    if cb(cs[-1])/ab>=1.4 and cr(cs[-1])/ar>=1.2: return "Orta"
    return "Zayif"

def fvg_zone(cs):
    if len(cs)<3: return None
    start=max(2,len(cs)-IFVG_LB)
    for i in range(len(cs)-1,start-1,-1):
        a,c=cs[i-2],cs[i]
        if c["low"]>a["high"]: return {"type":"bullish","low":a["high"],"high":c["low"]}
        if c["high"]<a["low"]: return {"type":"bearish","low":c["high"],"high":a["low"]}
    return None

def ifvg_reclaim(c15):
    if len(c15)<8: return {"valid":False,"direction":"YOK"}
    z=fvg_zone(c15)
    if not z: return {"valid":False,"direction":"YOK"}
    last=c15[-1]
    if z["type"]=="bearish" and last["close"]>z["high"]: return {"valid":True,"direction":"LONG","zl":z["low"],"zh":z["high"]}
    if z["type"]=="bullish" and last["close"]<z["low"]: return {"valid":True,"direction":"SHORT","zl":z["low"],"zh":z["high"]}
    return {"valid":False,"direction":"YOK"}

def ote(c15,direction):
    if len(c15)<20: return {"valid":False}
    r=c15[-20:]
    hi=max(x["high"] for x in r); lo=min(x["low"] for x in r)
    if hi<=lo: return {"valid":False}
    fr=hi-lo
    if direction=="LONG": zone={"low":hi-fr*OTE_MAX,"high":hi-fr*OTE_MIN}
    else: zone={"low":lo+fr*OTE_MIN,"high":lo+fr*OTE_MAX}
    p=c15[-1]["close"]
    return {"valid":zone["low"]<=p<=zone["high"]}

def ob(cs,direction):
    if len(cs)<14: return None
    ar=avg_rng(cs[-12:],12)
    if ar==0: return None
    for i in range(len(cs)-2,max(0,len(cs)-15)-1,-1):
        c=cs[i]; fut=cs[i+1:]
        if direction=="LONG" and bear(c):
            if not any(x["close"]>c["high"] for x in fut): continue
            dc=max(fut,key=lambda x:cb(x),default=None)
            if dc and cb(dc)<ar*0.35: continue
            return {"i":i,"dt":c["datetime"],"low":c["low"],"high":c["high"]}
        if direction=="SHORT" and bull(c):
            if not any(x["close"]<c["low"] for x in fut): continue
            dc=max(fut,key=lambda x:cb(x),default=None)
            if dc and cb(dc)<ar*0.35: continue
            return {"i":i,"dt":c["datetime"],"low":c["low"],"high":c["high"]}
    return None

def ob_touch(c5,o):
    for i in range(len(c5)-1,-1,-1):
        c=c5[i]
        if c["low"]<=o["high"] and c["high"]>=o["low"]: return i
    return None

def rej_candle(c,direction):
    b=cb(c); rng=cr(c)
    if rng==0: return False
    uw=c["high"]-max(c["open"],c["close"]); lw=min(c["open"],c["close"])-c["low"]
    br=b/rng
    if direction=="LONG": return c["close"]>c["open"] and ((lw>=b*1.2 if b>0 else False) or br>=0.25)
    if direction=="SHORT": return c["close"]<c["open"] and ((uw>=b*1.2 if b>0 else False) or br>=0.25)
    return False

def mitigation(c5,o,direction):
    if len(c5)<6: return {"valid":False,"ti":None,"bat":None,"q":"Yok"}
    ti=ob_touch(c5,o)
    if ti is None: return {"valid":False,"ti":None,"bat":None,"q":"Yok"}
    bat=len(c5)-1-ti
    if bat>MAX_BARS_MIT: return {"valid":False,"ti":ti,"bat":bat,"q":"Eski"}
    if not rej_candle(c5[-1],direction): return {"valid":False,"ti":ti,"bat":bat,"q":"Zayif"}
    return {"valid":True,"ti":ti,"bat":bat,"q":"Guclu" if bat<=1 else "Orta"}

def pdl(c5):
    g={}
    for c in c5:
        dt=c.get("dt")
        if dt: g.setdefault(dt.strftime("%Y-%m-%d"),[]).append(c)
    days=sorted(g.keys())
    if len(days)<2: return {"pdh":None,"pdl":None}
    prev=g[days[-2]]
    return {"pdh":max(x["high"] for x in prev),"pdl":min(x["low"] for x in prev)}

def sess_box(c5,sname):
    sc=[c for c in c5 if session(c.get("dt"))==sname]
    if len(sc)<3: return None
    r=sc[-24:] if len(sc)>24 else sc
    return {"high":max(x["high"] for x in r),"low":min(x["low"] for x in r)}

def build_sl(c15,o,direction):
    ar=avg_rng(c15[-10:],10)
    if ar==0: return None
    r=c15[-8:]
    buf=ar*0.15
    if direction=="LONG": return min(o["low"],min(x["low"] for x in r))-buf
    if direction=="SHORT": return max(o["high"],max(x["high"] for x in r))+buf
    return None

def find_tp(c15,c5,direction,entry):
    H,L=swings(c15)
    pd_=pdl(c5)
    sn="London" if session(c5[-1].get("dt"))=="London" else "New York"
    sb=sess_box(c5,sn)
    cands=[]
    if direction=="LONG":
        eqh=eq_highs(c15)
        cands.extend([x["price"] for x in H if x["price"]>entry])
        if eqh and eqh>entry: cands.append(eqh)
        if pd_["pdh"] and pd_["pdh"]>entry: cands.append(pd_["pdh"])
        if sb and sb["high"]>entry: cands.append(sb["high"])
        return min(cands) if cands else None
    if direction=="SHORT":
        eql=eq_lows(c15)
        cands.extend([x["price"] for x in L if x["price"]<entry])
        if eql and eql<entry: cands.append(eql)
        if pd_["pdl"] and pd_["pdl"]<entry: cands.append(pd_["pdl"])
        if sb and sb["low"]<entry: cands.append(sb["low"])
        return max(cands) if cands else None
    return None

def build_levels(c15,c5,direction,o,sym=""):
    entry=o["low"] if direction=="LONG" else o["high"]
    sl=build_sl(c15,o,direction)
    if sl is None: return None
    risk=abs(entry-sl)
    if risk<=0: return None
    target=find_tp(c15,c5,direction,entry)
    min_tp=entry+risk*2 if direction=="LONG" else entry-risk*2
    tp=(max(target,min_tp) if direction=="LONG" else min(target,min_tp)) if target else min_tp
    rr=abs(tp-entry)/risk
    return {"entry":rp(entry,sym),"sl":rp(sl,sym),"tp":rp(tp,sym),"rr":round(rr,2)}

def direction(sw,mss_,choch,disp):
    if sw=="Alt likidite sweep" and (mss_=="Bullish MSS" or choch=="Bullish CHoCH") and disp in ("Orta","Guclu"): return "LONG"
    if sw=="Ust likidite sweep" and (mss_=="Bearish MSS" or choch=="Bearish CHoCH") and disp in ("Orta","Guclu"): return "SHORT"
    return "YOK"

def sniper_ok(dir_,sw,mss_,choch,disp):
    return dir_!="YOK" and sw!="Yok" and (mss_!="Yok" or choch!="Yok") and disp!="Zayif"

def alt_model(c15,c5,bias,disp,mss_,choch):
    if len(c15)<20 or len(c5)<10: return {"valid":False,"model":"YOK","direction":"YOK"}
    ifvg=ifvg_reclaim(c15)
    if ifvg["valid"]:
        d=ifvg["direction"]
        conf=sum([d=="LONG" and bias=="Yukselis", d=="SHORT" and bias=="Dusus",
                  kz_active(), disp in ("Orta","Guclu"),
                  d=="LONG" and (mss_=="Bullish MSS" or choch=="Bullish CHoCH"),
                  d=="SHORT" and (mss_=="Bearish MSS" or choch=="Bearish CHoCH")])
        sok=(d=="LONG" and (mss_=="Bullish MSS" or choch=="Bullish CHoCH")) or (d=="SHORT" and (mss_=="Bearish MSS" or choch=="Bearish CHoCH"))
        if sok and (kz_active() or (d=="LONG" and bias=="Yukselis") or (d=="SHORT" and bias=="Dusus")) and conf>=2:
            return {"valid":True,"model":"IFVG_RECLAIM","direction":d}
    if bias in ("Yukselis","Dusus"):
        d="LONG" if bias=="Yukselis" else "SHORT"
        o=ote(c15,d)
        if o["valid"] and disp in ("Orta","Guclu"): return {"valid":True,"model":"OTE_RETRACEMENT","direction":d}
    return {"valid":False,"model":"YOK","direction":"YOK"}

def smt_detect(sym,mtf):
    rels=SMT_REL.get(sym,[])
    if not rels: return "Yok"
    t15=mtf.get(sym,{}).get("15min",[])
    if len(t15)<8: return "Yok"
    def sf(cs):
        if len(cs)<8: return {"h":False,"l":False}
        last=cs[-1]; r=cs[-8:-1]
        return {"h":last["high"]>max(x["high"] for x in r) and last["close"]<max(x["high"] for x in r),
                "l":last["low"]<min(x["low"] for x in r) and last["close"]>min(x["low"] for x in r)}
    tf=sf(t15); bull=0; bear=0
    for other,rel in rels:
        o15=mtf.get(other,{}).get("15min",[])
        if len(o15)<8: continue
        of=sf(o15)
        if rel=="positive":
            if tf["l"] and not of["l"]: bull+=1
            if tf["h"] and not of["h"]: bear+=1
        else:
            if tf["l"] and not of["h"]: bull+=1
            if tf["h"] and not of["l"]: bear+=1
    if bull>bear and bull>0: return "Bullish SMT"
    if bear>bull and bear>0: return "Bearish SMT"
    return "Yok"

def score(dir_,bias,h4,w1,fvg_,pd_,smt_,kz,disp,mit_q,rr,mss_,choch,model):
    s=40; n=[]
    if dir_=="LONG":
        if bias=="Yukselis": s+=14; n.append("Ana bias")
        if h4=="Yukselis": s+=8; n.append("4H bias")
        if w1=="Yukselis": s+=5; n.append("1W bias")
        if fvg_=="Bullish FVG": s+=8; n.append("Bullish FVG")
        if pd_=="Discount": s+=8; n.append("Discount")
        if smt_=="Bullish SMT": s+=6; n.append("Bullish SMT")
        if mss_=="Bullish MSS": s+=8; n.append("Bullish MSS")
        elif choch=="Bullish CHoCH": s+=5; n.append("Bullish CHoCH")
    if dir_=="SHORT":
        if bias=="Dusus": s+=14; n.append("Ana bias")
        if h4=="Dusus": s+=8; n.append("4H bias")
        if w1=="Dusus": s+=5; n.append("1W bias")
        if fvg_=="Bearish FVG": s+=8; n.append("Bearish FVG")
        if pd_=="Premium": s+=8; n.append("Premium")
        if smt_=="Bearish SMT": s+=6; n.append("Bearish SMT")
        if mss_=="Bearish MSS": s+=8; n.append("Bearish MSS")
        elif choch=="Bearish CHoCH": s+=5; n.append("Bearish CHoCH")
    if (bias=="Yukselis" and h4=="Dusus") or (bias=="Dusus" and h4=="Yukselis"): s-=8; n.append("Conflict")
    if kz: s+=8; n.append("Killzone")
    if disp=="Guclu": s+=8
    elif disp=="Orta": s+=4
    else: s-=8
    if mit_q=="Guclu": s+=8
    elif mit_q=="Orta": s+=4
    else: s-=8
    if rr:
        if rr>=3.0: s+=10
        elif rr>=2.5: s+=8
        elif rr>=2.0: s+=5
        elif rr<MIN_RR: s-=12
    if model=="IFVG_RECLAIM": s+=2
    elif model=="SNIPER_OB": s+=3
    q="Yok"
    if s>=88: q="A+"
    elif s>=68: q="A"
    return s,q,n

def analyze(sym,snap,mtf,perf):
    c5=snap.get("5min",[]); c15=snap.get("15min",[])
    c1h=snap.get("1h",[]); c4h=snap.get("4h",[]); c1w=snap.get("1week",[])
    if len(c5)<20 or len(c15)<30 or len(c1h)<20 or len(c4h)<10 or len(c1w)<5: return None
    cp=c5[-1]["close"]
    h1=htf_bias(c1h); h4=htf_bias(c4h); w1=htf_bias(c1w)
    bias=h1 if h1!="Notr" else (h4 if h4!="Notr" else w1)
    mss_,choch=mss_choch(c15); sw=sweep(c15); disp=displacement(c15)
    fvg_=fvg(c15); pd_=pd_zone(c15)
    pdir=direction(sw,mss_,choch,disp)
    dir_=pdir; model="SNIPER_OB"; alt=None
    if dir_!="YOK" and not sniper_ok(dir_,sw,mss_,choch,disp): dir_="YOK"
    if dir_=="YOK":
        alt=alt_model(c15,c5,bias,disp,mss_,choch)
        if alt["valid"]: dir_=alt["direction"]; model=alt["model"]
    if dir_=="YOK": return None
    o=ob(c15,dir_)
    if not o: return None
    lv=build_levels(c15,c5,dir_,o,sym)
    if not lv or lv["rr"]<MIN_RR: return None
    mit=mitigation(c5,o,dir_)
    if not mit["valid"]: return None
    ar=avg_rng(c5[-10:],10)
    if ar==0: return None
    ed=abs(cp-lv["entry"])
    if ed>ar*ENTRY_DIST_MULT: return None
    tot=abs(lv["tp"]-lv["entry"])
    if tot>0 and abs(cp-lv["entry"])/tot>=TP_PROG_BLOCK: return None
    if dir_=="LONG" and cp<lv["sl"]: return None
    if dir_=="SHORT" and cp>lv["sl"]: return None
    ar5=avg_rng(c5[-12:],12)
    if ar5>0:
        if abs(lv["tp"]-lv["entry"])<ar5*MIN_TP_MULT: return None
        if abs(lv["entry"]-lv["sl"])<ar5*MIN_SL_MULT: return None
        if abs(lv["tp"]-cp)<ar5*MIN_REM_TP_MULT: return None
    smt_=smt_detect(sym,{sym:snap,**{k:v for k,v in mtf.items() if k!=sym}})
    dt_now=c5[-1].get("dt"); kz=kz_active(dt_now); kzl=kz_label(dt_now)
    p=perf.get(sym,{}); padj=0
    if p.get("total",0)>=8 and p.get("win_rate") is not None:
        wr=p["win_rate"]
        if wr>=65: padj=5
        elif wr<35: padj=-8
    sc,q,notes=score(dir_,bias,h4,w1,fvg_,pd_,smt_,kz,disp,mit["q"],lv["rr"],mss_,choch,model)
    sc+=padj
    if sc>=88: q="A+"
    elif sc>=68: q="A"
    else: q="Yok"
    min_sc=MIN_SIGNAL_SCORE if kz else MIN_SCORE_OUT_KZ
    if sc<min_sc or q not in ("A","A+"): return None
    return {"symbol":sym,"datetime":c5[-1]["datetime"],"direction":dir_,"entry":lv["entry"],
            "sl":lv["sl"],"tp":lv["tp"],"rr":lv["rr"],"score":sc,"quality":q,
            "model":model,"bias":bias,"h4":h4,"kz":kzl,"disp":disp,"mss":mss_,"choch":choch,"smt":smt_}

def outcome(sig,future,max_bars=200):
    d=sig["direction"]; sl=sig["sl"]; tp=sig["tp"]
    for c in future[:max_bars]:
        if d=="LONG":
            if c["low"]<=sl: return "SL"
            if c["high"]>=tp: return "TP"
        elif d=="SHORT":
            if c["high"]>=sl: return "SL"
            if c["low"]<=tp: return "TP"
    return "TIMEOUT"

def run_bt(mtf):
    print("\n Backtest calisiyor..."); print("="*50)
    signals=[]; perf={}
    for sym in BACKTEST_PAIRS:
        tfs=mtf.get(sym,{})
        c5=tfs.get("5min",[]); c15=tfs.get("15min",[])
        c1h=tfs.get("1h",[]); c4h=tfs.get("4h",[]); c1w=tfs.get("1week",[])
        if len(c5)<50: print(f" {sym} yetersiz veri"); continue
        print(f"\n {sym} - {len(c5)} bar")
        found=0; last_i=-50
        for i in range(50,len(c5)-10):
            if i-last_i<12: continue
            s5=c5[:i+1]; s15=c15[:min(i//3+1,len(c15))]
            s1h=c1h[:min(i//12+1,len(c1h))]; s4h=c4h[:min(i//48+1,len(c4h))]
            s1w=c1w[:min(i//336+1,len(c1w))]
            if len(s15)<30 or len(s1h)<20: continue
            snap={"5min":s5,"15min":s15,"1h":s1h,"4h":s4h,"1week":s1w}
            sig=analyze(sym,snap,mtf,perf)
            if not sig: continue
            oc=outcome(sig,c5[i+1:])
            sig["outcome"]=oc; signals.append(sig); last_i=i; found+=1
            p=perf.setdefault(sym,{"wins":0,"losses":0,"total":0,"win_rate":None})
            if oc=="TP": p["wins"]+=1
            elif oc=="SL": p["losses"]+=1
            p["total"]=p["wins"]+p["losses"]
            if p["total"]>0: p["win_rate"]=round(p["wins"]/p["total"]*100,2)
        print(f" -> {found} sinyal")
    return {"signals":signals,"performance":perf}

def report(results):
    sigs=results["signals"]; perf=results["performance"]
    if not sigs: return "Hic sinyal bulunamadi."
    tp=[s for s in sigs if s["outcome"]=="TP"]
    sl=[s for s in sigs if s["outcome"]=="SL"]
    to=[s for s in sigs if s["outcome"]=="TIMEOUT"]
    dec=len(tp)+len(sl); wr=round(len(tp)/dec*100,2) if dec>0 else 0
    avg_rr_=round(sum(s.get("rr",0) for s in tp)/len(tp),2) if tp else 0
    ss={}
    for s in sigs:
        sym=s["symbol"]; st=ss.setdefault(sym,{"tp":0,"sl":0,"to":0})
        if s["outcome"]=="TP": st["tp"]+=1
        elif s["outcome"]=="SL": st["sl"]+=1
        else: st["to"]+=1
    ms={}
    for s in sigs:
        m=s.get("model","?"); st=ms.setdefault(m,{"tp":0,"sl":0})
        if s["outcome"]=="TP": st["tp"]+=1
        elif s["outcome"]=="SL": st["sl"]+=1
    ks={}
    for s in sigs:
        k=s.get("kz","?"); st=ks.setdefault(k,{"tp":0,"sl":0})
        if s["outcome"]=="TP": st["tp"]+=1
        elif s["outcome"]=="SL": st["sl"]+=1
    lines=["="*60," ELITE SNIPER BACKTEST RAPORU",
           f" {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}","="*60,"",
           "GENEL",f"Toplam: {len(sigs)} TP: {len(tp)} SL: {len(sl)} TO: {len(to)}",
           f"Win Rate: %{wr} Ort RR(TP): {avg_rr_}","","PARITE"]
    for sym,st in sorted(ss.items(),key=lambda x:-(x[1]["tp"])):
        d=st["tp"]+st["sl"]; w=round(st["tp"]/d*100,1) if d>0 else 0
        lines.append(f"{sym:<12} TP:{st['tp']:>3} SL:{st['sl']:>3} TO:{st['to']:>3} WR:%{w}")
    lines+=["","MODEL"]
    for m,st in sorted(ms.items(),key=lambda x:-(x[1]["tp"])):
        d=st["tp"]+st["sl"]; w=round(st["tp"]/d*100,1) if d>0 else 0
        lines.append(f"{m:<20} TP:{st['tp']:>3} SL:{st['sl']:>3} WR:%{w}")
    lines+=["","SESSION"]
    for k,st in sorted(ks.items(),key=lambda x:-(x[1]["tp"])):
        d=st["tp"]+st["sl"]; w=round(st["tp"]/d*100,1) if d>0 else 0
        lines.append(f"{k:<25} TP:{st['tp']:>3} SL:{st['sl']:>3} WR:%{w}")
    lines+=["","WIN RATE (bot_state)"]
    for sym,p in sorted(perf.items(),key=lambda x:-(x[1].get("win_rate") or 0)):
        wr_=p.get("win_rate") or 0
        lines.append(f"{sym:<12} WR:%{wr_:<6} TP:{p['wins']:>3} SL:{p['losses']:>3} T:{p['total']:>3}")
    lines+=["","="*60]
    return "\n".join(lines)

def update_state(perf):
    ex={"signals":{},"active_signals":{},"performance":{},"pending_setups":{}}
    if os.path.exists(BOT_STATE_FILE):
        try:
            with open(BOT_STATE_FILE,"r",encoding="utf-8") as f: ex=json.load(f)
        except: pass
    for sym,p in perf.items():
        ep=ex["performance"].setdefault(sym,{"wins":0,"losses":0,"total":0,"last_outcome":None,"win_rate":None})
        ep["wins"]+=p["wins"]; ep["losses"]+=p["losses"]
        ep["total"]=ep["wins"]+ep["losses"]
        if ep["total"]>0: ep["win_rate"]=round(ep["wins"]/ep["total"]*100,2)
    with open(BOT_STATE_FILE,"w",encoding="utf-8") as f: json.dump(ex,f,indent=2)
    print(f"\n bot_state.json guncellendi - {len(perf)} parite")

def main():
    print("ELITE SNIPER BACKTEST BASLIYOR")
    print(f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    mtf=load_data()
    has=any(len(mtf.get(s,{}).get("5min",[]))>100 for s in BACKTEST_PAIRS)
    if not has:
        print("HATA: Hic Dukascopy verisi bulunamadi!")
        print("XLS dosyalari bu script ile ayni klasorde olmali.")
        return
    results=run_bt(mtf)
    rpt=report(results)
    print("\n"+rpt)
    with open(BACKTEST_REPORT,"w",encoding="utf-8") as f: json.dump(results["signals"],f,indent=2,default=str)
    with open(BACKTEST_TXT,"w",encoding="utf-8") as f: f.write(rpt)
    print(f"\n{BACKTEST_REPORT} kaydedildi")
    print(f"{BACKTEST_TXT} kaydedildi")
    update_state(results["performance"])
    print("\nBACKTEST TAMAMLANDI!")

if __name__=="__main__":
    main()
