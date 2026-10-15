# -*- coding: utf-8 -*-
"""Junta as leituras de volta aos anúncios, grava as tabelas na base e escreve o relatório.

Cada anúncio herda os códigos das suas imagens distintas e do seu texto distinto (via
_mapa_itens.json). Tema "da peça inteira" = união do que veio da imagem e do texto, com a fonte
marcada (imagem, texto ou ambos). Pesos por impressões (ponto médio da faixa).

Nenhum número do relatório é digitado: tudo sai da base e dos arquivos de controle
(_validacao.json, _auditoria.json, _qualidade.json, _consenso.json). As frases com número podem
depois passar pela checagem de três verificadores (frases_para_conferir.py + consenso.py).

Tabelas gravadas na base: peca_temas(ad_id, tema, fonte), peca_tom(ad_id, tom, fonte)

Uso: python -m classificacao.relatorio --banco dados/banco/base_anuncios.sqlite --trabalho dados/leitura \
        --vocabulario <vocabulario.json> --saida relatorio.md
"""
import argparse
import sys
import json
import os
import sqlite3
from collections import Counter, defaultdict

try:
    from .concordancia import ler
    from .vocabulario import Vocabulario
except ImportError:
    from concordancia import ler
    from vocabulario import Vocabulario


def _json(caminho):
    if os.path.exists(caminho):
        with open(caminho, encoding='utf-8') as f:
            return json.load(f)
    return None


def pct(a, b):
    return ('%.1f%%' % (100.0 * a / b)).replace('.', ',') if b else '-'


def dec(v):
    return str(v).replace('.', ',') if isinstance(v, float) else str(v)


def num(v):
    return '{:,.0f}'.format(v or 0).replace(',', '.')


def juntar(banco, trab):
    mapa = _json(os.path.join(trab, '_mapa_itens.json')) or {}
    img, txt = ler(trab, 'img', 'img'), ler(trab, 'txt', 'txt')
    db = sqlite3.connect(banco)
    ads = {r[0]: {'entidade': r[1], 'imp': r[2] or 0} for r in
           db.execute('SELECT ad_id, entidade, impressoes_meio FROM anuncios')}
    temas, toms = [], []
    lidos = set()
    for ad, m in mapa.items():
        if ad not in ads:
            continue
        de_img, de_txt = set(), set()
        for h in m.get('imagens') or []:
            if h in img:
                lidos.add(ad)
                de_img |= set(img[h].get('temas') or [])
                if img[h].get('tom'):
                    toms.append((ad, img[h]['tom'], 'imagem'))
        t = m.get('texto')
        if t and t in txt:
            lidos.add(ad)
            de_txt |= set(txt[t].get('temas') or [])
            if txt[t].get('tom'):
                toms.append((ad, txt[t]['tom'], 'texto'))
        for tema in sorted(de_img | de_txt):
            fonte = 'ambos' if tema in de_img and tema in de_txt else ('imagem' if tema in de_img else 'texto')
            temas.append((ad, tema, fonte))
    for nome, linhas, cols in (('peca_temas', temas, ('ad_id', 'tema', 'fonte')), ('peca_tom', toms, ('ad_id', 'tom', 'fonte'))):
        db.execute('DROP TABLE IF EXISTS %s' % nome)
        db.execute('CREATE TABLE %s (%s)' % (nome, ', '.join(cols)))
        db.executemany('INSERT INTO %s VALUES (?,?,?)' % nome, linhas)
    db.commit()
    return db, ads, lidos, temas, toms


