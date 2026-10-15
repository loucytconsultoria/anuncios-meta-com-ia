# -*- coding: utf-8 -*-
"""Valida o que os subagentes gravaram, consolida por lote e lista o que precisa rodar de novo.

Para cada lote:
  - imagens: um arquivo <nome_da_imagem>.json por item (protocolo um a um). O campo "arquivo"
    dentro do JSON tem de bater com o nome do arquivo; se contradiz, o item é refeito.
  - textos:  um arquivo <lote>.jsonl com uma linha por item, na ordem do manifesto.
  - todo id do manifesto precisa ter resultado; JSON quebrado ou item faltando devolve o lote
    para a fila (o arquivo vira .invalido, nada é apagado em silêncio).
  - código fora do vocabulário NÃO derruba o lote: vira 'outro' e o termo vai para <campo>_outros.

Saída: <trabalho>/_pendentes.json ({img: [...], txt: [...], qa: [...], qatxt: [...]})

Uso: python -m classificacao.validar_lotes --trabalho dados/leitura --vocabulario exemplos/vocabularios/politicas_publicas.json
"""
import argparse
import sys
import glob
import json
import os

try:
    from .vocabulario import Vocabulario
except ImportError:
    from vocabulario import Vocabulario

SUBS = (('img', 'img', 'img'), ('txt', 'txt', 'txt'), ('qa', 'img', 'img'), ('qatxt', 'txt', 'txt'))


def _stem(p):
    return os.path.splitext(os.path.basename(p))[0]


def validar_sub(trab, sub, chave, tipo, voc):
    pend = []
    st = {'lotes': 0, 'ok': 0, 'invalidos': 0, 'sem_resultado': 0, 'codigos_corrigidos': 0, 'itens_com_erro': 0}
    for f in sorted(glob.glob(os.path.join(trab, 'lotes_' + sub, '*.json'))):
        st['lotes'] += 1
        nome = os.path.basename(f)[:-5]
        with open(f, encoding='utf-8') as fh:
            itens = json.load(fh)['itens']
        man = [x[chave] for x in itens]
        stem_de = {x[chave]: _stem(x['caminho']) for x in itens if 'caminho' in x}
        r = os.path.join(trab, 'resultados_' + sub, nome + '.jsonl')
        pasta = os.path.join(trab, 'resultados_' + sub, nome)
        linhas, ok = [], True
        if os.path.exists(r):
            with open(r, encoding='utf-8', errors='replace') as fh:
                for l in fh:
                    l = l.strip()
                    if not l:
                        continue
                    try:
                        linhas.append(json.loads(l))
                    except Exception:
                        ok = False
                        break
        elif os.path.isdir(pasta):
            for x in man:
                fx = os.path.join(pasta, stem_de.get(x, x) + '.json')
                if not os.path.exists(fx):
                    continue
                try:
                    with open(fx, encoding='utf-8', errors='replace') as fh:
                        d = json.loads(fh.read().strip())
                    if isinstance(d, list):
                        d = d[0]
                    if d.get('arquivo') not in (None, stem_de.get(x)):
                        continue                    # "arquivo" contradiz o nome do arquivo: refazer
                    d[chave] = x
                    linhas.append(d)
                except Exception:
                    pass
            if len(linhas) < len(man):
                st['sem_resultado'] += 1
                pend.append(nome)
                continue
        else:
            st['sem_resultado'] += 1
            pend.append(nome)
            continue
        ids = [d.get(chave) for d in linhas]
        if not ok or any(x not in ids for x in man):
            if os.path.exists(r):
                os.replace(r, r + '.invalido')
            st['invalidos'] += 1
            pend.append(nome)
            continue
        saida = []
        for d in linhas:
            if d.get('erro'):
                st['itens_com_erro'] += 1
                saida.append(d)
                continue
            d2, n = voc.limpar(d, tipo)
            st['codigos_corrigidos'] += n
            saida.append(d2)
        with open(r, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(json.dumps(d, ensure_ascii=False) for d in saida) + '\n')
        st['ok'] += 1
    return pend, st


def validar(trab, caminho_voc):
    voc = Vocabulario.carregar(caminho_voc)
    out, stats = {}, {}
    for sub, chave, tipo in SUBS:
        pend, st = validar_sub(trab, sub, chave, tipo, voc)
        out[sub] = pend
        stats[sub] = st
    with open(os.path.join(trab, '_pendentes.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f)
    with open(os.path.join(trab, '_validacao.json'), 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    return out, stats


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--trabalho', required=True)
    ap.add_argument('--vocabulario', required=True)
    a = ap.parse_args()
    out, stats = validar(a.trabalho, a.vocabulario)
    for k, v in stats.items():
        print(k, v)
    print('pendentes: ' + ' '.join('%s=%d' % (k, len(v)) for k, v in out.items()))


if __name__ == '__main__':
    main()
