"""Highlightly Iran adapter. Credentials are read only from the environment."""
import os,time,json,threading,urllib.request,urllib.parse,urllib.error,datetime
_lock=threading.RLock()
_cache={}; _last={}; _calls=[]
class IranError(Exception): pass

def _request(endpoint,params,ttl,scope):
    key=os.getenv('HIGHLIGHTLY_API_KEY','').strip()
    if not key: raise IranError('کلید سرویس بسکتبال در سرور تنظیم نشده است.')
    query=urllib.parse.urlencode(params); url='https://basketball.highlightly.net/'+endpoint+'?'+query
    now=time.time()
    with _lock:
        item=_cache.get(url)
        if item and now-item[0]<ttl: return item
        if now-_last.get(scope,0)<ttl:
            if item: return item
            raise IranError('برای حفظ سهمیه، دریافت تاریخ دیگر پس از فاصله بروزرسانی ممکن است.')
        _calls[:]=[t for t in _calls if now-t<86400]
        if len(_calls)>=60: raise IranError('سهمیه محافظتی دریافت داده تکمیل شده است؛ بعداً دوباره تلاش کنید.')
        _last[scope]=now; _calls.append(now)
        req=urllib.request.Request(url,headers={'x-rapidapi-key':key,'Accept':'application/json','User-Agent':'CourtSide/1.0'})
        try:
            with urllib.request.urlopen(req,timeout=18) as res: data=json.load(res)
        except urllib.error.HTTPError as e:
            _last[scope]=now-ttl+60
            if e.code in (401,403): raise IranError('اتصال رد شد؛ کلید یا فعال بودن اشتراک Basketball API را بررسی کنید.')
            if e.code==429: raise IranError('سهمیه سرویس داده تمام شده است؛ بعداً تلاش کنید.')
            raise IranError('سرویس داده لیگ ایران در دسترس نیست.')
        except Exception:
            _last[scope]=now-ttl+60
            raise IranError('ارتباط با سرویس داده لیگ ایران برقرار نشد.')
        result=(time.time(),data); _cache[url]=result
        return result

def _league():
    _, data=_request('leagues',{'countryCode':'IR','leagueName':'Super League'},86400,'iran-league')
    candidates=[x for x in data.get('data',[]) if x.get('country',{}).get('code','').upper()=='IR' and 'super' in x.get('name','').lower()]
    if not candidates: raise IranError('سوپرلیگ ایران در اشتراک یا داده فعلی سرویس پیدا نشد.')
    exact=[x for x in candidates if x.get('name','').lower().strip() in ('super league','superleague')]
    league=(exact or candidates)[0]
    seasons=[int(x['season']) for x in league.get('seasons',[]) if str(x.get('season','')).isdigit()]
    if not seasons: raise IranError('فصل لیگ ایران از سرویس دریافت نشد.')
    # Future schedules may exist; use the latest season at or before the current year.
    eligible=[s for s in seasons if s<=datetime.datetime.now(datetime.timezone.utc).year]
    return league['id'], max(eligible or seasons)

def scores(date):
    league_id,season=_league()
    if not date: date=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')
    try: date_fmt=datetime.datetime.strptime(date,'%Y%m%d').strftime('%Y-%m-%d')
    except ValueError: raise IranError('تاریخ نامعتبر است.')
    fetched,data=_request('matches',{'leagueId':league_id,'date':date_fmt,'timezone':'Asia/Tehran','limit':100},1800,'iran-matches')
    events=[]
    for match in data.get('data',[]):
        state=match.get('state') or {}; description=state.get('description','Unknown'); d=description.lower()
        if d in ('not started','to be announced'): phase='pre'
        elif d in ('first quarter','second quarter','third quarter','fourth quarter','over time','break time','half time'): phase='in'
        elif d.startswith('finished') or d=='awarded': phase='post'
        else: phase='pre'
        score=(state.get('score') or {}).get('current') or ''
        parts=score.split('-'); home=parts[0].strip() if len(parts)==2 and phase!='pre' else '–'; away=parts[1].strip() if len(parts)==2 and phase!='pre' else '–'
        def team(t,side,score):
            return {'homeAway':side,'score':score,'team':{'displayName':t.get('name','تیم نامشخص'),'logo':t.get('logo')}}
        status={'type':{'state':phase,'shortDetail':description}}
        events.append({'date':match.get('date',''),'status':status,'competitions':[{'status':status,'competitors':[team(match.get('homeTeam') or {},'home',home),team(match.get('awayTeam') or {},'away',away)]}]})
    return fetched,{'events':events}

def standings():
    league_id,season=_league()
    fetched,data=_request('standings',{'leagueId':league_id,'season':season},21600,'iran-standings')
    children=[]
    for group in data.get('groups',[]):
        entries=[]
        rows=sorted(group.get('standings',[]),key=lambda r:r.get('position') or 9999)
        for row in rows:
            t=row.get('team') or {}; wins=row.get('wins',0); losses=row.get('loses',0); played=row.get('gamesPlayed',0)
            percent=round(wins/played,3) if played else 0
            entries.append({'team':{'displayName':t.get('name','تیم نامشخص'),'logos':[{'href':t.get('logo','')}]},'stats':[{'name':'wins','displayValue':str(wins)},{'name':'losses','displayValue':str(losses)},{'name':'winPercent','value':percent,'displayValue':f'{percent:.3f}'},{'name':'playoffSeed','value':row.get('position',0)}]})
        if entries: children.append({'name':group.get('name','جدول'),'standings':{'entries':entries}})
    return fetched,{'children':children}
