# -*- coding: utf-8 -*-
"""Cliente da Biblioteca de Anúncios da Meta (endpoint ads_archive da Graph API).

O que este módulo resolve, além de "chamar a API":
  - ritmo controlado pela própria Meta: cada resposta traz o cabeçalho x-business-use-case-usage
    (ou x-app-usage) com a % da cota usada; o cliente desacelera ANTES do bloqueio (erro 613/4/17),
    que costuma durar horas quando acontece;
  - paginação por cursor (paging.next) com páginas grandes (limit até 1000);
  - erro 500 transitório ou "reduce the amount of data": repete com página menor;
  - o campo ad_snapshot_url vem com o access_token embutido na URL: limpar() troca pelo link
    público da Biblioteca antes de qualquer gravação em disco.

Credencial: somente pela variável de ambiente META_TOKEN (ver .env.example). Nunca é impressa.
"""
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

API_VERSAO = os.environ.get('META_API_VERSAO', 'v21.0')
API = 'https://graph.facebook.com/%s/ads_archive' % API_VERSAO

CAMPOS_LEVES = 'id,page_id,page_name,bylines,ad_delivery_start_time,ad_delivery_stop_time'
CAMPOS_TODOS = ','.join([
    'id', 'page_id', 'page_name', 'bylines', 'ad_creation_time', 'ad_delivery_start_time',
    'ad_delivery_stop_time', 'ad_creative_bodies', 'ad_creative_link_titles',
    'ad_creative_link_captions', 'ad_creative_link_descriptions', 'ad_snapshot_url',
    'impressions', 'spend', 'currency', 'publisher_platforms', 'estimated_audience_size',
    'demographic_distribution', 'delivery_by_region', 'target_locations', 'target_ages',
    'target_gender', 'languages',
])

_ultimo_uso = {'v': 0}


def token():
    t = os.environ.get('META_TOKEN', '').strip()
    if not t:
        raise SystemExit('Defina a variável de ambiente META_TOKEN (escopo ads_read). Ver .env.example.')
    return t


def log(caminho, msg):
    linha = '%s  %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(linha, flush=True)
    if caminho:
        with open(caminho, 'a', encoding='utf-8') as f:
            f.write(linha + '\n')


def ler_uso(headers):
    """Maior percentual entre call_count, total_cputime e total_time (0-100) e o tempo
    estimado para recuperar o acesso (minutos), nos dois formatos de cabeçalho da Meta."""
    h = headers.get('x-business-use-case-usage') or headers.get('x-app-usage') or ''
    try:
        d = json.loads(h)
    except Exception:
        return 0, 0
    maior, espera, itens = 0, 0, []
    if isinstance(d, dict):
        if any(k in d for k in ('call_count', 'total_cputime', 'total_time')):
            itens = [d]                                   # formato x-app-usage
        else:
            for v in d.values():                          # formato x-business-use-case-usage
                itens += v if isinstance(v, list) else [v]
    for item in itens:
        if not isinstance(item, dict):
            continue
        for k in ('call_count', 'total_cputime', 'total_time'):
            try:
                maior = max(maior, int(item.get(k) or 0))
            except (TypeError, ValueError):
                pass
        try:
            espera = max(espera, int(item.get('estimated_time_to_regain_access') or 0))
        except (TypeError, ValueError):
            pass
    return maior, espera


def _pausa_preventiva(logfile):
    """Espaça as chamadas conforme o uso reportado na resposta anterior."""
    u = _ultimo_uso['v']
    teto = int(os.environ.get('LIMITE_PAUSA', '85'))
    if u >= teto:
        log(logfile, '   uso da API em %d%% (teto %d) - pausa preventiva de 10 min' % (u, teto))
        time.sleep(600)
        _ultimo_uso['v'] = 70
    elif u >= 70:
        time.sleep(20)
    elif u >= 50:
        time.sleep(5)
    else:
        time.sleep(0.4)


