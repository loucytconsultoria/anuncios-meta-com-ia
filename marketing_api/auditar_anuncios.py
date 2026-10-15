# -*- coding: utf-8 -*-
"""Auditoria pós-subida: lê CADA anúncio da conta (sem amostragem) e aplica regras de conferência.

Por que existe: criar por script é rápido, e um erro de configuração se replica em dezenas de
anúncios em segundos. Esta leitura de volta compara o que a conta realmente guardou com a fonte
de verdade (a estrutura aprovada) e com regras de negócio:

  - botão (call_to_action) compatível com o destino do conjunto (site, mensagem, perfil...)
  - link compatível com o destino
  - texto do anúncio igual ao texto aprovado na estrutura
  - identificação obrigatória presente no texto (ex.: "Anúncio pago por ...")
  - nenhum resto de rascunho no texto ("Legenda A", "Opção 2", "lorem ipsum"...)
  - nome do anúncio com o código da peça
  - vídeo sem botão (a plataforma pode pôr um botão padrão para um destino que você não escolheu)

Saída: JSON com cada anúncio e seus problemas + resumo por tipo de problema.

Uso:
  python -m marketing_api.auditar_anuncios --estrutura exemplos/estrutura_exemplo.json --regras exemplos/regras_auditoria.json \
      [--conta-simulada dados/subida/conta.json] [--saida dados/subida/auditoria.json]
"""
import argparse
import collections
import json
import os
import re
import sys

try:
    from .cliente import Cliente
    from .simulado import ClienteSimulado
    from .subir_estrutura import carregar_estrutura
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cliente import Cliente
    from simulado import ClienteSimulado
    from subir_estrutura import carregar_estrutura

CAMPOS_AD = ('id,name,effective_status,adset_id,adset{id,name},'
             'creative{id,name,call_to_action_type,object_story_spec,video_id}')


def _spec(criativo):
    oss = (criativo or {}).get('object_story_spec') or {}
    d = oss.get('video_data') or oss.get('link_data') or {}
    cta = d.get('call_to_action') or {}
    return {'texto': d.get('message') or '', 'cta': cta.get('type') or (criativo or {}).get('call_to_action_type'),
            'link': d.get('link') or (cta.get('value') or {}).get('link'), 'video': bool(d.get('video_id') or oss.get('video_data'))}


def ler_anuncios(cli):
    ads, err = cli.paginar('%s/ads' % cli.conta, {'fields': CAMPOS_AD, 'limit': 300})
    if err:
        raise SystemExit('ERRO lendo anúncios: %s' % err.get('message'))
    conjs, _ = cli.paginar('%s/adsets' % cli.conta, {'fields': 'id,name', 'limit': 500})
    nome_conj = {str(c['id']): c['name'] for c in conjs}
    for a in ads:
        if not a.get('adset'):
            a['adset'] = {'id': a.get('adset_id'), 'name': nome_conj.get(str(a.get('adset_id')), '?')}
    return ads


def auditar(ads, est, regras):
    esperado_conj, esperado_ad = {}, {}
    for c in est['campanhas']:
        for a in c.get('conjuntos', []):
            esperado_conj[a['nome']] = a
            for ad in a.get('anuncios', []):
                esperado_ad[(a['nome'], ad['nome'])] = _spec(ad['criativo'])
    proibidos = [re.compile(p, re.I | re.M) for p in regras.get('padroes_proibidos', [])]
    rx_cod = re.compile(regras['codigo_no_nome']) if regras.get('codigo_no_nome') else None
    obrig = regras.get('texto_obrigatorio')
    out, cont = [], collections.Counter()
    for ad in ads:
        conj = (ad.get('adset') or {}).get('name')
        real = _spec(ad.get('creative'))
        probs = []
        a_est = esperado_conj.get(conj)
        if not a_est:
            probs.append('conjunto fora da estrutura')
        else:
            dest = regras.get('destinos', {}).get(a_est.get('destino'))
            if dest:
                if dest.get('cta') != real['cta']:
                    probs.append('botão %s ≠ destino %s (%s)' % (real['cta'], a_est.get('destino'), dest.get('cta')))
                pref = dest.get('link_prefixo')
                if pref and not (real['link'] or '').startswith(pref):
                    probs.append('link %s ≠ destino %s' % (real['link'], a_est.get('destino')))
        exp = esperado_ad.get((conj, ad.get('name')))
        if exp is None:
            probs.append('anúncio fora da estrutura')
        elif exp['texto'].strip() != real['texto'].strip():
            probs.append('texto ≠ texto aprovado')
        if obrig and obrig not in real['texto']:
            probs.append('sem identificação obrigatória')
        if not real['texto'].strip():
            probs.append('texto vazio')
        if any(p.search(real['texto']) for p in proibidos):
            probs.append('resto de rascunho no texto')
        if rx_cod and not rx_cod.search(ad.get('name') or ''):
            probs.append('nome sem código da peça')
        if regras.get('video_exige_botao') and real['video'] and not real['cta']:
            probs.append('vídeo sem botão')
        for p in probs:
            cont[re.split(r' \(| ≠| \d', p)[0][:50]] += 1
        out.append({'ad': ad.get('id'), 'nome': ad.get('name'), 'conjunto': conj, 'status': ad.get('effective_status') or ad.get('status'),
                    'cta': real['cta'], 'link': real['link'], 'problemas': probs})
    return out, cont


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--estrutura', required=True)
    ap.add_argument('--regras', required=True)
    ap.add_argument('--conta-simulada', default=None)
    ap.add_argument('--saida', default=os.path.join('dados', 'subida', 'auditoria_anuncios.json'))
    ap.add_argument('--var', action='append', default=[])
    a = ap.parse_args(argv)
    est, _, _ = carregar_estrutura(a.estrutura, dict(v.split('=', 1) for v in a.var))
    with open(a.regras, encoding='utf-8') as f:
        regras = json.load(f)
    cli = ClienteSimulado(arquivo=a.conta_simulada) if a.conta_simulada else Cliente()
    if not cli.pronto():
        print('FALTA CREDENCIAL: defina META_TOKEN e META_CONTA. Ver .env.example.')
        return 1
    out, cont = auditar(ler_anuncios(cli), est, regras)
    os.makedirs(os.path.dirname(os.path.abspath(a.saida)), exist_ok=True)
    with open(a.saida, 'w', encoding='utf-8') as f:
        json.dump({'anuncios': out, 'resumo': dict(cont)}, f, ensure_ascii=False, indent=1)
    n = sum(1 for o in out if o['problemas'])
    print('anúncios lidos: %d | com problema: %d' % (len(out), n))
    for p, k in cont.most_common():
        print('  %3d  %s' % (k, p))
    for o in out:
        if o['problemas']:
            print('  - %s [%s]: %s' % (o['nome'], o['conjunto'], '; '.join(o['problemas'])))
    return 0 if n == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
