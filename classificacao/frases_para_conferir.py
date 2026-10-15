# -*- coding: utf-8 -*-
"""Separa do relatório final toda frase com número, em blocos, para a checagem independente.

Tabelas geradas por script não entram (já saem da base); entram texto corrido, itens de lista e
linhas de tabela escritas à mão. Cada bloco é conferido por três verificadores
(workflows/verificar_tres.js, modo checagem) e o resultado é juntado por consenso.py checagem.

Uso: python -m classificacao.frases_para_conferir --documento relatorio.md --saida dados/checagem [--por-bloco 15]
"""
import argparse
import sys
import json
import os
import re

RX_NUM = re.compile(r'\d')


def extrair(documento, saida, por_bloco=15, pular_tabelas=True):
    os.makedirs(saida, exist_ok=True)
    frases, secao = [], ''
    with open(documento, encoding='utf-8') as f:
        texto = f.read()
    for linha in texto.split('\n'):
        t = linha.strip()
        if t.startswith('#'):
            if t.startswith('## '):
                secao = t[3:].strip()
            continue
        if not t or t.startswith('![') or t.startswith('|---') or t.startswith('<!--'):
            continue
        if pular_tabelas and t.startswith('|'):
            continue
        for pedaco in re.split(r'(?<=[.;:])\s+(?=[A-ZÀ-Ú])', t):
            p = pedaco.strip(' -*')
            if len(p) > 12 and RX_NUM.search(p):
                frases.append({'id': 'F%03d' % (len(frases) + 1), 'secao': secao, 'frase': p})
    with open(os.path.join(saida, '_frases.json'), 'w', encoding='utf-8') as f:
        json.dump(frases, f, ensure_ascii=False, indent=0)
    blocos = []
    for i in range(0, len(frases), por_bloco):
        nome = 'bloco_%02d' % (len(blocos) + 1)
        with open(os.path.join(saida, nome + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'bloco': nome, 'itens': frases[i:i + por_bloco]}, f, ensure_ascii=False, indent=0)
        blocos.append(nome)
    return frases, blocos


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--documento', required=True)
    ap.add_argument('--saida', required=True)
    ap.add_argument('--por-bloco', type=int, default=15)
    a = ap.parse_args()
    frases, blocos = extrair(a.documento, a.saida, a.por_bloco)
    print(json.dumps({'frases_com_numero': len(frases), 'blocos': blocos}))


if __name__ == '__main__':
    main()