def _reduz_limite(url):
    m = re.search(r'([?&]limit=)(\d+)', url)
    if not m:
        return url
    novo = max(25, int(m.group(2)) // 2)
    return url[:m.start(2)] + str(novo) + url[m.end(2):]


def get(url, logfile=None):
    """GET com pausa por uso, backoff em limite de taxa e retentativa em erro transitório."""
    tentativas = 0
    while True:
        tentativas += 1
        _pausa_preventiva(logfile)
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                corpo = r.read().decode('utf-8')
                _ultimo_uso['v'], _ = ler_uso(r.headers)
                return json.loads(corpo)
        except urllib.error.HTTPError as e:
            corpo = e.read().decode('utf-8', 'replace')
            _, espera = ler_uso(e.headers)
            if any(c in corpo for c in ('"code":613', '"code":4,', '"code":17,', '"code":80004')):
                pausa = max(900, espera * 60) * min(tentativas, 4)
                log(logfile, '   LIMITE DE TAXA (HTTP %s) - pausa de %d min' % (e.code, pausa // 60))
                time.sleep(pausa)
                _ultimo_uso['v'] = 0
                continue
            if e.code in (400, 401, 403) and 'OAuthException' in corpo and '"code":190' in corpo:
                log(logfile, '   TOKEN INVÁLIDO OU EXPIRADO')
                raise SystemExit(2)
            if e.code >= 500 or 'reduce the amount of data' in corpo:
                if tentativas >= 6:
                    raise RuntimeError('HTTP %s persistente: %s' % (e.code, corpo[:300]))
                log(logfile, '   HTTP %s (tentativa %d) - reduzindo a página e tentando de novo' % (e.code, tentativas))
                url = _reduz_limite(url)
                time.sleep(10 * tentativas)
                continue
            raise RuntimeError('HTTP %s: %s' % (e.code, corpo[:400]))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if tentativas >= 8:
                raise
            log(logfile, '   rede: %s (tentativa %d)' % (e, tentativas))
            time.sleep(15 * tentativas)


def url_busca(termo=None, page_ids=None, campos=CAMPOS_TODOS, limite=500, data_min=None,
              pais='BR', tipo_anuncio='POLITICAL_AND_ISSUE_ADS', exato=False):
    """Monta a URL de busca.

    Busca por termo usa palavras soltas (KEYWORD_UNORDERED). A busca por frase exata
    (KEYWORD_EXACT_PHRASE) perde anúncios na prática: só use se tiver medido que não perde.
    """
    p = {
        'ad_reached_countries': "['%s']" % pais,
        'ad_type': tipo_anuncio,
        'ad_active_status': 'ALL',
        'fields': campos,
        'limit': limite,
        'access_token': token(),
    }
    if data_min:
        p['ad_delivery_date_min'] = data_min
    if termo:
        p['search_terms'] = termo
        p['search_type'] = 'KEYWORD_EXACT_PHRASE' if exato else 'KEYWORD_UNORDERED'
    if page_ids:
        p['search_page_ids'] = json.dumps([str(x) for x in page_ids])
    return API + '?' + urllib.parse.urlencode(p)


def paginar(url, logfile=None, max_paginas=10000):
    """Gera os anúncios página a página."""
    n = 0
    while url and n < max_paginas:
        d = get(url, logfile)
        n += 1
        for a in d.get('data', []):
            yield a
        url = (d.get('paging') or {}).get('next')


def limpar(a):
    """Remove o token embutido em ad_snapshot_url (troca pelo link público da Biblioteca).
    Chamar antes de gravar qualquer anúncio em disco."""
    if a.get('id'):
        a['ad_snapshot_url'] = 'https://www.facebook.com/ads/library/?id=%s' % a['id']
    return a


# ------------------------------------------------------------------ normalização

def norm(x):
    """Maiúsculas, sem acento e sem pontuação: o rodapé legal real vem cheio de hífens."""
    x = unicodedata.normalize('NFKD', x or '').encode('ascii', 'ignore').decode().upper()
    x = re.sub(r'[^A-Z0-9 ]', ' ', x)
    return ' '.join(x.split())


def digitos(x):
    return ''.join(c for c in (x or '') if c.isdigit())


def faixa(v):
    """{'lower_bound','upper_bound'} -> (piso, teto). Faixa aberta no topo devolve teto None."""
    if not v:
        return None, None
    lo, hi = v.get('lower_bound'), v.get('upper_bound')
    lo = int(lo) if lo not in (None, '') else None
    hi = int(hi) if hi not in (None, '') else None
    return lo, hi


def meio(lo, hi):
    if lo is None:
        return None
    return (lo + (hi if hi is not None else lo)) / 2.0
