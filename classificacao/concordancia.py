# -*- coding: utf-8 -*-
"""Concordância entre a leitura principal e a releitura de controle (amostra independente).

Cada item da amostra foi lido DUAS vezes, por subagentes que não viram a resposta um do outro.
  - transcrição: precisão/cobertura/F1 das palavras (multiconjunto, sem acento, minúsculas)
  - classificação: índice de Jaccard entre os conjuntos de códigos de cada campo de lista e
    % de igualdade nos campos de valor único (ex.: tom)
A concordância não prova que as duas leituras estão certas, mas mede o ruído do processo e
aponta os piores casos para revisão humana.

Saída: <trabalho>/_qualidade.json
Uso: python -m classificacao.concordancia --trabalho dados/leitura --vocabulario <vocabulario.json>
"""
import argparse
import sys
import glob
import json
import os
import re
import unicodedata
from collections import Counter

try:
    from .vocabulario import Vocabulario
except ImportError:
    from vocabulario import Vocabulario


def palavras(t):
    t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().lower()
    return Counter(re.findall(r'[a-z0-9]+', t))


def f1_palavras(a, b):
    pa, pb = palavras(a), palavras(b)
    if not pa and not pb:
        return 1.0
    inter = sum((pa & pb).values())
    p = inter / max(sum(pa.values()), 1)
    r = inter / max(sum(pb.values()), 1)
    return 2 * p * r / (p + r) if p + r else 0.0


def jaccard(a, b):
    a, b = set(a or []), set(b or [])
    return len(a & b) / len(a | b) if a | b else 1.0


def ler(trab, sub, chave):
    out = {}
    for f in glob.glob(os.path.join(trab, 'resultados_' + sub, '*.jsonl')):
        with open(f, encoding='utf-8', errors='replace') as fh:
            for l in fh:
                try:
                    d = json.loads(l)
                    if not d.get('erro'):
                        out[d[chave]] = d
                except Exception:
                    pass
    return out


def _comparar(a, b, campos, voc, com_texto):
    comuns = sorted(k for k in b if k in a)
    res = {'comparados': len(comuns)}
    if not comuns:
        return res
    if com_texto:
        f1s = [(round(f1_palavras(a[k].get('texto_na_imagem'), b[k].get('texto_na_imagem')), 3), k) for k in comuns]
        vals = [x for x, _ in f1s]
        res['f1_palavras_media'] = round(sum(vals) / len(vals), 3)
        res['f1_palavras_mediana'] = sorted(vals)[len(vals) // 2]
        res['pct_f1_acima_0_9'] = round(100 * sum(1 for x in vals if x >= 0.9) / len(vals), 1)
        res['piores'] = sorted(f1s)[:5]
    for c in campos:
        if voc.campos[c]['tipo'] == 'um':
            res['pct_%s_igual' % c] = round(100 * sum(a[k].get(c) == b[k].get(c) for k in comuns) / len(comuns), 1)
        else:
            js = [jaccard(a[k].get(c), b[k].get(c)) for k in comuns]
            res['jaccard_%s_medio' % c] = round(sum(js) / len(js), 3)
    return res


def medir(trab, caminho_voc):
    voc = Vocabulario.carregar(caminho_voc)
    res = {'imagens': _comparar(ler(trab, 'img', 'img'), ler(trab, 'qa', 'img'), voc.campos_para('img'), voc, True),
           'textos': _comparar(ler(trab, 'txt', 'txt'), ler(trab, 'qatxt', 'txt'), voc.campos_para('txt'), voc, False)}
    with open(os.path.join(trab, '_qualidade.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--trabalho', required=True)
    ap.add_argument('--vocabulario', required=True)
    a = ap.parse_args()
    print(json.dumps(medir(a.trabalho, a.vocabulario), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
