# -*- coding: utf-8 -*-
"""Amostra para auditar a atribuição ANTES de escrever qualquer número.

Sorteia anúncios por tipo de vínculo (os mais fracos primeiro, com amostra maior) e os que
ficaram FORA dos totais por estarem em página da entidade mas pagos por outro. Grava um .md com
uma linha por anúncio para um leitor independente (pessoa ou subagente que não escreveu as
regras) dar o veredito: CERTO, ERRADO ou DÚVIDA. Erro encontrado vira regra nova no
atribuidor, e a consolidação roda de novo.

Uso: python -m coleta.amostra_auditoria --banco dados/banco/base_anuncios.sqlite --saida amostra.md [--por-vinculo 40]
"""
import argparse
import sys
import random
import sqlite3

ORDEM = ['nome_rodape', 'apelido_rodape', 'nome_rodape_entrega_fora_regiao', 'pagina', 'busca_documento',
         'doc_texto', 'doc_rodape_multiplo', 'doc_rodape']


def cel(x):
    return str(x or '').replace('|', '/').replace('\n', ' ')[:220]


def gerar(banco, saida, por_vinculo=40, semente=20260101):
    db = sqlite3.connect(banco)
    rnd = random.Random(semente)
    linhas = ['# Amostra para auditoria da atribuição', '',
              'Veredito por linha: CERTO (o anúncio é mesmo da entidade e pago por ela), ERRADO '
              '(outra entidade, outra região ou pago por terceiro) ou DÚVIDA.', '']
    cont, n = {}, 0
    for v in ORDEM:
        rows = db.execute('SELECT ad_id, entidade, rodape_legal, pagina FROM anuncios WHERE vinculo=?', (v,)).fetchall()
        cont[v] = len(rows)
        k = min(por_vinculo if v != 'doc_rodape' else max(1, por_vinculo // 2), len(rows))
        am = rnd.sample(rows, k)
        if not am:
            continue
        linhas += ['## vínculo = %s (%d no banco, %d na amostra)' % (v, len(rows), len(am)), '',
                   '| id | atribuído a | rodapé legal | página | veredito |', '|---|---|---|---|---|']
        for ad, ent, byl, pag in am:
            n += 1
            linhas.append('| %s | %s | %s | %s |  |' % (ad, cel(ent), cel(byl), cel(pag)))
        linhas.append('')
    rows = db.execute('SELECT ad_id, entidade_dona_pagina, rodape_legal, pagina FROM pagos_por_outro').fetchall() \
        if db.execute("SELECT name FROM sqlite_master WHERE name='pagos_por_outro'").fetchone() else []
    rows = [r for r in rows if r and r[0] is not None]
    cont['pagos_por_outro'] = len(rows)
    am = rnd.sample(rows, min(por_vinculo, len(rows)))
    if am:
        linhas += ['## Fora dos totais: página da entidade, pago por outro (%d no banco)' % len(rows), '',
                   'Veredito: CERTO_FORA (pago mesmo por outro) ou DEVERIA_CONTAR (excluído por engano).', '',
                   '| id | dono da página | rodapé legal | página | veredito |', '|---|---|---|---|---|']
        for ad, dono, byl, pag in am:
            n += 1
            linhas.append('| %s | %s | %s | %s |  |' % (ad, cel(dono), cel(byl), cel(pag)))
    with open(saida, 'w', encoding='utf-8') as f:
        f.write('\n'.join(linhas) + '\n')
    return n, cont


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--banco', required=True)
    ap.add_argument('--saida', required=True)
    ap.add_argument('--por-vinculo', type=int, default=40)
    a = ap.parse_args()
    n, cont = gerar(a.banco, a.saida, a.por_vinculo)
    print('amostra: %d anúncios -> %s' % (n, a.saida))
    print('no banco por vínculo:', cont)


if __name__ == '__main__':
    main()
