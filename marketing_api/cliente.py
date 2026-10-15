# -*- coding: utf-8 -*-
"""Cliente mínimo da Marketing API (Graph API) para escrita em conta de anúncios.

Princípios:
  - credenciais só por variável de ambiente (META_TOKEN, META_CONTA = act_<id>); nunca impressas;
  - ritmo pelo cabeçalho de uso que a Meta devolve em cada resposta (x-business-use-case-usage):
    espaça as chamadas conforme a ocupação da cota e, quando a Meta informa o tempo para
    recuperar o acesso, espera exatamente esse tempo;
  - POST de criação só é repetido em limite de chamadas (a Meta não criou nada nesses casos).
    Em timeout ou queda de rede o erro volta marcado REDE: quem chama procura o objeto pelo nome
    antes de tentar de novo, para não duplicar campanha/conjunto/anúncio;
  - lote (endpoint batch): até 50 sub-requisições contam como UMA chamada de cota; itens do lote
    que voltarem com erro de limite são reenviados sozinhos;
  - registro local gravado de forma atômica (arquivo temporário + os.replace com nova tentativa,
    porque antivírus e sincronizadores de pasta seguram o arquivo por milissegundos no Windows).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_VERSAO = os.environ.get('META_API_VERSAO', 'v21.0')
BASE = 'https://graph.facebook.com/%s' % API_VERSAO
REDE = '__rede__'
CODIGOS_LIMITE = {4, 17, 32, 613, 80004}


class Cliente:
    def __init__(self, token=None, conta=None, verbose=True):
        self.token = token if token is not None else os.environ.get('META_TOKEN', '').strip()
        self.conta = conta if conta is not None else os.environ.get('META_CONTA', '').strip()
        self.uso = {'pct': 0, 'espera_min': 0}
        self.verbose = verbose
        self.chamadas = 0

    def pronto(self):
        return bool(self.token and self.conta)

    # ------------------------------------------------------------ ritmo
    def _ler_uso(self, headers):
        try:
            h = headers.get('x-business-use-case-usage') or headers.get('X-Business-Use-Case-Usage')
            if not h:
                return
            for _, lst in json.loads(h).items():
                for item in lst:
                    self.uso['pct'] = max(item.get('call_count', 0), item.get('total_cputime', 0), item.get('total_time', 0))
                    self.uso['espera_min'] = item.get('estimated_time_to_regain_access', 0) or 0
        except Exception:
            pass

    def _respirar(self):
        """Enquanto a Meta não bloqueia, espaça as chamadas conforme a ocupação da janela de cota
        (que escoa sozinha). Pausar longo a cada chamada só por estar alto desperdiça horas."""
        if self.uso['espera_min']:
            espera = int(self.uso['espera_min']) * 60
            self._log('   [bloqueado; a Meta pede %d min — aguardando]' % self.uso['espera_min'])
            time.sleep(espera)
            self.uso = {'pct': 0, 'espera_min': 0}
            return
        pct = self.uso['pct']
        espera = 90 if pct >= 95 else 45 if pct >= 90 else 25 if pct >= 80 else 8 if pct >= 60 else 0
        if espera:
            if pct >= 90:
                self._log('   [cota em %d%% — espaçando %d s]' % (pct, espera))
            time.sleep(espera)

    def _log(self, msg):
        if self.verbose:
            print(msg)
            sys.stdout.flush()

    @staticmethod
    def _erro_http(e):
        try:
            return json.loads(e.read().decode()).get('error', {})
        except Exception:
            return {'message': str(e)}

    # ------------------------------------------------------------ chamadas
    def chamar(self, caminho, dados, metodo='POST'):
        """POST (criação/edição). Devolve (resultado, erro)."""
        self._respirar()
        dados = dict(dados)
        dados['access_token'] = self.token
        corpo = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                        for k, v in dados.items()}).encode()
        req = urllib.request.Request('%s/%s' % (BASE, caminho), data=corpo, method=metodo)
        for _ in range(4):
            self.chamadas += 1
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    self._ler_uso(r.headers)
                    return json.loads(r.read().decode()), None
            except urllib.error.HTTPError as e:
                self._ler_uso(e.headers)
                err = self._erro_http(e)
                if err.get('code') in CODIGOS_LIMITE:
                    self._log('   [limite de chamadas — aguardando 5 min]')
                    time.sleep(300)
                    continue
                return None, err
            except Exception as e:
                return None, {'message': 'rede/timeout: %s' % e, REDE: True}
        return None, {'message': 'limite de chamadas persistente'}

    def buscar(self, caminho, params=None):
        """GET. Leitura pode ser repetida sem risco."""
        self._respirar()
        p = dict(params or {})
        p['access_token'] = self.token
        url = '%s/%s?%s' % (BASE, caminho, urllib.parse.urlencode(
            {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in p.items()}))
        for tentativa in range(3):
            self.chamadas += 1
            try:
                with urllib.request.urlopen(url, timeout=90) as r:
                    self._ler_uso(r.headers)
                    return json.loads(r.read().decode()), None
            except urllib.error.HTTPError as e:
                self._ler_uso(e.headers)
                err = self._erro_http(e)
                if err.get('code') in CODIGOS_LIMITE:
                    self._log('   [limite de chamadas na leitura — aguardando 5 min]')
                    time.sleep(300)
                    continue
                return None, err
            except Exception as e:
                if tentativa < 2:
                    time.sleep(15)
                    continue
                return None, {'message': str(e)}
        return None, {'message': 'limite persistente'}

    def paginar(self, caminho, params=None, max_paginas=50):
        itens, depois = [], None
        for _ in range(max_paginas):
            p = dict(params or {})
            if depois:
                p['after'] = depois
            d, err = self.buscar(caminho, p)
            if err:
                return itens, err
            itens += d.get('data', [])
            pg = d.get('paging', {})
            depois = pg.get('cursors', {}).get('after')
            if not pg.get('next'):
                break
        return itens, None

    def lote(self, itens, _tentativa=0):
        """itens: [{"method", "relative_url", "body": dict|None}] (até 50). Devolve [(resultado, erro)]
        na mesma ordem. Itens recusados por limite são reenviados (até 3 rodadas)."""
        if not itens:
            return []
        assert len(itens) <= 50, 'lote de %d > 50' % len(itens)

        def corpo(v):
            return urllib.parse.urlencode({k: (json.dumps(x) if isinstance(x, (dict, list)) else x) for k, x in v.items()})

        batch = [dict({'method': it['method'], 'relative_url': it['relative_url']},
                      **({'body': corpo(it['body'])} if it.get('body') is not None else {})) for it in itens]
        self._respirar()
        dados = urllib.parse.urlencode({'access_token': self.token, 'include_headers': 'false',
                                        'batch': json.dumps(batch)}).encode()
        req = urllib.request.Request('%s/' % BASE, data=dados, method='POST')
        respostas = None
        for _ in range(4):
            self.chamadas += 1
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    self._ler_uso(r.headers)
                    respostas = json.loads(r.read().decode())
                    break
            except urllib.error.HTTPError as e:
                self._ler_uso(e.headers)
                err = self._erro_http(e)
                if err.get('code') in CODIGOS_LIMITE:
                    time.sleep(300)
                    continue
                return [(None, err) for _ in itens]
            except Exception as e:
                return [(None, {'message': 'rede/timeout: %s' % e, REDE: True}) for _ in itens]
        if respostas is None:
            return [(None, {'message': 'limite de chamadas persistente no lote'}) for _ in itens]
        saida, refazer = [], []
        for i, resp in enumerate(respostas):
            if resp is None:
                saida.append((None, {'message': 'sem resposta (item nulo no lote)'}))
                continue
            try:
                cj = json.loads(resp.get('body') or '{}')
            except Exception:
                cj = {'message': resp.get('body')}
            code = resp.get('code')
            if code is not None and 200 <= code < 300:
                saida.append((cj, None))
            else:
                erro = cj.get('error', cj)
                if erro.get('code') in CODIGOS_LIMITE and _tentativa < 3:
                    saida.append(None)
                    refazer.append(i)
                else:
                    saida.append((None, erro))
        if refazer:
            self._log('   [%d item(ns) do lote recusados por limite — aguardando 5 min]' % len(refazer))
            time.sleep(300)
            sub = self.lote([itens[i] for i in refazer], _tentativa + 1)
            for pos, i in enumerate(refazer):
                saida[i] = sub[pos]
        return saida


# ---------------------------------------------------------------- registro local

def carregar(caminho, padrao):
    """Registro corrompido NÃO é engolido: parar e avisar é melhor do que recriar tudo."""
    if not os.path.exists(caminho):
        return padrao
    with open(caminho, encoding='utf-8') as f:
        txt = f.read()
    if not txt.strip():
        return padrao
    try:
        return json.loads(txt)
    except Exception as e:
        raise SystemExit('ARQUIVO CORROMPIDO: %s (%s). Conserte ou apague antes de continuar.' % (caminho, e))


def gravar(caminho, obj):
    os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
    tmp = caminho + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    for tentativa in range(8):
        try:
            os.replace(tmp, caminho)
            return
        except PermissionError:
            if tentativa == 7:
                raise
            time.sleep(0.4 * (tentativa + 1))
