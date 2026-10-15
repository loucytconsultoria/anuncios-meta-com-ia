# -*- coding: utf-8 -*-
"""Coletor retomável da Biblioteca de Anúncios: uma etapa por sinal de atribuição.

Ordem recomendada (cada etapa pode ser interrompida e retomada; o que já foi feito é pulado):

  1. documento  uma busca por entidade pelo documento (palavras soltas), todos os campos.
                A busca da Meta também procura no rodapé "pago por"; buscar o documento devolve
                os anúncios daquele pagador quase sem ruído.
  2. nome       busca leve pelo nome completo (rede de segurança: rodapé sem documento).
                Guarda só o que tem o nome no rodapé; os campos completos vêm da etapa 3.
  3. consolidar (python -m coleta.consolidar) uma primeira vez, para confirmar páginas.
  4. pagina     baixa tudo das páginas que já veicularam anúncio confirmado, em lotes de 10 páginas.
  5. consolidar de novo: deduplicação e atribuição final.

Para atualizar a base depois: rode documento/pagina com --data-min no último dia da base e
--sufixo novo; a consolidação lê os arquivos em ordem e a versão mais nova de cada anúncio vence
(faixas de gasto e impressões de quem continuou no ar).

Uso:
  python -m coleta.coletar documento --entidades exemplos/entidades.csv --saida dados/coleta --data-min 2026-01-01
  python -m coleta.coletar nome      --entidades exemplos/entidades.csv --saida dados/coleta --data-min 2026-01-01
  python -m coleta.coletar pagina    --entidades exemplos/entidades.csv --saida dados/coleta --data-min 2026-01-01

Rode um processo de coleta por vez: a cota é da conta, não do processo.
"""
import argparse
import csv
import json
import os
import sys
import traceback

try:
    from . import biblioteca as B
    from .atribuicao import Atribuidor
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import biblioteca as B
    from atribuicao import Atribuidor


def ler_entidades(caminho):
    with open(caminho, encoding='utf-8-sig') as f:
        amostra = f.read(2048)
        f.seek(0)
        sep = ';' if amostra.count(';') > amostra.count(',') else ','
        return [dict(r) for r in csv.DictReader(f, delimiter=sep)]


def ler_jsonl(p):
    if not os.path.exists(p):
        return
    with open(p, encoding='utf-8') as f:
        for linha in f:
            linha = linha.strip()
            if linha:
                try:
                    yield json.loads(linha)
                except Exception:
                    pass


def feitos(caminho_estado):
    """Entidades já processadas sem erro (o arquivo de estado é JSON Lines, um registro por entidade)."""
    return {d['entidade'] for d in ler_jsonl(caminho_estado) if not d.get('erro')}


def anexar(caminho, objs):
    with open(caminho, 'a', encoding='utf-8') as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=False) + '\n')


def etapa_documento(ents, saida, data_min, sufixo=''):
    estado = os.path.join(saida, 'busca_documento%s.jsonl' % sufixo)
    ads_arq = os.path.join(saida, 'ads_completos%s.jsonl' % sufixo)
    logf = os.path.join(saida, 'log_busca_documento.txt')
    ja = feitos(estado)
    B.log(logf, 'entidades=%d ja_feitas=%d' % (len(ents), len(ja)))
    for i, e in enumerate(ents, 1):
        doc = B.digitos(e.get('documento'))
        if e['id'] in ja or not doc:
            continue
        reg = {'entidade': e['id'], 'documento': doc}
        try:
            ads = list(B.paginar(B.url_busca(termo=doc, data_min=data_min), logf))
            for a in ads:
                B.limpar(a)
                a['_fonte'] = 'documento:' + e['id']
            anexar(ads_arq, ads)
            reg.update({'n': len(ads), 'ids': [a['id'] for a in ads]})
            if ads or i % 50 == 0:
                B.log(logf, '%4d/%d %-30s anuncios=%-4d uso=%d%%' % (i, len(ents), e['nome'][:30], len(ads), B._ultimo_uso['v']))
        except SystemExit:
            raise
        except Exception as ex:
            reg['erro'] = str(ex)[:300]
            B.log(logf, '%4d/%d %-30s ERRO %s' % (i, len(ents), e['nome'][:30], ex))
            B.log(logf, traceback.format_exc()[-600:])
        anexar(estado, [reg])
    B.log(logf, 'FIM busca por documento')