def escrever(banco, trab, caminho_voc, saida):
    voc = Vocabulario.carregar(caminho_voc)
    db, ads, lidos, temas, toms = juntar(banco, trab)
    imp_lidos = sum(ads[a]['imp'] for a in lidos)
    por_tema = defaultdict(set)
    for ad, tema, _ in temas:
        por_tema[tema].add(ad)
    L = ['# Relatório de criativos', '',
         '_Gerado por script a partir da base e das leituras validadas. Gasto e impressões da Meta são faixas; '
         'aqui se usa o ponto médio das impressões como peso._', '',
         '## Cobertura', '',
         '- Anúncios na base: %s; com pelo menos uma unidade lida: %s (%s).' % (num(len(ads)), num(len(lidos)), pct(len(lidos), len(ads))),
         '- Impressões (ponto médio) dos anúncios lidos: %s de %s (%s).' % (
             num(imp_lidos), num(sum(a['imp'] for a in ads.values())), pct(imp_lidos, sum(a['imp'] for a in ads.values()))),
         '- Vocabulário: %s.' % voc.descrever(), '',
         '## Temas da peça inteira (imagem ou texto), por peso de impressões', '',
         '| tema | anúncios | % das impressões lidas |', '|---|---|---|']
    for tema, conj in sorted(por_tema.items(), key=lambda x: -sum(ads[a]['imp'] for a in x[1])):
        L.append('| %s | %d | %s |' % (tema, len(conj), pct(sum(ads[a]['imp'] for a in conj), imp_lidos)))
    L += ['', '## Temas por entidade (três principais)', '', '| entidade | anúncios lidos | temas principais |', '|---|---|---|']
    por_ent = defaultdict(Counter)
    lidos_ent = Counter(ads[a]['entidade'] for a in lidos)
    for ad, tema, _ in temas:
        por_ent[ads[ad]['entidade']][tema] += ads[ad]['imp']
    for ent, n in lidos_ent.most_common():
        L.append('| %s | %d | %s |' % (ent, n, ', '.join(t for t, _ in por_ent[ent].most_common(3)) or '(só identificação)'))
    tom_c = Counter(t for _, t, _ in toms)
    L += ['', '## Tom (leituras de imagem e de texto)', '', '| tom | leituras |', '|---|---|']
    L += ['| %s | %d |' % (t, n) for t, n in tom_c.most_common()]

    val = _json(os.path.join(trab, '_validacao.json')) or {}
    aud = _json(os.path.join(trab, '_auditoria.json')) or {}
    qual = _json(os.path.join(trab, '_qualidade.json')) or {}
    L += ['', '## Controles de qualidade', '']
    if val:
        for sub, st in val.items():
            L.append('- Validação `%s`: %d lotes, %d válidos, %d sem resultado, %d inválidos, %d códigos fora do vocabulário convertidos em `outro`.'
                     % (sub, st['lotes'], st['ok'], st['sem_resultado'], st['invalidos'], st['codigos_corrigidos']))
    if aud:
        L.append('- Auditoria de alinhamento: %d gravações conferidas na transcrição, %d desalinhadas (apagadas e devolvidas à fila: %s).'
                 % (aud['gravacoes_auditadas'], aud['desalinhadas'], 'sim' if aud.get('aplicado') else 'não'))
    qi, qt = qual.get('imagens') or {}, qual.get('textos') or {}
    if qi.get('comparados'):
        extras = '; '.join('%s %s' % (k, dec(v)) for k, v in qi.items() if k.startswith(('jaccard', 'pct_')) and k != 'pct_f1_acima_0_9')
        L.append('- Releitura de controle de imagens: %d itens; F1 médio das palavras transcritas %s; %s%% com F1 >= 0,9; %s.'
                 % (qi['comparados'], dec(qi['f1_palavras_media']), dec(qi['pct_f1_acima_0_9']), extras))
    if qt.get('comparados'):
        extras = '; '.join('%s %s' % (k, dec(v)) for k, v in qt.items() if k.startswith(('jaccard', 'pct_')))
        L.append('- Releitura de controle de textos: %d itens; %s.' % (qt['comparados'], extras))
    cons = None
    for p in (os.path.join(trab, 'verificacao', '_consenso.json'),):
        cons = _json(p)
    if cons:
        L.append('- Consenso de %d verificadores sobre %d itens: %s.' % (
            cons['verificadores'], cons['itens'], ', '.join('%s %d' % kv for kv in sorted(cons['resumo'].items()))))
    with open(saida, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    db.close()
    return saida


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--banco', required=True)
    ap.add_argument('--trabalho', required=True)
    ap.add_argument('--vocabulario', required=True)
    ap.add_argument('--saida', required=True)
    a = ap.parse_args()
    print('relatório:', escrever(a.banco, a.trabalho, a.vocabulario, a.saida))


if __name__ == '__main__':
    main()
