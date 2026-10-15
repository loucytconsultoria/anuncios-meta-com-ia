# -*- coding: utf-8 -*-
"""Cria na conta de anúncios uma estrutura descrita em JSON (campanhas -> conjuntos -> anúncios).

Quatro modos, um por vez:

  --simular   (padrão) offline: checa a estrutura localmente e mostra o plano. Nada é chamado.
  --validar   valida na API com execution_options=validate_only (nada é criado) e grava um RECIBO
              com o hash do arquivo de estrutura validado.
  --aplicar   cria de verdade, tudo PAUSED. Só roda se existir recibo de validação SEM erro para
              exatamente este arquivo (mesmo hash): mudou a estrutura, valide de novo. Conjunto que não
              pôde ser validado antes (a conta ainda não tinha campanha daquele objetivo) passa por
              validate_only logo antes de ser criado, já com a campanha nova.
  --prova     só leitura: compara a conta com a estrutura e lista cada divergência (sai com 1 se houver).

Idempotência: antes de criar, lê o que já existe na conta (pelo nome) e junta com o registro local.
O que existe é pulado; rodar --aplicar duas vezes não duplica nada. POST que cai por rede NÃO é
repetido às cegas: procura na conta um objeto com aquele nome antes de tentar de novo. Se um mesmo
tipo de conjunto falhar 3 vezes seguidas por parâmetro, o resto daquele tipo é pulado e anotado.
Nada é ativado por script: ativar é decisão humana, depois da auditoria dos anúncios.

Valores sensíveis não ficam no JSON: a estrutura usa marcadores {{PAGINA}}, {{INSTAGRAM}},
{{LINK_SITE}}..., trocados por variáveis de ambiente META_PAGINA, META_INSTAGRAM, LINK_SITE...
(ou --var CHAVE=valor).

Uso:
  python -m marketing_api.subir_estrutura --estrutura exemplos/estrutura_exemplo.json [--simular|--validar|--aplicar|--prova]
      [--registro dados/subida/registro.json] [--conta-simulada dados/subida/conta.json]
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

try:
    from .cliente import REDE, Cliente, carregar, gravar
    from .simulado import ClienteSimulado
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cliente import REDE, Cliente, carregar, gravar
    from simulado import ClienteSimulado

RX_MARCADOR = re.compile(r'\{\{([A-Z0-9_]+)\}\}')
VAR_AMBIENTE = {'PAGINA': 'META_PAGINA', 'INSTAGRAM': 'META_INSTAGRAM'}


# ---------------------------------------------------------------- estrutura
def carregar_estrutura(caminho, extras=None):
    with open(caminho, 'rb') as f:
        bruto = f.read()
    h = hashlib.sha256(bruto).hexdigest()
    texto = bruto.decode('utf-8')
    faltam = set()

    def troca(m):
        k = m.group(1)
        v = (extras or {}).get(k) or os.environ.get(VAR_AMBIENTE.get(k, k), '')
        if not v:
            faltam.add(k)
            return m.group(0)
        return v
    texto = RX_MARCADOR.sub(troca, texto)
    return json.loads(texto), h, sorted(faltam)


def checar_local(est, faltam):
    """Erros que não precisam da API para aparecer."""
    probs = []
    if faltam:
        probs.append('marcadores sem valor: %s' % ', '.join(faltam))
    vistos_c, vistos_a = set(), set()
    for c in est['campanhas']:
        if c['nome'] in vistos_c:
            probs.append('campanha repetida: %s' % c['nome'])
        vistos_c.add(c['nome'])
        if c['params'].get('status') != 'PAUSED':
            probs.append('campanha %s não está PAUSED' % c['nome'])
        for a in c.get('conjuntos', []):
            if (c['nome'], a['nome']) in vistos_a:
                probs.append('conjunto repetido: %s / %s' % (c['nome'], a['nome']))
            vistos_a.add((c['nome'], a['nome']))
            p = a['params']
            for k in ('optimization_goal', 'billing_event', 'targeting'):
                if k not in p:
                    probs.append('conjunto %s sem %s' % (a['nome'], k))
            if p.get('status') != 'PAUSED':
                probs.append('conjunto %s não está PAUSED' % a['nome'])
            if not (int(p.get('daily_budget') or 0) > 0 or int(p.get('lifetime_budget') or 0) > 0):
                probs.append('conjunto %s sem verba' % a['nome'])
            nomes_ads = [x['nome'] for x in a.get('anuncios', [])]
            if len(nomes_ads) != len(set(nomes_ads)):
                probs.append('anúncio repetido no conjunto %s' % a['nome'])
    return probs


def assinatura(c, a):
    geo = a['params']['targeting'].get('geo_locations', {})
    return (c['params']['objective'], a['params']['optimization_goal'],
            'cidades' if geo.get('cities') else ('pins' if geo.get('custom_locations') else 'regiao'))


def contagem(est):
    n_a = sum(len(c.get('conjuntos', [])) for c in est['campanhas'])
    n_ad = sum(len(a.get('anuncios', [])) for c in est['campanhas'] for a in c.get('conjuntos', []))
    return len(est['campanhas']), n_a, n_ad


# ---------------------------------------------------------------- modos
def simular(est, faltam):
    nc, na, nad = contagem(est)
    print('estrutura: %d campanhas, %d conjuntos, %d anúncios' % (nc, na, nad))
    for c in est['campanhas']:
        print('  %s  [%s]' % (c['nome'], c['params']['objective']))
        for a in c.get('conjuntos', []):
            p = a['params']
            t = p['targeting']
            print('     %-40s %-18s verba/dia %-6s idade %s-%s  anúncios %d%s' % (
                a['nome'][:40], p['optimization_goal'], p.get('daily_budget'), t.get('age_min'), t.get('age_max'),
                len(a.get('anuncios', [])), '  [PENDENTE: %s]' % a['pendente'] if a.get('pendente') else ''))
    probs = checar_local(est, faltam)
    print('\nchecagem local: %s' % ('ok' if not probs else '%d problema(s)' % len(probs)))
    for p in probs:
        print('   - ' + p)
    return 0 if not probs else 1


def existentes(cli):
    camps, err = cli.paginar('%s/campaigns' % cli.conta, {'fields': 'id,name,objective', 'limit': 200})
    if err:
        raise SystemExit('ERRO lendo campanhas: %s' % err.get('message'))
    ads, err = cli.paginar('%s/adsets' % cli.conta, {'fields': 'id,name,campaign_id', 'limit': 500})
    if err:
        raise SystemExit('ERRO lendo conjuntos: %s' % err.get('message'))
    anuncios, err = cli.paginar('%s/ads' % cli.conta, {'fields': 'id,name,adset_id', 'limit': 500})
    if err:
        raise SystemExit('ERRO lendo anúncios: %s' % err.get('message'))
    nome_c = {c['id']: c['name'] for c in camps}
    nome_a = {a['id']: a['name'] for a in ads}
    return ({c['name']: c['id'] for c in camps},
            {(nome_c.get(str(a.get('campaign_id')), '?'), a['name']): a['id'] for a in ads},
            {(nome_a.get(str(x.get('adset_id')), '?'), x['name']): x['id'] for x in anuncios},
            {c.get('objective'): c['id'] for c in camps})


def validar(cli, est, h, faltam, recibo):
    probs = checar_local(est, faltam)
    erros = ['local: ' + p for p in probs]
    adiados = []
    ja_camp, _, _, por_objetivo = existentes(cli)
    ok = 0
    for c in est['campanhas']:
        p = dict(c['params'], name=c['nome'], execution_options=['validate_only'])
        r, e = cli.chamar('%s/campaigns' % cli.conta, p)
        if e:
            erros.append('campanha %s: %s' % (c['nome'], e.get('error_user_msg') or e.get('message')))
        else:
            ok += 1
        # conjunto precisa de campaign_id real: a própria campanha (se já existe) ou outra de mesmo objetivo
        cid = ja_camp.get(c['nome']) or por_objetivo.get(c['params']['objective'])
        for a in c.get('conjuntos', []):
            if a.get('pendente'):
                continue
            if not cid:
                # conta sem campanha deste objetivo: o conjunto é validado (validate_only) logo antes de ser criado
                adiados.append('%s / %s' % (c['nome'], a['nome']))
                continue
            p = dict(a['params'], name=a['nome'], campaign_id=cid, execution_options=['validate_only'])
            r, e = cli.chamar('%s/adsets' % cli.conta, p)
            if e:
                erros.append('conjunto %s: %s' % (a['nome'], e.get('error_user_msg') or e.get('message')))
            else:
                ok += 1
    rec = {'estrutura_sha256': h, 'quando': datetime.datetime.now().isoformat(timespec='seconds'),
           'validados_ok': ok, 'erros': erros, 'validar_na_criacao': adiados}
    gravar(recibo, rec)
    print('validação: %d ok, %d erro(s), %d conjunto(s) a validar na criação (ainda sem campanha na conta). Recibo: %s'
          % (ok, len(erros), len(adiados), os.path.basename(recibo)))
    for e in erros:
        print('   - ' + e)
    return 0 if not erros else 1


def _procurar(cli, caminho, nome):
    itens, err = cli.paginar(caminho, {'fields': 'id,name', 'limit': 500})
    for x in itens or []:
        if x.get('name') == nome:
            return x['id']
    return None


def _criar(cli, caminho, dados, nome, caminho_busca):
    """POST com a regra de rede: se a conexão cair, procura pelo nome antes de repetir."""
    r, e = cli.chamar(caminho, dados)
    if e and e.get(REDE):
        achado = _procurar(cli, caminho_busca, nome)
        if achado:
            return {'id': achado, 'recuperado_apos_rede': True}, None
        r, e = cli.chamar(caminho, dados)
    return r, e


def aplicar(cli, est, h, faltam, recibo, registro):
    rec = carregar(recibo, None)
    if not rec or rec.get('estrutura_sha256') != h:
        print('RECUSADO: não há recibo de validação para este arquivo de estrutura. Rode --validar antes.')
        return 2
    if rec.get('erros'):
        print('RECUSADO: a última validação deste arquivo teve %d erro(s). Corrija e valide de novo.' % len(rec['erros']))
        return 2
    reg = carregar(registro, {'campanhas': {}, 'conjuntos': {}, 'anuncios': {}, 'criativos': {}, 'erros': [], 'pulados': []})
    reg['erros'], reg['pulados'] = [], []
    ja_camp, ja_conj, ja_ads, _ = existentes(cli)
    print('na conta: %d campanhas, %d conjuntos, %d anúncios' % (len(ja_camp), len(ja_conj), len(ja_ads)))
    criados = {'campanhas': 0, 'conjuntos': 0, 'anuncios': 0, 'recuperados_apos_rede': 0}
    falhas, tipos_pulados = {}, {}
    conta = cli.conta
    for c in est['campanhas']:
        cid = ja_camp.get(c['nome']) or reg['campanhas'].get(c['nome'])
        if not cid:
            r, e = _criar(cli, '%s/campaigns' % conta, dict(c['params'], name=c['nome'], status='PAUSED'),
                          c['nome'], '%s/campaigns' % conta)
            if e:
                reg['erros'].append([c['nome'], e])
                gravar(registro, reg)
                print('ERRO campanha %s: %s' % (c['nome'], e.get('message')))
                continue
            cid = r['id']
            criados['campanhas'] += 1
            criados['recuperados_apos_rede'] += bool(r.get('recuperado_apos_rede'))
            print('campanha criada: %s -> %s%s' % (c['nome'], cid, '  (recuperada pelo nome após queda de rede)' if r.get('recuperado_apos_rede') else ''))
        reg['campanhas'][c['nome']] = cid
        gravar(registro, reg)
        for a in c.get('conjuntos', []):
            chave = '%s / %s' % (c['nome'], a['nome'])
            if a.get('pendente'):
                reg['pulados'].append([chave, a['pendente']])
                continue
            ass = assinatura(c, a)
            if ass in tipos_pulados:
                reg['pulados'].append([chave, 'tipo pulado após 3 falhas: %s' % tipos_pulados[ass]])
                continue
            aid = ja_conj.get((c['nome'], a['nome'])) or reg['conjuntos'].get(chave)
            if not aid and chave in rec.get('validar_na_criacao', []):
                r, e = cli.chamar('%s/adsets' % conta, dict(a['params'], name=a['nome'], campaign_id=cid,
                                                           execution_options=['validate_only']))
                if e:
                    reg['erros'].append([chave + ' (validate_only)', e])
                    gravar(registro, reg)
                    print('   RECUSADO na validação antes de criar %s: %s' % (a['nome'], e.get('error_user_msg') or e.get('message')))
                    continue
            if not aid:
                r, e = _criar(cli, '%s/adsets' % conta, dict(a['params'], name=a['nome'], campaign_id=cid, status='PAUSED'),
                              a['nome'], '%s/adsets' % cid)
                if e:
                    msg = e.get('error_user_msg') or e.get('message')
                    reg['erros'].append([chave, e])
                    gravar(registro, reg)
                    print('   ERRO conjunto %s: %s' % (a['nome'], msg))
                    falhas[ass] = falhas.get(ass, 0) + 1
                    if falhas[ass] >= 3 and not e.get(REDE):
                        tipos_pulados[ass] = msg[:120]
                    continue
                falhas[ass] = 0
                aid = r['id']
                criados['conjuntos'] += 1
                criados['recuperados_apos_rede'] += bool(r.get('recuperado_apos_rede'))
                print('   conjunto %-44s -> %s%s' % (a['nome'][:44], aid,
                                                    '  (recuperado pelo nome após queda de rede, sem duplicar)' if r.get('recuperado_apos_rede') else ''))
            reg['conjuntos'][chave] = aid
            gravar(registro, reg)
            for ad in a.get('anuncios', []):
                kad = '%s / %s' % (chave, ad['nome'])
                if ja_ads.get((a['nome'], ad['nome'])) or reg['anuncios'].get(kad):
                    reg['anuncios'].setdefault(kad, ja_ads.get((a['nome'], ad['nome'])))
                    continue
                crid = reg['criativos'].get(ad['criativo']['name'])
                if not crid:
                    r, e = _criar(cli, '%s/adcreatives' % conta, ad['criativo'], ad['criativo']['name'], '%s/adcreatives' % conta)
                    if e:
                        reg['erros'].append([kad + ' (criativo)', e])
                        gravar(registro, reg)
                        continue
                    crid = r['id']
                    reg['criativos'][ad['criativo']['name']] = crid
                r, e = _criar(cli, '%s/ads' % conta, {'name': ad['nome'], 'adset_id': aid, 'creative': {'creative_id': crid},
                                                      'status': 'PAUSED'}, ad['nome'], '%s/ads' % aid)
                if e:
                    reg['erros'].append([kad, e])
                    gravar(registro, reg)
                    continue
                reg['anuncios'][kad] = r['id']
                criados['anuncios'] += 1
                criados['recuperados_apos_rede'] += bool(r.get('recuperado_apos_rede'))
                gravar(registro, reg)
    gravar(registro, reg)
    print('criados nesta execução: %s | erros %d | pulados %d' % (criados, len(reg['erros']), len(reg['pulados'])))
    if reg['erros']:
        print('rode --aplicar de novo para tentar só o que faltou; o que existe não é duplicado.')
    return criados


def comparar_conjunto(a, real):
    divs = []
    p, tr, tp = a['params'], real.get('targeting') or {}, a['params']['targeting']
    if (real.get('status') or '').upper() != 'PAUSED':
        divs.append('status: conta=%s (esperado PAUSED)' % real.get('status'))
    if int(real.get('daily_budget') or -1) != int(p.get('daily_budget') or -1):
        divs.append('daily_budget: conta=%s estrutura=%s' % (real.get('daily_budget'), p.get('daily_budget')))
    for k in ('optimization_goal', 'billing_event', 'destination_type'):
        if real.get(k) != p.get(k):
            divs.append('%s: conta=%s estrutura=%s' % (k, real.get(k), p.get(k)))
    if (tr.get('age_min'), tr.get('age_max')) != (tp.get('age_min'), tp.get('age_max')):
        divs.append('idade: conta=%s-%s estrutura=%s-%s' % (tr.get('age_min'), tr.get('age_max'), tp.get('age_min'), tp.get('age_max')))
    if set(tr.get('publisher_platforms') or []) != set(tp.get('publisher_platforms') or []):
        divs.append('plataformas: conta=%s estrutura=%s' % (tr.get('publisher_platforms'), tp.get('publisher_platforms')))

    def n_geo(g):
        g = g or {}
        return sum(len(g.get(k) or []) for k in ('custom_locations', 'cities', 'regions', 'countries'))
    if n_geo(tr.get('geo_locations')) != n_geo(tp.get('geo_locations')):
        divs.append('n localizações: conta=%d estrutura=%d' % (n_geo(tr.get('geo_locations')), n_geo(tp.get('geo_locations'))))
    return divs


def prova(cli, est):
    camps, err = cli.paginar('%s/campaigns' % cli.conta, {'fields': 'id,name,objective,status', 'limit': 200})
    if err:
        print('ERRO lendo campanhas: %s' % err.get('message'))
        return 1
    por_nome = {c['name']: c for c in camps}
    total = div = 0
    for c in est['campanhas']:
        real_c = por_nome.get(c['nome'])
        if not real_c:
            print('- %s -> NÃO ENCONTRADA' % c['nome'])
            div += 1
            continue
        if real_c.get('objective') != c['params']['objective']:
            print('- %s -> objective conta=%s estrutura=%s' % (c['nome'], real_c.get('objective'), c['params']['objective']))
            div += 1
        conjs, _ = cli.paginar('%s/adsets' % real_c['id'], {'fields': 'id,name,status,daily_budget,optimization_goal,'
                                                           'billing_event,destination_type,targeting', 'limit': 500})
        reais = {x['name']: x for x in conjs}
        for a in c.get('conjuntos', []):
            if a.get('pendente'):
                continue
            total += 1
            real = reais.get(a['nome'])
            if not real:
                print('- %s / %s -> NÃO ENCONTRADO' % (c['nome'], a['nome']))
                div += 1
                continue
            d = comparar_conjunto(a, real)
            if d:
                div += 1
                print('- %s / %s -> DIVERGE: %s' % (c['nome'], a['nome'], ' | '.join(d)))
    print('conjuntos conferidos: %d | com divergência: %d' % (total, div))
    return 0 if div == 0 else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--estrutura', required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--simular', action='store_true')
    g.add_argument('--validar', action='store_true')
    g.add_argument('--aplicar', action='store_true')
    g.add_argument('--prova', action='store_true')
    ap.add_argument('--registro', default=os.path.join('dados', 'subida', 'registro.json'))
    ap.add_argument('--recibo', default=None, help='padrão: <pasta do registro>/recibo_validacao.json')
    ap.add_argument('--conta-simulada', default=None, help='arquivo JSON de uma conta simulada (roda offline)')
    ap.add_argument('--var', action='append', default=[], help='CHAVE=valor para os marcadores {{CHAVE}}')
    a = ap.parse_args(argv)
    extras = dict(v.split('=', 1) for v in a.var)
    est, h, faltam = carregar_estrutura(a.estrutura, extras)
    if not (a.validar or a.aplicar or a.prova):
        return simular(est, faltam)
    recibo = a.recibo or os.path.join(os.path.dirname(os.path.abspath(a.registro)), 'recibo_validacao.json')
    if a.conta_simulada:
        cli = ClienteSimulado(arquivo=a.conta_simulada)
    else:
        cli = Cliente()
        if not cli.pronto():
            print('FALTA CREDENCIAL: defina META_TOKEN e META_CONTA (act_<id>). Ver .env.example.')
            return 1
    if a.validar:
        return validar(cli, est, h, faltam, recibo)
    if a.aplicar:
        r = aplicar(cli, est, h, faltam, recibo, a.registro)
        return r if isinstance(r, int) else 0
    return prova(cli, est)


if __name__ == '__main__':
    sys.exit(main())