def etapa_nome(ents, saida, data_min, sufixo=''):
    estado = os.path.join(saida, 'busca_nome%s.jsonl' % sufixo)
    ads_arq = os.path.join(saida, 'varredura_nomes%s.jsonl' % sufixo)
    logf = os.path.join(saida, 'log_busca_nome.txt')
    ja = feitos(estado)
    for i, e in enumerate(ents, 1):
        if e['id'] in ja:
            continue
        reg = {'entidade': e['id']}
        try:
            ads = list(B.paginar(B.url_busca(termo=e['nome'], campos=B.CAMPOS_LEVES, limite=1000, data_min=data_min),
                                 logf, max_paginas=5))
            alvo = B.norm(e['nome'])
            com_nome = [a for a in ads if alvo in B.norm(a.get('bylines') or '')]
            anexar(ads_arq, com_nome)
            reg.update({'n_brutos': len(ads), 'n_nome_no_rodape': len(com_nome)})
            if com_nome or i % 50 == 0:
                B.log(logf, '%4d/%d %-30s brutos=%-5d nome_no_rodape=%-4d' % (i, len(ents), e['nome'][:30], len(ads), len(com_nome)))
        except SystemExit:
            raise
        except Exception as ex:
            reg['erro'] = str(ex)[:300]
            B.log(logf, '%4d/%d %-30s ERRO %s' % (i, len(ents), e['nome'][:30], ex))
        anexar(estado, [reg])
    B.log(logf, 'FIM busca por nome')


def mapear_paginas(ents, saida):
    """Páginas com pelo menos um anúncio confirmado por sinal forte (documento ou nome)."""
    at = Atribuidor(ents)
    pags, nomes = {}, {}
    arquivos = sorted(f for f in os.listdir(saida) if f.startswith(('ads_completos', 'varredura_')) and f.endswith('.jsonl'))
    for arq in arquivos:
        for a in ler_jsonl(os.path.join(saida, arq)):
            eid, _ = at.sinal_forte(a)
            if eid and a.get('page_id'):
                p = str(a['page_id'])
                pags.setdefault(p, set()).add(eid)
                nomes[p] = a.get('page_name', '')
    out = {p: {'entidades': sorted(c), 'page_name': nomes.get(p, '')} for p, c in pags.items()}
    with open(os.path.join(saida, 'paginas_confirmadas.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    return out


def etapa_pagina(ents, saida, data_min, sufixo=''):
    logf = os.path.join(saida, 'log_paginas.txt')
    ads_arq = os.path.join(saida, 'ads_completos%s.jsonl' % sufixo)
    estado_arq = os.path.join(saida, '_estado_paginas%s.json' % sufixo)
    pags = mapear_paginas(ents, saida)
    estado = json.load(open(estado_arq, encoding='utf-8')) if os.path.exists(estado_arq) else {'feitos': []}
    feitos_ = set(estado['feitos'])
    ja_pag = {p for k in feitos_ for p in k.split(',')}
    # só páginas ainda não baixadas: página confirmada depois não desloca os lotes antigos
    pend = [p for p in sorted(pags) if p not in ja_pag]
    lotes = [pend[i:i + 10] for i in range(0, len(pend), 10)]
    B.log(logf, 'paginas confirmadas=%d lotes pendentes=%d' % (len(pags), len(lotes)))
    total = 0
    for k, lote in enumerate(lotes, 1):
        n = 0
        buf = []
        for a in B.paginar(B.url_busca(page_ids=lote, data_min=data_min), logf):
            B.limpar(a)
            a['_fonte'] = 'pagina'
            buf.append(a)
            n += 1
        anexar(ads_arq, buf)
        total += n
        feitos_.add(','.join(lote))
        tmp = estado_arq + '.tmp'
        json.dump({'feitos': sorted(feitos_)}, open(tmp, 'w', encoding='utf-8'))
        os.replace(tmp, estado_arq)
        B.log(logf, '   lote %d/%d: %d anuncios (acumulado %d)' % (k, len(lotes), n, total))
    B.log(logf, 'FIM coleta por pagina: %d anuncios' % total)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('etapa', choices=['documento', 'nome', 'pagina'])
    ap.add_argument('--entidades', required=True, help='CSV com id,nome,apelido,documento')
    ap.add_argument('--saida', required=True, help='pasta da coleta (arquivos JSON Lines)')
    ap.add_argument('--data-min', default=None, help='AAAA-MM-DD: só anúncios entregues a partir desta data')
    ap.add_argument('--sufixo', default='', help='sufixo dos arquivos (ex.: _atual_1015) para atualizações')
    args = ap.parse_args()
    os.makedirs(args.saida, exist_ok=True)
    ents = ler_entidades(args.entidades)
    {'documento': etapa_documento, 'nome': etapa_nome, 'pagina': etapa_pagina}[args.etapa](
        ents, args.saida, args.data_min, args.sufixo)


if __name__ == '__main__':
    main()
