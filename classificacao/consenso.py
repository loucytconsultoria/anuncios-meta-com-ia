# -*- coding: utf-8 -*-
"""Consenso de verificadores independentes (normalmente três) e fila de revisão humana.

Dois usos:

  rotulo    cada verificador classifica o mesmo item sem ver os outros (ex.: "esta afirmação
            sobre o anunciante se sustenta?", "qual a categoria deste ticket?"). Decisão por
            maioria absoluta; sem maioria, ou com votos faltando, o item vai para revisão humana.
            Entrada: <pasta>/v*.jsonl (v1.jsonl ou v1_bloco_01.jsonl) com {"id", "rotulo", "justificativa"}.

  checagem  cada frase com número de um relatório é reconferida contra a base por três
            verificadores que NÃO escreveram o texto. Qualquer DIVERGE manda a frase para revisão
            humana com o que cada verificador apurou (valor, consulta usada).
            Entrada: <pasta>/_frases.json e <pasta>/resultados/<bloco>_<verificador>.jsonl com
            {"id", "veredito": CONFERE|DIVERGE|NAO_VERIFICAVEL, "valor_na_base", "consulta", "observacao"}.

Uso:
  python -m classificacao.consenso rotulo   --pasta dados/verificacao [--votos 3]
  python -m classificacao.consenso checagem --pasta dados/checagem
"""
import argparse
import sys
import glob
import json
import os
from collections import Counter, defaultdict


def _jsonl(caminho):
    with open(caminho, encoding='utf-8', errors='replace') as fh:
        for l in fh:
            try:
                yield json.loads(l)
            except Exception:
                continue


def consenso_rotulo(pasta, votos_esperados=3, esperados=None):
    votos = defaultdict(list)
    arqs = sorted(glob.glob(os.path.join(pasta, 'v*.jsonl')))
    for f in arqs:
        v = os.path.basename(f)[:-6].split('_')[0]          # v1.jsonl ou v1_bloco_01.jsonl
        for d in _jsonl(f):
            if d.get('id') is not None:
                votos[str(d['id'])].append(dict(d, verificador=v))
    ids = sorted(set(esperados or []) | set(votos))
    decisoes, resumo = {}, Counter()
    for i in ids:
        vs = votos.get(i, [])
        c = Counter(str(x.get('rotulo')) for x in vs)
        top, n_top = (c.most_common(1)[0] if c else (None, 0))
        if len(vs) < votos_esperados:
            estado = 'incompleto'
        elif n_top * 2 > len(vs):
            estado = 'unanime' if n_top == len(vs) else 'maioria'
        else:
            estado = 'sem_maioria'
        decisoes[i] = {'decisao': top if estado in ('unanime', 'maioria') else None, 'estado': estado,
                       'votos': dict(c), 'revisao_humana': estado in ('sem_maioria', 'incompleto'),
                       'justificativas': [x.get('justificativa', '') for x in vs if str(x.get('rotulo')) != top]}
        resumo[estado] += 1
    out = {'verificadores': len({os.path.basename(f).split('_')[0].replace('.jsonl', '') for f in arqs}), 'itens': len(ids), 'resumo': dict(resumo), 'decisoes': decisoes}
    with open(os.path.join(pasta, '_consenso.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def consenso_checagem(pasta):
    with open(os.path.join(pasta, '_frases.json'), encoding='utf-8') as f:
        frases = {x['id']: x for x in json.load(f)}
    por_id = defaultdict(list)
    arquivos = glob.glob(os.path.join(pasta, 'resultados', '*.jsonl'))
    for f in arquivos:
        v = os.path.basename(f)[:-6].split('_')[-1]
        for d in _jsonl(f):
            if d.get('id') in frases:
                por_id[d['id']].append(dict(d, verificador=v))
    resumo, revisar = Counter(), []
    for i, fr in frases.items():
        vs = por_id.get(i, [])
        c = Counter(x.get('veredito') for x in vs)
        if not vs:
            estado = 'sem_checagem'
        elif c['DIVERGE']:
            estado = 'com_divergencia'
        elif c['NAO_VERIFICAVEL'] and not c['CONFERE']:
            estado = 'nao_verificavel'
        else:
            estado = 'ok'
        resumo[estado] += 1
        if c['DIVERGE']:
            revisar.append({'id': i, 'secao': fr.get('secao'), 'frase': fr['frase'], 'vereditos': dict(c),
                            'apuracoes': [{k: x.get(k) for k in ('verificador', 'veredito', 'valor_na_base', 'consulta', 'observacao')}
                                          for x in vs]})
    out = {'frases': len(frases), 'arquivos_de_verificador': len(arquivos), 'resumo': dict(resumo), 'para_revisar': revisar}
    with open(os.path.join(pasta, '_resultado.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('modo', choices=['rotulo', 'checagem'])
    ap.add_argument('--pasta', required=True)
    ap.add_argument('--votos', type=int, default=3)
    a = ap.parse_args()
    if a.modo == 'rotulo':
        out = consenso_rotulo(a.pasta, a.votos)
        print(json.dumps({k: out[k] for k in ('verificadores', 'itens', 'resumo')}, ensure_ascii=False))
        for i, d in out['decisoes'].items():
            if d['revisao_humana']:
                print('  revisar %s: %s %s' % (i, d['estado'], d['votos']))
    else:
        out = consenso_checagem(a.pasta)
        print(json.dumps({k: out[k] for k in ('frases', 'resumo')}, ensure_ascii=False))
        for r in out['para_revisar']:
            print('  - %s %s' % (r['id'], r['frase'][:120]))


if __name__ == '__main__':
    main()
