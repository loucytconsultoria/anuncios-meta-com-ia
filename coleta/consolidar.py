# -*- coding: utf-8 -*-
"""Consolidação: deduplica, atribui cada anúncio a uma entidade e grava SQLite + CSV.

Três passes:
  1. deduplicar  todos os arquivos ads_completos*.jsonl, do mais antigo ao mais novo: a versão
                 mais nova de cada anúncio vence (faixas de gasto/impressões de quem seguiu no ar).
                 Anúncio que só apareceu na varredura leve entra com campos mínimos.
  2. sinais      documento/nome no rodapé. Vínculo por NOME só vale se a maior parte da entrega
                 foi na região-alvo (evita homônimo de outra região); a busca pelo documento que
                 devolveu o anúncio vira o vínculo 'busca_documento' quando nada mais casou.
  3. páginas     páginas confirmadas pelos sinais fortes atribuem o resto do que veicularam,
                 desde que o rodapé se pareça com a entidade; pago por outro fica fora dos totais.

Gasto: a Meta só publica faixas. O ponto médio da faixa mais baixa (0 a 99) infla quem faz
muitos anúncios pequenos; nessa faixa o gasto é estimado por impressões x CPM de referência
(medido nos anúncios de 100 ou mais com faixa de impressões fechada), limitado à própria faixa.
Piso e teto são sempre somados à parte: o valor real está entre os dois.

Uso:
  python -m coleta.consolidar --entidades exemplos/entidades.csv --coleta dados/coleta --saida dados/banco \
      [--regiao-alvo "Meridiano"] [--inicio 2026-01-01] [--corte 2026-12-31]
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

try:
    from . import biblioteca as B
    from .atribuicao import Atribuidor
    from .coletar import ler_entidades, ler_jsonl
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import biblioteca as B
    from atribuicao import Atribuidor
    from coletar import ler_entidades, ler_jsonl

VINCULOS_POR_NOME = ('nome_rodape', 'apelido_rodape')


def pct_na_regiao(a, regiao):
    if not regiao:
        return None
    reg = {r.get('region'): float(r.get('percentage') or 0) for r in (a.get('delivery_by_region') or [])}
    return reg.get(regiao, 0.0) if reg else None


def dia(s):
    return (s or '')[:10]


def deduplicar(pasta):
    completos, fonte_doc = {}, defaultdict(set)
    n_linhas = 0
    for arq in sorted(glob.glob(os.path.join(pasta, 'ads_completos*.jsonl'))):
        for a in ler_jsonl(arq):
            n_linhas += 1
            f = a.pop('_fonte', '')
            if f.startswith('documento:'):
                fonte_doc[a['id']].add(f.split(':', 1)[1])
            completos[a['id']] = a              # a versão mais nova vale
    leves = {}
    for arq in sorted(glob.glob(os.path.join(pasta, 'varredura_*.jsonl'))):
        for a in ler_jsonl(arq):
            n_linhas += 1
            if a['id'] not in completos and a['id'] not in leves:
                leves[a['id']] = a
    todos = dict(leves)
    todos.update(completos)
    return todos, completos, fonte_doc, n_linhas


def atribuir_todos(todos, fonte_doc, at, regiao):
    forte = {}
    for i, a in todos.items():
        eid, v = at.sinal_forte(a)
        if v in VINCULOS_POR_NOME:
            p = pct_na_regiao(a, regiao)
            if p is not None and p < 0.5:
                eid, v = None, 'nome_fora_regiao:' + eid
        if not eid and fonte_doc.get(i):
            fd = sorted(fonte_doc[i])
            if len(fd) == 1 and v not in ('outro_documento', 'excludente'):
                eid, v = fd[0], 'busca_documento'
        forte[i] = (eid, v)
    at.registrar_paginas([(todos[i], eid) for i, (eid, v) in forte.items() if eid and v != 'busca_documento'])

    atrib, descartes, outro_pagador = {}, {}, []
    for i, a in todos.items():
        eid, v = forte[i]
        if v.startswith('nome_fora_regiao:'):
            # nome no rodapé, entrega fora da região: só conta se saiu de página já confirmada da
            # mesma entidade; senão é homônimo de outra região
            dono = v.split(':', 1)[1]
            if dono in at.paginas.get(str(a.get('page_id') or ''), set()):
                eid, v = dono, 'nome_rodape_entrega_fora_regiao'
            else:
                eid, v = None, 'nome_fora_regiao'
        elif not eid:
            eid, v = at.atribuir(a)
        if eid:
            atrib[i] = (eid, v)
        else:
            descartes[i] = v.split(':')[0]
            if v.startswith('pagina_outro_pagador'):
                outro_pagador.append((i, v.split(':')[1]))
    return atrib, descartes, outro_pagador


def cpm_referencia(todos, ids):
    sg = si = 0.0
    for i in ids:
        a = todos[i]
        gp, gt = B.faixa(a.get('spend'))
        ip, it = B.faixa(a.get('impressions'))
        if gp is not None and gp >= 100 and it is not None:
            sg += B.meio(gp, gt)
            si += B.meio(ip, it)
    return sg / si * 1000 if si else None


def linha_anuncio(i, eid, v, a, ent, cpm_ref, regiao, completos, corte):
    ip, it = B.faixa(a.get('impressions'))
    gp, gt = B.faixa(a.get('spend'))
    im, gm = B.meio(ip, it), B.meio(gp, gt)
    if gp is not None and gt is not None and gt < 100 and im is not None and cpm_ref:
        ge, metodo = min(max(im * cpm_ref / 1000.0, float(gp)), float(gt)), 'impressoes_x_cpm'
    else:
        ge, metodo = gm, 'ponto_medio'
    fim = dia(a.get('ad_delivery_stop_time'))
    p = pct_na_regiao(a, regiao)
    return {
        'ad_id': i, 'entidade_id': eid, 'entidade': ent['nome'], 'vinculo': v,
        'page_id': a.get('page_id'), 'pagina': a.get('page_name'), 'rodape_legal': a.get('bylines'),
        'inicio': dia(a.get('ad_delivery_start_time')), 'fim': fim,
        'ativo_no_corte': 'sim' if not fim or fim >= corte else 'nao',
        'impressoes_piso': ip, 'impressoes_teto': it if it is not None else ip, 'impressoes_meio': im,
        'gasto_piso': gp, 'gasto_teto': gt if gt is not None else gp, 'gasto_meio': gm,
        'gasto_estimado': round(ge, 2) if ge is not None else None, 'gasto_estimado_metodo': metodo,
        'moeda': a.get('currency'),
        'plataformas': '|'.join(a.get('publisher_platforms') or []),
        'pct_impressoes_regiao_alvo': round(p * 100, 1) if p is not None else None,
        'texto': ' || '.join(x for x in (a.get('ad_creative_bodies') or []) if x)[:30000],
        'titulo_link': ' | '.join(x for x in (a.get('ad_creative_link_titles') or []) if x),
        'link_biblioteca': 'https://www.facebook.com/ads/library/?id=%s' % i,
        'campos_completos': 'sim' if i in completos else 'nao',
    }


def consolidar(entidades, pasta_coleta, saida, regiao=None, inicio=None, corte=None, config=None, verbose=True):
    os.makedirs(saida, exist_ok=True)
    corte = corte or dt.date.today().isoformat()
    epor = {e['id']: e for e in entidades}
    at = Atribuidor(entidades, config)
    todos, completos, fonte_doc, n_linhas = deduplicar(pasta_coleta)
    atrib, descartes, outro_pagador = atribuir_todos(todos, fonte_doc, at, regiao)
    cpm_ref = cpm_referencia(todos, atrib)

    linhas = []
    for i, (eid, v) in sorted(atrib.items()):
        a = todos[i]
        fim = dia(a.get('ad_delivery_stop_time'))
        if inicio and fim and fim < inicio:
            continue
        linhas.append(linha_anuncio(i, eid, v, a, epor[eid], cpm_ref, regiao, completos, corte))

    # agregação por entidade: pisos e tetos somados separadamente
    por_e = defaultdict(list)
    for r in linhas:
        por_e[r['entidade_id']].append(r)
    ent_rows = []
    for e in entidades:
        rs = por_e.get(e['id'], [])
        soma = lambda k: sum(r[k] for r in rs if r[k] is not None)
        ent_rows.append({
            'entidade_id': e['id'], 'entidade': e['nome'], 'n_anuncios': len(rs),
            'n_paginas': len({r['page_id'] for r in rs}),
            'gasto_piso': soma('gasto_piso'), 'gasto_teto': soma('gasto_teto'),
            'gasto_estimado': round(soma('gasto_estimado'), 2),
            'impressoes_piso': soma('impressoes_piso'), 'impressoes_teto': soma('impressoes_teto'),
            'vinculos': json.dumps(dict(Counter(r['vinculo'] for r in rs)), ensure_ascii=False),
        })
    ent_rows.sort(key=lambda r: -r['gasto_estimado'])

    # ---------------------------------------------------------------- gravação
    db_path = os.path.join(saida, 'base_anuncios.sqlite')
    if os.path.exists(db_path):
        os.remove(db_path)
    db = sqlite3.connect(db_path)
    _tabela(db, 'anuncios', linhas)
    _tabela(db, 'entidades', ent_rows)
    _tabela(db, 'descartes', [{'ad_id': i, 'motivo': m} for i, m in sorted(descartes.items())])
    _tabela(db, 'pagos_por_outro', [{'ad_id': i, 'entidade_dona_pagina': d,
                                     'rodape_legal': todos[i].get('bylines'), 'pagina': todos[i].get('page_name')}
                                    for i, d in outro_pagador])
    regioes = []
    for r in linhas:
        for x in todos[r['ad_id']].get('delivery_by_region') or []:
            regioes.append({'ad_id': r['ad_id'], 'regiao': x.get('region'), 'pct': float(x.get('percentage') or 0)})
    _tabela(db, 'anuncio_regioes', regioes)
    db.commit()
    db.close()
    _csv(os.path.join(saida, 'anuncios.csv'), linhas)
    _csv(os.path.join(saida, 'entidades.csv'), ent_rows)

    resumo = {
        'linhas_lidas': n_linhas, 'anuncios_unicos': len(todos), 'duplicatas_removidas': n_linhas - len(todos),
        'com_campos_completos': len(completos), 'atribuidos': len(atrib), 'no_periodo': len(linhas),
        'por_vinculo': dict(Counter(v for _, v in atrib.values())),
        'nao_atribuidos': dict(Counter(descartes.values())),
        'pagos_por_outro_fora_dos_totais': len(outro_pagador),
        'cpm_referencia': round(cpm_ref, 2) if cpm_ref else None,
        'entidades_com_anuncio': sum(1 for r in ent_rows if r['n_anuncios']),
        'gasto_total': {'piso': sum(r['gasto_piso'] for r in ent_rows), 'teto': sum(r['gasto_teto'] for r in ent_rows),
                        'estimado': round(sum(r['gasto_estimado'] for r in ent_rows), 2)},
    }
    with open(os.path.join(saida, '_consolidado.json'), 'w', encoding='utf-8') as f:
        json.dump(resumo, f, ensure_ascii=False, indent=1)
    if verbose:
        print(json.dumps(resumo, ensure_ascii=False, indent=1))
    return resumo, ent_rows


def _tabela(db, nome, linhas):
    if not linhas:
        db.execute('CREATE TABLE %s (vazio TEXT)' % nome)
        return
    cols = list(linhas[0].keys())
    db.execute('CREATE TABLE %s (%s)' % (nome, ', '.join('"%s"' % c for c in cols)))
    db.executemany('INSERT INTO %s VALUES (%s)' % (nome, ','.join('?' * len(cols))),
                   [tuple(r[c] for c in cols) for r in linhas])


def _csv(caminho, linhas):
    with open(caminho, 'w', encoding='utf-8-sig', newline='') as f:
        if not linhas:
            return
        w = csv.DictWriter(f, list(linhas[0].keys()), delimiter=';')
        w.writeheader()
        w.writerows(linhas)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--entidades', required=True)
    ap.add_argument('--coleta', required=True)
    ap.add_argument('--saida', required=True)
    ap.add_argument('--regiao-alvo', default=None, help='nome da região em delivery_by_region')
    ap.add_argument('--inicio', default=None)
    ap.add_argument('--corte', default=None)
    a = ap.parse_args()
    consolidar(ler_entidades(a.entidades), a.coleta, a.saida, a.regiao_alvo, a.inicio, a.corte)


if __name__ == '__main__':
    main()
